import json
import logging

from auth import get_current_user
from config import settings
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis import Redis

logger = logging.getLogger(__name__)
router = APIRouter()
_redis = Redis.from_url(settings.redis_url)
PRESET_TTL = 86400 * 90  # 90 days


class PresetData(BaseModel):
    name: str
    language: str | None = "auto"
    max_shorts: int | None = 5
    caption_style: str | None = "default"
    reframe_mode: str | None = "center"
    add_music: str | None = "none"
    footage_layout: str | None = "none"
    footage_category: str | None = None
    caption_position: str | None = "auto"
    add_watermark: bool | None = True


class PresetResponse(BaseModel):
    name: str
    data: dict


@router.get("", response_model=list[PresetResponse])
def list_presets(user: str = Depends(get_current_user)):
    key = f"presets:{user}"
    raw = _redis.get(key)
    if not raw:
        return []
    presets = json.loads(raw)
    return [PresetResponse(name=name, data=data) for name, data in presets.items()]


@router.post("")
def save_preset(preset: PresetData, user: str = Depends(get_current_user)):
    key = f"presets:{user}"
    raw = _redis.get(key)
    presets = json.loads(raw) if raw else {}
    presets[preset.name] = preset.model_dump(exclude={"name"})
    _redis.set(key, json.dumps(presets, ensure_ascii=False))
    _redis.expire(key, PRESET_TTL)
    return {"ok": True, "name": preset.name}


@router.delete("/{name}")
def delete_preset(name: str, user: str = Depends(get_current_user)):
    key = f"presets:{user}"
    raw = _redis.get(key)
    if not raw:
        raise HTTPException(status_code=404, detail="Preset not found")
    presets = json.loads(raw)
    if name not in presets:
        raise HTTPException(status_code=404, detail="Preset not found")
    del presets[name]
    if presets:
        _redis.set(key, json.dumps(presets, ensure_ascii=False))
        _redis.expire(key, PRESET_TTL)
    else:
        _redis.delete(key)
    return {"ok": True}
