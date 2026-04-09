# StreamCUT — Полная документация проекта

> **VideoShorts AI** — сервис автоматической нарезки длинных видео на шортсы (Reels / TikTok / YouTube Shorts) с помощью AI.

---

## Содержание

1. [Общий обзор](#1-общий-обзор)
2. [Архитектура системы](#2-архитектура-системы)
3. [Структура проекта](#3-структура-проекта)
4. [Стек технологий и зависимости](#4-стек-технологий-и-зависимости)
5. [Полный пайплайн обработки видео](#5-полный-пайплайн-обработки-видео)
6. [Описание всех сервисов backend](#6-описание-всех-сервисов-backend)
7. [API эндпоинты](#7-api-эндпоинты)
8. [Система авторизации](#8-система-авторизации)
9. [Публикация на платформы](#9-публикация-на-платформы)
10. [Frontend](#10-frontend)
11. [Переменные окружения (.env)](#11-переменные-окружения-env)
12. [Docker и деплой](#12-docker-и-деплой)
13. [Локальная разработка на Windows](#13-локальная-разработка-на-windows)
14. [Тестирование](#14-тестирование)
15. [Известные особенности и подводные камни](#15-известные-особенности-и-подводные-камни)

---

## 1. Общий обзор

StreamCUT принимает ссылку на любое видео с YouTube (или другой платформы, поддерживаемой yt-dlp), автоматически:
1. Скачивает видео
2. Транскрибирует аудио через Whisper API
3. Отправляет транскрипт в LLM (GPT-4o-mini / Gemini / Ollama) для определения «лучших моментов»
4. Нарезает видео на клипы по найденным таймкодам
5. Конвертирует в вертикальный формат 9:16 (1080×1920) с умным рефреймингом
6. Накладывает анимированные субтитры в стиле Karaoke
7. Опционально добавляет фоновую музыку и публикует на YouTube / TikTok

---

## 2. Архитектура системы

```
┌─────────────────────────────────────────────────────┐
│                     USER BROWSER                     │
│               frontend/ (HTML/CSS/JS)                │
└────────────────────────┬────────────────────────────┘
                         │ HTTP → /api/*
                         ▼
┌─────────────────────────────────────────────────────┐
│                   NGINX (port 80)                    │
│  /        → frontend static                          │
│  /api/*   → proxy → backend:8000                     │
│  /storage/→ processed MP4 files                      │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│             FastAPI Backend (port 8000)              │
│  main.py — REST API                                  │
│  auth.py — JWT авторизация                           │
│  routers/auth.py, routers/oauth.py                   │
└────────────────────────┬────────────────────────────┘
                         │ Celery task → Redis broker
                         ▼
┌─────────────────────────────────────────────────────┐
│              Celery Worker (worker.py)               │
│  Выполняет полный пайплайн обработки видео           │
│  + Celery Beat (автоочистка каждые N часов)          │
│                                                      │
│  services/                                           │
│  ├── downloader.py   (yt-dlp)                        │
│  ├── transcriber.py  (OpenAI/Groq Whisper API)       │
│  ├── analyzer.py     (GPT-4o-mini/Gemini/Ollama)     │
│  ├── cutter.py       (FFmpeg)                        │
│  ├── reframer.py     (YOLOv8 + MediaPipe)            │
│  ├── caption_renderer.py (ASS + FFmpeg)              │
│  ├── publisher.py    (YouTube/TikTok API)            │
│  └── storage.py      (локальный диск / Cloudflare R2)│
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│               Redis (port 6379)                      │
│  - Broker для Celery задач                           │
│  - Хранилище состояния задач (job:*:state)           │
│  - Хранилище пользователей (user:*)                  │
│  - OAuth токены (oauth:*:youtube / :tiktok)          │
│  - Lock транскрипции (lock:transcribe)               │
│  - Кэш транскриптов (по MD5 URL+lang)                │
└─────────────────────────────────────────────────────┘

                External APIs:
                ├── OpenAI (Whisper + GPT-4o-mini)
                ├── Groq (Whisper large-v3, быстрый)
                ├── Google Gemini (gemini-2.5-flash)
                ├── Ollama (локальная LLM, напр. gemma4)
                ├── YouTube Data API v3
                └── TikTok Content Posting API
```

### Хранилище файлов (`storage/`)

```
storage/
├── downloads/   ← скачанные исходные видео (удаляются после обработки)
├── processed/   ← готовые шортсы MP4 (по job_id/)
│   └── {job_id}/
│       ├── short_1_title.mp4
│       ├── short_2_title.mp4
│       └── ...
├── temp/        ← временные клипы в процессе нарезки
└── cache/       ← кэш транскриптов (JSON файлы)
```

---

## 3. Структура проекта

```
StreamCut/
├── .env                    ← переменные окружения (создать из .env.example)
├── .env.example            ← шаблон переменных окружения
├── .gitignore
├── CLAUDE.md               ← краткий справочник для AI-ассистента
├── PROGRESS.md             ← лог разработки по фазам
├── docker-compose.yml      ← основная конфигурация Docker
├── docker-compose.gpu.yml  ← вариант с GPU (для faster-whisper локально)
├── nginx.conf              ← конфигурация Nginx reverse proxy
├── requirements.txt        ← корневой requirements (не используется, см. backend/)
├── preview*.jpg            ← скриншоты UI для разных стилей субтитров
├── storage/                ← папка хранилища (маппируется в Docker)
│
├── backend/
│   ├── Dockerfile          ← образ бэкенда (python:3.11-slim + ffmpeg + pytorch cpu)
│   ├── requirements.txt    ← Python зависимости
│   ├── main.py             ← FastAPI приложение, все REST эндпоинты
│   ├── worker.py           ← Celery worker + полный пайплайн + beat schedule
│   ├── config.py           ← настройки из .env через pydantic-settings
│   ├── auth.py             ← JWT-авторизация, хранение юзеров в Redis
│   ├── yolov8n.pt          ← модель YOLOv8 nano (детекция людей, 6.3 МБ)
│   ├── celerybeat-schedule ← файл состояния Celery Beat
│   ├── models/
│   │   └── schemas.py      ← Pydantic модели (JobStatus, JobResponse, VideoMoment и др.)
│   ├── routers/
│   │   ├── auth.py         ← /auth/register, /auth/login, /auth/me
│   │   └── oauth.py        ← /auth/youtube/*, /auth/tiktok/*, /auth/connections
│   ├── services/
│   │   ├── downloader.py   ← VideoDownloader (yt-dlp)
│   │   ├── transcriber.py  ← AudioTranscriber (OpenAI/Groq Whisper API)
│   │   ├── analyzer.py     ← MomentAnalyzer (OpenAI/Gemini/Ollama)
│   │   ├── cutter.py       ← VideoCutter (FFmpeg, рефрейминг 9:16)
│   │   ├── reframer.py     ← SmartReframer (YOLOv8 + MediaPipe)
│   │   ├── caption_renderer.py ← CaptionRenderer (ASS субтитры + FFmpeg)
│   │   ├── publisher.py    ← YouTubePublisher, TikTokPublisher
│   │   ├── storage.py      ← StorageService (локальный диск / Cloudflare R2)
│   │   └── token_encryption.py ← Fernet шифрование OAuth токенов
│   ├── utils/
│   │   └── helpers.py      ← cleanup_old_files и прочие утилиты
│   ├── fonts/              ← шрифты для субтитров (Montserrat ExtraBold и др.)
│   └── music/              ← фоновая музыка
│       ├── upbeat.mp3
│       ├── calm.mp3
│       └── motivation.mp3
│
└── frontend/
    ├── index.html          ← разметка SPA
    ├── style.css           ← тёмная тема, все стили
    └── app.js              ← вся логика UI (авторизация, polling, рендеринг)
```

---

## 4. Стек технологий и зависимости

### Backend (Python 3.11)

| Библиотека | Версия | Назначение |
|---|---|---|
| `fastapi` | 0.111.0 | REST API фреймворк |
| `uvicorn[standard]` | 0.30.0 | ASGI сервер |
| `celery` | 5.3.6 | Очередь задач (async обработка) |
| `redis` | 5.0.4 | Клиент Redis (broker + state store) |
| `yt-dlp` | ≥2025.3.31 | Скачивание видео |
| `openai` | ≥1.60.0 | Whisper API + GPT-4o-mini |
| `pydantic` | 2.7.1 | Data validation |
| `pydantic-settings` | 2.3.0 | Загрузка .env |
| `httpx` | ≥0.28.0 | Async HTTP клиент |
| `opencv-python-headless` | 4.9.0.80 | Работа с кадрами видео |
| `numpy` | 1.26.4 | Математика для reframer |
| `ultralytics` | ≥8.3.0 | YOLOv8 (детекция людей) |
| `mediapipe` | 0.10.14 | Детекция лиц |
| `python-jose[cryptography]` | 3.3.0 | JWT токены |
| `passlib[bcrypt]` | 1.7.4 | Хэширование паролей |
| `bcrypt` | 4.1.3 | Алгоритм bcrypt |
| `google-api-python-client` | 2.131.0 | YouTube API |
| `google-auth-oauthlib` | 1.2.0 | OAuth для YouTube |
| `google-genai` | ≥1.0.0 | Google Gemini API |
| `cryptography` | 42.0.8 | Fernet (шифрование OAuth токенов) |
| `torch` (CPU) | — | Нужен для ultralytics/YOLOv8 |

### Системные зависимости

| Инструмент | Назначение |
|---|---|
| **FFmpeg** | Нарезка, конвертация, рендеринг субтитров, аудио |
| **ffprobe** | Получение метаданных видео (размеры, длительность) |
| **Redis** | Брокер Celery + хранилище состояния |

### Frontend

Чистый Vanilla JS + HTML + CSS. Никаких фреймворков, никакой сборки.

---

## 5. Полный пайплайн обработки видео

```
POST /jobs  →  FastAPI  →  celery_app.send_task("process_video")
                                        │
                                        ▼
                              [Celery Worker: process_video]
                                        │
                    ┌───────────────────▼───────────────────┐
                    │  ШАГ 1: СКАЧИВАНИЕ (2-20%)            │
                    │  VideoDownloader.download()            │
                    │  • yt-dlp скачивает лучшее качество   │
                    │  • Прогресс-коллбэк → Redis → UI      │
                    │  • Лимит по длительности              │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │  ШАГ 2: ТРАНСКРИПЦИЯ (22-44%)         │
                    │  AudioTranscriber.transcribe()         │
                    │  • Проверка кэша (MD5 url+lang)        │
                    │  • FFmpeg: нарезает на 10-мин чанки   │
                    │  • Чанки → Whisper API (OpenAI/Groq)  │
                    │  • Word-level timestamps               │
                    │  • Результат кэшируется               │
                    │  • Redis lock (только 1 транскрипция) │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │  ШАГ 3: AI АНАЛИЗ МОМЕНТОВ (46-55%)   │
                    │  MomentAnalyzer.analyze()              │
                    │  • Фильтрация: музыка, галлюцинации,   │
                    │    повторы, тишина                     │
                    │  • Формирование промпта → LLM          │
                    │    (OpenAI GPT-4o-mini / Gemini /      │
                    │     Ollama с чанкингом по 15 мин)      │
                    │  • snap_to_speech (привязка к речи)    │
                    │  • Проверка: речи ≥70% в клипе         │
                    │  • Сортировка по score, выбор топ-N   │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │  ШАГ 4-5: ПАРАЛЛЕЛЬНАЯ НАРЕЗКА (55-95%)│
                    │  asyncio.Semaphore(3) — макс 3 клипа  │
                    │                                        │
                    │  Для каждого момента:                  │
                    │  a) VideoCutter.cut_clip() — FFmpeg    │
                    │  b) Рефрейминг в 9:16:                 │
                    │     • center: crop по центру            │
                    │     • ai: SmartReframer               │
                    │       - talking head? → blur bg        │
                    │       - face detected? → split-screen  │
                    │       - else → smart YOLO crop track  │
                    │  c) CaptionRenderer.render_captions()  │
                    │     • ASS субтитры (6 стилей)          │
                    │     • Hook текст первые 3 сек          │
                    │     • Опц.: фоновая музыка             │
                    │  d) Upload в R2 или локально           │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │  ШАГ 6: ПУБЛИКАЦИЯ (95-100%, опц.)     │
                    │  YouTubePublisher / TikTokPublisher     │
                    └───────────────────┬───────────────────┘
                                        │
                                        ▼
                              status="done", shorts=[...]
                              сохранено в Redis на 24 часа
```

### Статусы задачи (JobStatus)

| Статус | Описание | Прогресс |
|---|---|---|
| `pending` | Создана, ждёт worker | 0% |
| `downloading` | Скачивание видео | 2–20% |
| `transcribing` | Транскрипция Whisper | 22–44% |
| `analyzing` | AI анализ моментов | 46–55% |
| `cutting` | Нарезка клипов | 55% |
| `rendering` | Рендеринг (нарезка + рефрейм + субтитры) | 55–95% |
| `publishing` | Загрузка на платформы | 95% |
| `done` | Завершено | 100% |
| `error` | Ошибка | 0% |

---

## 6. Описание всех сервисов backend

### `services/downloader.py` — VideoDownloader

Скачивает видео через yt-dlp. Поддерживает любые платформы (YouTube, TikTok, Twitch VOD, Vimeo и др.). Прогресс-коллбэк во время скачивания. Ограничение по длительности (`MAX_VIDEO_DURATION`). `get_video_info()` — только метаданные без скачивания.

### `services/transcriber.py` — AudioTranscriber

Транскрибирует аудио через API.

- **Провайдеры:** OpenAI Whisper-1 (по умолчанию) или Groq Whisper large-v3
- **Чанки:** разбивает видео на 10-минутные MP3 чанки (~5-8 МБ), т.к. API лимит 25 МБ
- **Кодек:** 16kHz моно, 64k bitrate (оптимально для Whisper)
- **Word-level timestamps:** включены всегда
- **Автоопределение языка:** из ответа Whisper (если `language=auto`)
- **Redis lock:** только одна транскрипция одновременно (защита от rate limit)
- **Retry:** 3 попытки при rate limit (429), потом fallback на OpenAI
- **Кэш:** JSON файл по MD5(url+lang) в `storage/cache/`

### `services/analyzer.py` — MomentAnalyzer

Определяет «лучшие моменты» для шортсов через LLM.

**Провайдеры** (`ANALYZER_PROVIDER`): `openai` (GPT-4o-mini), `gemini` (gemini-2.5-flash), `ollama` (любая локальная LLM).

**Алгоритм фильтрации транскрипта:**
1. Удаление сегментов с `no_speech_prob > 0.5`
2. Паттерн-матчинг: `[music]`, `♪`, пустые строки
3. Детекция повторов (галлюцинации Whisper на музыке)
4. Детекция музыкальных блоков (кластеры коротких сегментов)
5. Детекция текстов песен

**Chunking для Ollama:** длинные транскрипты разбиваются на чанки по 15 мин, каждый анализируется отдельно. Fallback на OpenAI GPT-4o-mini если Ollama вернул не-JSON.

**Постобработка результата LLM:**
- `snap_to_speech` — привязка границ к реальным сегментам речи
- Проверка: плотность речи ≥70%
- Проверка: музыки в исходнике ≤30%
- Слишком короткие — расширяются, слишком длинные — обрезаются

### `services/cutter.py` — VideoCutter

Нарезка и конвертация видео через FFmpeg.

| Метод | Описание |
|---|---|
| `cut_clip()` | Вырезает клип: libx264, crf=23, fast preset |
| `convert_to_vertical()` | Центральный кроп 9:16 → scale 1080×1920 |
| `convert_to_vertical_fit()` | Talking head: размытый фон (gblur=30) + видео поверх |
| `convert_to_vertical_split()` | Split-screen: контент (60%) + лицо снизу (40%) |
| `convert_to_vertical_smart()` | Умный кроп с плавным следованием за субъектом |

### `services/reframer.py` — SmartReframer

Умный рефрейминг горизонтальных видео в вертикальные (YOLOv8n + MediaPipe).

**Логика выбора (при `reframe_mode=ai`):**
1. Видео уже вертикальное → просто scale
2. `is_talking_head()` — лицо занимает >3% площади кадра → blur-bg режим
3. `detect_face_region()` — найдено лицо → **split-screen** (контент + лицо)
4. Иначе → `compute_crop_trajectory()` → плавное следование через EMA (α=0.15)

### `services/caption_renderer.py` — CaptionRenderer

Генерирует ASS субтитры и накладывает на видео.

**Стили субтитров:**

| Стиль | Описание |
|---|---|
| `default` | Белый текст, fs=52, border 3px |
| `highlight` | Белый текст, fs=56, полупрозрачный бокс |
| `minimal` | Белый текст, fs=44, минимальный бордер |
| `karaoke` | 2-3 слова, текущее — жёлтым (fs=72) |
| `glow` | Karaoke, выделение розовым (fs=64) |
| `bold` | Karaoke, выделение синим (fs=80) |

**Karaoke-режим:** группирует слова по 2-3, текущее выделяется цветом через ASS теги `\c`.

**Hook текст:** первые 3 секунды клипа — цепляющая фраза от AI (CAPS, жёлтый, верх экрана, fade).

**Музыка:** FFmpeg микширует — голос 100%, музыка 15%. Автоподбор по `moment.mood`.

### `services/publisher.py`

- **YouTubePublisher:** OAuth2 + YouTube Data API v3, resumable upload
- **TikTokPublisher:** TikTok Content Posting API v2 (требует одобрения приложения)

### `services/storage.py`

Абстракция: локальный диск или Cloudflare R2 (S3-совместимый). Автоматически выбирается по наличию `R2_ACCESS_KEY_ID`.

---

## 7. API эндпоинты

Базовый URL в prod: `http://localhost/api/`
В разработке: `http://localhost:8000/`

### Основные

| Метод | URL | Описание |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/video-info?url=...` | Метаданные видео |
| GET | `/download-video?url=...` | Скачать видео (FileResponse MP4) |

### Задачи (Jobs)

| Метод | URL | Описание |
|---|---|---|
| POST | `/jobs` | Создать задачу обработки |
| GET | `/jobs` | Список всех задач пользователя |
| GET | `/jobs/{job_id}` | Статус задачи |
| DELETE | `/jobs/{job_id}` | Удалить задачу и файлы |
| GET | `/jobs/{job_id}/zip` | Скачать все шортсы архивом |

**POST /jobs body:**
```json
{
  "url": "https://youtube.com/watch?v=...",
  "language": "auto",
  "max_shorts": 5,
  "min_duration": 15,
  "max_duration": 60,
  "caption_style": "karaoke",
  "reframe_mode": "center",
  "add_music": "none",
  "srt_timecodes": null,
  "publish_targets": null
}
```

**GET /jobs/{id} response:**
```json
{
  "job_id": "uuid",
  "status": "rendering",
  "message": "Готово 2/3 шортсов",
  "progress": 82,
  "steps": [
    {"id": "download", "label": "Скачивание видео", "status": "done"},
    {"id": "transcribe", "label": "Транскрипция аудио", "status": "done"},
    {"id": "analyze", "label": "AI-анализ моментов", "status": "done"},
    {"id": "cut", "label": "Нарезка шортсов", "status": "done"},
    {"id": "reframe", "label": "AI рефрейминг", "status": "done"},
    {"id": "render", "label": "Рендеринг субтитров", "status": "active", "detail": "2/3"},
    {"id": "publish", "label": "Публикация", "status": "pending"}
  ],
  "shorts": null
}
```

### Batch

| Метод | URL | Описание |
|---|---|---|
| POST | `/batch` | Создать пакет (до 50 URL) |
| GET | `/batch/{batch_id}` | Статус пакета |

### Авторизация

| Метод | URL | Описание |
|---|---|---|
| POST | `/auth/register` | Регистрация (username ≥3, password ≥6) |
| POST | `/auth/login` | Вход (form-data), возвращает JWT |
| GET | `/auth/me` | Данные текущего пользователя |
| GET | `/auth/youtube/connect` | Redirect на Google OAuth |
| GET | `/auth/youtube/callback` | OAuth callback |
| DELETE | `/auth/youtube/disconnect` | Отключить YouTube |
| GET | `/auth/tiktok/connect` | Redirect на TikTok OAuth |
| GET | `/auth/tiktok/callback` | TikTok callback |
| DELETE | `/auth/tiktok/disconnect` | Отключить TikTok |
| GET | `/auth/connections` | Статус подключений платформ |

---

## 8. Система авторизации

- **JWT токены** (HS256, срок: `JWT_EXPIRE_MINUTES`, по умолчанию 1440 мин = 24 ч)
- **Пользователи хранятся в Redis:** ключ `user:{username}` → JSON с bcrypt-паролем
- **Гостевой режим:** нет токена → `get_current_user()` возвращает `"guest"`, доступ не блокируется
- **Изоляция задач:** `job:{job_id}:owner` → username, другие пользователи получают 403

---

## 9. Публикация на платформы

### YouTube

1. Зарегистрировать приложение в Google Cloud Console
2. Включить YouTube Data API v3
3. Создать OAuth 2.0 credentials (тип: Web application)
4. Добавить redirect URI: `{APP_BASE_URL}/api/auth/youtube/callback`
5. Заполнить `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` в `.env`

### TikTok

1. Зарегистрировать приложение на developers.tiktok.com
2. Запросить `video.publish` и `video.upload` scopes
3. Заполнить `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`

### Шифрование OAuth токенов

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Результат → `OAUTH_ENCRYPTION_KEY` в `.env`.

---

## 10. Frontend

Одностраничное приложение (SPA) на чистом HTML/CSS/JS.

### Функциональность

- **Форма ввода:** textarea с автоизменением высоты, поддержка нескольких URL (каждый на новой строке)
- **Preview:** автоматическое получение thumbnail через `/video-info` (debounce 800ms)
- **Настройки:** язык, кол-во шортсов, стиль субтитров, рефрейминг, музыка, публикация
- **SRT загрузка:** .srt файл → парсинг таймкодов → передача в API вместо AI анализа
- **Прогресс:** polling каждые 1.5 сек (single) / 2 сек (batch), ETA, step-индикатор
- **Batch режим:** пакетная обработка до 50 видео
- **История:** список завершённых задач с ZIP скачиванием
- **Авторизация:** модал логин/регистрация, JWT в localStorage, гостевой режим
- **Скачивание:** ZIP архив всех шортсов, прямые ссылки MP4

### API_BASE в разработке

В `frontend/app.js` строка 1 — изменить для локальной разработки:
```javascript
// Для разработки (без Nginx):
const API_BASE = "http://localhost:8000";

// Для prod (через Nginx — значение по умолчанию):
const API_BASE = "/api";
```

---

## 11. Переменные окружения (.env)

```ini
# === ОБЯЗАТЕЛЬНЫЕ ===
OPENAI_API_KEY=sk-...           # OpenAI API ключ (Whisper + GPT-4o-mini)
JWT_SECRET=your-secret-key      # ОБЯЗАТЕЛЬНО сменить в prod!

# === ИНФРАСТРУКТУРА ===
REDIS_URL=redis://localhost:6379/0   # В Docker: redis://redis:6379/0
STORAGE_PATH=C:/Projects/StreamCut/storage  # Путь к хранилищу
APP_BASE_URL=http://localhost         # Для OAuth redirect

# === ТРАНСКРИПЦИЯ ===
TRANSCRIPTION_PROVIDER=openai  # openai или groq
TRANSCRIPTION_LANGUAGE=auto    # auto, ru, en, ...
GROQ_API_KEY=                   # Если TRANSCRIPTION_PROVIDER=groq

# === AI АНАЛИЗ ===
ANALYZER_PROVIDER=openai        # openai, gemini, или ollama
GEMINI_API_KEY=                 # Если ANALYZER_PROVIDER=gemini
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_MODEL=gemma4

# === ПАРАМЕТРЫ ШОРТСОВ ===
MAX_VIDEO_DURATION=3600         # Макс. длина входного видео (сек)
MAX_SHORTS=7                    # Макс. шортсов с одного видео
MIN_SHORT_DURATION=15           # Мин. длина шортса (сек)
MAX_SHORT_DURATION=60           # Макс. длина шортса (сек)

# === JWT ===
JWT_EXPIRE_MINUTES=1440         # Срок жизни токена (мин)

# === ПУБЛИКАЦИЯ (опционально) ===
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
OAUTH_ENCRYPTION_KEY=           # Fernet ключ (обязателен при публикации)

# === CLOUDFLARE R2 (опционально) ===
R2_ENDPOINT=https://xxx.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=streamcut
R2_PUBLIC_URL=https://cdn.yourdomain.com

# === АВТООЧИСТКА ===
CLEANUP_INTERVAL_HOURS=1        # Частота очистки (часов)
FILE_MAX_AGE_HOURS=24           # Возраст файлов для удаления (часов)
```

---

## 12. Docker и деплой

### Сервисы в docker-compose.yml

| Сервис | Образ | Порт | Описание |
|---|---|---|---|
| `redis` | redis:7-alpine | 6379 | Брокер + state store |
| `backend` | ./backend/Dockerfile | 8000 | FastAPI (uvicorn --reload) |
| `worker` | ./backend/Dockerfile | — | Celery worker + beat --concurrency=2 |
| `nginx` | nginx:alpine | 80 | Reverse proxy + статика |

### Запуск в Docker

```bash
cp .env.example .env
# Заполнить OPENAI_API_KEY и JWT_SECRET в .env

docker-compose up -d
# Открыть: http://localhost
```

### Dockerfile особенности

```dockerfile
FROM python:3.11-slim
# Устанавливает: ffmpeg, git, wget
# PyTorch CPU устанавливается отдельно (--index-url pytorch.org/whl/cpu)
# При сборке скачивает YOLOv8n.pt
```

### Nginx роутинг

- `/` → `frontend/index.html`
- `/api/*` → rewrite убирает `/api` → proxy `http://backend:8000`
- `/storage/*` → `storage/processed/` (прямые ссылки на MP4)
- `proxy_read_timeout 300s` — длинные задачи не прерываются

---

## 13. Локальная разработка на Windows

### Предварительные требования

**1. Python 3.11**
```powershell
# Проверить версию:
python --version
```

**2. FFmpeg** (должен быть в PATH):
```powershell
winget install Gyan.FFmpeg
# Перезапустить терминал, проверить:
ffmpeg -version
ffprobe -version
```

**3. Redis** — выбрать один вариант:
```powershell
# Вариант A: Docker (рекомендуется)
docker run -d -p 6379:6379 --name redis redis:7-alpine

# Вариант B: Memurai (Windows native, без WSL)
# Скачать с memurai.com

# Вариант C: WSL2
wsl -d Ubuntu -- redis-server --daemonize yes
```

### Шаг 1: Установка Python зависимостей

```powershell
cd C:\Projects\StreamCut\backend

# Создать виртуальное окружение
python -m venv .venv
.\.venv\Scripts\activate

# PyTorch CPU (большой, устанавливаем отдельно)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# Остальные зависимости
pip install -r requirements.txt
```

### Шаг 2: Настройка .env

Создать `C:\Projects\StreamCut\.env` (скопировать из `.env.example`):

```ini
OPENAI_API_KEY=sk-...
REDIS_URL=redis://localhost:6379/0
STORAGE_PATH=C:/Projects/StreamCut/storage
JWT_SECRET=dev-secret-123
APP_BASE_URL=http://localhost:3000
```

> **Важно для Windows:** в STORAGE_PATH использовать прямые слэши `/`, не обратные `\`.

### Шаг 3: Изменить API_BASE в frontend

В `frontend/app.js` строка 1:
```javascript
const API_BASE = "http://localhost:8000";
```

### Шаг 4: Запуск (4 терминала)

**Терминал 1: Redis** (если не запущен через Docker)
```powershell
docker start redis
# или memurai запускается как служба Windows
```

**Терминал 2: Celery Worker**
```powershell
cd C:\Projects\StreamCut\backend
.\.venv\Scripts\activate
celery -A worker worker --loglevel=info --concurrency=1
```

> На Windows Celery может иметь проблемы с asyncio. Если есть ошибки — добавить `--pool=solo`:
> ```powershell
> celery -A worker worker --loglevel=info --pool=solo
> ```

**Терминал 3: FastAPI Backend**
```powershell
cd C:\Projects\StreamCut\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000
# API: http://localhost:8000
# Swagger: http://localhost:8000/docs
```

**Терминал 4: Frontend**
```powershell
cd C:\Projects\StreamCut\frontend
python -m http.server 3000
# UI: http://localhost:3000
```

### Альтернатива: Docker для инфраструктуры + локальный backend

```powershell
# Запустить только Redis (без worker и nginx)
docker run -d -p 6379:6379 --name redis redis:7-alpine

# Backend локально (быстрая итерация)
cd C:\Projects\StreamCut\backend
.\.venv\Scripts\activate
uvicorn main:app --reload --port 8000

# Worker локально (в отдельном терминале)
celery -A worker worker --loglevel=info --concurrency=1
```

### Папки хранилища

Создаются автоматически при старте `config.py`. Если нужно вручную:
```powershell
New-Item -ItemType Directory -Force -Path C:\Projects\StreamCut\storage\downloads
New-Item -ItemType Directory -Force -Path C:\Projects\StreamCut\storage\processed
New-Item -ItemType Directory -Force -Path C:\Projects\StreamCut\storage\temp
New-Item -ItemType Directory -Force -Path C:\Projects\StreamCut\storage\cache
```

---

## 14. Тестирование

### Проверка здоровья сервисов

```powershell
# Health check
curl http://localhost:8000/health

# Swagger UI
Start-Process "http://localhost:8000/docs"
```

### Базовые API запросы

```powershell
# Video info (без скачивания)
curl "http://localhost:8000/video-info?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"

# Регистрация пользователя
curl -X POST http://localhost:8000/auth/register `
  -H "Content-Type: application/json" `
  -d '{"username":"testuser","password":"password123"}'

# Логин (получить токен)
curl -X POST http://localhost:8000/auth/login `
  -d "username=testuser&password=password123"

# Создание задачи (подставить токен из предыдущего ответа)
curl -X POST http://localhost:8000/jobs `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer {YOUR_TOKEN}" `
  -d '{"url":"https://youtube.com/watch?v=...", "max_shorts":2, "caption_style":"karaoke"}'

# Проверка статуса задачи
curl http://localhost:8000/jobs/{job_id} `
  -H "Authorization: Bearer {YOUR_TOKEN}"
```

### Проверка Celery

```powershell
cd C:\Projects\StreamCut\backend
.\.venv\Scripts\activate

# Активные задачи
celery -A worker inspect active

# Статистика
celery -A worker inspect stats
```

### Тестирование с коротким видео

Для ускорения: используй видео 1-3 мин с явной речью. Ожидаемое время обработки:
- Скачивание: 5-30 сек (зависит от качества)
- Транскрипция: 30-90 сек (OpenAI Whisper)
- AI анализ: 5-15 сек (GPT-4o-mini)
- Нарезка + рендеринг: 30-90 сек на клип

---

## 15. Известные особенности и подводные камни

### Windows-специфичные

1. **Celery + asyncio на Windows** — Celery использует asyncio внутри worker через `asyncio.new_event_loop()`. На Windows может быть нестабильным с `--concurrency > 1`. Рекомендуется `--concurrency=1` или `--pool=solo`.

2. **Пути в .env:** только прямые слэши: `C:/Projects/StreamCut/storage`, не `C:\...`

3. **FFmpeg в PATH:** после установки через winget перезапустить терминал.

4. **Mediapipe на Windows:** иногда требует `pip install mediapipe==0.10.14` явно (не последнюю версию).

### Транскрипция

- Без `GROQ_API_KEY` используется OpenAI Whisper-1 (~$0.006/мин). Groq быстрее и в 3-5 раз дешевле.
- Кэш сохраняется в `storage/cache/`. При изменении `language` — другой кэш.
- Redis lock: если предыдущая транскрипция зависла, lock может не освободиться. Удалить: `redis-cli del lock:transcribe`.

### AI Анализ

- GPT-4o-mini иногда возвращает таймкоды за пределами видео — фильтруется автоматически.
- Для Ollama: модель должна поддерживать `response_format: json_object` (llama3.1, gemma4, mistral-nemo).
- Очень длинное видео (>2 ч) → транскрипт может превысить лимит токенов GPT-4o-mini. Решение: используется `segments[::2]` (каждый второй сегмент).

### Субтитры

- Karaoke/glow/bold стили требуют word-level timestamps от Whisper. Groq поддерживает частично.
- Шрифт `Montserrat ExtraBold` должен быть в `backend/fonts/`. Без него ASS отрисуется системным шрифтом.
- На Windows при локальном запуске пути в ASS фильтре должны использовать `/` (FFmpeg понимает оба, но лучше явно).

### Музыка

- Файлы `backend/music/upbeat.mp3`, `calm.mp3`, `motivation.mp3` — нужно добавить самостоятельно (не включены в репозиторий по правам).
- В Docker пути абсолютные: `/app/music/`. При локальной разработке — нужно изменить пути в `caption_renderer.py` или создать символическую ссылку.

### Celery Beat (автоочистка)

- Файл `celerybeat-schedule` хранит расписание. При изменении `CLEANUP_INTERVAL_HOURS` удалить этот файл.
- В Docker worker запускается с `--beat`. В разработке можно добавить `--beat` к celery worker или запустить отдельно: `celery -A worker beat`.

### Cloudflare R2

- Если `R2_ACCESS_KEY_ID` пустой — используется локальный диск автоматически.
- Нужен `boto3`: `pip install boto3` (не в основном requirements.txt).

### Публикация

- **YouTube:** требует одобрения OAuth consent screen в Google Cloud для публичного использования. В тестовом режиме работает только для добавленных Test Users.
- **TikTok:** `video.publish` scope требует рассмотрения заявки TikTok (может занять недели).

---

*Документация создана 09.04.2026. Актуальна для текущего состояния кодовой базы.*
