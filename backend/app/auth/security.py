import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt

from app.config import settings
from app.models import now


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, iterations, salt, expected = encoded.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def access_token(user_id: str, session_id: str) -> str:
    return jwt.encode({"sub": user_id, "sid": session_id, "iat": now(), "exp": now() + timedelta(minutes=15)}, settings.jwt_secret, algorithm="HS256")


def decode_access(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["sub", "sid", "exp"]})
