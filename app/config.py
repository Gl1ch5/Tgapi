import os
import secrets
from pathlib import Path

APP_NAME = "grzxk tg API"
VERSION = "1.0.0"

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "tgapi.sqlite3"

PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))

# Behind HTTPS reverse proxy the cookie should be Secure. Set COOKIE_SECURE=0 for plain http dev.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") not in ("0", "false", "no")
SESSION_TTL = int(os.getenv("SESSION_TTL", str(7 * 24 * 3600)))

DEVICE_MODEL = os.getenv("TG_DEVICE_MODEL", APP_NAME)
SYSTEM_VERSION = os.getenv("TG_SYSTEM_VERSION", "Linux")
APP_VERSION = os.getenv("TG_APP_VERSION", VERSION)


def _load_secret() -> bytes:
    env = os.getenv("SECRET_KEY")
    if env:
        return env.encode()
    path = DATA_DIR / "secret.key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip().encode()


SECRET_KEY = _load_secret()
