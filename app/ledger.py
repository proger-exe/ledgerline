from collections import defaultdict
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LedgerEntry, LedgerLine


def line(account: str, amount: Decimal, asset: str, project_id: int | None = None, category: str | None = None):
    return LedgerLine(account=account, amount=amount, asset=asset, project_id=project_id, category=category)


def post(s: AsyncSession, kind: str, lines: list[LedgerLine], **fields) -> LedgerEntry:
    """The only way anything gets into the ledger."""
    totals = defaultdict(Decimal)
    for ln in lines:
        totals[ln.asset] += ln.amount
    if any(totals.values()) or not lines:
        raise ValueError(f"unbalanced ledger entry: {dict(totals)}")
    entry = LedgerEntry(kind=kind, lines=lines, **fields)
    s.add(entry)
    return entry


def reverse(s: AsyncSession, orig: LedgerEntry, user_id: int, reason: str, *,
            project_id: int | None = None, category: str | None = None) -> list[LedgerEntry]:
    """Cancel `orig` with a mirror entry; optionally re-post it under another project/category."""
    out = [post(s, "REVERSAL", [line(ln.account, -ln.amount, ln.asset, ln.project_id, ln.category) for ln in orig.lines],
                reverses_id=orig.id, payment_id=orig.payment_id, created_by=user_id,
                description=f"Reversal of #{orig.id}: {reason}")]
    if project_id is not None or category is not None:
        out.append(post(s, orig.kind, [
            line(ln.account, ln.amount, ln.asset,
                 project_id if project_id is not None else ln.project_id,
                 category if category is not None and ln.account == "expense" else ln.category)
            for ln in orig.lines
        ], payment_id=orig.payment_id, created_by=user_id, description=f"{orig.description} (corrected: {reason})"))
    return out
