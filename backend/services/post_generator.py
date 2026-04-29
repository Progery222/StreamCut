import asyncio
import json
import logging
import re

from config import settings
from models.schemas import PostItem, TranscriptSegment, VideoMoment
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_SYSTEM_PROMPTS = {
    "meaningful": (
        "Ты — глубокий аналитик и автор контента. Извлеки главную мысль из предоставленного транскрипта "
        "и оформи её как вдумчивый тред для Threads (лимит 500 символов). "
        "Напиши 2-4 абзаца, которые глубоко анализируют ключевые идеи. "
        'Верни ТОЛЬКО валидный JSON объект {"content": "твой пост"}. Никакого текста до или после JSON.'
    ),
    "trigger": (
        "Ты — провокационный автор контента. Найди самый спорный, неожиданный или дерзкий ракурс "
        "в предоставленном транскрипте. Напиши провокационный хук + смелую мысль для X/Twitter (лимит 280 символов). "
        'Верни ТОЛЬКО валидный JSON объект {"content": "твой пост"}. Никакого текста до или после JSON.'
    ),
    "bite": (
        "Ты — лаконичный автор контента. Извлеки самый цепляющий факт, цитату или инсайт "
        "из предоставленного транскрипта. Напиши одну ёмкую фразу для X/Twitter (лимит 280 символов). "
        'Верни ТОЛЬКО валидный JSON объект {"content": "твой пост"}. Никакого текста до или после JSON.'
    ),
}

_INSIGHT_SYSTEM_PROMPT = (
    "Ты — контент-аналитик. Прочитай предоставленный фрагмент транскрипта и извлеки ключевые тезисы, "
    "главные аргументы, заметные цитаты и важные факты. Будь краток, но полон. "
    'Верни ТОЛЬКО валидный JSON объект {"insights": ["инсайт 1", "инсайт 2", ...]}. Никакого текста до или после JSON.'
)

_CHUNK_DURATION = 900  # 15 минут, как в analyzer.py


class PostGenerator:
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
            logger.info(f"Генератор постов: Ollama ({self.ollama_model})")
        elif self.provider == "gemini" and settings.gemini_api_key:
            self.client = None
            logger.info("Генератор постов: Gemini 2.0 Flash")
        else:
            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
            logger.info("Генератор постов: OpenAI GPT-4o-mini")

    async def generate_posts(
        self,
        transcript: list[TranscriptSegment],
        moments: list[VideoMoment] | None = None,
    ) -> list[PostItem]:
        if not transcript:
            logger.warning("Пустой транскрипт — посты не сгенерированы")
            return []

        # Собираем контекст (с чанкированием при необходимости)
        context = await self._build_context(transcript, moments)

        # Параллельно генерируем 3 типа постов
        results = await asyncio.gather(
            self._generate_post(context, "meaningful"),
            self._generate_post(context, "trigger"),
            self._generate_post(context, "bite"),
            return_exceptions=True,
        )

        posts: list[PostItem] = []
        types = ["meaningful", "trigger", "bite"]
        platforms = {"meaningful": "threads", "trigger": "x", "bite": "x"}

        for i, result in enumerate(results):
            post_type = types[i]
            if isinstance(result, Exception):
                logger.error(f"Ошибка генерации {post_type}: {result}")
                continue
            content = result
            char_count = len(content)
            posts.append(
                PostItem(
                    type=post_type,
                    content=content,
                    char_count=char_count,
                    platform=platforms[post_type],
                    moment_title=None,
                )
            )

        return posts

    async def _build_context(
        self,
        segments: list[TranscriptSegment],
        moments: list[VideoMoment] | None,
    ) -> str:
        total_dur = segments[-1].end if segments else 0

        # Если транскрипт короткий — отправляем целиком
        if total_dur <= _CHUNK_DURATION * 1.5:
            transcript_text = self._format_transcript(segments)
        else:
            # Чанкирование для длинных транскриптов
            transcript_text = await self._chunked_transcript(segments)

        context_parts = [f"ТРАНСКРИПТ:\n{transcript_text}"]

        if moments:
            top_moments = sorted(moments, key=lambda m: m.score, reverse=True)[:3]
            moment_lines = []
            for m in top_moments:
                moment_lines.append(f"- {m.title}: {m.description} (score {m.score})")
            context_parts.append("ЛУЧШИЕ МОМЕНТЫ:\n" + "\n".join(moment_lines))

        return "\n\n".join(context_parts)

    async def _chunked_transcript(self, segments: list[TranscriptSegment]) -> str:
        """Разбивает длинный транскрипт на чанки и извлекает ключевые инсайты."""
        chunk_start = 0
        chunk_idx = 0
        all_insights: list[str] = []

        tasks = []
        chunk_segments_list: list[list[TranscriptSegment]] = []

        total_dur = segments[-1].end if segments else 0
        while chunk_start < total_dur:
            chunk_end = chunk_start + _CHUNK_DURATION
            chunk_segs = [s for s in segments if s.end > chunk_start and s.start < chunk_end]
            if chunk_segs:
                chunk_segments_list.append(chunk_segs)
            chunk_start = chunk_end
            chunk_idx += 1

        # Параллельно извлекаем инсайты из каждого чанка
        for chunk_segs in chunk_segments_list:
            transcript = self._format_transcript(chunk_segs)
            prompt = f"Вот фрагмент транскрипта:\n\n{transcript}\n\nИзвлеки ключевые тезисы, аргументы, цитаты и факты."
            tasks.append(self._call_llm(_INSIGHT_SYSTEM_PROMPT, prompt))

        if not tasks:
            return self._format_transcript(segments)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Ошибка инсайтов чанка {i}: {result}")
                continue
            try:
                data = self._parse_json_response(result)
                if isinstance(data, dict):
                    insights = data.get("insights", [])
                    if isinstance(insights, list):
                        all_insights.extend(str(x) for x in insights)
            except (ValueError, json.JSONDecodeError) as e:
                logger.warning(f"Не удалось распарсить инсайты чанка {i}: {e}")

        if all_insights:
            return "КЛЮЧЕВЫЕ ТЕЗИСЫ ИЗ ТРАНСКРИПТА:\n" + "\n".join(f"- {ins}" for ins in all_insights)
        return self._format_transcript(segments)

    async def _generate_post(self, context: str, post_type: str) -> str:
        system_prompt = _SYSTEM_PROMPTS[post_type]
        user_prompt = f"На основе следующего контента видео напиши пост:\n\n{context}"

        raw = await self._call_llm(system_prompt, user_prompt)

        try:
            data = self._parse_json_response(raw)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Не-JSON ответ для {post_type}, retry...: {e}")
            raw = await self._call_llm(system_prompt, user_prompt)
            data = self._parse_json_response(raw)

        if isinstance(data, dict):
            content = data.get("content", "")
        elif isinstance(data, str):
            content = data
        else:
            content = str(data)

        return content.strip()

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        if self.provider == "gemini" and settings.gemini_api_key:
            return await self._call_gemini(system_prompt, user_prompt)
        elif self.provider == "ollama":
            try:
                return await self._call_ollama(system_prompt, user_prompt)
            except Exception as e:
                logger.warning("Failed to call Ollama: %s", e)
        return await self._call_openai(system_prompt, user_prompt)

    async def _call_openai(self, system_prompt: str, user_prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=settings.analyzer_openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def _call_ollama(self, system_prompt: str, user_prompt: str) -> str:
        response = await self.client.chat.completions.create(
            model=self.ollama_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8000,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    async def _call_gemini(self, system_prompt: str, user_prompt: str) -> str:
        from google import genai

        loop = asyncio.get_event_loop()

        def _sync_call():
            client = genai.Client(api_key=settings.gemini_api_key)
            response = client.models.generate_content(
                model=settings.analyzer_gemini_model,
                contents=f"{system_prompt}\n\n{user_prompt}",
                config=genai.types.GenerateContentConfig(
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            )
            return response.text

        return await loop.run_in_executor(None, _sync_call)

    @staticmethod
    def _parse_json_response(raw: str) -> dict:
        raw_stripped = raw.strip()
        if "```json" in raw_stripped:
            raw_stripped = raw_stripped.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in raw_stripped:
            raw_stripped = raw_stripped.split("```", 1)[1].split("```", 1)[0].strip()

        for i, ch in enumerate(raw_stripped):
            if ch in ("{", "["):
                raw_stripped = raw_stripped[i:]
                break

        try:
            return json.loads(raw_stripped)
        except json.JSONDecodeError:
            match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"AI вернул не-JSON: {raw[:300]}") from None

    @staticmethod
    def _format_transcript(segments: list[TranscriptSegment]) -> str:
        lines = []
        for seg in segments:
            lines.append(f"[{seg.start:.1f}] {seg.text}")
        return "\n".join(lines)
