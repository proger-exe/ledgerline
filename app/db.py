from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app import config

engine = create_async_engine(config.DATABASE_URL, pool_pre_ping=True)
Session = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session():
    async with Session() as session:
        yield session


APPEND_ONLY = ("ledger_entries", "ledger_lines", "payment_events", "audit_log", "policies")


async def init_db() -> None:
    """Create tables and make accounting/audit tables append-only at the DB level."""
    # ponytail: create_all instead of migrations; add alembic before the first schema change in prod.
    from app import models  # noqa: F401  (registers tables)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("""
            CREATE OR REPLACE FUNCTION forbid_change() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION '% is append-only: post a correcting entry instead', TG_TABLE_NAME; END
            $$ LANGUAGE plpgsql"""))
        for table in APPEND_ONLY:
            await conn.execute(text(
                f"CREATE OR REPLACE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION forbid_change()"))
