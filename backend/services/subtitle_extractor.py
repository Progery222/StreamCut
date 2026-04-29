import asyncio
import hashlib
import logging
import re
import shutil
from pathlib import Path

from config import settings
from models.schemas import TranscriptSegment

logger = logging.getLogger(__name__)

_COOKIES_PATH = Path(__file__).resolve().parent.parent.parent / "yt_cookies.txt"


class SubtitleExtractor:
    """Extract subtitles from video platforms.

    Phase 1: YouTube only (via yt-dlp auto-subtitles).
    Rumble and other platforms fall back to Whisper transcription.
    """

    def __init__(self):
        self.ytdlp_path = shutil.which("yt-dlp") or "yt-dlp"

    async def extract(self, url: str, language: str = "en") -> list[TranscriptSegment] | None:
        """Extract subtitles for a given URL.

        Returns a list of TranscriptSegment on success, None on failure.
        """
        if not self._is_youtube_url(url):
            return None

        return await self._extract_youtube(url, language)

    def _is_youtube_url(self, url: str) -> bool:
        return bool(re.search(r"(?:youtube\.com|youtu\.be)", url.lower()))

    async def _extract_youtube(self, url: str, language: str) -> list[TranscriptSegment] | None:
        temp_dir = settings.temp_path / f"subs_{self._hash_url(url)}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        vtt_template = str(temp_dir / "sub")
        vtt_file = temp_dir / f"sub.{language}.vtt"

        cmd = [
            self.ytdlp_path,
            "--write-auto-sub",
            "--sub-lang",
            language,
            "--sub-format",
            "vtt",
            "--skip-download",
            "--no-warnings",
            "-o",
            vtt_template,
            url,
        ]
        if _COOKIES_PATH.exists():
            cmd.extend(["--cookies", str(_COOKIES_PATH)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if not vtt_file.exists():
                logger.debug(f"No subtitles available for {url}: {stderr.decode()[:200]}")
                return None

            vtt_content = vtt_file.read_text(encoding="utf-8", errors="replace")
            segments = self._parse_youtube_vtt(vtt_content)

            if not segments:
                return None

            logger.info(f"Extracted {len(segments)} subtitle segments from YouTube")
            return segments

        except Exception as e:
            logger.warning(f"Subtitle extraction failed for {url}: {e}")
            return None

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _parse_youtube_vtt(self, vtt_content: str) -> list[TranscriptSegment]:
        """Parse YouTube ASR VTT, remove sliding-window overlap, merge into sentences."""
        cues = self._parse_vtt_cues(vtt_content)
        if not cues:
            return []

        cleaned = self._remove_overlap(cues)
        merged = self._merge_fragments(cleaned)

        segments = []
        for start, end, text in merged:
            segments.append(
                TranscriptSegment(
                    start=round(start, 2),
                    end=round(end, 2),
                    text=text,
                    words=[],
                    no_speech_prob=0.0,
                )
            )
        return segments

    def _parse_vtt_cues(self, vtt_content: str) -> list[tuple[float, float, list[str]]]:
        """Parse raw VTT into (start_sec, end_sec, text_lines) cues."""
        lines = vtt_content.splitlines()
        cues = []
        i = 0
        ts_pattern = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d+)\s+-->\s+(\d{2}):(\d{2}):(\d{2})\.(\d+)")

        while i < len(lines):
            line = lines[i].strip()
            match = ts_pattern.match(line)
            if match:
                start_sec = (
                    int(match.group(1)) * 3600
                    + int(match.group(2)) * 60
                    + int(match.group(3))
                    + int(match.group(4).ljust(3, "0")[:3]) / 1000
                )
                end_sec = (
                    int(match.group(5)) * 3600
                    + int(match.group(6)) * 60
                    + int(match.group(7))
                    + int(match.group(8).ljust(3, "0")[:3]) / 1000
                )
                i += 1
                text_lines = []
                while i < len(lines) and lines[i].strip() != "":
                    tl = re.sub(r"<[^>]+>", "", lines[i].strip()).strip()
                    if tl:
                        text_lines.append(tl)
                    i += 1
                if text_lines:
                    cues.append((start_sec, end_sec, text_lines))
                continue
            i += 1

        return cues

    def _remove_overlap(self, cues: list[tuple[float, float, list[str]]]) -> list[tuple[float, float, str]]:
        """Remove YouTube ASR sliding-window carry-over between cues."""
        result = []
        prev_text = ""

        for start, end, text_lines in cues:
            full_text = text_lines[-1] if text_lines else ""

            if not full_text or full_text == prev_text:
                continue

            overlap = self._find_overlap(prev_text, full_text)
            if overlap > 0:
                new_part = full_text[overlap:].strip()
                if new_part:
                    result.append((start, end, new_part))
            else:
                result.append((start, end, full_text))

            prev_text = full_text

        return result

    def _find_overlap(self, text_a: str, text_b: str) -> int:
        """Find longest suffix of text_a that is a prefix of text_b."""
        a = text_a.lower()
        b = text_b.lower()
        max_len = min(len(a), len(b))
        for length in range(max_len, 2, -1):
            if a.endswith(b[:length]):
                return length
        return 0

    def _merge_fragments(
        self,
        entries: list[tuple[float, float, str]],
        window_seconds: float = 4.0,
        max_chars: int = 300,
    ) -> list[tuple[float, float, str]]:
        """Merge short subtitle fragments into sentence-level segments."""
        if not entries:
            return []

        groups = []
        current_start = entries[0][0]
        current_end = entries[0][1]
        current_parts = [entries[0][2]]
        last_end = entries[0][1]
        current_len = len(entries[0][2])

        for i in range(1, len(entries)):
            start, end, text = entries[i]
            gap = start - last_end
            prev_text = current_parts[-1].rstrip() if current_parts else ""
            sentence_end = prev_text.endswith((".", "!", "?", '."', '!"', '?"'))

            if gap > window_seconds or (sentence_end and gap > 1.0) or current_len + len(text) > max_chars:
                groups.append((current_start, current_end, " ".join(current_parts)))
                current_start = start
                current_end = end
                current_parts = [text]
                current_len = len(text)
            else:
                current_parts.append(text)
                current_len += len(text) + 1
                current_end = end

            last_end = end

        if current_parts:
            groups.append((current_start, current_end, " ".join(current_parts)))

        return groups

    def _hash_url(self, url: str) -> str:
        return hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()[:12]
