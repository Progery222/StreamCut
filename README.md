# StreamCUT

AI-powered service that automatically converts long-form videos into short-form vertical clips (Reels / TikTok / YouTube Shorts) with smart reframing, animated subtitles, B-roll footage overlays, and direct publishing.

Supports YouTube, Rutube, VK Video, Rumble, Dailymotion, and 1000+ sites via yt-dlp.

## Features

- **AI Moment Detection** — GPT-4o-mini / Gemini / Ollama analyzes transcripts to find the most engaging moments
- **Smart Reframing** — YOLOv8 + MediaPipe track speakers and faces for intelligent vertical cropping
- **6 Caption Styles** — Karaoke (word-level highlight), Neon Glow, Bold, Default, Highlight, Minimal
- **B-Roll Footage System** — overlay gameplay/satisfying footage in background, top, or bottom layouts with session-aware dedup
- **Batch Processing** — process up to 50 URLs in one request
- **Background Music** — auto-mix upbeat, calm, or motivation tracks
- **Direct Publishing** — upload to YouTube and TikTok via OAuth2
- **Transcript Caching** — Redis-backed cache avoids re-transcribing the same video
- **Auto Cleanup** — Celery Beat removes expired files on schedule

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, Celery, Redis |
| AI | OpenAI Whisper / Groq Whisper, GPT-4o-mini / Gemini / Ollama |
| Video | FFmpeg, yt-dlp, OpenCV, YOLOv8, MediaPipe |
| Frontend | Vanilla HTML/CSS/JS (no frameworks) |
| Infra | Docker Compose, Nginx |
| Storage | Local disk or Cloudflare R2 (S3-compatible) |

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Progery222/StreamCut.git
cd StreamCut
cp .env.example .env    # fill in OPENAI_API_KEY at minimum
docker-compose up -d
```

Open http://localhost in your browser.

### Local Development

```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery Worker
cd backend && celery -A worker worker --loglevel=info

# Terminal 3: FastAPI Backend
cd backend && uvicorn main:app --reload --port 8000

# Terminal 4: Frontend
cd frontend && python -m http.server 3000
```

### GPU Support (Nvidia)

```bash
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

## Project Structure

```
backend/
  main.py                  # FastAPI application & REST endpoints
  worker.py                # Celery worker — full video processing pipeline
  config.py                # Settings from .env (Pydantic)
  auth.py                  # JWT authentication
  models/schemas.py        # Pydantic request/response models
  services/
    downloader.py          # yt-dlp video downloader
    transcriber.py         # Whisper API / Groq transcription
    analyzer.py            # LLM moment detection (GPT / Gemini / Ollama)
    cutter.py              # FFmpeg clip extraction & footage compositing
    caption_renderer.py    # ASS subtitle generation & burn-in
    reframer.py            # YOLOv8 + MediaPipe smart cropping
    footage_library.py     # B-roll footage selector with Redis dedup
    publisher.py           # YouTube & TikTok upload via OAuth2
    storage.py             # Local disk / Cloudflare R2 abstraction
  scripts/
    prepare_footage.py     # CLI: slice raw videos into footage chunks
  routers/
    auth.py                # /auth/* endpoints
    oauth.py               # YouTube & TikTok OAuth2 flows
frontend/
  index.html               # Single-page UI
  style.css                # Dark theme with glassmorphism
  app.js                   # All client-side logic
```

## API

### Core

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/video-info?url=` | Video metadata (title, duration, thumbnail) |
| `GET` | `/footage/categories` | Available B-roll footage categories |

### Jobs

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/jobs` | Create a processing job |
| `GET` | `/jobs` | List user's jobs |
| `GET` | `/jobs/{id}` | Job status with progress steps |
| `DELETE` | `/jobs/{id}` | Delete job and files |
| `GET` | `/jobs/{id}/zip` | Download all shorts as ZIP |

### Batch

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/batch` | Create batch job (up to 50 URLs) |
| `GET` | `/batch/{id}` | Batch status with per-job breakdown |

### Auth

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/register` | Register new user |
| `POST` | `/auth/login` | Login, returns JWT token |
| `GET` | `/auth/me` | Current user info |
| `GET` | `/auth/connections` | YouTube & TikTok connection status |

## Processing Pipeline

```
1. Download      — yt-dlp fetches video (best quality up to 1080p)
2. Transcribe    — Whisper API / Groq with word-level timestamps
3. Analyze       — LLM detects engaging moments, validates speech density
4. Cut           — FFmpeg extracts clips at exact timestamps
5. Reframe       — AI vertical cropping (YOLOv8 person tracking)
6. Render        — Subtitles + B-roll footage + music compositing
7. Publish       — Optional upload to YouTube / TikTok
```

## Footage System

Overlay B-roll footage (gameplay, satisfying videos, etc.) on generated shorts.

**Layouts:**
- `background` — full-screen footage with streamer overlaid (1/3 screen height)
- `footage_top` — 50/50 split, footage on top, streamer on bottom
- `footage_bottom` — 50/50 split, streamer on top, footage on bottom

**Preparing footage:**

```bash
# Place raw videos in storage/adhd_cut/ with a categories.json manifest
# Example categories.json: {"minecraft.mp4": "gameplay", "paint_pour.mp4": "paint"}

cd backend
python -m scripts.prepare_footage --source-dir ../storage/adhd_cut
```

This slices videos into 15/20/30/45/60s chunks organized by category and generates `library.json`.

## Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key (for Whisper + GPT analysis) |
| `REDIS_URL` | Yes | Redis connection URL |
| `STORAGE_PATH` | Yes | Path for downloads, processed files, temp |
| `JWT_SECRET` | Yes | Secret key for JWT tokens (change in production!) |
| `GROQ_API_KEY` | No | Groq API key (faster transcription alternative) |
| `GEMINI_API_KEY` | No | Google Gemini key (free analysis alternative) |
| `TRANSCRIPTION_PROVIDER` | No | `openai` (default) or `groq` |
| `ANALYZER_PROVIDER` | No | `openai` (default), `gemini`, or `ollama` |
| `OLLAMA_BASE_URL` | No | Ollama endpoint for local LLM analysis |
| `YOUTUBE_CLIENT_ID/SECRET` | No | YouTube OAuth2 credentials for publishing |
| `TIKTOK_CLIENT_KEY/SECRET` | No | TikTok OAuth2 credentials for publishing |
| `R2_*` | No | Cloudflare R2 credentials for cloud storage |

See [.env.example](.env.example) for the full list with defaults.

## Development

```bash
# Lint
ruff check .

# Format
ruff format .

# Test
pytest tests/ -v
```

Configuration in `pyproject.toml` (line-length 120, Python 3.11 target).

## Architecture

```
                    ┌──────────┐
    Browser ──────> │  Nginx   │ :80
                    └────┬─────┘
                         │
              ┌──────────┴──────────┐
              │                     │
        /api/* │              static │ + /storage/*
              v                     v
        ┌──────────┐         ┌──────────┐
        │ FastAPI  │         │ Frontend │
        │ :8000    │         │  HTML/JS │
        └────┬─────┘         └──────────┘
             │
             │ Celery task
             v
        ┌──────────┐    ┌───────┐
        │  Worker  │───>│ Redis │
        │ (FFmpeg, │    │ :6379 │
        │  AI,     │    └───────┘
        │  yt-dlp) │
        └──────────┘
```

## License

MIT
