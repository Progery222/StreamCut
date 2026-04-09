import uuid
import json
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
import redis as redis_lib

from auth import get_current_user
from config import settings
from services.publisher import YouTubePublisher, TikTokPublisher
from services.token_encryption import encrypt_tokens, decrypt_tokens

router = APIRouter()
_redis = redis_lib.from_url(settings.redis_url)


# --- YouTube ---

@router.get("/youtube/connect")
async def youtube_connect(username: str = Depends(get_current_user)):
    state = str(uuid.uuid4())
    _redis.setex(f"oauth_state:{state}", 600, username)
    redirect_uri = f"{settings.app_base_url}/api/auth/youtube/callback"
    url = YouTubePublisher().build_auth_url(redirect_uri, state)
    return RedirectResponse(url)


@router.get("/youtube/callback")
async def youtube_callback(code: str, state: str):
    raw = _redis.get(f"oauth_state:{state}")
    if not raw:
        raise HTTPException(status_code=400, detail="Невалидный state")
    username = raw.decode()
    _redis.delete(f"oauth_state:{state}")

    redirect_uri = f"{settings.app_base_url}/api/auth/youtube/callback"
    tokens = YouTubePublisher().exchange_code(code, redirect_uri)
    encrypted = encrypt_tokens(tokens)
    _redis.set(f"oauth:{username}:youtube", encrypted)

    return RedirectResponse(f"{settings.app_base_url}/?connected=youtube")


@router.delete("/youtube/disconnect")
async def youtube_disconnect(username: str = Depends(get_current_user)):
    _redis.delete(f"oauth:{username}:youtube")
    return {"message": "YouTube отключён"}


# --- TikTok ---

@router.get("/tiktok/connect")
async def tiktok_connect(username: str = Depends(get_current_user)):
    state = str(uuid.uuid4())
    _redis.setex(f"oauth_state:{state}", 600, username)
    redirect_uri = f"{settings.app_base_url}/api/auth/tiktok/callback"
    url = TikTokPublisher().build_auth_url(redirect_uri, state)
    return RedirectResponse(url)


@router.get("/tiktok/callback")
async def tiktok_callback(code: str, state: str):
    raw = _redis.get(f"oauth_state:{state}")
    if not raw:
        raise HTTPException(status_code=400, detail="Невалидный state")
    username = raw.decode()
    _redis.delete(f"oauth_state:{state}")

    redirect_uri = f"{settings.app_base_url}/api/auth/tiktok/callback"
    tokens = TikTokPublisher().exchange_code(code, redirect_uri)
    encrypted = encrypt_tokens(tokens)
    _redis.set(f"oauth:{username}:tiktok", encrypted)

    return RedirectResponse(f"{settings.app_base_url}/?connected=tiktok")


@router.delete("/tiktok/disconnect")
async def tiktok_disconnect(username: str = Depends(get_current_user)):
    _redis.delete(f"oauth:{username}:tiktok")
    return {"message": "TikTok отключён"}


# --- Connections ---

@router.get("/connections")
async def get_connections(username: str = Depends(get_current_user)):
    platforms = {}
    for platform in ["youtube", "tiktok"]:
        platforms[platform] = _redis.exists(f"oauth:{username}:{platform}") == 1
    return platforms
