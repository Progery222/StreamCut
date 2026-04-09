import re
import hashlib
from pathlib import Path


def safe_filename(text: str, max_length: int = 50) -> str:
    safe = re.sub(r'[^\w\s\-]', '', text, flags=re.UNICODE)
    safe = re.sub(r'[-\s]+', '-', safe).strip('-')
    return safe[:max_length] or "video"


def url_to_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def cleanup_old_files(directory: Path, max_age_hours: int = 24):
    import time
    cutoff = time.time() - (max_age_hours * 3600)
    for item in directory.iterdir():
        if item.stat().st_mtime < cutoff:
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                import shutil
                shutil.rmtree(item)
