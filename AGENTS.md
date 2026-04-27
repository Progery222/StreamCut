# AGENTS.md — StreamCUT (VideoShorts AI)

> Hard-earned context for OpenCode agents working in this repo.

## Stack & Architecture

- **Backend**: Python 3.11, FastAPI, Celery (Redis broker), Pydantic Settings
- **AI / ML**: faster-whisper (transcription), OpenAI GPT-4o-mini (analysis), YOLOv8 (reframing), MediaPipe
- **Video**: yt-dlp, FFmpeg, OpenCV
- **Frontend**: Vanilla HTML/CSS/JS (no framework), served by nginx
- **Deploy**: Docker Compose (redis, backend, worker, nginx)

Entrypoints:
- `backend/main.py` — FastAPI app, includes routers from `backend/routers/`
- `backend/worker.py` — Celery worker with `process_video` task
- `frontend/index.html` + `app.js` — SPA frontend
- `nginx.conf` — proxies `/api/*` to backend, serves frontend, `/storage/` for downloads

## Critical Commands

```bash
# Lint + format (must pass before commit)
uv run ruff check .
uv run ruff format .

# Run tests
uv run pytest tests/ -v

# Local development (4 terminals)
redis-server                              # 1. Redis
cd backend && uvicorn main:app --reload --port 8000   # 2. Backend
cd backend && celery -A worker worker --loglevel=info # 3. Worker
cd frontend && python -m http.server 3000             # 4. Frontend

# Docker (production-like)
docker compose up -d --build
# Backend exposed on :8003, nginx on :80, frontend via nginx
```

## Docker Gotchas

- **Build context is `./backend`**, not repo root. Dockerfiles must copy `requirements.txt` directly (not from `backend/`).
- Both `backend/Dockerfile` and `backend/Dockerfile.worker` exist. They are nearly identical; worker omits `EXPOSE` and runs Celery instead of uvicorn.
- Backend container mounts `./backend:/app` for live reload in dev; worker also mounts it.
- Healthcheck: backend hits `http://localhost:8000/health` internally. Nginx waits for `service_healthy`.

## Config & Environment

- `backend/config.py` — `pydantic-settings` reads from `.env` (auto-loaded). All env vars are lowercase in code.
- **Must set**: `OPENAI_API_KEY`
- Optional: `MINIO_URL` + `MINIO_*` for S3-compatible storage (preferred over legacy Cloudflare R2)
- `STORAGE_PATH` defaults to `/app/storage`; locally it's `./storage/`
- Subdirs auto-created: `downloads/`, `processed/`, `temp/`, `cache/`, `footage_library/`

## Storage Layer (`services/storage.py`)

- Abstracts local disk vs MinIO/R2. Falls back to local if no MinIO/R2 config.
- `processed/` → served via `/storage/` nginx alias (attachment header) OR via MinIO public URL
- `downloads/` — cached original videos keyed by URL hash
- `cache/` — cached Whisper transcriptions keyed by `(url, language)` hash
- `temp/` — always local, cleaned up after each job

## Job Flow & State

1. `POST /jobs` → creates UUID job, stores owner in Redis, enqueues Celery task
2. Worker updates progress in Redis (`job:{id}:state`, TTL 24h)
3. `GET /jobs/{id}` polls state JSON (status, progress, message, steps, shorts[])
4. Steps: download → transcribe → analyze → cut → reframe → render → publish

Parallelism: up to 3 clips rendered simultaneously via `asyncio.Semaphore(3)`.

## Auth

- JWT via `Authorization: Bearer <token>` header. `JWT_SECRET` from env.
- Users stored in Redis (`user:{username}`). Guest mode works without auth (returns `"guest"`).
- OAuth tokens encrypted with `OAUTH_ENCRYPTION_KEY` (Fernet).

## Frontend Notes

- API base is `/api` — nginx rewrites `/api/(.*)` → `/$1` and proxies to backend.
- Frontend uses vanilla JS with no build step. Direct DOM manipulation.
- Auth token in `localStorage`. Preset management, OAuth connections, and history all API-driven.

## Code Style

- `pyproject.toml`: ruff line-length 120, target py311, ignores `B008` (FastAPI Depends), `E501`
- Tests path: `tests/`, pythonpath includes `backend/`
- **Russian UI strings** in frontend; backend logs in Russian; code comments can be Russian.
- Prefer `str | None` over `Optional[str]` (py311).

## Feature-Specific Notes

### Footage Library (`services/footage_library.py`)
- Requires running `python -m scripts.prepare_footage --source-dir <dir>` to populate `storage/footage_library/`
- If `footage_layout != "none"` but library is empty, worker throws RuntimeError
- Clips pick footage by category with anti-duplication per batch/session via Redis

### Reframing (`services/reframer.py`)
- `reframe_mode`: `"center"` (default) or `"ai"`
- AI mode: talking-head detection → fit; face detected → split-screen; otherwise → smart crop trajectory

### Captions (`services/caption_renderer.py`)
- `caption_position`: `"auto"` adapts to layout; `"fixed_bottom"` forces y≈1420
- `add_watermark` defaults to `True` (Rumble-style watermark)

### Publishing
- YouTube / TikTok OAuth flows in `backend/routers/oauth.py`
- Tokens stored encrypted in Redis (`oauth:{username}:{platform}`)

## When Modifying

- **Add API field?** Update `backend/models/schemas.py` + `main.py` endpoint + frontend `app.js` form + `collectSettings()`
- **Add worker option?** Pass through `options` dict in `celery_app.send_task()`, read in `worker.py` `_process_video_async()`
- **Add frontend setting?** Add to `index.html` form, `app.js` `collectSettings()` and `applySettings()`, preset save/load
- **Docker change?** Verify both `Dockerfile` and `Dockerfile.worker`; remember build context is `./backend`

## Existing Instruction Files

- `CLAUDE.md` — Original project brief with stack overview and commands
- `.env.example` — All env vars documented in Russian
