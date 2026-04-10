# StreamCUT (VideoShorts AI)

## Стек
- Python 3.11, FastAPI, Celery, Redis
- AI: faster-whisper (транскрипция), OpenAI GPT-4o-mini (анализ)
- Видео: yt-dlp, FFmpeg
- Frontend: HTML/CSS/JS (без фреймворков)
- Деплой: Docker Compose + Nginx

## Структура
```
backend/
  main.py              # FastAPI
  worker.py            # Celery worker
  config.py            # Настройки из .env
  services/            # downloader, transcriber, analyzer, cutter, caption_renderer
  models/schemas.py    # Pydantic модели
  utils/helpers.py     # Утилиты
frontend/
  index.html, style.css, app.js
storage/               # downloads/, processed/, temp/
```

## Команды

### Docker (продакшн)
```bash
cp .env.example .env   # заполнить OPENAI_API_KEY
docker-compose up -d
# http://localhost
```

### Локальная разработка
```bash
# Терминал 1: Redis
redis-server

# Терминал 2: Worker
cd backend && celery -A worker worker --loglevel=info

# Терминал 3: Backend
cd backend && uvicorn main:app --reload --port 8000

# Терминал 4: Frontend
cd frontend && python -m http.server 3000
```

## API
- `POST /jobs` — создать задачу (body: {url, language, max_shorts, caption_style})
- `GET /jobs/{id}` — статус задачи
- `DELETE /jobs/{id}` — удалить задачу
- `GET /video-info?url=...` — инфо о видео
- `GET /health` — проверка

## Storage

По умолчанию файлы хранятся локально в `storage/`. При настройке MinIO (`MINIO_URL`) переключается на S3-совместимое хранилище:

- `processed/` → MinIO `streamcut/processed/{job_id}/` (готовые шортсы)
- `downloads/` → MinIO `streamcut/downloads/{url_hash}.mp4` (кеш скачанных видео)
- `cache/` → MinIO `streamcut/cache/{hash}.json` (кеш транскрипций)
- `footage_library/` → MinIO `streamcut/footage_library/` (B-roll чанки, скачиваются и кешируются локально)
- `temp/` → всегда локально (рабочие файлы FFmpeg)

`StorageService` (`services/storage.py`) — единый абстрактный слой. Приоритет: MINIO_URL > R2 vars > local fallback.

## Переменные окружения
См. `.env.example`

## Проверки
```bash
# Линтер
ruff check .

# Форматирование
ruff format .

# Тесты
pytest tests/ -v
```
Конфигурация в `pyproject.toml`. Тесты должны проходить и линтер не должен выдавать ошибок перед коммитом.
