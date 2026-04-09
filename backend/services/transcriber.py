import asyncio
import logging
import math
from pathlib import Path
from typing import List
from openai import AsyncOpenAI
from models.schemas import TranscriptSegment, WordTimestamp
from config import settings
import redis as redis_lib

logger = logging.getLogger(__name__)

# Глобальный lock — только одна транскрипция за раз (Groq rate limit)
_redis = redis_lib.from_url(settings.redis_url)
TRANSCRIBE_LOCK_KEY = "lock:transcribe"
TRANSCRIBE_LOCK_TTL = 600  # 10 мин макс

# OpenAI Whisper API: макс 25 МБ на файл
# Нарезаем аудио на 10-минутные чанки (~5-8 МБ каждый)
CHUNK_DURATION_SEC = 600  # 10 минут (~5-8 МБ, лимит Groq/OpenAI 25 МБ)


class AudioTranscriber:
    def __init__(self, model_size: str = "medium"):
        self.provider = settings.transcription_provider
        if self.provider == "groq" and settings.groq_api_key:
            self.client = AsyncOpenAI(
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            self.model_name = "whisper-large-v3"
            logger.info("Транскрипция: Groq Whisper large-v3")
        else:
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
            self.model_name = "whisper-1"
            logger.info("Транскрипция: OpenAI Whisper")

        # Fallback на OpenAI если Groq rate limit
        self._has_openai_fallback = (
            self.provider == "groq" and settings.openai_api_key
        )
        if self._has_openai_fallback:
            self._fallback_client = AsyncOpenAI(api_key=settings.openai_api_key)
            self._fallback_model = "whisper-1"

    async def _get_duration(self, path: Path) -> float:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())

    async def _extract_audio_chunk(
        self, video_path: Path, output_path: Path,
        start: float, duration: float,
    ) -> Path:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(duration),
            "-vn",
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "libmp3lame",
            "-b:a", "64k",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg ошибка: {stderr.decode()}")
        return output_path

    async def _call_whisper(self, chunk_path: Path, lang: str = None):
        """Вызов Whisper API с retry и fallback на OpenAI при rate limit."""
        # Попытки через основной провайдер (Groq)
        for attempt in range(3):
            with open(chunk_path, "rb") as f:
                kwargs = {
                    "model": self.model_name, "file": f,
                    "response_format": "verbose_json",
                    "timestamp_granularities": ["segment", "word"],
                }
                if lang:
                    kwargs["language"] = lang
                try:
                    return await self.client.audio.transcriptions.create(**kwargs)
                except Exception as e:
                    if "429" in str(e) or "rate" in str(e).lower():
                        wait = 15 * (attempt + 1)
                        logger.warning(f"Rate limit ({self.provider}), retry {attempt+1}/3 через {wait}с")
                        await asyncio.sleep(wait)
                    else:
                        raise

        # Fallback на OpenAI
        if self._has_openai_fallback:
            logger.warning(f"Groq rate limit исчерпан, fallback на OpenAI Whisper")
            with open(chunk_path, "rb") as f:
                kwargs = {
                    "model": self._fallback_model, "file": f,
                    "response_format": "verbose_json",
                    "timestamp_granularities": ["segment", "word"],
                }
                if lang:
                    kwargs["language"] = lang
                return await self._fallback_client.audio.transcriptions.create(**kwargs)

        raise RuntimeError("Whisper API: rate limit, fallback недоступен")

    async def _acquire_lock(self, job_id: str = ""):
        """Ждём пока другая транскрипция не закончится."""
        while True:
            acquired = _redis.set(TRANSCRIBE_LOCK_KEY, job_id, nx=True, ex=TRANSCRIBE_LOCK_TTL)
            if acquired:
                logger.info(f"Transcribe lock acquired: {job_id}")
                return
            logger.info(f"Transcribe lock busy, waiting... ({job_id})")
            await asyncio.sleep(5)

    def _release_lock(self):
        _redis.delete(TRANSCRIBE_LOCK_KEY)
        logger.info("Transcribe lock released")

    async def transcribe(
        self,
        video_path: Path,
        language: str = "auto",
    ) -> List[TranscriptSegment]:
        await self._acquire_lock(str(video_path.stem))
        try:
            return await self._transcribe_inner(video_path, language)
        finally:
            self._release_lock()

    async def _transcribe_inner(
        self,
        video_path: Path,
        language: str = "auto",
    ) -> List[TranscriptSegment]:
        total_duration = await self._get_duration(video_path)
        num_chunks = math.ceil(total_duration / CHUNK_DURATION_SEC)

        logger.info(f"Транскрипция через OpenAI API: {total_duration:.0f}с, {num_chunks} чанков")

        all_segments = []
        lang = None if language == "auto" else language

        for i in range(num_chunks):
            chunk_start = i * CHUNK_DURATION_SEC
            chunk_dur = min(CHUNK_DURATION_SEC, total_duration - chunk_start)

            chunk_path = video_path.parent / f"{video_path.stem}_chunk_{i}.mp3"

            try:
                await self._extract_audio_chunk(
                    video_path, chunk_path, chunk_start, chunk_dur,
                )

                logger.info(f"Чанк {i+1}/{num_chunks}: {chunk_start:.0f}s-{chunk_start+chunk_dur:.0f}s")

                response = await self._call_whisper(chunk_path, lang)

                if lang is None and hasattr(response, "language"):
                    detected = response.language
                    # Whisper возвращает полное название, API требует ISO-639-1
                    lang_map = {
                        "english": "en", "russian": "ru", "spanish": "es",
                        "french": "fr", "german": "de", "italian": "it",
                        "portuguese": "pt", "chinese": "zh", "japanese": "ja",
                        "korean": "ko", "arabic": "ar", "hindi": "hi",
                        "turkish": "tr", "polish": "pl", "ukrainian": "uk",
                    }
                    lang = lang_map.get(detected, detected if len(detected) <= 3 else None)
                    logger.info(f"Определён язык: {detected} -> {lang}")

                def _get(obj, key):
                    return obj[key] if isinstance(obj, dict) else getattr(obj, key, None)

                raw_words = []
                if hasattr(response, "words") and response.words:
                    for w in response.words:
                        raw_words.append(WordTimestamp(
                            word=_get(w, "word").strip(),
                            start=round(_get(w, "start") + chunk_start, 2),
                            end=round(_get(w, "end") + chunk_start, 2),
                        ))

                for seg in response.segments:
                    seg_start = round(_get(seg, "start") + chunk_start, 2)
                    seg_end = round(_get(seg, "end") + chunk_start, 2)
                    seg_words = [
                        w for w in raw_words
                        if w.start >= seg_start - 0.05 and w.end <= seg_end + 0.05
                    ] or None
                    seg_text = _get(seg, "text") or ""
                    seg_nsp = _get(seg, "no_speech_prob")
                    all_segments.append(
                        TranscriptSegment(
                            start=seg_start,
                            end=seg_end,
                            text=seg_text.strip(),
                            words=seg_words,
                            no_speech_prob=seg_nsp,
                        )
                    )

            finally:
                chunk_path.unlink(missing_ok=True)

        logger.info(f"Транскрипция завершена: {len(all_segments)} сегментов")
        return all_segments

    def segments_to_text(self, segments: List[TranscriptSegment]) -> str:
        return " ".join(s.text for s in segments)

    def segments_to_srt(self, segments: List[TranscriptSegment], output_path: Path) -> Path:
        def format_time(seconds: float) -> str:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            s = int(seconds % 60)
            ms = int((seconds % 1) * 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                f.write(f"{i}\n")
                f.write(f"{format_time(seg.start)} --> {format_time(seg.end)}\n")
                f.write(f"{seg.text}\n\n")

        return output_path
