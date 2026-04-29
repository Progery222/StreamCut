import json
import logging
import uuid

import redis as redis_lib
from auth import get_current_user
from config import settings
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from models.schemas import BatchResponse, CreateBatchRequest, CreateJobRequest, JobResponse, JobStatus
from routers.auth import router as auth_router
from routers.oauth import router as oauth_router
from routers.presets import router as presets_router
from services.footage_library import FootageLibrary
from worker import celery_app, update_job_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="VideoShorts AI",
    description="Автоматическая нарезка видео на шортсы",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(oauth_router, prefix="/auth", tags=["oauth"])
app.include_router(presets_router, prefix="/presets", tags=["presets"])

app.mount(
    "/storage",
    StaticFiles(directory=str(settings.processed_path)),
    name="storage",
)

redis_client = redis_lib.from_url(settings.redis_url)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/footage/categories")
async def footage_categories():
    """Return the list of available footage categories from the prepared library."""
    lib = FootageLibrary(settings.footage_library_path).load()
    return {"categories": lib.list_categories()}


@app.get("/jobs/active-count")
async def active_jobs_count():
    """Public endpoint for dashboard health monitoring (no auth required)."""
    count = 0
    for key in redis_client.scan_iter("job:*:state"):
        raw = redis_client.get(key)
        if raw:
            data = json.loads(raw)
            if data.get("status") not in ("done", "error"):
                count += 1
    return {"count": count}


@app.post("/jobs", response_model=JobResponse)
async def create_job(request: CreateJobRequest, username: str = Depends(get_current_user)):
    job_id = str(uuid.uuid4())

    logger.info(f"Новая задача: {job_id} для {request.url} от {username}")

    update_job_state(job_id, "pending", 0, "Задача создана, ожидает обработки")
    redis_client.set(f"job:{job_id}:owner", username)

    celery_app.send_task(
        "process_video",
        args=[
            job_id,
            request.url,
            {
                "language": request.language,
                "max_shorts": request.max_shorts,
                "min_duration": request.min_duration,
                "max_duration": request.max_duration,
                "caption_style": request.caption_style,
                "reframe_mode": request.reframe_mode,
                "add_music": request.add_music,
                "footage_layout": request.footage_layout,
                "footage_category": request.footage_category,
                "caption_position": request.caption_position,
                "add_watermark": request.add_watermark,
                "srt_timecodes": request.srt_timecodes,
                "publish_targets": request.publish_targets,
                "output_mode": request.output_mode,
                "username": username,
            },
        ],
        task_id=job_id,
    )

    return JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Задача создана",
        progress=0,
    )


@app.post("/batch", response_model=BatchResponse)
async def create_batch(request: CreateBatchRequest, username: str = Depends(get_current_user)):
    if not request.urls:
        raise HTTPException(status_code=400, detail="Список ссылок пуст")
    if len(request.urls) > 50:
        raise HTTPException(status_code=400, detail="Максимум 50 ссылок за раз")

    batch_id = str(uuid.uuid4())
    jobs = []

    options = {
        "language": request.language,
        "max_shorts": request.max_shorts,
        "min_duration": request.min_duration,
        "max_duration": request.max_duration,
        "caption_style": request.caption_style,
        "reframe_mode": request.reframe_mode,
        "add_music": request.add_music,
        "footage_layout": request.footage_layout,
        "footage_category": request.footage_category,
        "caption_position": request.caption_position,
        "add_watermark": request.add_watermark,
        "publish_targets": request.publish_targets,
        "output_mode": request.output_mode,
        "username": username,
    }

    for url in request.urls:
        url = url.strip()
        if not url:
            continue
        job_id = str(uuid.uuid4())
        update_job_state(job_id, "pending", 0, "В очереди")
        redis_client.set(f"job:{job_id}:owner", username)
        redis_client.sadd(f"batch:{batch_id}", job_id)
        redis_client.set(f"job:{job_id}:batch", batch_id)

        celery_app.send_task(
            "process_video",
            args=[job_id, url, options],
            task_id=job_id,
        )

        jobs.append(
            JobResponse(
                job_id=job_id,
                status=JobStatus.PENDING,
                message=f"В очереди: {url[:60]}",
                progress=0,
            )
        )

    redis_client.setex(f"batch:{batch_id}:owner", 86400, username)
    logger.info(f"Batch {batch_id}: {len(jobs)} задач от {username}")

    return BatchResponse(batch_id=batch_id, jobs=jobs, total=len(jobs))


@app.get("/batch/{batch_id}")
async def get_batch(batch_id: str, username: str = Depends(get_current_user)):
    owner = redis_client.get(f"batch:{batch_id}:owner")
    if owner and owner.decode() != username:
        raise HTTPException(status_code=403, detail="Нет доступа")

    job_ids = redis_client.smembers(f"batch:{batch_id}")
    if not job_ids:
        raise HTTPException(status_code=404, detail="Batch не найден")

    jobs = []
    for jid in job_ids:
        job_id = jid.decode()
        raw = redis_client.get(f"job:{job_id}:state")
        if not raw:
            continue
        data = json.loads(raw)
        jobs.append(
            JobResponse(
                job_id=job_id,
                status=JobStatus(data.get("status", "pending")),
                message=data.get("message", ""),
                progress=data.get("progress", 0),
                steps=data.get("steps"),
                shorts=data.get("shorts"),
                posts=data.get("posts"),
                error=data.get("error"),
            )
        )

    done = sum(1 for j in jobs if j.status in (JobStatus.DONE, JobStatus.ERROR))
    total = len(jobs)
    return {"batch_id": batch_id, "jobs": jobs, "total": total, "completed": done}


@app.get("/jobs", response_model=list[JobResponse])
async def list_jobs(username: str = Depends(get_current_user)):
    jobs = []
    for key in redis_client.scan_iter("job:*:owner"):
        if redis_client.get(key).decode() == username:
            job_id = key.decode().split(":")[1]
            raw = redis_client.get(f"job:{job_id}:state")
            if raw:
                data = json.loads(raw)
                jobs.append(
                    JobResponse(
                        job_id=job_id,
                        status=JobStatus(data.get("status", "pending")),
                        message=data.get("message", ""),
                        progress=data.get("progress", 0),
                        steps=data.get("steps"),
                        shorts=data.get("shorts"),
                        posts=data.get("posts"),
                        error=data.get("error"),
                    )
                )
    return jobs


@app.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, username: str = Depends(get_current_user)):
    owner = redis_client.get(f"job:{job_id}:owner")
    if owner and owner.decode() != username:
        raise HTTPException(status_code=403, detail="Нет доступа")

    raw = redis_client.get(f"job:{job_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    data = json.loads(raw)

    return JobResponse(
        job_id=job_id,
        status=JobStatus(data.get("status", "pending")),
        message=data.get("message", ""),
        progress=data.get("progress", 0),
        steps=data.get("steps"),
        shorts=data.get("shorts"),
        posts=data.get("posts"),
        error=data.get("error"),
    )


@app.get("/jobs/{job_id}/posts-txt")
async def download_posts_txt(job_id: str, username: str = Depends(get_current_user)):
    owner = redis_client.get(f"job:{job_id}:owner")
    if owner and owner.decode() != username:
        raise HTTPException(status_code=403, detail="Нет доступа")

    raw = redis_client.get(f"job:{job_id}:state")
    if not raw:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    data = json.loads(raw)
    posts = data.get("posts")
    if not posts:
        raise HTTPException(status_code=404, detail="Посты не найдены")

    lines = []
    for post in posts:
        post_type = post.get("type", "")
        platform = post.get("platform", "")
        content = post.get("content", "")
        if post_type == "meaningful":
            header = "=== Смысловой пост (Threads) ==="
        elif post_type == "trigger":
            header = "=== Триггерный пост (X) ==="
        elif post_type == "bite":
            header = "=== Байтный пост (X) ==="
        else:
            header = f"=== {post_type} ({platform}) ==="
        lines.append(header)
        lines.append(content)
        lines.append("")

    text = "\n".join(lines)
    return PlainTextResponse(
        text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="posts-{job_id}.txt"'},
    )


@app.get("/jobs/{job_id}/zip")
async def download_zip(job_id: str, username: str = Depends(get_current_user)):
    owner = redis_client.get(f"job:{job_id}:owner")
    if owner and owner.decode() != username:
        raise HTTPException(status_code=403, detail="Нет доступа")

    import io
    import zipfile

    from fastapi.responses import StreamingResponse
    from services.storage import storage

    buffer = io.BytesIO()
    found = False

    # Try local files first
    job_dir = settings.processed_path / job_id
    if job_dir.exists():
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in job_dir.iterdir():
                if f.is_file() and f.suffix == ".mp4":
                    zf.write(f, f.name)
                    found = True

    # Fall back to MinIO
    if not found and storage.enabled:
        keys = storage.list_keys(f"processed/{job_id}/")
        mp4_keys = [k for k in keys if k.endswith(".mp4")]
        if mp4_keys:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for key in mp4_keys:
                    data = storage.download_bytes(key)
                    if data:
                        filename = key.rsplit("/", 1)[-1]
                        zf.writestr(filename, data)
                        found = True

    if not found:
        raise HTTPException(status_code=404, detail="Файлы не найдены")

    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=shorts-{job_id[:8]}.zip"},
    )


@app.delete("/jobs/{job_id}")
async def delete_job(job_id: str, username: str = Depends(get_current_user)):
    owner = redis_client.get(f"job:{job_id}:owner")
    if owner and owner.decode() != username:
        raise HTTPException(status_code=403, detail="Нет доступа")

    # Delete local files
    job_dir = settings.processed_path / job_id
    if job_dir.exists():
        import shutil

        shutil.rmtree(job_dir)

    # Delete from MinIO
    from services.storage import storage

    if storage.enabled:
        storage.delete_prefix(f"processed/{job_id}/")

    redis_client.delete(f"job:{job_id}:state")
    redis_client.delete(f"job:{job_id}:owner")
    return {"message": "Задача удалена"}


@app.get("/download-video")
async def download_video(url: str):
    """Скачивает видео через yt-dlp и отдаёт файл пользователю."""
    import hashlib

    from fastapi.responses import FileResponse
    from services.downloader import VideoDownloader

    downloader = VideoDownloader(settings.temp_path)
    file_id = hashlib.md5(url.encode()).hexdigest()[:12]

    try:
        info = await downloader.get_video_info(url)
        title = info.get("title", "video") or "video"
        # Безопасное имя файла
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-").strip()[:80] or "video"

        video_path = await downloader.download(url=url, job_id=f"dl_{file_id}", max_duration=0)

        return FileResponse(
            path=str(video_path),
            filename=f"{safe_title}.mp4",
            media_type="video/mp4",
            background=None,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@app.get("/video-info")
async def get_video_info(url: str):
    from services.downloader import VideoDownloader

    downloader = VideoDownloader(settings.downloads_path)
    try:
        info = await downloader.get_video_info(url)
        return {
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "uploader": info.get("uploader"),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
