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
        "Ты — deep аналитик и автор контента. Напиши 3-5 осмысленных постов для Threads (лимит 500 символов), "
        "основанных на ключевых идеях видео. Пиши от лица автора видео, используя его стиль и манеру речи. "
        "Каждый пост должен раскрывать одну конкретную идею. Не повторяйся. "
        "{theme_block} {style_block} "
        'Верни ТОЛЬКО JSON: {{"posts": [{{"content": "пост 1"}}, ...]}}. Никакого текста до или после JSON.'
    ),
    "trigger": (
        "Ты — автор провокационного контента. Найди 2-4 спорных/цепляющих ракурса из видео и напиши посты для X/Twitter "
        "(лимит 280 символов). Пиши в стиле автора видео. "
        "{theme_block} {style_block} "
        'Верни ТОЛЬКО JSON: {{"posts": [{{"content": "пост 1"}}, ...]}}. Никакого текста до или после JSON.'
    ),
    "bite": (
        "Ты — мастер ёмких фраз. Извлеки 3-5 самых цепляющих цитат/фактов/инсайтов из видео и напиши по одной ёмкой фразе "
        "для X/Twitter (лимит 280 символов). "
        "{theme_block} {style_block} "
        'Верни ТОЛЬКО JSON: {{"posts": [{{"content": "пост 1"}}, ...]}}. Никакого текста до или после JSON.'
    ),
}

_INSIGHT_SYSTEM_PROMPT = (
    "Ты — контент-аналитик. Прочитай предоставленный фрагмент транскрипта и извлеки ключевые тезисы, "
    "главные аргументы, заметные цитаты и важные факты. Будь краток, но полон. "
    'Верни ТОЛЬКО валидный JSON объект {{"insights": ["инсайт 1", "инсайт 2", ...]}}. Никакого текста до или после JSON.'
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

    def _filter_speech(self, segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
        """Filters out non-speech and very short segments from the transcript."""
        filtered = [
            seg
            for seg in segments
            if (seg.no_speech_prob is None or seg.no_speech_prob <= 0.6) and len(seg.text.strip()) >= 3
        ]

        if segments and len(filtered) / len(segments) < 0.1:
            logger.warning(
                "После фильтрации речи осталось менее 10%% сегментов (%d из %d)",
                len(filtered),
                len(segments),
            )

        if not filtered:
            logger.warning("Все сегменты отфильтрованы — возвращаем оригинальный транскрипт")
            return segments

        return filtered

    async def generate_posts(
        self,
        transcript: list[TranscriptSegment],
        moments: list[VideoMoment] | None = None,
        post_footer: str | None = None,
    ) -> list[PostItem]:
        if not transcript:
            logger.warning("Пустой транскрипт — посты не сгенерированы")
            return []

        # Step 1: Filter non-speech segments
        filtered = self._filter_speech(transcript)
        if not filtered:
            logger.warning("Все сегменты отфильтрованы — используем оригинальный транскрипт")
            filtered = transcript

        # Step 2: Extract theme and style (1 call each, non-blocking)
        theme = await self._extract_theme(filtered)
        style = await self._extract_style(filtered)

        # Step 3: Split filtered transcript into chunks
        chunks = self._split_into_chunks(filtered)
        logger.info(f"Транскрипт разбит на {len(chunks)} чанков, тема='{theme}', стиль='{style}'")

        # Step 4: Generate posts per chunk with theme+style in prompts
        all_tasks = []
        task_meta = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_text = self._format_transcript(chunk)
            for post_type in ["meaningful", "trigger", "bite"]:
                all_tasks.append(self._generate_posts_from_chunk(chunk_text, post_type, theme, style))
                task_meta.append((chunk_idx, post_type))

        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Group posts by chunk
        chunk_posts: dict[int, list[dict]] = {}
        for i, result in enumerate(results):
            chunk_idx, post_type = task_meta[i]
            if isinstance(result, Exception):
                logger.error(f"Ошибка генерации {post_type} в чанке {chunk_idx}: {result}")
                continue
            if chunk_idx not in chunk_posts:
                chunk_posts[chunk_idx] = []
            for content in result:
                chunk_posts[chunk_idx].append({"content": content, "type": post_type})

        # Step 5: Batch quality-rate per chunk
        rated_posts: list[dict] = []
        for _chunk_idx, posts_list in chunk_posts.items():
            rated = await self._rate_posts_batch(posts_list, theme, "mixed")
            rated_posts.extend(rated)

        # Step 6: Deduplicate, apply footer, build PostItems
        posts: list[PostItem] = []
        platforms = {"meaningful": "threads", "trigger": "x", "bite": "x"}
        seen = set()

        for p in rated_posts:
            content = p["content"]
            post_type = p["type"]
            if post_footer:
                content = content + "\n\n" + post_footer
            normalized = content.lower().strip()
            if normalized not in seen:
                seen.add(normalized)
                posts.append(
                    PostItem(
                        type=post_type,
                        content=content,
                        char_count=len(content),
                        platform=platforms[post_type],
                        moment_title=None,
                    )
                )

        logger.info(f"Сгенерировано {len(posts)} уникальных постов из {len(chunks)} чанков")
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

    def _split_into_chunks(self, segments: list[TranscriptSegment]) -> list[list[TranscriptSegment]]:
        """Разбивает транскрипт на чанки по 15 минут."""
        total_dur = segments[-1].end if segments else 0
        if total_dur <= _CHUNK_DURATION * 1.5:
            return [segments]

        chunks: list[list[TranscriptSegment]] = []
        chunk_start = 0
        while chunk_start < total_dur:
            chunk_end = chunk_start + _CHUNK_DURATION
            chunk_segs = [s for s in segments if s.end > chunk_start and s.start < chunk_end]
            if chunk_segs:
                chunks.append(chunk_segs)
            chunk_start = chunk_end
        return chunks

    async def _generate_posts_from_chunk(
        self,
        chunk_text: str,
        post_type: str,
        theme: str | None = None,
        style: str | None = None,
    ) -> list[str]:
        theme_block = f"Тема видео: {theme}. Оформи посты вокруг этой темы." if theme else ""
        style_block = f"Стиль автора: {style}. Пиши в этом стиле." if style else ""
        system_prompt = _SYSTEM_PROMPTS[post_type].format(theme_block=theme_block, style_block=style_block)
        user_prompt = f"Вот фрагмент транскрипта видео:\n\n{chunk_text}\n\nСгенерируй посты согласно инструкции."

        raw = await self._call_llm(system_prompt, user_prompt)

        try:
            data = self._parse_json_response(raw)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"Не-JSON ответ для {post_type}, retry...: {e}")
            raw = await self._call_llm(system_prompt, user_prompt)
            data = self._parse_json_response(raw)

        contents: list[str] = []
        if isinstance(data, dict):
            posts = data.get("posts", [])
            if isinstance(posts, list):
                for p in posts:
                    if isinstance(p, dict):
                        contents.append(str(p.get("content", "")).strip())
                    elif isinstance(p, str):
                        contents.append(p.strip())
            elif "content" in data:
                contents.append(str(data.get("content", "")).strip())
        elif isinstance(data, list):
            for p in data:
                if isinstance(p, dict):
                    contents.append(str(p.get("content", "")).strip())
                elif isinstance(p, str):
                    contents.append(p.strip())
        elif isinstance(data, str):
            contents.append(data.strip())

        seen = set()
        unique = []
        for c in contents:
            if c and c not in seen:
                seen.add(c)
                unique.append(c)

        return unique

    async def _rate_posts_batch(self, posts: list[dict], theme: str | None, post_type: str) -> list[dict]:
        if not posts:
            return posts

        posts_to_rate = posts[:20]
        posts_text = "\n".join(f"{i}. {p['content']}" for i, p in enumerate(posts_to_rate))
        theme_line = f"Тема видео: {theme}." if theme else ""

        system_prompt = (
            "Ты — эксперт по оценке контента. Оцени каждый пост по шкале 1-10 по критериям: "
            "тематическая релевантность, оригинальность, цепляемость. "
            f"{theme_line} "
            'Верни ТОЛЬКО JSON: {{"ratings": [{{"index": 0, "score": 8}}, ...]}}. '
            "Индексы начинаются с 0."
        )
        user_prompt = f"Оцени эти посты:\n\n{posts_text}"

        try:
            raw = await self._call_llm(system_prompt, user_prompt)
            data = self._parse_json_response(raw)

            scores = {}
            if isinstance(data, dict):
                ratings = data.get("ratings", [])
            elif isinstance(data, list):
                ratings = data
            else:
                ratings = []

            for r in ratings:
                if isinstance(r, dict):
                    idx = r.get("index")
                    score = r.get("score")
                    if isinstance(idx, int) and isinstance(score, (int, float)):
                        scores[idx] = float(score)

            filtered = [p for i, p in enumerate(posts_to_rate) if scores.get(i, 0) >= 6]

            if len(filtered) < 3:
                if len(posts_to_rate) <= 3:
                    filtered = posts_to_rate[:]
                else:
                    sorted_by_score = sorted(
                        range(len(posts_to_rate)),
                        key=lambda i: scores.get(i, 0),
                        reverse=True,
                    )
                    filtered = [posts_to_rate[i] for i in sorted_by_score[:3]]

            if len(posts) > len(posts_to_rate):
                filtered.extend(posts[len(posts_to_rate) :])

            return filtered
        except Exception as e:
            logger.warning("Ошибка batch-рейтинга постов: %s", e)
            return posts

    async def _extract_theme(self, segments: list[TranscriptSegment]) -> str | None:
        transcript_text = " ".join(seg.text for seg in segments)[:3000]

        if len(transcript_text) < 50:
            return None

        system_prompt = (
            "Ты — аналитик контента. Определи главную тему видео и ключевые angle "
            "на основе транскрипта. Верни ТОЛЬКО JSON: "
            '{{"theme": "короткая тема 2-3 слова", "angles": ["angle1", "angle2", "angle3"]}}'
        )
        user_prompt = f"Вот транскрипт видео:\n\n{transcript_text}"

        try:
            raw = await self._call_llm(system_prompt, user_prompt)
            data = self._parse_json_response(raw)
            if isinstance(data, dict):
                theme = data.get("theme")
                if isinstance(theme, str):
                    return theme.strip()
        except Exception as e:
            logger.warning("Ошибка извлечения темы: %s", e)

        return None

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

    async def _extract_style(self, segments: list[TranscriptSegment]) -> str | None:
        transcript_text = " ".join(seg.text.strip() for seg in segments)
        if len(transcript_text) < 50:
            return None

        truncated = transcript_text[:2000]
        system_prompt = (
            "Ты — аналитик стиля речи. Определи стиль спикера на основе транскрипта: "
            "тон, лексика, характерные выражения, юмор. "
            'Верни ТОЛЬКО JSON: {{"tone": "tone_descriptor", "vocabulary": "level", '
            '"expressions": ["expr1", ...], "humor": "type_or_none", '
            '"style_summary": "краткое описание стиля в 1-2 предложениях"}}'
        )
        user_prompt = f"Транскрипт:\n{truncated}"

        try:
            raw = await self._call_llm(system_prompt, user_prompt)
            data = self._parse_json_response(raw)
            style_summary = data.get("style_summary")
            if style_summary:
                return str(style_summary).strip()
        except Exception as e:
            logger.warning("Не удалось извлечь стиль речи: %s", e)

        return None
