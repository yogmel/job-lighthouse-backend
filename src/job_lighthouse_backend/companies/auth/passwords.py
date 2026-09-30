"""Password hashing (Argon2id). Only hashes are ever stored."""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

# Verified against when there is no real hash (unknown email, Google-only
# account), so those cases take as long as a wrong password.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """True if ``password`` matches. ``None`` hash always fails, in constant-ish time."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and (
            password_hash is not None
        )
    except (VerificationError, InvalidHashError):
        return False
