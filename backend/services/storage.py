import logging
from pathlib import Path
from typing import Optional
from config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """Абстракция хранилища: локальный диск или Cloudflare R2."""

    def __init__(self):
        self._s3_client = None

    @property
    def use_r2(self) -> bool:
        return bool(settings.r2_access_key_id and settings.r2_bucket)

    def _get_s3(self):
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                region_name="auto",
            )
        return self._s3_client

    def upload(self, local_path: Path, key: str) -> str:
        if not self.use_r2:
            return f"/storage/{key}"

        s3 = self._get_s3()
        s3.upload_file(
            str(local_path),
            settings.r2_bucket,
            key,
            ExtraArgs={"ContentType": "video/mp4"},
        )
        url = f"{settings.r2_public_url}/{key}"
        logger.info(f"Uploaded to R2: {url}")
        return url

    def get_url(self, key: str) -> str:
        if self.use_r2:
            return f"{settings.r2_public_url}/{key}"
        return f"/storage/{key}"


storage = StorageService()
