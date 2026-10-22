"""API + Mini App static files + bot polling + watcher, in one process: `uvicorn app.main:app`."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import bot, config, watcher
from app.api import router
from app.db import Session, init_db
from app.models import Category, Policy, User
from app.policy import DEFAULT_POLICY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
CATEGORIES = ["Infrastructure", "Development", "Media", "Advertising", "Accounts", "Subscriptions", "Payroll",
              "Contractors", "Legal", "Taxes", "Operations", "Projects", "Other"]


async def seed() -> None:
    async with Session() as s:
        if not await s.scalar(select(Category.id).limit(1)):
            s.add_all(Category(name=n) for n in CATEGORIES)
        if not await s.scalar(select(Policy.id).limit(1)):
            s.add(Policy(data=DEFAULT_POLICY))
        if config.OWNER_TELEGRAM_ID and not await s.scalar(select(User.id).where(User.telegram_id == config.OWNER_TELEGRAM_ID)):
            s.add(User(telegram_id=config.OWNER_TELEGRAM_ID, display_name="Owner", role="owner"))
        await s.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await seed()
    # ponytail: single process runs bot polling + watcher; run one replica (or split them out) if you scale.
    tasks = [asyncio.create_task(watcher.run()), asyncio.create_task(bot.run())]
    yield
    for t in tasks:
        t.cancel()
    await bot.bot.session.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)


@app.exception_handler(IntegrityError)
async def conflict(_: Request, __: IntegrityError):
    return JSONResponse({"detail": "Someone changed this at the same moment. Refresh and try again."}, 409)


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "web", html=True), name="web")
