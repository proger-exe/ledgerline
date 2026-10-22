"""Isolated signer: `uvicorn app.signer:app --port 8001` in its own container.

- Holds the only copy of the hot-wallet key (docker secret file, never env/DB/API).
- No public port; reachable only on the internal network and only with the shared token.
- Reads the payment itself from the DB (read-only role) and re-runs the policy.
- Enforces its own hard caps that nobody can change through the app.
- Journal (local sqlite) makes it idempotent: one payment → one signed transaction, ever.
"""
import asyncio
import hmac
import json
import os
import sqlite3
import time
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from tronpy.keys import PrivateKey

from app import config, policy
from app.blockchain import provider
from app.db import Session
from app.models import CounterpartyAddress, Payment, PaymentEvent, User, Wallet

KEY_HEX = Path(os.environ.get("SIGNER_KEY_FILE", "/run/secrets/signer_key")).read_text().strip()
ADDRESS = PrivateKey(bytes.fromhex(KEY_HEX)).public_key.to_base58check_address()
MAX_TX = Decimal(os.environ.get("SIGNER_MAX_TX", "1000"))
MAX_DAILY = Decimal(os.environ.get("SIGNER_MAX_DAILY", "3000"))
MICRO = 10**6

journal = sqlite3.connect(os.environ.get("SIGNER_JOURNAL", "/data/signer.db"), check_same_thread=False)
journal.execute("CREATE TABLE IF NOT EXISTS signed (payment_id INTEGER PRIMARY KEY, micro INTEGER, at REAL, result TEXT)")
lock = asyncio.Lock()  # ponytail: one signature at a time; plenty for a finance team
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


class SignIn(BaseModel):
    payment_id: int


async def check(payment_id: int) -> Payment:
    async with Session() as s:
        p = await s.get(Payment, payment_id)
        if not p:
            raise HTTPException(409, "unknown payment")
        if p.status != "SIGNING":
            raise HTTPException(409, f"payment is {p.status}, not SIGNING")
        w = await s.get(Wallet, p.wallet_id)
        if w.type == "TREASURY":
            raise HTTPException(409, "treasury transfers are never signed by the hot signer")
        if w.address != ADDRESS or w.network != "TRON" or w.asset != p.asset:
            raise HTTPException(409, f"signer holds no key for wallet {w.name}")
        a = await s.get(CounterpartyAddress, p.address_id)
        if not a or a.counterparty_id != p.counterparty_id or a.address != p.destination_address:
            raise HTTPException(409, "destination doesn't match the recipient's saved address")
        if p.amount > MAX_TX:
            raise HTTPException(409, f"amount exceeds the signer's hard cap of {MAX_TX} per transfer")

        initiator = await s.get(User, p.submitted_by or p.created_by)
        req = await policy.requirement(s, p, initiator.role)
        if req["error"]:
            raise HTTPException(409, req["error"])
        voters = await s.scalars(select_voters(p))
        if not policy.satisfied(req["slots"], [(u.id, u.role) for u in voters]):
            raise HTTPException(409, f"missing approvals required by rule {req['rule']!r}")
        return p


def select_voters(p: Payment):
    return select(User).join(PaymentEvent, PaymentEvent.actor_id == User.id).where(
        PaymentEvent.payment_id == p.id, PaymentEvent.action == "APPROVE", User.status == "active",
        User.id != p.created_by, User.id != (p.submitted_by or p.created_by))


@app.post("/sign")
async def sign(body: SignIn, x_signer_token: str = Header()):
    if not config.SIGNER_TOKEN or not hmac.compare_digest(x_signer_token, config.SIGNER_TOKEN):
        raise HTTPException(403, "bad signer token")
    async with lock:
        row = journal.execute("SELECT result FROM signed WHERE payment_id = ?", (body.payment_id,)).fetchone()
        if row:
            return json.loads(row[0])
        p = await check(body.payment_id)
        micro = int(p.amount * MICRO)
        (day,) = journal.execute("SELECT coalesce(sum(micro), 0) FROM signed WHERE at > ?", (time.time() - 86400,)).fetchone()
        if day + micro > MAX_DAILY * MICRO:
            raise HTTPException(409, f"signer's 24h hard cap of {MAX_DAILY} would be exceeded")
        prov = provider(p.network)
        tx = await prov.build_transaction(ADDRESS, p.destination_address, p.asset, p.amount)
        signed = prov.sign(tx, KEY_HEX)
        result = {"tx_hash": signed["txID"], "signed": signed}
        journal.execute("INSERT INTO signed VALUES (?, ?, ?, ?)", (p.id, micro, time.time(), json.dumps(result)))
        journal.commit()
        return result


@app.get("/health")
async def health():
    return {"address": ADDRESS}
