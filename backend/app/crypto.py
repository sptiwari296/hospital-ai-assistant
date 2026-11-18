"""Field-level encryption for highly sensitive identifiers (data protection at rest).

National ID numbers are stored only as Fernet ciphertext (AES-128-CBC + HMAC-SHA256).
Even someone with a database dump cannot read them without FIELD_ENCRYPTION_KEY,
which lives outside the database (in production: a secrets manager / KMS).
"""
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


@lru_cache
def _fernet() -> Fernet:
    key = os.getenv("FIELD_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("FIELD_ENCRYPTION_KEY is not set (run scripts/setup_security.py)")
    return Fernet(key.encode())


def encrypt(value: str | None) -> str | None:
    return _fernet().encrypt(value.encode()).decode() if value else value


def decrypt(token: str | None) -> str | None:
    if not token:
        return token
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise RuntimeError("cannot decrypt field: wrong FIELD_ENCRYPTION_KEY") from None
