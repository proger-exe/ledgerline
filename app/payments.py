"""Payment lifecycle. Every transition is a PaymentEvent row.

DRAFT → PENDING_APPROVAL → APPROVED → SIGNING → BROADCASTED → CONFIRMING → CONFIRMED
                      ↘ REJECTED    ↘ CANCELLED       ↘ FAILED
Ledger is posted only at CONFIRMED; until then the amount is "in flight" (reserved).
"""
import logging
from datetime import UTC, datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config, ledger, notify, policy
from app.blockchain import BroadcastRejected, provider
from app.db import Session
from app.models import IN_FLIGHT, Budget, CounterpartyAddress, LedgerEntry, LedgerLine, Payment, PaymentEvent, User, Wallet

log = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(UTC)


def event(s: AsyncSession, p: Payment, action: str, actor_id: int | None = None, comment: str = "") -> None:
    s.add(PaymentEvent(payment_id=p.id, action=action, status=p.status, actor_id=actor_id, comment=comment))


async def lock(s: AsyncSession, payment_id: int) -> Payment | None:
    return await s.scalar(select(Payment).where(Payment.id == payment_id).with_for_update(of=Payment))


async def wallet_available(s: AsyncSession, w: Wallet, exclude_payment: int | None = None):
    """Booked balance minus everything not yet booked, capped by the last seen on-chain balance.

    The chain balance alone double-counts a broadcast payment (already gone on-chain, still in flight).
    """
    def in_flight(statuses):
        q = select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.wallet_id == w.id, Payment.status.in_(statuses))
        return q.where(Payment.id != exclude_payment) if exclude_payment else q

    booked = await s.scalar(select(func.coalesce(func.sum(LedgerLine.amount), 0)).where(LedgerLine.account == f"wallet:{w.id}"))
    return min(booked - await s.scalar(in_flight(IN_FLIGHT)),
               w.balance - await s.scalar(in_flight(("PENDING_APPROVAL", "APPROVED", "SIGNING"))))


async def votes(s: AsyncSession, p: Payment) -> list[User]:
    return list(await s.scalars(select(User).join(PaymentEvent, PaymentEvent.actor_id == User.id).where(
        PaymentEvent.payment_id == p.id, PaymentEvent.action == "APPROVE", User.status == "active")))


async def submit(s: AsyncSession, p: Payment, user: User, action: str) -> None:
    """Apply policy for initiator `user`: → PENDING_APPROVAL, or APPROVED if no rule matches."""
    p.submitted_by = user.id
    req = await policy.requirement(s, p, user.role)
    if req["error"]:
        raise HTTPException(422, req["error"])
    w = await s.get(Wallet, p.wallet_id)
    available = await wallet_available(s, w, exclude_payment=p.id)
    if p.amount > available:
        raise HTTPException(422, f"Payment couldn't be created. The {w.name} wallet has {available:,.2f} {w.asset} "
                                 f"available, but this payment requires {p.amount:,.2f} {p.asset}.")
    p.approval = {"rule": req["rule"], "slots": req["slots"]}
    if req["slots"]:
        p.status = "PENDING_APPROVAL"
    else:
        p.status, p.approved_at = "APPROVED", now()
    event(s, p, action, user.id)


def check_voter(p: Payment, user: User, voters: list[User]) -> None:
    if p.status != "PENDING_APPROVAL":
        raise HTTPException(409, f"This payment is {p.status.lower().replace('_', ' ')}: nothing to decide.")
    if user.id in (p.created_by, p.submitted_by):
        raise HTTPException(403, "You can't approve or reject a payment you created.")
    if not policy.can_fill(p.approval["slots"], user.role):
        raise HTTPException(403, "This payment needs approval from a different role.")
    if user.id in {v.id for v in voters}:
        raise HTTPException(409, "You already approved this payment.")


async def approve(s: AsyncSession, p: Payment, user: User) -> None:
    voters = await votes(s, p)
    check_voter(p, user, voters)
    voters.append(user)
    if policy.satisfied(p.approval["slots"], [(v.id, v.role) for v in voters]):
        p.status, p.approved_at = "APPROVED", now()
        address = await s.get(CounterpartyAddress, p.address_id)
        verifier = next((v for v in voters if v.role in ("owner", "cfo")), None)
        if verifier and not address.verified:
            address.verified, address.verified_by = True, verifier.id
    event(s, p, "APPROVE", user.id)


async def reject(s: AsyncSession, p: Payment, user: User, comment: str) -> None:
    check_voter(p, user, await votes(s, p))
    p.status = "REJECTED"
    event(s, p, "REJECTED", user.id, comment)


async def fail(s: AsyncSession, p: Payment, reason: str) -> None:
    p.status, p.error = "FAILED", reason
    event(s, p, "FAILED", comment=reason)
    await s.commit()
    await notify.payment(p.id, "failed")


async def execute(payment_id: int) -> None:
    """APPROVED → SIGNING → BROADCASTED. Idempotent: the watcher re-runs it for stuck payments."""
    async with Session() as s:
        p = await lock(s, payment_id)
        if not p or p.status not in ("APPROVED", "SIGNING"):
            return
        prov, w = provider(p.network), p.wallet
        if p.status == "APPROVED":
            try:
                w.balance = await prov.get_balance(w.address, p.asset)
                w.native_balance = await prov.get_balance(w.address, prov.native)
                fee = await prov.estimate_fee(p.destination_address, p.asset)
            except Exception:
                log.exception("chain unreachable, payment %s stays APPROVED", p.id)
                return
            if w.balance < p.amount:
                return await fail(s, p, f"Payment couldn't be sent. The {w.name} wallet has {w.balance:,.2f} {p.asset}, "
                                        f"but this payment requires {p.amount:,.2f} {p.asset}.")
            if w.native_balance < fee:
                return await fail(s, p, f"Payment couldn't be sent. The {w.name} wallet has {w.native_balance:,.2f} "
                                        f"{prov.native} for network fees, but this transfer may burn up to "
                                        f"{fee:,.2f} {prov.native}. Top up {prov.native} and create the payment again.")
            p.status = "SIGNING"
            event(s, p, "SIGNING")
            await s.commit()

        try:
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.post(f"{config.SIGNER_URL}/sign", json={"payment_id": p.id},
                                    headers={"X-Signer-Token": config.SIGNER_TOKEN})
        except httpx.HTTPError as e:
            log.warning("signer unreachable for payment %s: %s", p.id, e)
            return
        if r.status_code in (403, 409, 422):
            return await fail(s, p, f"Signer refused: {r.json().get('detail', r.text)}")
        if r.status_code != 200:
            log.warning("signer error %s for payment %s: %s", r.status_code, p.id, r.text)
            return
        signed = r.json()
        if p.tx_hash != signed["tx_hash"]:
            p.tx_hash = signed["tx_hash"]
            event(s, p, "SIGNED")
            await s.commit()

        try:
            await prov.broadcast(signed["signed"])
        except BroadcastRejected as e:
            if not await prov.get_transaction(p.tx_hash):  # rejected re-broadcast of an already mined tx is fine
                return await fail(s, p, f"The network rejected the transaction ({e}). No funds left the wallet.")
        except httpx.HTTPError as e:
            log.warning("broadcast of payment %s unclear, will retry: %s", p.id, e)
            return
        p.status, p.broadcasted_at = "BROADCASTED", now()
        event(s, p, "BROADCASTED")
        await s.commit()


async def track(payment_id: int) -> None:
    """BROADCASTED/CONFIRMING → CONFIRMED (+ ledger) or FAILED."""
    async with Session() as s:
        p = await lock(s, payment_id)
        if not p or p.status not in ("BROADCASTED", "CONFIRMING"):
            return
        info = await provider(p.network).get_transaction(p.tx_hash)
        if info is None:
            if p.broadcasted_at < now() - timedelta(minutes=10):
                await fail(s, p, "The network dropped the transaction: it never made it into a block. "
                                 "No funds left the wallet.")
            return
        p.block, p.confirmations, p.fee = info["block"], info["confirmations"], info["fee"]
        if not info["success"]:
            return await fail(s, p, f"The transaction reverted on-chain. The {p.asset} stayed in the wallet; "
                                    f"the network fee was charged.")
        if p.confirmations < config.CONFIRMATIONS:
            if p.status == "BROADCASTED":
                p.status = "CONFIRMING"
                event(s, p, "CONFIRMING", comment=f"Block {p.block}")
            await s.commit()
            return
        p.status, p.confirmed_at = "CONFIRMED", now()
        event(s, p, "CONFIRMED")
        ledger.post(s, "PAYMENT", [
            ledger.line("expense", p.amount, p.asset, p.project_id, p.category),
            ledger.line(f"wallet:{p.wallet_id}", -p.amount, p.asset, p.project_id),
        ], payment_id=p.id, tx_hash=p.tx_hash, created_by=p.created_by,
            description=f"{p.code} · {p.counterparty.name} · {p.description}".strip(" ·"))
        await s.commit()
        await notify.payment(p.id, "confirmed")
        await budget_check(s, p)


def month_bounds(period: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(period, "%Y-%m").replace(tzinfo=UTC)
    return start, (start + timedelta(days=32)).replace(day=1)


async def month_spent(s: AsyncSession, project_id: int, period: str):
    start, end = month_bounds(period)
    return await s.scalar(select(func.coalesce(func.sum(LedgerLine.amount), 0)).join(LedgerEntry).where(
        LedgerLine.account == "expense", LedgerLine.project_id == project_id,
        LedgerEntry.created_at >= start, LedgerEntry.created_at < end))


async def budget_check(s: AsyncSession, p: Payment) -> None:
    period = p.confirmed_at.strftime("%Y-%m")
    budget = await s.scalar(select(Budget).where(Budget.project_id == p.project_id, Budget.period == period))
    if not budget:
        return
    spent = await month_spent(s, p.project_id, period)
    if spent > budget.allocated >= spent - p.amount:  # this payment crossed the line
        manager = [await s.get(User, p.project.manager_id)] if p.project.manager_id else []
        await notify.alert(("owner", "cfo"), f"<b>Budget exceeded</b>\n\n{p.project.name} · {period}\n"
                           f"Spent {spent:,.2f} of {budget.allocated:,.2f} {budget.currency}",
                           f"/projects/{p.project_id}", manager)
