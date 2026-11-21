import hashlib
import os
import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ai_platform.settings import settings

password_hasher = PasswordHasher()


class KeyUnavailable(RuntimeError):
    pass


def digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def token() -> str:
    return secrets.token_urlsafe(32)


@lru_cache
def dummy_password() -> str:
    return password_hasher.hash(token())


def password_matches(encoded: str | None, password: str) -> bool:
    try:
        password_hasher.verify(encoded or dummy_password(), password)
        return encoded is not None
    except VerifyMismatchError:
        return False


def cipher(version: str) -> AESGCM:
    config = settings()
    if config.credential_key_file is None or config.credential_key_version != version:
        raise KeyUnavailable("Configured credential key version is unavailable")
    try:
        key = config.credential_key_file.read_bytes()
    except OSError as exc:
        raise KeyUnavailable("Configured credential key is unavailable") from exc
    if len(key) != 32:
        raise KeyUnavailable("Credential key must contain 32 raw bytes")
    return AESGCM(key)


def encrypt(value: str, aad: str) -> tuple[bytes, str]:
    version = settings().credential_key_version
    nonce = os.urandom(12)
    return nonce + cipher(version).encrypt(nonce, value.encode(), aad.encode()), version


def decrypt(payload: bytes, version: str, aad: str) -> str:
    try:
        return cipher(version).decrypt(payload[:12], payload[12:], aad.encode()).decode()
    except (InvalidTag, ValueError) as exc:
        raise KeyUnavailable("Stored credential cannot be decrypted") from exc
