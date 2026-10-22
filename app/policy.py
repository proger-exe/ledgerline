"""Policy engine. Shared by the API (at submit/approve) and the signer (re-check before signing).

Policy is JSON stored in the DB (owner edits it in the app, every save is a new version):
  limits         per-role autonomy: above transaction/daily limit the `over_limit` fact is true
  wallet_access  wallet types a role may pay from (hard block)
  assets         assets that may be sent at all (hard block)
  rules          ordered; the FIRST rule whose `if` matches decides the approvers; no match = auto-approved
                 conditions: amount_over, wallet_type, new_address, over_limit, over_budget, role
                 approvers: list of slots, each slot = roles that can fill it; owner fills any slot;
                 one person fills one slot; creator never approves own payment
  alerts         thresholds for bot notifications
"""
from datetime import timedelta
from decimal import Decimal
from itertools import permutations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IN_FLIGHT, ROLES, WALLET_TYPES, CounterpartyAddress, LedgerLine, Payment, Policy, Wallet

DEFAULT_POLICY = {
    "limits": {
        "owner": {"transaction": None, "daily": None},
        "cfo": {"transaction": 1000, "daily": 5000},
        "accountant": {"transaction": 250, "daily": 750},
    },
    "wallet_access": {
        "owner": ["TREASURY", "OPERATIONS", "PROJECT", "OTHER"],
        "cfo": ["OPERATIONS", "PROJECT"],
        "accountant": ["OPERATIONS"],
    },
    "assets": ["USDT"],
    "rules": [
        {"name": "Treasury transaction", "if": {"wallet_type": "TREASURY"}, "approvers": [["cfo"], ["cfo"]]},
        {"name": "Over 1000", "if": {"amount_over": 1000}, "approvers": [["owner"], ["cfo"]]},
        {"name": "New address", "if": {"new_address": True}, "approvers": [["cfo"]]},
        {"name": "Over 250", "if": {"amount_over": 250}, "approvers": [["cfo"]]},
        {"name": "Over initiator limit", "if": {"over_limit": True}, "approvers": [["cfo"]]},
        {"name": "Over project balance", "if": {"over_budget": True}, "approvers": [["cfo"]]},
    ],
    "alerts": {"low_operations_balance": 100, "low_gas_balance": 50, "large_deposit": 1000},
}

CONDITIONS = {"amount_over", "wallet_type", "new_address", "over_limit", "over_budget", "role"}


def validate(p: dict) -> None:
    """Raise ValueError with a human message if the policy is malformed."""
    try:
        for role, lim in p["limits"].items():
            assert role in ROLES, f"unknown role {role!r} in limits"
            assert set(lim) <= {"transaction", "daily"}, f"limits.{role}: only transaction/daily allowed"
            for v in lim.values():
                assert v is None or (isinstance(v, (int, float)) and v >= 0), f"limits.{role}: bad number {v!r}"
        for role, types in p["wallet_access"].items():
            assert role in ROLES, f"unknown role {role!r} in wallet_access"
            assert set(types) <= set(WALLET_TYPES), f"wallet_access.{role}: unknown wallet type"
        assert all(isinstance(a, str) for a in p["assets"]), "assets must be a list of strings"
        for rule in p["rules"]:
            name = rule["name"]
            assert set(rule["if"]) <= CONDITIONS, f"rule {name!r}: unknown condition {set(rule['if']) - CONDITIONS}"
            assert all(slot and set(slot) <= set(ROLES) for slot in rule["approvers"]), f"rule {name!r}: bad approvers"
        for v in p["alerts"].values():
            assert isinstance(v, (int, float)), "alerts must be numbers"
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"malformed policy: {e!r}") from e
    except AssertionError as e:
        raise ValueError(str(e)) from e


def evaluate(p: dict, *, role: str, wallet_type: str, asset: str, amount: Decimal, new_address: bool,
             spent_24h: Decimal, project_available: Decimal) -> dict:
    """→ {"error": str | None, "rule": str | None, "slots": [[role, ...], ...]}"""
    if wallet_type not in p["wallet_access"].get(role, []):
        return {"error": f"Your role can't send from {wallet_type.lower()} wallets.", "rule": None, "slots": []}
    if asset not in p["assets"]:
        return {"error": f"{asset} payments are disabled by policy.", "rule": None, "slots": []}
    lim = p["limits"].get(role, {})
    tx_lim, day_lim = lim.get("transaction"), lim.get("daily")
    facts = {
        "wallet_type": wallet_type,
        "new_address": new_address,
        "over_limit": (tx_lim is not None and amount > Decimal(str(tx_lim)))
        or (day_lim is not None and spent_24h + amount > Decimal(str(day_lim))),
        "over_budget": amount > project_available,
        "role": role,
    }
    for rule in p["rules"]:
        if all(amount > Decimal(str(v)) if k == "amount_over" else facts[k] == v for k, v in rule["if"].items()):
            return {"error": None, "rule": rule["name"], "slots": rule["approvers"]}
    return {"error": None, "rule": None, "slots": []}


def satisfied(slots: list[list[str]], approvers: list[tuple[int, str]]) -> bool:
    """Can distinct approvers (user_id, role) fill every slot? Owner fills any slot."""
    # ponytail: brute-force matching, fine for the 1–3 slots a policy realistically has.
    return any(
        all(role == "owner" or role in slot for (_, role), slot in zip(combo, slots))
        for combo in permutations(approvers, len(slots))
    )


def can_fill(slots: list[list[str]], role: str) -> bool:
    return role == "owner" or any(role in slot for slot in slots)


async def active_policy(s: AsyncSession) -> dict:
    return await s.scalar(select(Policy.data).order_by(Policy.id.desc()).limit(1)) or DEFAULT_POLICY


async def project_available(s: AsyncSession, project_id: int | None, *, before_payment: int | None = None) -> Decimal:
    """Ledger balance of the project across wallets minus payments still in flight (optionally only earlier ones)."""
    balance = await s.scalar(
        select(func.coalesce(func.sum(LedgerLine.amount), 0))
        .where(LedgerLine.account.like("wallet:%"), LedgerLine.project_id.is_not_distinct_from(project_id))
    )
    q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.project_id.is_not_distinct_from(project_id), Payment.status.in_(IN_FLIGHT))
    if before_payment is not None:
        q = q.where(Payment.id < before_payment)
    return Decimal(balance) - Decimal(await s.scalar(q))


async def requirement(s: AsyncSession, p: Payment, role: str) -> dict:
    """Evaluate the active policy for payment `p` initiated by someone with `role`.

    Facts only count earlier payments, so the signer re-running this later reaches the same verdict
    unless the world genuinely changed (then it refuses and the payment fails with the reason).
    """
    initiator = p.submitted_by or p.created_by
    wallet = await s.get(Wallet, p.wallet_id)
    address = await s.get(CounterpartyAddress, p.address_id)
    spent_q = select(func.coalesce(func.sum(Payment.amount), 0)).where(
        Payment.submitted_by == initiator,
        Payment.status.in_((*IN_FLIGHT, "CONFIRMED")),
        Payment.created_at > (p.created_at or func.now()) - timedelta(hours=24),
    )
    if p.id:
        spent_q = spent_q.where(Payment.id < p.id)
    return evaluate(
        await active_policy(s),
        role=role,
        wallet_type=wallet.type,
        asset=p.asset,
        amount=p.amount,
        new_address=not address.verified,
        spent_24h=Decimal(await s.scalar(spent_q)),
        project_available=await project_available(s, p.project_id, before_payment=p.id),
    )
