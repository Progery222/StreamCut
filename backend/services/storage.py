import logging
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """S3-compatible storage abstraction (MinIO / Cloudflare R2 / local fallback).

    Priority: MINIO_URL > R2 vars > local filesystem.
    """

    def __init__(self):
        self._s3_client = None

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint and self._access_key and self._bucket)

    # ── S3 config resolution (MinIO takes priority over R2) ──────────────

    @property
    def _endpoint(self) -> str:
        return settings.minio_url or settings.r2_endpoint

    @property
    def _access_key(self) -> str:
        return settings.minio_access_key or settings.r2_access_key_id

    @property
    def _secret_key(self) -> str:
        return settings.minio_secret_key or settings.r2_secret_access_key

    @property
    def _bucket(self) -> str:
        return settings.minio_bucket or settings.r2_bucket

    @property
    def _public_url(self) -> str:
        return settings.minio_public_url or settings.r2_public_url or self._endpoint

    # ── S3 client ────────────────────────────────────────────────────────

    def _get_s3(self):
        if self._s3_client is None:
            import boto3
            from botocore.config import Config as BotoConfig

            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self._endpoint,
                aws_access_key_id=self._access_key,
                aws_secret_access_key=self._secret_key,
                region_name="auto",
                config=BotoConfig(signature_version="s3v4"),
            )
            self._ensure_bucket()
        return self._s3_client

    def _ensure_bucket(self):
        try:
            self._s3_client.head_bucket(Bucket=self._bucket)
        except Exception:
            try:
                self._s3_client.create_bucket(Bucket=self._bucket)
                logger.info(f"Created bucket: {self._bucket}")
            except Exception as e:
                logger.warning(f"Bucket check/create failed: {e}")

    # ── Upload ───────────────────────────────────────────────────────────

    def upload(self, local_path: Path, key: str, content_type: str = "video/mp4") -> str:
        if not self.enabled:
            return f"/storage/{key}"

        s3 = self._get_s3()
        s3.upload_file(
            str(local_path),
            self._bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        url = f"{self._public_url}/{self._bucket}/{key}"
        logger.info(f"Uploaded: {key}")
        return url

    def upload_bytes(self, data: bytes, key: str, content_type: str = "application/json") -> str:
        if not self.enabled:
            return ""

        s3 = self._get_s3()
        s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
        return f"{self._public_url}/{self._bucket}/{key}"

    # ── Download ─────────────────────────────────────────────────────────

    def download(self, key: str, local_path: Path) -> bool:
        if not self.enabled:
            return False

        try:
            s3 = self._get_s3()
            local_path.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(self._bucket, key, str(local_path))
            return True
        except Exception as e:
            logger.debug(f"Download failed ({key}): {e}")
            return False

    def download_bytes(self, key: str) -> bytes | None:
        if not self.enabled:
            return None

        try:
            s3 = self._get_s3()
            resp = s3.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception:
            return None

    # ── Exists / Delete / List ───────────────────────────────────────────

    def exists(self, key: str) -> bool:
        if not self.enabled:
            return False

        try:
            s3 = self._get_s3()
            s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> bool:
        if not self.enabled:
            return False

        try:
            s3 = self._get_s3()
            s3.delete_object(Bucket=self._bucket, Key=key)
            return True
        except Exception as e:
            logger.warning(f"Delete failed ({key}): {e}")
            return False

    def delete_prefix(self, prefix: str) -> int:
        if not self.enabled:
            return 0

        count = 0
        for key in self.list_keys(prefix):
            if self.delete(key):
                count += 1
        return count

    def list_keys(self, prefix: str) -> list[str]:
        if not self.enabled:
            return []

        try:
            s3 = self._get_s3()
            keys = []
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    keys.append(obj["Key"])
            return keys
        except Exception as e:
            logger.warning(f"List failed ({prefix}): {e}")
            return []

    # ── URL helpers ──────────────────────────────────────────────────────

    def get_url(self, key: str) -> str:
        if self.enabled:
            return f"{self._public_url}/{self._bucket}/{key}"
        return f"/storage/{key}"


storage = StorageService()
