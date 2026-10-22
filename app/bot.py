"""The bot is a doorway, not a CLI: any message shows the home card with Mini App buttons."""
import logging

from aiogram import Dispatcher
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message
from sqlalchemy import select

from app.api import summary
from app.db import Session
from app.models import User
from app.notify import bot, webapp

log = logging.getLogger(__name__)
dp = Dispatcher()


@dp.message()
async def home(m: Message) -> None:
    async with Session() as s:
        user = await s.scalar(select(User).where(User.telegram_id == m.from_user.id))
        if not user or user.status != "active":
            await m.answer(f"<b>Ledgerline</b>\n\nNo access yet. Send your Telegram ID to the owner:\n"
                           f"<code>{m.from_user.id}</code>")
            return
        numbers = await summary(s, user) if user.role != "project_manager" else None
    text = "<b>Ledgerline</b>"
    if numbers:
        text += (f"\n\nTotal\n<b>${numbers['total']:,.0f}</b>\n\nOperations\n<b>${numbers['operations']:,.0f}</b>"
                 f"\n\nPending approvals\n<b>{numbers['pending_approvals']}</b>")
    pay = "Request money" if user.role == "project_manager" else "New Payment"
    buttons = [[InlineKeyboardButton(text="Open Finance", web_app=webapp("/"))],
               [InlineKeyboardButton(text=pay, web_app=webapp("/pay"))]]
    if numbers:
        buttons[1].append(InlineKeyboardButton(text="Approvals", web_app=webapp("/approvals")))
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def run() -> None:
    try:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Finance", web_app=webapp("/")))
    except Exception:
        log.exception("couldn't set the menu button (is WEBAPP_URL https?)")
    try:
        await dp.start_polling(bot, handle_signals=False)
    except Exception:
        log.exception("bot polling stopped (check BOT_TOKEN); the API keeps working")
