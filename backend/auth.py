import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Header
import redis as redis_lib
from config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_redis = redis_lib.from_url(settings.redis_url)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(
        {"sub": username, "exp": expire},
        settings.jwt_secret,
        algorithm="HS256",
    )


def get_user(username: str) -> dict | None:
    raw = _redis.get(f"user:{username}")
    if not raw:
        return None
    return json.loads(raw)


def create_user(username: str, password: str):
    _redis.set(f"user:{username}", json.dumps({
        "hashed_password": hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }))


def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "guest"
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        username = payload.get("sub")
        if username is None:
            return "guest"
    except JWTError:
        return "guest"

    if get_user(username) is None:
        return "guest"

    return username
