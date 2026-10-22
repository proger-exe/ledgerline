"""Telegram notifications. Called after commit; never fails the caller."""
import logging
from decimal import Decimal
from html import escape

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select

from app import config
from app.db import Session
from app.models import Payment, PaymentEvent, User
from app.policy import can_fill

log = logging.getLogger(__name__)
bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))


def webapp(route: str = "/") -> WebAppInfo:
    return WebAppInfo(url=f"{config.WEBAPP_URL}/?r={route}")


def button(text: str, route: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=webapp(route))]])


def money(amount: Decimal, asset: str) -> str:
    return f"{amount:,.2f} {asset}"


async def send(users, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    for u in {u.telegram_id: u for u in users}.values():
        try:
            await bot.send_message(u.telegram_id, text, reply_markup=markup)
        except TelegramAPIError as e:  # user never pressed /start, blocked the bot, ...
            log.warning("notify %s failed: %s", u.telegram_id, e)


async def users_with(s, roles) -> list[User]:
    return list(await s.scalars(select(User).where(User.role.in_(roles), User.status == "active")))


async def alert(roles: tuple[str, ...], text: str, route: str = "/", extra_users=()) -> None:
    async with Session() as s:
        users = await users_with(s, roles) + list(extra_users)
    await send(users, text, button("Open Finance", route))


async def payment(payment_id: int, event: str) -> None:
    try:
        async with Session() as s:
            p = await s.get(Payment, payment_id)
            last = await s.scalar(select(PaymentEvent).where(PaymentEvent.payment_id == p.id)
                                  .order_by(PaymentEvent.id.desc()).limit(1))
            head = f"<b>{money(p.amount, p.asset)}</b>\n{escape(p.counterparty.name)}"
            body = (f"\n\nProject:\n{escape(p.project.name)}\n\nCategory:\n{escape(p.category)}"
                    f"\n\nCreated by:\n{escape(p.creator.display_name)}")
            creator = [p.creator]
            if event == "approval_required":
                voted = set(await s.scalars(select(PaymentEvent.actor_id).where(
                    PaymentEvent.payment_id == p.id, PaymentEvent.action == "APPROVE")))
                slots = (p.approval or {}).get("slots", [])
                users = [u for u in await users_with(s, ("owner", "cfo", "accountant")) if can_fill(slots, u.role)
                         and u.id not in (p.created_by, p.submitted_by) and u.id not in voted]
                # Approve is never offered inside the chat: the approver has to open the details first.
                await send(users, f"<b>Payment approval required</b>\n\n{head}{body}",
                           button("Review payment", f"/payments/{p.id}"))
            elif event == "request":
                users = await users_with(s, ("owner", "cfo", "accountant"))
                await send(users, f"<b>Payment request</b>\n\n{head}{body}", button("Review request", f"/payments/{p.id}"))
            elif event == "rejected":
                await send(creator, f"<b>Payment rejected</b>\n\n{head}\n\n{escape(last.comment)}",
                           button("Open payment", f"/payments/{p.id}"))
            elif event == "confirmed":
                await send(creator, f"<b>Payment confirmed</b>\n\n{head}\n{escape(p.project.name)} · {escape(p.category)}",
                           button("Open payment", f"/payments/{p.id}"))
            elif event == "failed":
                users = creator + await users_with(s, ("owner", "cfo"))
                await send(users, f"<b>Payment failed</b>\n\n{head}\n\n{escape(p.error or '')}",
                           button("Open payment", f"/payments/{p.id}"))
    except Exception:
        log.exception("payment notification %s/%s failed", payment_id, event)
