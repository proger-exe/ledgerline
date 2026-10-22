"""Background loop: pushes stuck payments, tracks confirmations, syncs balances, books deposits."""
import asyncio
import logging
import time
from datetime import timedelta

from sqlalchemy import select

from app import config, ledger, notify, payments
from app.blockchain import provider
from app.db import Session
from app.models import Counterparty, CounterpartyAddress, LedgerEntry, Payment, Wallet
from app.policy import active_policy

log = logging.getLogger(__name__)
_alerted: dict[str, float] = {}  # ponytail: in-memory alert throttle, resets on restart


def once_per(key: str, hours: float = 6) -> bool:
    if time.time() - _alerted.get(key, 0) < hours * 3600:
        return False
    _alerted[key] = time.time()
    return True


def short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


async def run() -> None:
    while True:
        try:
            await tick()
        except Exception:
            log.exception("watcher tick failed")
        await asyncio.sleep(config.WATCH_INTERVAL)


async def tick() -> None:
    async with Session() as s:
        cutoff = payments.now() - timedelta(minutes=2)
        stuck = list(await s.scalars(select(Payment.id).where(
            Payment.status.in_(("APPROVED", "SIGNING")), Payment.approved_at < cutoff)))
        tracked = list(await s.scalars(select(Payment.id).where(Payment.status.in_(("BROADCASTED", "CONFIRMING")))))
        wallets = list(await s.scalars(select(Wallet.id).where(Wallet.status == "active")))
    jobs = [(payments.execute, pid) for pid in stuck] + [(payments.track, pid) for pid in tracked] \
        + [(sync_wallet, wid) for wid in wallets]
    for job, arg in jobs:  # one broken payment/wallet must not stall the rest
        try:
            await job(arg)
        except Exception:
            log.exception("%s(%s) failed", job.__name__, arg)


async def sync_wallet(wallet_id: int) -> None:
    alerts: list[tuple[tuple[str, ...], str, str]] = []
    async with Session() as s:
        w = await s.get(Wallet, wallet_id)
        prov = provider(w.network)
        w.balance = await prov.get_balance(w.address, w.asset)
        w.native_balance = await prov.get_balance(w.address, prov.native)
        ours = dict((await s.execute(select(Wallet.address, Wallet.id).where(Wallet.network == w.network))).all())
        cfg = (await active_policy(s))["alerts"]

        # Inclusive cursor: same-block transfers split across pages aren't lost; re-seen ones are skipped below.
        for t in await prov.get_transfers(w.address, w.asset, w.scan_cursor_ms):
            w.scan_cursor_ms = max(w.scan_cursor_ms, t["ts"])
            if await s.scalar(select(LedgerEntry.id).where(LedgerEntry.tx_hash == t["tx_hash"])) \
                    or await s.scalar(select(Payment.id).where(Payment.tx_hash == t["tx_hash"])):
                continue  # already booked, or our own payment (booked on confirmation)
            a, asset, src, dst = t["amount"], t["asset"], ours.get(t["from"]), ours.get(t["to"])
            if src and dst:
                ledger.post(s, "INTERNAL", [ledger.line(f"wallet:{dst}", a, asset), ledger.line(f"wallet:{src}", -a, asset)],
                            tx_hash=t["tx_hash"], description="Transfer between wallets")
            elif dst:
                name = await s.scalar(select(Counterparty.name).join(CounterpartyAddress).where(
                    CounterpartyAddress.address == t["from"])) or short(t["from"])
                ledger.post(s, "DEPOSIT", [ledger.line(f"wallet:{dst}", a, asset), ledger.line("income", -a, asset)],
                            tx_hash=t["tx_hash"], description=f"Deposit from {name}")
                if a >= cfg.get("large_deposit", 10**12):
                    alerts.append((("owner", "cfo"), f"<b>Large incoming transfer</b>\n\n+{a:,.2f} {asset}\n"
                                                    f"to {w.name} from {short(t['from'])}", f"/wallets/{w.id}"))
            else:
                ledger.post(s, "UNRECONCILED", [ledger.line("suspense", a, asset), ledger.line(f"wallet:{src}", -a, asset)],
                            tx_hash=t["tx_hash"], description=f"Unknown outgoing transfer to {short(t['to'])}")
                alerts.append((("owner",), f"<b>Suspicious operation</b>\n\n−{a:,.2f} {asset} left {w.name} to "
                                           f"{short(t['to'])} without a payment in Ledgerline.", f"/wallets/{w.id}"))
            await s.flush()
        await s.commit()

    if w.type == "OPERATIONS":
        if w.balance < cfg.get("low_operations_balance", 0) and once_per(f"low:{w.id}"):
            alerts.append((("owner", "cfo"), f"<b>Low balance</b>\n\n{w.name}: {w.balance:,.2f} {w.asset}", f"/wallets/{w.id}"))
        if w.native_balance < cfg.get("low_gas_balance", 0) and once_per(f"gas:{w.id}"):
            alerts.append((("owner", "cfo"), f"<b>Low {prov.native} for network fees</b>\n\n{w.name}: "
                                             f"{w.native_balance:,.2f} {prov.native}", f"/wallets/{w.id}"))
    for roles, text, route in alerts:
        await notify.alert(roles, text, route)

