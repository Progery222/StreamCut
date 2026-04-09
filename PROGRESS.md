# PROGRESS

## Фаза 1: Инфраструктура
- [x] 1.1 requirements.txt, .env.example
- [x] 1.2 docker-compose.yml, Dockerfile, nginx.conf
- [x] 1.3 config.py — настройки из .env

## Фаза 2: Backend сервисы
- [x] 2.1 models/schemas.py — Pydantic модели
- [x] 2.2 services/downloader.py — скачивание через yt-dlp
- [x] 2.3 services/transcriber.py — транскрипция через faster-whisper
- [x] 2.4 services/analyzer.py — анализ моментов через GPT-4o-mini
- [x] 2.5 services/cutter.py — нарезка и конвертация 9:16 через FFmpeg
- [x] 2.6 services/caption_renderer.py — ASS субтитры + burn-in

## Фаза 3: Оркестрация
- [x] 3.1 worker.py — Celery задача с полным пайплайном
- [x] 3.2 main.py — FastAPI эндпоинты

## Фаза 4: Frontend
- [x] 4.1 index.html — структура страницы
- [x] 4.2 style.css — тёмная тема
- [x] 4.3 app.js — логика UI и поллинг

## Фаза 5: Расширения (после MVP)
- [x] 5.1 AI рефрейминг (YOLO + MediaPipe) — `services/reframer.py`, опция "AI-трекинг" в UI
- [x] 5.2 Анимированные субтитры (karaoke-эффект) — word-level timestamps + ASS \kf теги
- [x] 5.3 Авторизация (JWT) — `auth.py`, `routers/auth.py`, модалка логин/регистрация
- [x] 5.4 Прямая публикация (TikTok/YouTube API) — `services/publisher.py`, `routers/oauth.py`, OAuth2
- [x] 5.5 Автоочистка файлов (Celery beat) — `cleanup_files` задача, beat_schedule
- [x] 5.6 Транскрипция через OpenAI Whisper API (вместо локального GPU)
