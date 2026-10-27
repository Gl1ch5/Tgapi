import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet

from .config import SECRET_KEY

_fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"tgapi-session:" + SECRET_KEY).digest()))


def encrypt(text: str) -> str:
    return _fernet.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), dk)
    except ValueError:
        return False


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def new_api_key() -> str:
    return "tgk_" + secrets.token_urlsafe(32)


def new_token() -> str:
    return secrets.token_urlsafe(32)


class RateLimiter:
    """Sliding-window limiter, in memory. Good enough for a single-process panel."""

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        t = time.monotonic()
        dq = self.hits[key]
        while dq and dq[0] < t - self.window:
            dq.popleft()
        if len(dq) >= self.limit:
            return False
        dq.append(t)
        return True


login_limiter = RateLimiter(limit=10, window=300)
