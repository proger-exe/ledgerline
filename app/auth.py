import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.db import get_session
from app.models import AuditLog, User


def verify_init_data(init_data: str, bot_token: str, max_age: int) -> dict:
    """Validate Telegram Mini App initData signature and freshness; return the Telegram user dict."""
    try:
        fields = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError as e:
        raise ValueError("malformed initData") from e
    received = fields.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), received):
        raise ValueError("bad signature")
    if time.time() - int(fields.get("auth_date", 0)) > max_age:
        raise ValueError("expired")
    return json.loads(fields["user"])


async def current_user(request: Request, s: AsyncSession = Depends(get_session)) -> User:
    header = request.headers.get("Authorization", "")
    if not header.startswith("tma "):
        raise HTTPException(401, "Open Ledgerline from Telegram.")
    try:
        tg = verify_init_data(header.removeprefix("tma "), config.BOT_TOKEN, config.SESSION_MAX_AGE)
    except (ValueError, KeyError):
        raise HTTPException(401, "Your Telegram session expired. Close and reopen Ledgerline.")
    user = await s.scalar(select(User).where(User.telegram_id == tg["id"]))
    if not user or user.status != "active":
        raise HTTPException(403, f"No access yet. Ask the owner to add Telegram ID {tg['id']}.")
    if user.username != tg.get("username"):
        user.username = tg.get("username")
        await s.commit()
    return user


def require(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise HTTPException(403, f"Your role ({user.role.replace('_', ' ')}) can't do this.")


def audit(s: AsyncSession, request: Request | None, user: User | None, action: str, obj: str, obj_id,
          old: dict | None = None, new: dict | None = None) -> None:
    ip = request and request.client and request.client.host  # uvicorn --proxy-headers resolves the real client
    plain = lambda d: d and json.loads(json.dumps(d, default=str))  # noqa: E731  Decimal/datetime -> str
    s.add(AuditLog(user_id=user and user.id, telegram_id=user and user.telegram_id, action=action, object_type=obj,
                   object_id=str(obj_id), old=plain(old), new=plain(new), ip=ip))
