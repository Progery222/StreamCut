import re
import json
import hashlib
import logging
import asyncio
from pathlib import Path
from celery import Celery
from celery.schedules import crontab
from config import settings
from services.downloader import VideoDownloader
from services.transcriber import AudioTranscriber
from services.analyzer import MomentAnalyzer
from services.cutter import VideoCutter
from services.caption_renderer import CaptionRenderer
from services.reframer import SmartReframer
import redis as redis_lib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

celery_app = Celery(
    "videoshorts",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=86400,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "cleanup-old-files": {
            "task": "cleanup_files",
            "schedule": crontab(minute=0, hour=f"*/{settings.cleanup_interval_hours}"),
        },
    },
)

_redis = redis_lib.from_url(settings.redis_url)


@celery_app.task(name="cleanup_files")
def cleanup_files():
    from utils.helpers import cleanup_old_files
    max_age = settings.file_max_age_hours

    for d in [settings.processed_path, settings.downloads_path, settings.temp_path, settings.cache_path]:
        if d.exists():
            cleanup_old_files(d, max_age)

    for key in _redis.scan_iter("job:*:state"):
        job_id = key.decode().split(":")[1]
        job_dir = settings.processed_path / job_id
        if not job_dir.exists():
            _redis.delete(key)
            _redis.delete(f"job:{job_id}:owner")

    logger.info(f"Cleanup completed: max_age={max_age}h")


STEPS = [
    {"id": "download", "label": "Скачивание видео"},
    {"id": "transcribe", "label": "Транскрипция аудио"},
    {"id": "analyze", "label": "AI-анализ моментов"},
    {"id": "cut", "label": "Нарезка шортсов"},
    {"id": "reframe", "label": "AI рефрейминг"},
    {"id": "render", "label": "Рендеринг субтитров"},
    {"id": "publish", "label": "Публикация"},
]


def _build_steps(active_id: str, detail: str = None, done_ids: list = None) -> list:
    done_ids = done_ids or []
    result = []
    for s in STEPS:
        if s["id"] in done_ids:
            result.append({**s, "status": "done"})
        elif s["id"] == active_id:
            result.append({**s, "status": "active", "detail": detail})
        else:
            result.append({**s, "status": "pending"})
    return result


def update_job_state(job_id: str, status: str, progress: int, message: str, **kwargs):
    data = {
        "status": status,
        "progress": progress,
        "message": message,
        **kwargs,
    }
    _redis.setex(f"job:{job_id}:state", 86400, json.dumps(data))


@celery_app.task(bind=True, name="process_video")
def process_video(self, job_id: str, url: str, options: dict):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            _process_video_async(job_id, url, options)
        )
        return result
    finally:
        loop.close()


async def _process_video_async(job_id: str, url: str, options: dict):
    downloader = VideoDownloader(settings.downloads_path)
    transcriber = AudioTranscriber(settings.whisper_model)
    analyzer = MomentAnalyzer()
    cutter = VideoCutter(settings.temp_path, settings.processed_path)
    renderer = CaptionRenderer()
    reframe_mode = options.get("reframe_mode", "center")
    reframer = SmartReframer() if reframe_mode == "ai" else None

    done_steps = []

    try:
        # === ШАГ 1: Скачивание ===
        def on_download_progress(percent):
            mapped = 2 + int(percent * 0.18)  # 2-20%
            update_job_state(
                job_id, "downloading", mapped,
                f"Скачивание видео... {percent}%",
                steps=_build_steps("download", f"{percent}%", done_steps),
            )

        update_job_state(
            job_id, "downloading", 2, "Скачивание видео...",
            steps=_build_steps("download", "Подготовка...", done_steps),
        )
        logger.info(f"[{job_id}] Скачивание: {url}")

        video_path = await downloader.download(
            url=url,
            job_id=job_id,
            max_duration=settings.max_video_duration,
            progress_callback=on_download_progress,
        )
        done_steps.append("download")
        logger.info(f"[{job_id}] Видео скачано: {video_path}")

        # === ШАГ 2: Транскрипция (с кэшем) ===
        language = options.get("language", "auto")
        cache_key = hashlib.md5(f"{url}:{language}".encode()).hexdigest()
        cache_file = settings.cache_path / f"{cache_key}.json"

        segments = None
        if cache_file.exists():
            try:
                from models.schemas import TranscriptSegment
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**s) for s in cached]
                logger.info(f"[{job_id}] Транскрипция из кэша: {len(segments)} сегментов")
                update_job_state(
                    job_id, "transcribing", 44, f"Транскрипция из кэша: {len(segments)} сегментов",
                    steps=_build_steps("transcribe", "Из кэша ✓", done_steps),
                )
            except Exception as e:
                logger.warning(f"[{job_id}] Ошибка чтения кэша: {e}")
                segments = None

        if segments is None:
            update_job_state(
                job_id, "transcribing", 22, "Транскрипция аудио...",
                steps=_build_steps("transcribe", "Whisper обрабатывает...", done_steps),
            )
            logger.info(f"[{job_id}] Транскрипция...")
            segments = await transcriber.transcribe(video_path, language)

            try:
                cache_file.write_text(
                    json.dumps([s.model_dump() for s in segments], ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info(f"[{job_id}] Транскрипция сохранена в кэш")
            except Exception as e:
                logger.warning(f"[{job_id}] Не удалось сохранить кэш: {e}")

        done_steps.append("transcribe")
        logger.info(f"[{job_id}] Транскрипция: {len(segments)} сегментов")

        if not segments:
            raise ValueError("Транскрипция не дала результатов. Возможно, видео без речи.")

        update_job_state(
            job_id, "transcribing", 44, f"Транскрипция завершена: {len(segments)} сегментов",
            steps=_build_steps("transcribe", f"{len(segments)} сегментов", done_steps),
        )

        # === ШАГ 3: Анализ моментов ===
        srt_timecodes = options.get("srt_timecodes")
        if srt_timecodes:
            # Ручные таймкоды из SRT — пропускаем AI анализ
            from models.schemas import VideoMoment
            moments = []
            for i, tc in enumerate(srt_timecodes):
                moments.append(VideoMoment(
                    start=float(tc["start"]),
                    end=float(tc["end"]),
                    title=tc.get("title", f"Клип {i+1}"),
                    description="",
                    score=10,
                ))
            update_job_state(
                job_id, "analyzing", 55, f"Использованы SRT таймкоды: {len(moments)} клипов",
                steps=_build_steps("analyze", f"{len(moments)} из SRT", done_steps),
            )
            logger.info(f"[{job_id}] SRT таймкоды: {len(moments)} клипов")
        else:
            update_job_state(
                job_id, "analyzing", 46, "AI анализирует лучшие моменты...",
                steps=_build_steps("analyze", "GPT-4o-mini думает...", done_steps),
            )
            logger.info(f"[{job_id}] Анализ моментов...")

            moments = await analyzer.analyze(
                segments=segments,
                max_moments=options.get("max_shorts", settings.max_shorts),
                min_duration=options.get("min_duration", settings.min_short_duration),
                max_duration=options.get("max_duration", settings.max_short_duration),
                video_path=video_path,
            )
        done_steps.append("analyze")

        if not moments:
            raise ValueError("Не удалось выделить интересные моменты из видео.")

        logger.info(f"[{job_id}] Выбрано {len(moments)} моментов")

        # === ШАГ 4-5: Параллельная нарезка и рендеринг ===
        job_output_dir = settings.processed_path / job_id
        job_output_dir.mkdir(exist_ok=True)

        total_moments = len(moments)
        caption_style = options.get("caption_style", "default")
        add_music = options.get("add_music", "none")
        completed_count = [0]

        async def process_clip(i, moment):
            logger.info(f"[{job_id}] Шортс {i+1}: {moment.start:.1f}s - {moment.end:.1f}s")

            clip_path = settings.temp_path / f"{job_id}_clip_{i}.mp4"
            await cutter.cut_clip(video_path, moment.start, moment.end, clip_path)

            vertical_path = settings.temp_path / f"{job_id}_vertical_{i}.mp4"

            if reframer:
                clip_w, clip_h = await cutter._get_video_dimensions(clip_path)
                if clip_h < clip_w:
                    loop = asyncio.get_event_loop()
                    is_th = await loop.run_in_executor(
                        None, reframer.is_talking_head, clip_path
                    )
                    if is_th:
                        logger.info(f"[{job_id}] Clip {i+1}: talking head")
                        await cutter.convert_to_vertical_fit(clip_path, vertical_path)
                    else:
                        face_box = await loop.run_in_executor(
                            None, reframer.detect_face_region, clip_path
                        )
                        if face_box:
                            logger.info(f"[{job_id}] Clip {i+1}: split-screen")
                            await cutter.convert_to_vertical_split(
                                clip_path, vertical_path, face_box, clip_w, clip_h
                            )
                        else:
                            logger.info(f"[{job_id}] Clip {i+1}: smart crop")
                            keyframes = await reframer.compute_crop_trajectory(clip_path, clip_w, clip_h)
                            crop_filter = reframer.generate_crop_filter(keyframes, clip_w, clip_h)
                            await cutter.convert_to_vertical_smart(clip_path, vertical_path, crop_filter)
                else:
                    await cutter.convert_to_vertical(clip_path, vertical_path)
            else:
                await cutter.convert_to_vertical(clip_path, vertical_path)

            output_filename = f"short_{i+1}_{_safe_filename(moment.title)}.mp4"
            final_path = job_output_dir / output_filename

            clip_segments = [
                s for s in segments
                if s.end > moment.start - 0.1 and s.start < moment.end + 0.1
            ]

            # Авто-подбор музыки по настроению клипа
            clip_music = add_music
            if add_music == "auto" and moment.mood:
                clip_music = moment.mood

            await renderer.render_captions(
                video_path=vertical_path,
                segments=clip_segments,
                output_path=final_path,
                style=caption_style,
                video_start=moment.start,
                add_music=clip_music,
                hook_text=moment.hook,
            )

            clip_path.unlink(missing_ok=True)
            vertical_path.unlink(missing_ok=True)

            completed_count[0] += 1
            pct = 55 + int((completed_count[0] / total_moments) * 40)
            update_job_state(
                job_id, "rendering", pct,
                f"Готово {completed_count[0]}/{total_moments} шортсов",
                steps=_build_steps("render", f"{completed_count[0]}/{total_moments}", done_steps + ["cut", "reframe"]),
            )

            file_size = final_path.stat().st_size if final_path.exists() else 0

            # Upload to R2 if configured
            from services.storage import storage
            storage_key = f"{job_id}/{output_filename}"
            video_url = storage.upload(final_path, storage_key) if storage.use_r2 else f"/storage/{storage_key}"

            return {
                "index": i + 1,
                "title": moment.title,
                "description": moment.description,
                "score": moment.score,
                "start": moment.start,
                "end": moment.end,
                "duration": round(moment.end - moment.start, 1),
                "filename": output_filename,
                "url": video_url,
                "file_size": file_size,
            }

        update_job_state(
            job_id, "cutting", 55, f"Обработка {total_moments} клипов параллельно...",
            steps=_build_steps("cut", f"0/{total_moments}", done_steps),
        )

        # Параллельная обработка (до 3 одновременно)
        semaphore = asyncio.Semaphore(3)

        async def limited_process(i, moment):
            async with semaphore:
                return await process_clip(i, moment)

        results = await asyncio.gather(
            *[limited_process(i, m) for i, m in enumerate(moments)]
        )
        shorts = list(results)
        shorts.sort(key=lambda x: x["index"])

        done_steps.extend(["cut", "reframe", "render"])
        video_path.unlink(missing_ok=True)

        # === ШАГ 6: Публикация (если запрошена) ===
        publish_targets = options.get("publish_targets") or []
        if publish_targets:
            username = options.get("username", "")
            update_job_state(
                job_id, "publishing", 95, "Публикация шортсов...",
                steps=_build_steps("publish", "Загрузка...", done_steps),
                shorts=shorts,
            )

            from services.publisher import YouTubePublisher, TikTokPublisher
            from services.token_encryption import decrypt_tokens

            publishers = {}
            for target in publish_targets:
                raw = _redis.get(f"oauth:{username}:{target}")
                if not raw:
                    logger.warning(f"[{job_id}] Нет токена для {target}, пропуск")
                    continue
                token_data = decrypt_tokens(raw.decode())
                if target == "youtube":
                    publishers["youtube"] = (YouTubePublisher(), token_data)
                elif target == "tiktok":
                    publishers["tiktok"] = (TikTokPublisher(), token_data)

            for i, short in enumerate(shorts):
                final_path = job_output_dir / short["filename"]
                published = {}
                for target, (pub, tokens) in publishers.items():
                    try:
                        pub_url = await pub.upload(
                            tokens, final_path,
                            title=short["title"],
                            description=short.get("description", ""),
                        )
                        published[target] = pub_url
                        logger.info(f"[{job_id}] Опубликован {target}: {pub_url}")
                    except Exception as pub_err:
                        logger.error(f"[{job_id}] Ошибка публикации {target}: {pub_err}")
                        published[target] = f"error: {pub_err}"

                shorts[i]["published"] = published

            done_steps.append("publish")

        update_job_state(
            job_id, "done", 100, f"Готово! Создано {len(shorts)} шортсов",
            steps=_build_steps("done", None, done_steps),
            shorts=shorts,
        )
        logger.info(f"[{job_id}] Готово! {len(shorts)} шортсов")

        return {"status": "done", "shorts": shorts}

    except Exception as e:
        logger.error(f"[{job_id}] Ошибка: {e}", exc_info=True)
        update_job_state(
            job_id, "error", 0, f"Ошибка: {str(e)}",
            error=str(e),
            steps=_build_steps("error", str(e), done_steps),
        )
        raise


def _safe_filename(text: str) -> str:
    safe = re.sub(r'[^\w\s-]', '', text.lower())
    safe = re.sub(r'[-\s]+', '-', safe).strip('-')
    return safe[:50] or "short"
