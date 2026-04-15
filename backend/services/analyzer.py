import base64
import json
import logging
import asyncio
import re
from pathlib import Path
from typing import List, Optional
from openai import AsyncOpenAI
from models.schemas import TranscriptSegment, VideoMoment
from config import settings

logger = logging.getLogger(__name__)

# Паттерны музыки/шума, которые Whisper часто выдаёт
_MUSIC_PATTERNS = re.compile(
    r"^\s*[\[(\{]?\s*("
    r"music|музыка|музыкальн|applause|аплодисменты|laughter|смех"
    r"|silence|тишина|noise|шум|instrumental|intro|outro"
    r")\s*[\])\}]?\s*$",
    re.IGNORECASE,
)
_MUSIC_SYMBOLS = re.compile(r"^[\s♪♫🎵🎶🎤\.\,\!\?\-–—…\*#]+$")

# Whisper галлюцинирует на музыке — повторяет одну фразу или выдаёт бред
_HALLUCINATION_PHRASES = {
    "thank you", "thanks for watching", "subscribe", "подпишись",
    "спасибо за просмотр", "ставьте лайки", "до свидания",
    "thank you for watching", "please subscribe", "like and subscribe",
    "благодарю за внимание", "продолжение следует",
}


SYSTEM_PROMPT = """Ты — опытный видеоредактор и специалист по вирусному контенту. Ты работаешь с подкастами, стримами и длинными видео.
Задача — найти самые ЯРКИЕ и ЦЕПЛЯЮЩИЕ моменты для коротких вертикальных видео (шортсов/reels).

Критерии ХОРОШЕГО шортса (должны совпадать минимум 2-3):
- Спорное мнение, неожиданный факт или провокационная мысль
- Эмоциональная история, смешной момент или драма
- Законченная мысль, понятная БЕЗ контекста основного видео
- Практический совет или инсайт, который хочется сохранить
- Яркая цитата, которой хочется поделиться

ОБЯЗАТЕЛЬНО ПРОПУСКАЙ — ЭТО НЕ ШОРТСЫ:
- Музыкальные вставки, заставки, интро/аутро, джинглы
- Тишину, фоновую музыку, шум, паузы
- Настройку оборудования, ожидание, тех. проблемы
- "Привет всем", "подписывайтесь", "ставьте лайки" — скучно
- Болтовню ни о чём, переходы между темами
- Первые 2-3 минуты стрима (обычно заставка + приветствие)

Если момент не заставляет остановить скролл — НЕ БЕРИ ЕГО. Лучше 2 огненных клипа, чем 5 средних.

КРИТИЧЕСКИ ВАЖНО:
- Анализируй ТЕКСТ ТРАНСКРИПТА — это твой главный источник.
- Имей в виду, что пользователь часто просит сгенерировать МНОГО клипов (например 50). Ты должен найти как можно больше моментов, не останавливайся после первых 5!
- Верни ТОЛЬКО валидный JSON объект {"moments": [...]}. Никакого текста до или после JSON."""

USER_PROMPT_TEMPLATE = """Вот транскрипт видео с временными метками (формат: [секунды] текст).
ВАЖНО: язык транскрипта — {detected_lang}. Все поля (title, hook, description) пиши СТРОГО на {detected_lang}.

{transcript}

Выбери МАКСИМАЛЬНО ВОЗМОЖНОЕ количество лучших моментов для шортсов (стремись сгенерировать ровно {max_moments} моментов, если длина транскрипта это позволяет!). Каждый момент должен быть от {min_dur} до {max_dur} секунд. Бери реально интересные, вирусные, цепляющие моменты. Не ленись и пройди по всему транскрипту от начала до конца, чтобы найти заявленное количество — {max_moments}. ОБЯЗАТЕЛЬНО делай шортсы РАЗНОЙ длины, подстраивайся под смысл разговора (например, один на 25 секунд, другой на 50). Не делай все клипы короткими!

Верни JSON объект с ключом "moments" — массив объектов:
[
  {{
    "start": <число секунд>,
    "end": <число секунд>,
    "title": "<короткий цепляющий заголовок>",
    "description": "<описание что происходит в клипе, 1-2 предложения>",
    "score": <оценка виральности от 1 до 10>,
    "hook": "<цепляющая фраза для первых 3 сек, максимум 8 слов — крючок>",
    "mood": "<настроение клипа: upbeat, calm или motivation>"
  }}
]

Требования:
1. start и end — точные числа секунд из транскрипта. Начинай с НАЧАЛА фразы, не с тишины
2. Клипы не должны перекрываться
3. Выбирай только завершённые мысли, не обрезай на полуслове
4. Сортируй по score (сначала лучшие). Ставь score 7+ только действительно цепляющим моментам
5. ЯЗЫК: title, hook и description пиши НА ТОМ ЖЕ ЯЗЫКЕ что и текст транскрипта. Если транскрипт на английском — ВСЁ на английском (title, hook, description — ALL IN ENGLISH). Если на русском — ВСЁ на русском. НЕ ПЕРЕВОДИ. Определи язык по первым строкам транскрипта
6. В клипе должна быть ПЛОТНАЯ речь — никаких длинных пауз или тишины внутри клипа

CRITICAL: The language of title, hook, description MUST match the transcript language. If transcript is in English — respond in English. If in Russian — respond in Russian."""


class MomentAnalyzer:
    def __init__(self):
        self.provider = settings.analyzer_provider
        if self.provider == "ollama":
            import httpx
            self.client = AsyncOpenAI(
                base_url=settings.ollama_base_url,
                api_key="ollama",
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
            self.ollama_model = settings.ollama_model
            logger.info(f"Анализатор: Ollama ({self.ollama_model})")
        elif self.provider == "gemini" and settings.gemini_api_key:
            self.client = None  # Gemini uses its own SDK
            logger.info("Анализатор: Gemini 2.0 Flash")
        else:
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
            logger.info("Анализатор: OpenAI GPT-4o-mini")

    @staticmethod
    def _is_music_segment(seg: TranscriptSegment) -> bool:
        if seg.no_speech_prob is not None and seg.no_speech_prob > 0.5:
            return True
        text = seg.text.strip()
        if _MUSIC_PATTERNS.match(text):
            return True
        if _MUSIC_SYMBOLS.match(text):
            return True
        if len(text) < 3:
            return True
        if text.lower() in _HALLUCINATION_PHRASES:
            return True
        return False

    @staticmethod
    def _is_song_lyrics(seg: TranscriptSegment) -> bool:
        """Детектит слова песен — короткие ритмичные фразы с императивами."""
        text = seg.text.strip().lower()
        # Типичные паттерны песен: команды, повторы, ритм
        song_markers = [
            "slide to the", "take it down", "bring it up", "put it on",
            "lean back", "criss-cross", "spin out", "hoedown", "boogie",
            "get into it", "have fun", "get real loose", "throw down",
            "dip with it", "clap your", "hands up", "move your",
            "shake your", "let me see", "come on", "let's go",
            "everybody", "one more time", "right now",
        ]
        for marker in song_markers:
            if marker in text:
                return True
        # Очень короткие сегменты (< 4 сек) с низким содержанием — часто песня
        duration = seg.end - seg.start
        if duration < 4 and len(text.split()) <= 8:
            return True
        return False

    @staticmethod
    def _detect_repetitions(segments: List[TranscriptSegment]) -> set:
        """Находит индексы сегментов с повторяющимся текстом — признак галлюцинаций/песен."""
        bad_indices = set()
        if len(segments) < 3:
            return bad_indices

        for i in range(len(segments) - 2):
            texts = [segments[i + j].text.strip().lower() for j in range(3)]
            if texts[0] == texts[1] == texts[2] and len(texts[0]) > 0:
                bad_indices.update({i, i + 1, i + 2})
            if texts[0] == texts[1] and len(texts[0]) > 0:
                bad_indices.update({i, i + 1})

        from collections import Counter
        text_counts = Counter(s.text.strip().lower() for s in segments if len(s.text.strip()) > 5)
        # Фраза 3+ раз — подозрительно (песня/галлюцинация)
        frequent = {text for text, count in text_counts.items() if count >= 3}
        if frequent:
            for i, s in enumerate(segments):
                if s.text.strip().lower() in frequent:
                    bad_indices.add(i)
            logger.info(f"Частые повторы (галлюцинации): {frequent}")

        return bad_indices

    @staticmethod
    def _detect_music_blocks(segments: List[TranscriptSegment]) -> set:
        """Находит блоки музыки — кластеры коротких сегментов с высоким no_speech_prob."""
        bad_indices = set()
        window = 5  # проверяем окнами по 5 сегментов
        for i in range(len(segments) - window + 1):
            block = segments[i:i + window]
            avg_nsp = sum(s.no_speech_prob or 0 for s in block) / window
            avg_dur = sum(s.end - s.start for s in block) / window
            # Блок коротких сегментов с повышенным no_speech_prob = музыка
            if avg_nsp > 0.3 and avg_dur < 5:
                bad_indices.update(range(i, i + window))
        if bad_indices:
            logger.info(f"Музыкальные блоки: {len(bad_indices)} сегментов")
        return bad_indices

    def _filter_speech_segments(
        self, segments: List[TranscriptSegment]
    ) -> List[TranscriptSegment]:
        repetition_indices = self._detect_repetitions(segments)
        music_block_indices = self._detect_music_blocks(segments)

        filtered = []
        for i, s in enumerate(segments):
            if i in repetition_indices:
                continue
            if i in music_block_indices:
                continue
            if self._is_music_segment(s):
                continue
            if self._is_song_lyrics(s):
                continue
            filtered.append(s)

        removed = len(segments) - len(filtered)
        if removed:
            logger.info(f"Отфильтровано {removed} сегментов (музыка/тишина/галлюцинации)")
        return filtered

    async def _call_openai(self, user_prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def _call_ollama(self, user_prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.ollama_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def _call_gemini(self, user_prompt: str) -> str:
        from google import genai
        loop = asyncio.get_event_loop()

        def _sync_call():
            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=f"{SYSTEM_PROMPT}\n\n{user_prompt}",
                config=genai.types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            )
            return response.text

        return await loop.run_in_executor(None, _sync_call)

    async def _analyze_chunked(
        self, segments: List[TranscriptSegment],
        max_moments: int, min_duration: int, max_duration: int,
        detected_lang: str,
    ) -> str:
        """Разбивает длинный транскрипт на чанки по ~15 мин и анализирует каждый."""
        CHUNK_DURATION = 900  # 15 минут
        total_dur = segments[-1].end if segments else 0

        async def _call_provider(prompt: str) -> str:
            if self.provider == "gemini" and settings.gemini_api_key:
                return await self._call_gemini(prompt)
            elif self.provider == "ollama":
                try:
                    return await self._call_ollama(prompt)
                except Exception:
                    pass
            return await self._call_openai(prompt)

        if total_dur <= CHUNK_DURATION * 1.5:
            transcript = self._format_transcript(segments)
            user_prompt = USER_PROMPT_TEMPLATE.format(
                transcript=transcript, max_moments=max_moments,
                min_dur=min_duration, max_dur=max_duration, detected_lang=detected_lang,
            )
            return await _call_provider(user_prompt)

        all_moments = []
        chunk_start = 0
        chunk_idx = 0
        num_chunks = max(1, int(total_dur / CHUNK_DURATION))
        moments_per_chunk = max(3, (max_moments * 2) // num_chunks)

        while chunk_start < total_dur:
            chunk_end = chunk_start + CHUNK_DURATION
            chunk_segs = [s for s in segments if s.end > chunk_start and s.start < chunk_end]

            if not chunk_segs:
                chunk_start = chunk_end
                chunk_idx += 1
                continue

            transcript = self._format_transcript(chunk_segs)
            logger.info(f"Ollama чанк {chunk_idx}: {chunk_start:.0f}s-{chunk_end:.0f}s ({len(chunk_segs)} сегментов)")

            user_prompt = USER_PROMPT_TEMPLATE.format(
                transcript=transcript, max_moments=moments_per_chunk,
                min_dur=min_duration, max_dur=max_duration, detected_lang=detected_lang,
            )

            try:
                raw = await _call_provider(user_prompt)
                try:
                    data = self._parse_json_response(raw)
                except (ValueError, json.JSONDecodeError):
                    logger.warning(f"Чанк {chunk_idx}: не-JSON, retry...")
                    raw = await _call_provider(user_prompt)
                    try:
                        data = self._parse_json_response(raw)
                    except (ValueError, json.JSONDecodeError):
                        logger.warning(f"Чанк {chunk_idx}: пропуск (нет JSON после retry)")
                        chunk_start = chunk_end
                        chunk_idx += 1
                        continue

                if isinstance(data, list):
                    chunk_moments = data
                else:
                    chunk_moments = data.get("moments", data.get("clips", data.get("shorts", [])))
                logger.info(f"Ollama чанк {chunk_idx}: найдено {len(chunk_moments)} моментов")
                all_moments.extend(chunk_moments)
            except Exception as e:
                logger.warning(f"Ollama чанк {chunk_idx} ошибка: {e}")

            chunk_start = chunk_end
            chunk_idx += 1

        logger.info(f"Ollama всего из чанков: {len(all_moments)} моментов")
        return json.dumps({"moments": all_moments})

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        """Извлекает JSON из ответа AI (может быть обёрнут в markdown или текст)."""
        raw_stripped = raw.strip()
        if "```json" in raw_stripped:
            raw_stripped = raw_stripped.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in raw_stripped:
            raw_stripped = raw_stripped.split("```", 1)[1].split("```", 1)[0].strip()

        for i, ch in enumerate(raw_stripped):
            if ch in ('{', '['):
                raw_stripped = raw_stripped[i:]
                break

        try:
            return json.loads(raw_stripped)
        except json.JSONDecodeError:
            match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', raw)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"AI вернул не-JSON: {raw[:300]}")

    def _format_transcript(self, segments: List[TranscriptSegment]) -> str:
        lines = []
        for seg in segments:
            lines.append(f"[{seg.start:.1f}] {seg.text}")
        return "\n".join(lines)

    async def analyze(
        self,
        segments: List[TranscriptSegment],
        max_moments: int = 5,
        min_duration: int = 30,
        max_duration: int = 90,
        video_path: Optional[Path] = None,
    ) -> List[VideoMoment]:
        all_segments = segments  # оригинал для проверки
        segments = self._filter_speech_segments(segments)

        if not segments:
            logger.warning("После фильтрации не осталось сегментов с речью")
            return []

        # Определяем язык транскрипта по первым сегментам
        sample_text = " ".join(s.text for s in segments[:20])
        russian_chars = sum(1 for c in sample_text if '\u0400' <= c <= '\u04ff')
        is_russian = russian_chars > len(sample_text) * 0.3
        detected_lang = "русском" if is_russian else "English (английском)"
        logger.info(f"Язык транскрипта: {detected_lang}")

        # Используем чанкирование всегда, чтобы обойти потери контекста и лимиты токенов
        raw = await self._analyze_chunked(
            segments, max_moments, min_duration, max_duration, detected_lang,
        )

        logger.info(f"Ответ от AI: {raw[:200]}...")

        data = self._parse_json_response(raw)

        if isinstance(data, list):
            moments_data = data
        else:
            moments_data = data.get("moments", data.get("clips", data.get("shorts", [])))

        moments = []
        total_duration = all_segments[-1].end if all_segments else (segments[-1].end if segments else 0)

        logger.info(f"AI вернул {len(moments_data)} моментов, total_duration={total_duration:.1f}s")

        for m in moments_data:
            start = float(m.get("start", 0))
            end = float(m.get("end", 0))

            logger.info(f"Момент: {start:.1f}-{end:.1f} '{m.get('title', '?')}'")

            if start >= end or (start == 0 and end == 0):
                logger.info(f"  -> пропуск: невалидные таймкоды")
                continue
            if end > total_duration + 30:
                logger.info(f"  -> пропуск: end ({end:.1f}) > total_duration ({total_duration:.1f})")
                continue

            # Привязка границ к реальным сегментам речи (убираем тишину)
            start, end = self._snap_to_speech(segments, start, end)
            if start >= end:
                logger.info(f"  -> пропуск: snap_to_speech обнулил диапазон")
                continue

            duration = end - start
            if duration < min_duration:
                gap = min_duration - duration
                start = max(0, start - gap / 2)
                end = min(total_duration, end + gap / 2)
                duration = end - start
            # Слишком длинные — всегда обрезаем до заданного лимита
            if duration > max_duration:
                end = start + max_duration
                duration = max_duration
                logger.info(f"  -> обрезан до {duration:.0f}s")

            # Проверка плотности речи — минимум 70% времени должна быть речь
            speech_ratio = self._speech_density(segments, start, end)
            if speech_ratio < 0.7:
                logger.info(f"Пропуск момента {start:.1f}-{end:.1f}: речь {speech_ratio:.0%} (< 70%)")
                continue

            # Проверка: нет ли музыки в оригинале в этом диапазоне
            orig_in_range = [s for s in all_segments if s.end > start and s.start < end]
            music_count = sum(1 for s in orig_in_range if self._is_music_segment(s))
            if orig_in_range and music_count / len(orig_in_range) > 0.3:
                logger.info(f"Пропуск момента {start:.1f}-{end:.1f}: {music_count}/{len(orig_in_range)} музыка в оригинале")
                continue

            moments.append(
                VideoMoment(
                    start=round(start, 2),
                    end=round(end, 2),
                    title=m.get("title", f"Момент {len(moments)+1}"),
                    description=m.get("description", ""),
                    score=int(m.get("score", 5)),
                    hook=m.get("hook"),
                    mood=m.get("mood", "upbeat"),
                )
            )

        moments.sort(key=lambda x: x.score, reverse=True)
        logger.info(f"Выбрано {len(moments)} моментов")
        return moments[:max_moments]

    @staticmethod
    def _snap_to_speech(
        segments: List[TranscriptSegment], start: float, end: float
    ) -> tuple:
        """Сдвигает start/end к границам ближайших сегментов речи внутри диапазона."""
        in_range = [s for s in segments if s.end > start and s.start < end]
        if not in_range:
            return start, end
        return max(start, in_range[0].start - 0.3), min(end, in_range[-1].end + 0.3)

    @staticmethod
    def _speech_density(
        segments: List[TranscriptSegment], start: float, end: float
    ) -> float:
        """Доля времени с речью в интервале [start, end]."""
        duration = end - start
        if duration <= 0:
            return 0
        speech_time = sum(
            min(s.end, end) - max(s.start, start)
            for s in segments
            if s.end > start and s.start < end
        )
        return speech_time / duration
