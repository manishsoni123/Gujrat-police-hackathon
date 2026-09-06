"""JWT (HS256, PyJWT), bcrypt passwords and API keys (CONTRACT §2.2, §2.6)."""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.tz import utcnow

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# Contract §2.6 says sk_ + 40 chars; the seeded defaults in §1.3 are 42 chars, so 40–48 are accepted (generated keys are exactly 40).
API_KEY_RE = re.compile(r"^sk_[0-9a-z]{40,48}$")
_ALPHABET = string.ascii_lowercase + string.digits
REQUIRED_CLAIMS = ["exp", "iat", "sub"]


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def password_policy_ok(password: str) -> bool:
    return len(password) >= 10 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password)


def not_before_epoch(token_not_before: datetime | None) -> int | None:
    """`users.token_not_before` as the first acceptable integer `iat` (ceil, so a token minted in the
    same second as a password reset is still newer than the reset)."""
    if token_not_before is None:
        return None
    return int(math.ceil(token_not_before.timestamp()))


def token_issued_after_reset(claims: dict[str, Any], token_not_before: datetime | None) -> bool:
    """False when the token's `iat` predates the user's `token_not_before` (password reset/change,
    deactivation, role/scope change): such tokens are revoked (CONTRACT Amendments 2026-09-05)."""
    floor = not_before_epoch(token_not_before)
    if floor is None:
        return True
    try:
        return int(claims.get("iat", 0)) >= floor
    except (TypeError, ValueError):
        return False


def create_access_token(user: Any) -> tuple[str, Any]:
    now = utcnow()
    exp = now + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    iat = int(now.timestamp())
    floor = not_before_epoch(getattr(user, "token_not_before", None))
    if floor is not None and iat < floor:
        iat = floor  # login right after a reset: never mint a token that is already revoked
    claims = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "department_id": user.department_id,
        "district": user.district,
        "iat": iat,
        "exp": int(exp.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256")
    return (token.decode("utf-8") if isinstance(token, bytes) else token), exp


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        # leeway: a token minted right after a password reset carries iat = ceil(token_not_before), up to 1 s ahead
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"], options={"require": REQUIRED_CLAIMS}, leeway=5)
    except jwt.PyJWTError:
        return None


def generate_api_key() -> str:
    return "sk_" + "".join(secrets.choice(_ALPHABET) for _ in range(40))


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def api_key_prefix(key: str) -> str:
    return key[3:11] if key.startswith("sk_") else key[:8]


def valid_api_key_format(key: str) -> bool:
    return bool(API_KEY_RE.match(key or ""))
