"""Password hashing (argon2id), opaque server-side sessions, CSRF tokens, simple rate limiter."""
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .config import get_settings

_ph = PasswordHasher()
COOKIE_NAME = "cf_session"
CSRF_HEADER = "x-csrf-token"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hash_: str, password: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except (VerificationError, InvalidHashError):
        return False


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if password.lower() in {"password123", "1234567890", "qwertyuiop"} or len(set(password)) < 5:
        return "Password is too easy to guess."
    if not (any(c.isalpha() for c in password) and any(c.isdigit() or not c.isalnum() for c in password)):
        return "Use letters plus at least one digit or symbol."
    return None


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """HMAC so a leaked DB can't be used to forge sessions without the secret key."""
    return hmac.new(get_settings().secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
