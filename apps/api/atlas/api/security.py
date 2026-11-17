"""Password hashing and policy, JWT access tokens, opaque tokens, TOTP, and CSRF helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from zxcvbn import zxcvbn

from atlas.api.settings import Settings

MIN_PASSWORD_LENGTH = 12


def make_hasher(settings: Settings) -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.argon_time_cost,
        memory_cost=settings.argon_memory_kib,
        parallelism=settings.argon_parallelism,
    )


class PasswordPolicyError(ValueError):
    pass


def check_password_policy(password: str, user_inputs: list[str] | None = None) -> None:
    """Length of at least 12, zxcvbn score of 3 or more, no composition rules."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > 256:
        raise PasswordPolicyError("Use at most 256 characters.")
    result = zxcvbn(password, user_inputs=[u for u in (user_inputs or []) if u])
    if result["score"] < 3:
        hint = result["feedback"].get("warning") or "Choose a less guessable password."
        raise PasswordPolicyError(hint)


def verify_password(hasher: PasswordHasher, stored: str, password: str) -> bool:
    try:
        return hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def dummy_hash(hasher: PasswordHasher) -> str:
    """A valid hash used to equalise timing when the account does not exist."""
    return hasher.hash("not-a-real-password-" + secrets.token_hex(4))


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def constant_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --- JWT ---------------------------------------------------------------------------------------


def issue_jwt(
    settings: Settings,
    subject: str,
    typ: str,
    ttl: int,
    claims: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": subject,
        "typ": typ,
        "iat": now,
        "nbf": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        **(claims or {}),
    }
    return jwt.encode(payload, settings.jwt_private_pem, algorithm="EdDSA")


def decode_jwt(settings: Settings, token: str, typ: str) -> dict[str, Any] | None:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_public_pem,
            algorithms=["EdDSA"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "typ"]},
        )
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == typ else None


# --- TOTP and recovery codes -------------------------------------------------------------------


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str, issuer: str = "Atlas Screening") -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str) -> bool:
    code = code.strip().replace(" ", "")
    return code.isdigit() and len(code) == 6 and pyotp.TOTP(secret).verify(code, valid_window=1)


def new_recovery_codes(count: int = 10) -> list[str]:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return [
        "".join(secrets.choice(alphabet) for _ in range(5))
        + "-"
        + "".join(secrets.choice(alphabet) for _ in range(5))
        for _ in range(count)
    ]


def hash_recovery(code: str, pepper: str) -> str:
    return hash_token(code.strip().lower(), pepper)


# --- CSRF --------------------------------------------------------------------------------------


def new_csrf() -> str:
    return secrets.token_urlsafe(24)
