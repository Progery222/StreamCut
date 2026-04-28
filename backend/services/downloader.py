import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget
import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class VideoDownloader:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def _get_ydl_opts(self, job_id: str, progress_callback: Optional[Callable] = None) -> dict:
        output_template = str(self.output_dir / f"{job_id}.%(ext)s")

        def progress_hook(d):
            if d["status"] == "downloading" and progress_callback:
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                if total > 0:
                    percent = int((downloaded / total) * 100)
                    progress_callback(percent)

        ydl_opts = {
            "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": output_template,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "extract_flat": False,
            "extractor_args": {
                "youtube": {"skip": ["dash", "hls"]},
            },
                "impersonate": ImpersonateTarget("chrome", None, None, None),
        }
        
        cookie_path = Path("storage/cookies.txt")
        if cookie_path.exists():
            ydl_opts["cookiefile"] = str(cookie_path)
            
        return ydl_opts

    async def download(
        self,
        url: str,
        job_id: str,
        max_duration: int = 3600,
        progress_callback: Optional[Callable] = None,
    ) -> Path:
        loop = asyncio.get_event_loop()

        def _download():
            from services.storage import storage

            # 0) If url is a local file path, copy it directly
            local_path = Path(url)
            if local_path.exists() and local_path.is_file():
                ext = local_path.suffix.lstrip(".") or "mp4"
                target = self.output_dir / f"{job_id}.{ext}"
                import shutil
                shutil.copy2(local_path, target)
                logger.info(f"Video from local path: {local_path} -> {target}")
                return target

            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

            # 1) Check local cache
            for ext in ["mp4", "mkv", "webm", "avi"]:
                cached = self.output_dir / f"{url_hash}.{ext}"
                if cached.exists() and cached.stat().st_size > 0:
                    target = self.output_dir / f"{job_id}.{ext}"
                    import shutil
                    shutil.copy2(cached, target)
                    logger.info(f"Video from local cache: {cached}")
                    return target

            # 2) Check MinIO cache
            if storage.enabled:
                for ext in ["mp4", "mkv", "webm"]:
                    s3_key = f"downloads/{url_hash}.{ext}"
                    target = self.output_dir / f"{job_id}.{ext}"
                    if storage.download(s3_key, target):
                        logger.info(f"Video from MinIO cache: {s3_key}")
                        return target

            # 3) Download fresh
            ydl_opts = self._get_ydl_opts(job_id, progress_callback)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if not info:
                    raise ValueError("Не удалось получить информацию о видео")

                duration = info.get("duration", 0)
                if max_duration > 0 and duration > max_duration:
                    raise ValueError(
                        f"Видео слишком длинное: {duration}с (максимум {max_duration}с)"
                    )

                ydl.download([url])

                for ext in ["mp4", "mkv", "webm", "avi"]:
                    path = self.output_dir / f"{job_id}.{ext}"
                    if path.exists():
                        # Save to local cache
                        import shutil
                        cache_path = self.output_dir / f"{url_hash}.{ext}"
                        if not cache_path.exists():
                            shutil.copy2(path, cache_path)
                        # Upload to MinIO cache
                        if storage.enabled:
                            try:
                                storage.upload(path, f"downloads/{url_hash}.{ext}")
                            except Exception as e:
                                logger.warning(f"MinIO cache upload failed: {e}")
                        logger.info(f"Video downloaded: {path}")
                        return path

                raise FileNotFoundError("Скачанный файл не найден")

        return await loop.run_in_executor(None, _download)

    async def get_video_info(self, url: str) -> dict:
        loop = asyncio.get_event_loop()

        def _get_info():
            ydl_opts = {
                "quiet": True,
            "impersonate": ImpersonateTarget("chrome", None, None, None),
            }
            cookie_path = Path("storage/cookies.txt")
            if cookie_path.exists():
                ydl_opts["cookiefile"] = str(cookie_path)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        return await loop.run_in_executor(None, _get_info)
