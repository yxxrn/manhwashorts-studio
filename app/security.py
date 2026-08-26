"""Password hashing, token encryption, and session helpers.

Uses only stdlib ``hashlib.scrypt`` for password hashing (no external bcrypt
dependency) and Fernet for symmetric encryption of OAuth credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

# scrypt parameters: N=2**15 keeps hashing ~100ms on a small VPS.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_LEN = 32
# OpenSSL defaults maxmem to 32 MiB, but 128*N*r is exactly 32 MiB here, so the
# allocation fails without an explicit, slightly larger ceiling.
_SCRYPT_MAXMEM = 128 * _SCRYPT_N * _SCRYPT_R * 2


def hash_password(password: str) -> str:
    """Return ``scrypt$N$r$p$salt$hash`` with a fresh random salt."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification. Returns False on any malformed input.

    Hash parameters are data stored in the database, not trusted configuration.
    Refuse unexpected values *before* calling ``hashlib.scrypt`` so a corrupt or
    tampered row cannot turn login into an attacker-controlled CPU/RAM allocation.
    Parameter migrations should explicitly add the previous approved tuple here.
    """
    if not password or not encoded:
        return False
    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, digest_hex = encoded.split("$")
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if (
            scheme != "scrypt"
            or (n, r, p) != (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P)
            or len(salt) != _SALT_BYTES
            or len(expected) != _KEY_LEN
        ):
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=_KEY_LEN,
            maxmem=_SCRYPT_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def _fernet() -> Fernet:
    return Fernet(settings.resolve_fernet_key())


def encrypt_json(payload: dict[str, Any]) -> str:
    """Encrypt a credential dict for at-rest storage."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _fernet().encrypt(raw).decode("ascii")


def decrypt_json(token: str) -> dict[str, Any]:
    """Decrypt a credential blob. Raises ValueError if tampered or wrong key."""
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode("ascii")).decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("could not decrypt credentials") from exc


def redact(value: str | None, keep: int = 4) -> str:
    """Mask a secret for safe logging: ``ghp_abcd...`` -> ``ghp_...3D6D``."""
    if not value:
        return "<unset>"
    if len(value) <= keep:
        return "*" * len(value)
    return f"...{value[-keep:]}"


def new_idempotency_key() -> str:
    return secrets.token_hex(16)
