"""JWT (HS256), bcrypt passwords and API keys (CONTRACT §2.2, §2.6)."""

from __future__ import annotations

import hashlib
import re
import secrets
import string
import uuid
from datetime import timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.tz import utcnow

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# Contract §2.6 says sk_ + 40 chars; the seeded defaults in §1.3 are 42 chars, so 40–48 are accepted (generated keys are exactly 40).
API_KEY_RE = re.compile(r"^sk_[0-9a-z]{40,48}$")
_ALPHABET = string.ascii_lowercase + string.digits


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def password_policy_ok(password: str) -> bool:
    return len(password) >= 10 and any(c.isalpha() for c in password) and any(c.isdigit() for c in password)


def create_access_token(user: Any) -> tuple[str, Any]:
    now = utcnow()
    exp = now + timedelta(hours=settings.JWT_EXPIRE_HOURS)
    claims = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "department_id": user.department_id,
        "district": user.district,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(claims, settings.JWT_SECRET, algorithm="HS256"), exp


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return None


def generate_api_key() -> str:
    return "sk_" + "".join(secrets.choice(_ALPHABET) for _ in range(40))


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def api_key_prefix(key: str) -> str:
    return key[3:11] if key.startswith("sk_") else key[:8]


def valid_api_key_format(key: str) -> bool:
    return bool(API_KEY_RE.match(key or ""))
