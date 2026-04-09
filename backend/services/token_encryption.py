import json
from cryptography.fernet import Fernet
from config import settings


def _get_fernet() -> Fernet:
    key = settings.oauth_encryption_key
    if not key:
        raise RuntimeError("OAUTH_ENCRYPTION_KEY не задан")
    return Fernet(key.encode())


def encrypt_tokens(data: dict) -> str:
    return _get_fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_tokens(encrypted: str) -> dict:
    return json.loads(_get_fernet().decrypt(encrypted.encode()).decode())
