from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

Money = Numeric(24, 6)
ROLES = ("owner", "cfo", "accountant", "project_manager", "viewer")
WALLET_TYPES = ("TREASURY", "OPERATIONS", "PROJECT", "OTHER")
# Money that left (or is about to leave) a wallet but is not in the ledger yet: ledger posts on CONFIRMED.
IN_FLIGHT = ("PENDING_APPROVAL", "APPROVED", "SIGNING", "BROADCASTED", "CONFIRMING")


def created_at():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = created_at()


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("network", "address"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16))
    network: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(128))
    asset: Mapped[str] = mapped_column(String(16))
    balance: Mapped[Decimal] = mapped_column(Money, default=0)  # on-chain, synced by the watcher
    native_balance: Mapped[Decimal] = mapped_column(Money, default=0)  # gas coin (TRX)
    status: Mapped[str] = mapped_column(String(16), default="active")
    scan_cursor_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = created_at()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    code: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("project_id", "period", "currency"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    currency: Mapped[str] = mapped_column(String(16), default="USDT")
    allocated: Mapped[Decimal] = mapped_column(Money)
    created_at: Mapped[datetime] = created_at()


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = created_at()


class Counterparty(Base):
    __tablename__ = "counterparties"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(16), default="company")  # company | person | service
    default_category: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = created_at()
    addresses: Mapped[list["CounterpartyAddress"]] = relationship(lazy="selectin", order_by="CounterpartyAddress.id")


class CounterpartyAddress(Base):
    __tablename__ = "counterparty_addresses"
    __table_args__ = (UniqueConstraint("network", "address"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"))
    network: Mapped[str] = mapped_column(String(16))
    asset: Mapped[str] = mapped_column(String(16))
    address: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(64), default="")
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallets.id"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    counterparty_id: Mapped[int] = mapped_column(ForeignKey("counterparties.id"), index=True)
    address_id: Mapped[int] = mapped_column(ForeignKey("counterparty_addresses.id"))
    amount: Mapped[Decimal] = mapped_column(Money)
    asset: Mapped[str] = mapped_column(String(16))
    network: Mapped[str] = mapped_column(String(16))
    destination_address: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # initiator the policy is applied to
    status: Mapped[str] = mapped_column(String(20), index=True)
    approval: Mapped[dict | None] = mapped_column(JSONB)  # {"rule": ..., "slots": [[roles]]} at submit time
    tx_hash: Mapped[str | None] = mapped_column(String(128), unique=True)
    block: Mapped[int | None] = mapped_column(BigInteger)
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    fee: Mapped[Decimal | None] = mapped_column(Money)  # in the network's gas coin
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broadcasted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    wallet: Mapped[Wallet] = relationship(lazy="joined")
    project: Mapped[Project] = relationship(lazy="joined")
    counterparty: Mapped[Counterparty] = relationship(lazy="joined")
    creator: Mapped[User] = relationship(lazy="joined", foreign_keys=[created_by])

    @property
    def code(self) -> str:
        return f"CF-{self.id}"


class PaymentEvent(Base):
    """Status history + individual approvals. Append-only."""
    __tablename__ = "payment_events"
    __table_args__ = (
        Index("one_approval_per_user", "payment_id", "actor_id", unique=True, postgresql_where=text("action = 'APPROVE'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))  # new status, or APPROVE for a single approver's vote
    status: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = created_at()
    actor: Mapped[User | None] = relationship(lazy="joined")


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class LedgerEntry(Base):
    """Double-entry journal. Append-only: errors are fixed by a REVERSAL entry, never by editing."""
    __tablename__ = "ledger_entries"
    __table_args__ = (Index("ledger_tx_once", "tx_hash", unique=True, postgresql_where=text("tx_hash IS NOT NULL")),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # PAYMENT DEPOSIT TRANSFER INTERNAL OPENING UNRECONCILED REVERSAL
    description: Mapped[str] = mapped_column(Text, default="")
    payment_id: Mapped[int | None] = mapped_column(ForeignKey("payments.id"), index=True)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_entries.id"), unique=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()
    lines: Mapped[list["LedgerLine"]] = relationship(lazy="selectin", order_by="LedgerLine.id")


class LedgerLine(Base):
    """amount > 0 = debit, < 0 = credit. Lines of an entry sum to zero per asset.

    account: wallet:<id> (asset, split by project) | expense | income | opening | suspense
    """
    __tablename__ = "ledger_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("ledger_entries.id"), index=True)
    account: Mapped[str] = mapped_column(String(32), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)  # None = unallocated
    category: Mapped[str | None] = mapped_column(String(64))
    asset: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(Money)


class Policy(Base):
    """Every save is a new version; the latest row is active."""
    __tablename__ = "policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(48))
    object_type: Mapped[str] = mapped_column(String(32))
    object_id: Mapped[str] = mapped_column(String(64))
    old: Mapped[dict | None] = mapped_column(JSONB)
    new: Mapped[dict | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()
    user: Mapped[User | None] = relationship(lazy="joined")
