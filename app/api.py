import csv
import io
from datetime import timedelta
from decimal import Decimal
from typing import Annotated, Literal

import segno
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config, ledger, notify, payments, policy
from app.auth import audit, current_user, require
from app.blockchain import provider
from app.db import get_session
from app.models import (
    IN_FLIGHT, ROLES, WALLET_TYPES, Attachment, AuditLog, Budget, Category, Counterparty, CounterpartyAddress,
    LedgerEntry, LedgerLine, Payment, PaymentEvent, Policy, Project, User, Wallet,
)
from app.payments import month_bounds, now

router = APIRouter(prefix="/api")
Me = Annotated[User, Depends(current_user)]
DB = Annotated[AsyncSession, Depends(get_session)]
Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6)]
STAFF = ("owner", "cfo", "accountant")  # roles that move money
READERS = ("owner", "cfo", "accountant", "viewer")  # see everything; project managers see their projects only


def this_month() -> str:
    return now().strftime("%Y-%m")


async def my_project_ids(s: AsyncSession, user: User) -> list[int] | None:
    """None = all projects."""
    if user.role != "project_manager":
        return None
    return list(await s.scalars(select(Project.id).where(Project.manager_id == user.id)))


def scoped(q, ids: list[int] | None, column=Payment.project_id):
    return q if ids is None else q.where(column.in_(ids))


def payment_row(p: Payment) -> dict:
    return {"id": p.id, "code": p.code, "amount": p.amount, "asset": p.asset, "status": p.status,
            "counterparty": p.counterparty.name, "counterparty_id": p.counterparty_id,
            "project": p.project.name, "project_id": p.project_id, "category": p.category,
            "description": p.description, "created_at": p.created_at, "tx_hash": p.tx_hash}


async def get_payment(s: AsyncSession, user: User, pid: int, lock: bool = False) -> Payment:
    p = await (payments.lock(s, pid) if lock else s.get(Payment, pid))
    ids = await my_project_ids(s, user)
    if not p or (ids is not None and p.project_id not in ids):
        raise HTTPException(404, "Payment not found.")
    return p


async def project_balances(s: AsyncSession) -> dict[int | None, Decimal]:
    """Internal balance per project (None = unallocated) minus money in flight."""
    rows = await s.execute(select(LedgerLine.project_id, func.sum(LedgerLine.amount))
                           .where(LedgerLine.account.like("wallet:%")).group_by(LedgerLine.project_id))
    out = {pid: amount for pid, amount in rows}
    for pid, amount in await s.execute(select(Payment.project_id, func.sum(Payment.amount))
                                       .where(Payment.status.in_(IN_FLIGHT)).group_by(Payment.project_id)):
        out[pid] = out.get(pid, 0) - amount
    return out


async def budgets(s: AsyncSession, period: str, ids: list[int] | None = None) -> dict[int, dict]:
    start, end = month_bounds(period)
    alloc = dict((await s.execute(scoped(select(Budget.project_id, Budget.allocated).where(Budget.period == period),
                                         ids, Budget.project_id))).all())
    spent = dict((await s.execute(select(LedgerLine.project_id, func.sum(LedgerLine.amount)).join(LedgerEntry).where(
        LedgerLine.account == "expense", LedgerEntry.created_at >= start, LedgerEntry.created_at < end)
        .group_by(LedgerLine.project_id))).all())
    reserved = dict((await s.execute(select(Payment.project_id, func.sum(Payment.amount))
                                     .where(Payment.status.in_(IN_FLIGHT)).group_by(Payment.project_id))).all())
    return {pid: {"period": period, "allocated": a, "spent": spent.get(pid, 0), "reserved": reserved.get(pid, 0),
                  "remaining": a - spent.get(pid, 0) - reserved.get(pid, 0)} for pid, a in alloc.items()}


# ---------------------------------------------------------------- bootstrap / overview

@router.get("/bootstrap")
async def bootstrap(user: Me, s: DB):
    ids = await my_project_ids(s, user)
    projects = await s.scalars(scoped(select(Project).where(Project.status == "active"), ids, Project.id).order_by(Project.name))
    wallets = await s.scalars(select(Wallet).where(Wallet.status == "active").order_by(Wallet.id))
    return {
        "me": {"id": user.id, "name": user.display_name, "role": user.role, "telegram_id": user.telegram_id},
        "network": config.TRON_NETWORK,
        "categories": list(await s.scalars(select(Category.name).order_by(Category.id))),
        "projects": [{"id": p.id, "name": p.name, "code": p.code} for p in projects],
        "wallets": [{"id": w.id, "name": w.name, "type": w.type, "asset": w.asset, "network": w.network,
                     "address": w.address, "balance": w.balance if ids is None else None} for w in wallets],
        "users": [{"id": u.id, "name": u.display_name, "role": u.role}
                  for u in await s.scalars(select(User).where(User.status == "active").order_by(User.display_name))],
    }


async def pending_for(s: AsyncSession, user: User) -> list[Payment]:
    if user.role not in STAFF:
        return []
    out = []
    for p in await s.scalars(select(Payment).where(Payment.status == "PENDING_APPROVAL").order_by(Payment.id.desc())):
        voters = await payments.votes(s, p)
        try:
            payments.check_voter(p, user, voters)
            out.append(p)
        except HTTPException:
            pass
    return out


async def activity(s: AsyncSession, user: User, limit: int = 10) -> list[dict]:
    ids = await my_project_ids(s, user)
    items = [{"type": "payment", **payment_row(p)} for p in await s.scalars(
        scoped(select(Payment), ids).order_by(Payment.id.desc()).limit(limit))]
    if ids is None:
        names = dict((await s.execute(select(Project.id, Project.name))).all())
        for e in await s.scalars(select(LedgerEntry).where(LedgerEntry.kind != "PAYMENT")
                                 .order_by(LedgerEntry.id.desc()).limit(limit)):
            wallet_lines = [ln for ln in e.lines if ln.account.startswith("wallet:")]
            if not wallet_lines:
                continue
            net = sum(ln.amount for ln in wallet_lines)
            projects = {names.get(ln.project_id, "Unallocated") for ln in wallet_lines}
            items.append({"type": "entry", "id": e.id, "kind": e.kind, "title": e.description, "created_at": e.created_at,
                          "amount": net if net else max(ln.amount for ln in wallet_lines),
                          "signed": bool(net), "asset": wallet_lines[0].asset, "subtitle": " · ".join(sorted(projects))})
    return sorted(items, key=lambda i: i["created_at"], reverse=True)[:limit]


async def summary(s: AsyncSession, user: User) -> dict:
    wallets = list(await s.scalars(select(Wallet).where(Wallet.status == "active", Wallet.asset == "USDT")))
    by_type = lambda t: sum((w.balance for w in wallets if w.type == t), Decimal(0))  # noqa: E731
    return {"total": sum((w.balance for w in wallets), Decimal(0)), "treasury": by_type("TREASURY"),
            "operations": by_type("OPERATIONS"), "pending_approvals": len(await pending_for(s, user))}


@router.get("/overview")
async def overview(user: Me, s: DB):
    ids = await my_project_ids(s, user)
    start, _ = month_bounds(this_month())
    month_net = await s.scalar(select(func.coalesce(func.sum(LedgerLine.amount), 0)).join(LedgerEntry).where(
        LedgerLine.account.like("wallet:%"), LedgerEntry.kind != "OPENING", LedgerEntry.created_at >= start))
    b = await budgets(s, this_month(), ids)
    return {
        **({"pending_approvals": 0} if ids is not None else await summary(s, user)),
        "month_net": month_net if ids is None else None,
        "available_budget": sum((x["remaining"] for x in b.values()), Decimal(0)),
        "activity": await activity(s, user),
    }


# ---------------------------------------------------------------- payments

class PaymentIn(BaseModel):
    wallet_id: int
    project_id: int
    counterparty_id: int
    address_id: int
    amount: Amount
    category: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=500)


async def new_payment(s: AsyncSession, user: User, body: PaymentIn) -> Payment:
    require(user, *STAFF, "project_manager")
    w = await s.get(Wallet, body.wallet_id)
    if not w or w.status != "active":
        raise HTTPException(422, "Choose a wallet to pay from.")
    if w.type == "TREASURY":
        raise HTTPException(422, "Treasury is a cold wallet: its transfers are signed offline. "
                                 "Pay from Operations and top it up from Treasury when needed.")
    project = await s.get(Project, body.project_id)
    if not project or project.status != "active":
        raise HTTPException(422, "Choose a project.")
    if user.role == "project_manager" and project.manager_id != user.id:
        raise HTTPException(403, f"You don't manage {project.name}.")
    a = await s.get(CounterpartyAddress, body.address_id)
    if not a or a.counterparty_id != body.counterparty_id:
        raise HTTPException(422, "This address doesn't belong to the recipient.")
    if (a.network, a.asset) != (w.network, w.asset):
        raise HTTPException(422, f"The recipient's address takes {a.asset} on {a.network}, "
                                 f"but {w.name} holds {w.asset} on {w.network}.")
    if not await s.scalar(select(Category.id).where(Category.name == body.category)):
        raise HTTPException(422, f"Unknown category {body.category!r}.")
    return Payment(wallet_id=w.id, project_id=project.id, counterparty_id=a.counterparty_id, address_id=a.id,
                   amount=body.amount, asset=w.asset, network=w.network, destination_address=a.address,
                   category=body.category, description=body.description.strip(), created_by=user.id,
                   submitted_by=user.id, status="DRAFT")


def after_change(bg: BackgroundTasks, p: Payment) -> None:
    if p.status == "PENDING_APPROVAL":
        bg.add_task(notify.payment, p.id, "approval_required")
    elif p.status == "APPROVED":
        bg.add_task(payments.execute, p.id)


@router.post("/payments/preview")
async def preview_payment(body: PaymentIn, user: Me, s: DB):
    p = await new_payment(s, user, body)
    w, a = await s.get(Wallet, p.wallet_id), await s.get(CounterpartyAddress, p.address_id)
    prov = provider(w.network)
    try:
        fee = await prov.estimate_fee(a.address, w.asset)
    except Exception:
        fee = None
    available = await payments.wallet_available(s, w)
    req = await policy.requirement(s, p, user.role) if user.role in STAFF else None
    return {
        "approval": req, "new_address": not a.verified, "fee": fee, "fee_asset": prov.native,
        "wallet": {"name": w.name, "available": available, "asset": w.asset, "native_balance": w.native_balance}
        if user.role in STAFF else None,
        "project_available": await policy.project_available(s, p.project_id),
        "problem": (req or {}).get("error") or (
            f"The {w.name} wallet has {available:,.2f} {w.asset} available, but this payment requires "
            f"{p.amount:,.2f} {p.asset}." if p.amount > available and user.role in STAFF else None),
    }


@router.post("/payments")
async def create_payment(body: PaymentIn, user: Me, s: DB, bg: BackgroundTasks, request: Request):
    p = await new_payment(s, user, body)
    s.add(p)
    await s.flush()
    if user.role == "project_manager":  # a payment request: staff reviews and submits it
        payments.event(s, p, "CREATED", user.id)
        bg.add_task(notify.payment, p.id, "request")
    else:
        await payments.submit(s, p, user, "CREATED")
        after_change(bg, p)
    audit(s, request, user, "CREATED_PAYMENT", "payment", p.code, new=body.model_dump())
    await s.commit()
    return {"id": p.id, "status": p.status, "approval": p.approval}


@router.get("/payments")
async def list_payments(user: Me, s: DB, status: str = "all", q: str = "", project_id: int | None = None,
                        counterparty_id: int | None = None, before: int | None = None, limit: int = 30):
    groups = {"pending": ("DRAFT", *IN_FLIGHT), "completed": ("CONFIRMED",), "failed": ("FAILED", "REJECTED", "CANCELLED")}
    query = scoped(select(Payment).join(Payment.counterparty), await my_project_ids(s, user))
    if status in groups:
        query = query.where(Payment.status.in_(groups[status]))
    if project_id:
        query = query.where(Payment.project_id == project_id)
    if counterparty_id:
        query = query.where(Payment.counterparty_id == counterparty_id)
    if before:
        query = query.where(Payment.id < before)
    if q := q.strip():
        query = query.where(payment_match(q))
    rows = await s.scalars(query.order_by(Payment.id.desc()).limit(min(limit, 100)))
    return [payment_row(p) for p in rows]


def payment_match(q: str):
    like = f"%{q}%"
    code = q.upper().removeprefix("CF-").removeprefix("#")
    return or_(Payment.id == int(code) if code.isdigit() and len(code) < 10 else false(),
               Payment.tx_hash.ilike(like), Payment.description.ilike(like),
               Payment.destination_address.ilike(like), Counterparty.name.ilike(like))


@router.get("/approvals")
async def approvals(user: Me, s: DB):
    return [payment_row(p) for p in await pending_for(s, user)]


@router.get("/payments/{pid}")
async def payment_detail(pid: int, user: Me, s: DB):
    p = await get_payment(s, user, pid)
    events = list(await s.scalars(select(PaymentEvent).where(PaymentEvent.payment_id == p.id).order_by(PaymentEvent.id)))
    voters = await payments.votes(s, p)
    a = await s.get(CounterpartyAddress, p.address_id)
    history = (await s.execute(select(func.count(), func.avg(Payment.amount)).where(
        Payment.counterparty_id == p.counterparty_id, Payment.status == "CONFIRMED", Payment.id != p.id))).one()
    last = await s.scalar(select(Payment).where(Payment.counterparty_id == p.counterparty_id, Payment.status == "CONFIRMED",
                                                Payment.id != p.id).order_by(Payment.id.desc()).limit(1))
    # ponytail: naive risk heuristic; replace with scoring once there is history to learn from
    if not a.verified:
        risk = ("High", "New destination address")
    elif not history[0]:
        risk = ("Medium", "First payment to this recipient")
    elif history[1] and p.amount > 2 * history[1]:
        risk = ("Medium", "Unusually large for this recipient")
    else:
        risk = ("Low", "Known counterparty")
    try:
        payments.check_voter(p, user, voters)
        can_vote = user.role in STAFF
    except HTTPException:
        can_vote = False
    entry = await s.scalar(select(LedgerEntry.id).where(LedgerEntry.payment_id == p.id,
                                                        LedgerEntry.kind == "PAYMENT").order_by(LedgerEntry.id.desc()))
    prov = provider(p.network)
    return {
        **payment_row(p),
        "wallet": {"id": p.wallet_id, "name": p.wallet.name, "address": p.wallet.address},
        "destination": {"address": p.destination_address, "label": a.label, "verified": a.verified},
        "created_by": p.creator.display_name,
        "approval": {**(p.approval or {}), "votes": [{"name": v.display_name, "role": v.role} for v in voters]},
        "risk": {"level": risk[0], "reason": risk[1], "previous": history[0],
                 "last": last and {"amount": last.amount, "date": last.confirmed_at}},
        "timeline": [{"action": e.action, "status": e.status, "actor": e.actor and e.actor.display_name,
                      "comment": e.comment, "at": e.created_at} for e in events],
        "attachments": [{"id": x.id, "filename": x.filename} for x in
                        await s.scalars(select(Attachment).where(Attachment.payment_id == p.id))],
        "blockchain": {"network": p.network, "tx_hash": p.tx_hash, "block": p.block, "confirmations": p.confirmations,
                       "fee": p.fee, "fee_asset": prov.native, "explorer": p.tx_hash and prov.explorer_url(p.tx_hash)},
        "error": p.error,
        "can": {
            "vote": can_vote,
            "submit": p.status == "DRAFT" and user.role in STAFF,
            "cancel": p.status in ("DRAFT", "PENDING_APPROVAL") and (user.id == p.created_by or user.role in ("owner", "cfo")),
            "reclassify": bool(entry) and user.role in ("owner", "cfo"),
            "attach": user.role in (*STAFF, "project_manager"),
        },
    }


class Reclassify(BaseModel):
    project_id: int | None = None
    category: str | None = None
    reason: str = Field(min_length=1, max_length=300)


@router.post("/payments/{pid}/reclassify")
async def reclassify(pid: int, body: Reclassify, user: Me, s: DB, request: Request):
    """Fix project/category of a confirmed payment: reversal + corrected entry, the ledger is never edited."""
    require(user, "owner", "cfo")
    if body.project_id is None and not body.category:
        raise HTTPException(422, "Choose the correct project or category.")
    p = await get_payment(s, user, pid, lock=True)
    entry = await s.scalar(select(LedgerEntry).where(LedgerEntry.payment_id == p.id, LedgerEntry.kind == "PAYMENT")
                           .order_by(LedgerEntry.id.desc()).limit(1))
    if not entry:
        raise HTTPException(409, "Only confirmed payments are in the ledger.")
    if body.category and not await s.scalar(select(Category.id).where(Category.name == body.category)):
        raise HTTPException(422, f"Unknown category {body.category!r}.")
    if body.project_id and not await s.get(Project, body.project_id):
        raise HTTPException(422, "Unknown project.")
    old = {"project_id": p.project_id, "category": p.category}
    ledger.reverse(s, entry, user.id, body.reason, project_id=body.project_id, category=body.category)
    p.project_id, p.category = body.project_id or p.project_id, body.category or p.category
    audit(s, request, user, "RECLASSIFIED_PAYMENT", "payment", p.code, old=old, new=body.model_dump())
    await s.commit()
    return {"ok": True}


class Comment(BaseModel):
    comment: str = Field("", max_length=1000)


@router.post("/payments/{pid}/{action}")
async def payment_action(pid: int, action: Literal["submit", "approve", "reject", "cancel"], user: Me, s: DB,
                         bg: BackgroundTasks, request: Request, body: Comment = Comment()):
    p = await get_payment(s, user, pid, lock=True)
    old = p.status
    if action == "submit":
        require(user, *STAFF)
        if p.status != "DRAFT":
            raise HTTPException(409, "Only payment requests can be submitted.")
        await payments.submit(s, p, user, "SUBMITTED")
        after_change(bg, p)
    elif action == "approve":
        await payments.approve(s, p, user)
        after_change(bg, p)
    elif action == "reject":
        if not body.comment.strip():
            raise HTTPException(422, "Tell the creator why the payment is rejected.")
        await payments.reject(s, p, user, body.comment.strip())
        bg.add_task(notify.payment, p.id, "rejected")
    else:
        if p.status not in ("DRAFT", "PENDING_APPROVAL"):
            raise HTTPException(409, "Only payments that haven't been approved yet can be cancelled.")
        if user.id != p.created_by and user.role not in ("owner", "cfo"):
            raise HTTPException(403, "Only the creator or a finance admin can cancel this payment.")
        p.status = "CANCELLED"
        payments.event(s, p, "CANCELLED", user.id, body.comment)
    audit(s, request, user, f"{action.upper()}_PAYMENT", "payment", p.code,
          old={"status": old}, new={"status": p.status, "amount": p.amount, "comment": body.comment})
    await s.commit()
    return {"status": p.status}


@router.put("/payments/{pid}/attachments")
async def attach(pid: int, filename: str, user: Me, s: DB, request: Request):
    require(user, *STAFF, "project_manager")
    p = await get_payment(s, user, pid)
    data = await request.body()
    if not data or len(data) > 10 * 2**20:
        raise HTTPException(422, "Attach a file up to 10 MB.")
    x = Attachment(payment_id=p.id, filename=filename[:255], content_type=request.headers.get("content-type", ""),
                   data=data, created_by=user.id)
    s.add(x)
    await s.flush()
    audit(s, request, user, "ATTACHED_FILE", "payment", p.code, new={"filename": x.filename, "size": len(data)})
    await s.commit()
    return {"id": x.id, "filename": x.filename}


async def deliver(user: User, data: bytes, filename: str, caption: str) -> None:
    """Files go to the user's Telegram chat: Mini App webviews can't reliably download."""
    try:
        await notify.bot.send_document(user.telegram_id, BufferedInputFile(data, filename), caption=caption)
    except TelegramAPIError as e:
        raise HTTPException(502, f"Telegram didn't accept the file ({e.message}). Open the bot, press Start "
                                 "and try again.")


@router.post("/attachments/{aid}/send")
async def send_attachment(aid: int, user: Me, s: DB):
    x = await s.get(Attachment, aid)
    if not x:
        raise HTTPException(404, "File not found.")
    p = await get_payment(s, user, x.payment_id)
    await s.refresh(x, ["data"])
    await deliver(user, x.data, x.filename, f"{p.code} · {x.filename}")
    return {"ok": True}


# ---------------------------------------------------------------- counterparties

class AddressIn(BaseModel):
    network: str = "TRON"
    asset: str = "USDT"
    address: str = Field(min_length=10, max_length=128)
    label: str = Field("", max_length=64)


class CounterpartyIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: Literal["company", "person", "service"] = "company"
    default_category: str | None = None
    notes: str = ""
    address: AddressIn | None = None


async def add_address(s: AsyncSession, user: User, cp: Counterparty, body: AddressIn) -> CounterpartyAddress:
    address = body.address.strip()
    try:
        ok = provider(body.network).valid_address(address)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if not ok:
        raise HTTPException(422, f"This isn't a valid {body.network} address.")
    taken = await s.scalar(select(CounterpartyAddress).where(CounterpartyAddress.network == body.network,
                                                             CounterpartyAddress.address == address))
    if taken:
        raise HTTPException(409, "This address is already saved for another recipient.")
    if await s.scalar(select(Wallet.id).where(Wallet.address == address)):
        raise HTTPException(409, "This is one of our own wallets. Use Transfer instead.")
    trusted = user.role in ("owner", "cfo")  # a finance admin adding it is the verification
    a = CounterpartyAddress(counterparty_id=cp.id, network=body.network, asset=body.asset, address=address,
                            label=body.label, verified=trusted, verified_by=user.id if trusted else None)
    s.add(a)
    return a


def counterparty_stats():
    return (select(Payment.counterparty_id, func.count().label("n"), func.sum(Payment.amount).label("total"),
                   func.max(Payment.confirmed_at).label("last"))
            .where(Payment.status == "CONFIRMED").group_by(Payment.counterparty_id).subquery())


@router.get("/counterparties")
async def list_counterparties(user: Me, s: DB, q: str = ""):
    stats = counterparty_stats()
    query = (select(Counterparty, stats.c.n, stats.c.total, stats.c.last)
             .outerjoin(stats, stats.c.counterparty_id == Counterparty.id).where(Counterparty.status == "active"))
    if q := q.strip():
        query = query.where(or_(Counterparty.name.ilike(f"%{q}%"), Counterparty.id.in_(
            select(CounterpartyAddress.counterparty_id).where(CounterpartyAddress.address.ilike(f"%{q}%")))))
    rows = await s.execute(query.order_by(stats.c.n.desc().nulls_last(), Counterparty.name).limit(100))
    return [{"id": c.id, "name": c.name, "type": c.type, "default_category": c.default_category,
             "payments": n or 0, "total": total or 0, "last_payment": last,
             "addresses": [{"id": a.id, "network": a.network, "asset": a.asset, "address": a.address,
                            "label": a.label, "verified": a.verified} for a in c.addresses]}
            for c, n, total, last in rows]


@router.post("/counterparties")
async def create_counterparty(body: CounterpartyIn, user: Me, s: DB, request: Request):
    require(user, *STAFF)
    cp = Counterparty(name=body.name.strip(), type=body.type, default_category=body.default_category, notes=body.notes)
    s.add(cp)
    await s.flush()
    a = body.address and await add_address(s, user, cp, body.address)
    await s.flush()
    audit(s, request, user, "CREATED_COUNTERPARTY", "counterparty", cp.id, new=body.model_dump())
    await s.commit()
    return {"id": cp.id, "address_id": a and a.id, "verified": bool(a and a.verified)}


@router.post("/counterparties/{cid}/addresses")
async def create_address(cid: int, body: AddressIn, user: Me, s: DB, request: Request):
    require(user, *STAFF)
    cp = await s.get(Counterparty, cid)
    if not cp:
        raise HTTPException(404, "Recipient not found.")
    a = await add_address(s, user, cp, body)
    await s.flush()
    audit(s, request, user, "ADDED_ADDRESS", "counterparty", cp.id, new=body.model_dump())
    await s.commit()
    return {"id": a.id, "verified": a.verified}


@router.get("/counterparties/{cid}")
async def counterparty_detail(cid: int, user: Me, s: DB):
    require(user, *READERS)
    cp = await s.get(Counterparty, cid)
    if not cp:
        raise HTTPException(404, "Recipient not found.")
    stats = counterparty_stats()
    n, total, last = (await s.execute(select(stats.c.n, stats.c.total, stats.c.last)
                                      .where(stats.c.counterparty_id == cid))).one_or_none() or (0, 0, None)
    recent = await s.scalars(select(Payment).where(Payment.counterparty_id == cid).order_by(Payment.id.desc()).limit(30))
    return {"id": cp.id, "name": cp.name, "type": cp.type, "notes": cp.notes, "default_category": cp.default_category,
            "verified": any(a.verified for a in cp.addresses), "payments": n, "total": total, "last_payment": last,
            "addresses": [{"id": a.id, "network": a.network, "asset": a.asset, "address": a.address, "label": a.label,
                           "verified": a.verified} for a in cp.addresses],
            "recent": [payment_row(p) for p in recent]}


# ---------------------------------------------------------------- projects & budgets

@router.get("/projects")
async def list_projects(user: Me, s: DB):
    ids = await my_project_ids(s, user)
    balances, b = await project_balances(s), await budgets(s, this_month(), ids)
    rows = await s.scalars(scoped(select(Project).where(Project.status == "active"), ids, Project.id).order_by(Project.name))
    out = [{"id": p.id, "name": p.name, "code": p.code, "manager_id": p.manager_id, "available": balances.get(p.id, 0),
            "budget": b.get(p.id)} for p in rows]
    if ids is None:
        out.append({"id": None, "name": "Unallocated", "code": "", "available": balances.get(None, 0), "budget": None})
    return out


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=32)
    manager_id: int | None = None
    status: Literal["active", "archived"] = "active"


@router.post("/projects")
async def create_project(body: ProjectIn, user: Me, s: DB, request: Request):
    require(user, "owner", "cfo")
    if await s.scalar(select(Project.id).where(Project.code == body.code.upper())):
        raise HTTPException(409, f"Project code {body.code.upper()} is taken.")
    p = Project(name=body.name.strip(), code=body.code.upper(), manager_id=body.manager_id, status=body.status)
    s.add(p)
    await s.flush()
    audit(s, request, user, "CREATED_PROJECT", "project", p.id, new=body.model_dump())
    await s.commit()
    return {"id": p.id}


@router.put("/projects/{pid}")
async def update_project(pid: int, body: ProjectIn, user: Me, s: DB, request: Request):
    require(user, "owner", "cfo")
    p = await s.get(Project, pid)
    if not p:
        raise HTTPException(404, "Project not found.")
    old = {"name": p.name, "code": p.code, "manager_id": p.manager_id, "status": p.status}
    p.name, p.code, p.manager_id, p.status = body.name.strip(), body.code.upper(), body.manager_id, body.status
    audit(s, request, user, "UPDATED_PROJECT", "project", p.id, old=old, new=body.model_dump())
    await s.commit()
    return {"ok": True}


@router.get("/projects/{pid}")
async def project_detail(pid: int, user: Me, s: DB, period: str | None = None):
    ids = await my_project_ids(s, user)
    p = await s.get(Project, pid)
    if not p or (ids is not None and pid not in ids):
        raise HTTPException(404, "Project not found.")
    period = period or this_month()
    start, end = month_bounds(period)
    breakdown = await s.execute(select(LedgerLine.category, func.sum(LedgerLine.amount)).join(LedgerEntry).where(
        LedgerLine.account == "expense", LedgerLine.project_id == pid, LedgerEntry.created_at >= start,
        LedgerEntry.created_at < end).group_by(LedgerLine.category).order_by(func.sum(LedgerLine.amount).desc()))
    recent = await s.scalars(select(Payment).where(Payment.project_id == pid).order_by(Payment.id.desc()).limit(30))
    manager = p.manager_id and await s.get(User, p.manager_id)
    return {"id": p.id, "name": p.name, "code": p.code, "manager": manager and manager.display_name,
            "manager_id": p.manager_id, "available": (await project_balances(s)).get(p.id, 0),
            "budget": (await budgets(s, period, [pid])).get(pid), "period": period,
            "categories": [{"category": c, "amount": a} for c, a in breakdown if a],
            "payments": [payment_row(x) for x in recent]}


class BudgetIn(BaseModel):
    project_id: int
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    allocated: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=6)]


@router.put("/budgets")
async def set_budget(body: BudgetIn, user: Me, s: DB, request: Request):
    require(user, "owner", "cfo")
    b = await s.scalar(select(Budget).where(Budget.project_id == body.project_id, Budget.period == body.period))
    old = b and {"allocated": b.allocated}
    if b:
        b.allocated = body.allocated
    else:
        s.add(Budget(project_id=body.project_id, period=body.period, allocated=body.allocated))
    audit(s, request, user, "SET_BUDGET", "project", body.project_id, old=old, new=body.model_dump())
    await s.commit()
    return {"ok": True}


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.post("/categories")
async def create_category(body: CategoryIn, user: Me, s: DB, request: Request):
    require(user, "owner", "cfo")
    name = body.name.strip()
    if not name or await s.scalar(select(Category.id).where(func.lower(Category.name) == name.lower())):
        raise HTTPException(409, "This category already exists.")
    s.add(Category(name=name))
    audit(s, request, user, "CREATED_CATEGORY", "category", name)
    await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- wallets & transfers

class WalletIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal[WALLET_TYPES]
    network: Literal["TRON"] = "TRON"
    address: str
    asset: Literal["USDT"] = "USDT"


@router.get("/wallets")
async def list_wallets(user: Me, s: DB):
    require(user, *READERS)
    return [{"id": w.id, "name": w.name, "type": w.type, "network": w.network, "asset": w.asset, "address": w.address,
             "balance": w.balance, "native_balance": w.native_balance}
            for w in await s.scalars(select(Wallet).where(Wallet.status == "active").order_by(Wallet.id))]


@router.post("/wallets")
async def create_wallet(body: WalletIn, user: Me, s: DB, request: Request):
    """Watch a wallet. Its current balance is booked as an unallocated opening balance."""
    require(user, "owner")
    prov = provider(body.network)
    address = body.address.strip()
    if not prov.valid_address(address):
        raise HTTPException(422, f"This isn't a valid {body.network} address.")
    if await s.scalar(select(Wallet.id).where(Wallet.network == body.network, Wallet.address == address)):
        raise HTTPException(409, "This wallet is already added.")
    try:
        balance = await prov.get_balance(address, body.asset)
        gas = await prov.get_balance(address, prov.native)
    except Exception:
        raise HTTPException(502, f"Couldn't reach the {body.network} network to read the balance. Try again in a minute.")
    w = Wallet(name=body.name.strip(), type=body.type, network=body.network, address=address, asset=body.asset,
               balance=balance, native_balance=gas, scan_cursor_ms=int(now().timestamp() * 1000))
    s.add(w)
    await s.flush()
    if balance:
        ledger.post(s, "OPENING", [ledger.line(f"wallet:{w.id}", balance, w.asset), ledger.line("opening", -balance, w.asset)],
                    created_by=user.id, description=f"Opening balance of {w.name}")
    audit(s, request, user, "CREATED_WALLET", "wallet", w.id, new={**body.model_dump(), "balance": balance})
    await s.commit()
    return {"id": w.id}


@router.get("/wallets/{wid}")
async def wallet_detail(wid: int, user: Me, s: DB):
    require(user, *READERS)
    w = await s.get(Wallet, wid)
    if not w:
        raise HTTPException(404, "Wallet not found.")
    names = dict((await s.execute(select(Project.id, Project.name))).all())
    split = (await s.execute(select(LedgerLine.project_id, func.sum(LedgerLine.amount)).where(
        LedgerLine.account == f"wallet:{w.id}").group_by(LedgerLine.project_id)
        .order_by(func.sum(LedgerLine.amount).desc()))).all()
    ledger_total = sum((a for _, a in split), Decimal(0))
    return {"id": w.id, "name": w.name, "type": w.type, "network": w.network, "asset": w.asset, "address": w.address,
            "balance": w.balance, "native_balance": w.native_balance, "native": provider(w.network).native,
            "available": await payments.wallet_available(s, w), "ledger_balance": ledger_total,
            "internal": [{"project_id": pid, "project": names.get(pid, "Unallocated"), "amount": a} for pid, a in split if a],
            "qr": segno.make(w.address, error="m").svg_inline(scale=6, border=2, dark="#1d1d1f")}


class TransferIn(BaseModel):
    wallet_id: int
    from_project_id: int | None = None
    to_project_id: int | None = None
    amount: Amount
    description: str = Field("", max_length=300)


@router.post("/transfers")
async def transfer(body: TransferIn, user: Me, s: DB, request: Request):
    """Move money between projects inside one wallet: ledger only, nothing touches the chain."""
    require(user, "owner", "cfo")
    if body.from_project_id == body.to_project_id:
        raise HTTPException(422, "Choose two different projects.")
    w = await s.get(Wallet, body.wallet_id)
    if not w:
        raise HTTPException(422, "Choose a wallet.")
    names = dict((await s.execute(select(Project.id, Project.name))).all())
    src, dst = names.get(body.from_project_id, "Unallocated"), names.get(body.to_project_id, "Unallocated")
    available = await policy.project_available(s, body.from_project_id)
    if body.amount > available:
        raise HTTPException(422, f"{src} has {available:,.2f} {w.asset} available, but the transfer is {body.amount:,.2f}.")
    e = ledger.post(s, "TRANSFER", [ledger.line(f"wallet:{w.id}", -body.amount, w.asset, body.from_project_id),
                                    ledger.line(f"wallet:{w.id}", body.amount, w.asset, body.to_project_id)],
                    created_by=user.id, description=f"{src} → {dst}" + (f": {body.description}" if body.description else ""))
    await s.flush()
    audit(s, request, user, "TRANSFERRED", "ledger", e.id, new=body.model_dump())
    await s.commit()
    return {"id": e.id}


# ---------------------------------------------------------------- analytics & reports

PERIODS = {"7d": (7, "day"), "30d": (30, "day"), "3m": (91, "week"), "6m": (182, "week"), "1y": (365, "month")}


@router.get("/analytics")
async def analytics(user: Me, s: DB, period: str = "30d"):
    require(user, *READERS)
    days, unit = PERIODS.get(period, PERIODS["30d"])
    since = now() - timedelta(days=days)
    bucket = func.date_trunc(unit, LedgerEntry.created_at)
    series: dict = {}
    for t, account, amount in await s.execute(
            select(bucket, LedgerLine.account, func.sum(LedgerLine.amount)).join(LedgerEntry)
            .where(LedgerEntry.created_at >= since, LedgerLine.account.in_(("income", "expense")))
            .group_by(bucket, LedgerLine.account).order_by(bucket)):
        row = series.setdefault(t, {"t": t, "income": Decimal(0), "expenses": Decimal(0)})
        row["income" if account == "income" else "expenses"] += -amount if account == "income" else amount
    for row in series.values():
        row["net"] = row["income"] - row["expenses"]
    breakdown = await s.execute(select(LedgerLine.category, func.sum(LedgerLine.amount)).join(LedgerEntry).where(
        LedgerLine.account == "expense", LedgerEntry.created_at >= since)
        .group_by(LedgerLine.category).order_by(func.sum(LedgerLine.amount).desc()))
    income = sum((r["income"] for r in series.values()), Decimal(0))
    expenses = sum((r["expenses"] for r in series.values()), Decimal(0))
    return {"period": period, "unit": unit, "income": income, "expenses": expenses, "net": income - expenses,
            **{k: v for k, v in (await summary(s, user)).items() if k in ("treasury", "operations")},
            "series": list(series.values()),
            "categories": [{"category": c, "amount": a} for c, a in breakdown if a]}


class ReportIn(BaseModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    project_id: int | None = None


@router.post("/reports/monthly")
async def monthly_report(body: ReportIn, user: Me, s: DB, request: Request):
    """General journal for the month as CSV, delivered to the user's Telegram chat."""
    require(user, *READERS)
    start, end = month_bounds(body.month)
    names = dict((await s.execute(select(Project.id, Project.name))).all())
    q = (select(LedgerEntry, LedgerLine).join(LedgerLine).where(LedgerEntry.created_at >= start, LedgerEntry.created_at < end)
         .order_by(LedgerEntry.id, LedgerLine.id))
    if body.project_id:
        q = q.where(LedgerLine.project_id == body.project_id)
    rows = (await s.execute(q)).all()
    pay = {p.id: p for p in await s.scalars(select(Payment).where(Payment.id.in_({e.payment_id for e, _ in rows if e.payment_id})))}
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "entry", "kind", "account", "project", "category", "asset", "debit", "credit",
                "payment", "counterparty", "tx_hash", "description"])
    income = expenses = Decimal(0)
    for e, ln in rows:
        p = pay.get(e.payment_id)
        income -= ln.amount if ln.account == "income" else 0
        expenses += ln.amount if ln.account == "expense" else 0
        w.writerow([e.created_at.strftime("%Y-%m-%d %H:%M"), e.id, e.kind, ln.account,
                    names.get(ln.project_id, "Unallocated"), ln.category or "", ln.asset,
                    ln.amount if ln.amount > 0 else "", -ln.amount if ln.amount < 0 else "",
                    p.code if p else "", p.counterparty.name if p else "", e.tx_hash or "", e.description])
    scope = names.get(body.project_id, "All projects")
    await deliver(
        user, out.getvalue().encode("utf-8-sig"), f"core-finance-{body.month}.csv",
        f"Monthly finance report · {body.month} · {scope}\n\nIncome {income:,.2f}\nExpenses {expenses:,.2f}\n"
                f"Net {income - expenses:,.2f}")
    audit(s, request, user, "EXPORTED_REPORT", "report", body.month, new=body.model_dump())
    await s.commit()
    return {"ok": True, "rows": len(rows)}


# ---------------------------------------------------------------- admin: audit, policy, users

@router.get("/audit")
async def audit_log(user: Me, s: DB, before: int | None = None):
    require(user, "owner", "cfo", "viewer")
    q = select(AuditLog).order_by(AuditLog.id.desc()).limit(100)
    if before:
        q = q.where(AuditLog.id < before)
    return [{"id": a.id, "at": a.created_at, "user": a.user and a.user.display_name, "telegram_id": a.telegram_id,
             "action": a.action, "object": f"{a.object_type} {a.object_id}", "old": a.old, "new": a.new, "ip": a.ip}
            for a in await s.scalars(q)]


@router.get("/policy")
async def get_policy(user: Me, s: DB):
    require(user, "owner", "cfo")
    return await policy.active_policy(s)


@router.put("/policy")
async def put_policy(body: dict, user: Me, s: DB, request: Request):
    require(user, "owner")
    try:
        policy.validate(body)
    except ValueError as e:
        raise HTTPException(422, f"Policy not saved: {e}")
    old = await policy.active_policy(s)
    s.add(Policy(data=body, created_by=user.id))
    audit(s, request, user, "CHANGED_POLICY", "policy", "active", old=old, new=body)
    await s.commit()
    return {"ok": True}


class UserIn(BaseModel):
    telegram_id: int
    display_name: str = Field(min_length=1, max_length=128)
    role: Literal[ROLES]
    status: Literal["active", "disabled"] = "active"


@router.get("/users")
async def list_users(user: Me, s: DB):
    require(user, "owner")
    return [{"id": u.id, "telegram_id": u.telegram_id, "username": u.username, "name": u.display_name, "role": u.role,
             "status": u.status} for u in await s.scalars(select(User).order_by(User.id))]


@router.post("/users")
async def create_user(body: UserIn, user: Me, s: DB, request: Request):
    require(user, "owner")
    if await s.scalar(select(User.id).where(User.telegram_id == body.telegram_id)):
        raise HTTPException(409, "This Telegram user is already added.")
    u = User(**body.model_dump())
    s.add(u)
    await s.flush()
    audit(s, request, user, "CREATED_USER", "user", u.id, new=body.model_dump())
    await s.commit()
    return {"id": u.id}


@router.put("/users/{uid}")
async def update_user(uid: int, body: UserIn, user: Me, s: DB, request: Request):
    require(user, "owner")
    u = await s.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found.")
    if u.id == user.id and (body.role != u.role or body.status != u.status):
        raise HTTPException(422, "You can't change your own role or access.")
    old = {"display_name": u.display_name, "role": u.role, "status": u.status}
    u.display_name, u.role, u.status = body.display_name, body.role, body.status
    audit(s, request, user, "UPDATED_USER", "user", u.id, old=old, new=body.model_dump())
    await s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- search

@router.get("/search")
async def search(q: str, user: Me, s: DB):
    q = q.strip()
    if len(q) < 2:
        return {"payments": [], "counterparties": [], "projects": [], "wallets": []}
    ids, like = await my_project_ids(s, user), f"%{q}%"
    found = await s.scalars(scoped(select(Payment).join(Payment.counterparty), ids)
                            .where(payment_match(q)).order_by(Payment.id.desc()).limit(10))
    projects = await s.scalars(scoped(select(Project), ids, Project.id).where(
        or_(Project.name.ilike(like), Project.code.ilike(like))).limit(5))
    out = {"payments": [payment_row(p) for p in found],
           "projects": [{"id": p.id, "name": p.name, "code": p.code} for p in projects],
           "counterparties": [], "wallets": []}
    if ids is None:
        out["counterparties"] = [{"id": c["id"], "name": c["name"], "payments": c["payments"]}
                                 for c in (await list_counterparties(user, s, q))[:5]]
        out["wallets"] = [{"id": w.id, "name": w.name, "address": w.address} for w in await s.scalars(
            select(Wallet).where(or_(Wallet.name.ilike(like), Wallet.address.ilike(like))).limit(5))]
    return out
