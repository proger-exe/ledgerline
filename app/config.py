import os
from pathlib import Path


def secret(name: str, default: str = "") -> str:
    """Docker secret file (NAME_FILE) wins over a plain env var."""
    path = os.environ.get(f"{name}_FILE")
    return Path(path).read_text().strip() if path else os.environ.get(name, default)


BOT_TOKEN = secret("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+asyncpg://core:core@localhost:5432/core")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID") or 0)

TRON_NETWORK = os.environ.get("TRON_NETWORK", "mainnet")  # mainnet | nile
TRONGRID_API_KEY = os.environ.get("TRONGRID_API_KEY", "")
CONFIRMATIONS = int(os.environ.get("CONFIRMATIONS", "19"))

SIGNER_URL = os.environ.get("SIGNER_URL", "http://signer:8001")
SIGNER_TOKEN = secret("SIGNER_TOKEN")

# Telegram re-signs initData on every Mini App launch; older data = expired session.
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", str(12 * 3600)))
WATCH_INTERVAL = int(os.environ.get("WATCH_INTERVAL", "30"))
