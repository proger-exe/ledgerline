import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from app.auth import verify_init_data

TOKEN = "123456:TEST"


def init_data(token=TOKEN, auth_date=None, **extra):
    """Sign like Telegram does: HMAC(key=HMAC("WebAppData", token), sorted k=v lines)."""
    fields = {"auth_date": str(auth_date or int(time.time())), "query_id": "AAH",
              "user": json.dumps({"id": 42, "first_name": "Puff", "username": "puff"}), **extra}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    return urlencode({**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})


def test_valid():
    assert verify_init_data(init_data(signature="abc"), TOKEN, 3600)["id"] == 42


def test_tampered():
    with pytest.raises(ValueError):
        verify_init_data(init_data().replace("Puff", "Evil"), TOKEN, 3600)


def test_wrong_token():
    with pytest.raises(ValueError):
        verify_init_data(init_data(token="999:OTHER"), TOKEN, 3600)


def test_expired():
    with pytest.raises(ValueError):
        verify_init_data(init_data(auth_date=int(time.time()) - 7200), TOKEN, 3600)


def test_garbage():
    with pytest.raises(ValueError):
        verify_init_data("not init data", TOKEN, 3600)
