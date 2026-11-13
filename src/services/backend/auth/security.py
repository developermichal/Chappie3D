"""Authentication and security utilities for Chappie backend."""

import hashlib
import hmac
import secrets

DEFAULT_ITERATIONS = 100_000
DEFAULT_ALGORITHM = "sha256"


def hash_password(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    """Hash a plaintext password using salted PBKDF2-HMAC-SHA256."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")

    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        hash_name=DEFAULT_ALGORITHM,
        password=password.encode("utf-8"),
        salt=salt,
        iterations=iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash safely."""
    if not plain_password or not hashed_password:
        return False

    try:
        parts = hashed_password.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False

        iterations = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected_hash = bytes.fromhex(parts[3])

        candidate_hash = hashlib.pbkdf2_hmac(
            hash_name=DEFAULT_ALGORITHM,
            password=plain_password.encode("utf-8"),
            salt=salt,
            iterations=iterations,
        )
        return hmac.compare_digest(candidate_hash, expected_hash)
    except (ValueError, IndexError, TypeError):
        return False
