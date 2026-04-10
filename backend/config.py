from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    openai_api_key: str
    redis_url: str = "redis://localhost:6379/0"
    storage_path: str = "/app/storage"
    max_video_duration: int = 3600
    whisper_model: str = "medium"
    transcription_language: str = "auto"
    max_shorts: int = 7
    min_short_duration: int = 15
    max_short_duration: int = 60
    jwt_secret: str = "change-me-in-production"
    jwt_expire_minutes: int = 1440
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    oauth_encryption_key: str = ""
    app_base_url: str = "http://localhost"
    cleanup_interval_hours: int = 1
    file_max_age_hours: int = 24
    groq_api_key: str = ""
    gemini_api_key: str = ""
    transcription_provider: str = "openai"  # openai or groq
    analyzer_provider: str = "openai"  # openai, gemini or ollama
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "gemma4"
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    r2_public_url: str = ""
    # MinIO / S3-compatible storage (preferred over R2)
    minio_url: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "streamcut"
    minio_public_url: str = ""

    class Config:
        env_file = ".env"

    @property
    def downloads_path(self) -> Path:
        return Path(self.storage_path) / "downloads"

    @property
    def processed_path(self) -> Path:
        return Path(self.storage_path) / "processed"

    @property
    def temp_path(self) -> Path:
        return Path(self.storage_path) / "temp"

    @property
    def cache_path(self) -> Path:
        return Path(self.storage_path) / "cache"

    @property
    def footage_library_path(self) -> Path:
        return Path(self.storage_path) / "footage_library"


settings = Settings()

for p in [
    settings.downloads_path,
    settings.processed_path,
    settings.temp_path,
    settings.cache_path,
    settings.footage_library_path,
]:
    p.mkdir(parents=True, exist_ok=True)
