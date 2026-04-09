import asyncio
import logging
from pathlib import Path
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)


class VideoCutter:
    def __init__(self, temp_dir: Path, output_dir: Path):
        self.temp_dir = temp_dir
        self.output_dir = output_dir

    async def cut_clip(
        self,
        source: Path,
        start: float,
        end: float,
        output_path: Path,
    ) -> Path:
        duration = end - start

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(source),
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",
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

    async def convert_to_vertical(
        self,
        source: Path,
        output_path: Path,
    ) -> Path:
        width, height = await self._get_video_dimensions(source)
        is_vertical = height > width

        if is_vertical:
            vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        else:
            target_w = int(height * 9 / 16)
            x_offset = max(0, (width - target_w) // 2)
            vf = f"crop={target_w}:{height}:{x_offset}:0,scale=1080:1920"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg ошибка при конвертации: {stderr.decode()}")

        return output_path

    async def convert_to_vertical_fit(
        self,
        source: Path,
        output_path: Path,
    ) -> Path:
        """Talking head: масштабируем видео в 9:16 с размытым фоном (без кропа)."""
        filter_complex = (
            "[0:v]split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=30[blurred];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[scaled];"
            "[blurred][scaled]overlay=(W-w)/2:(H-h)/2[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(f"Fit failed, fallback: {stderr.decode()[:200]}")
            return await self.convert_to_vertical(source, output_path)

        return output_path

    async def convert_to_vertical_smart(
        self,
        source: Path,
        output_path: Path,
        crop_filter: str,
    ) -> Path:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-vf", crop_filter,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg ошибка при AI рефрейминге: {stderr.decode()}")

        return output_path

    async def convert_to_vertical_split(
        self,
        source: Path,
        output_path: Path,
        face_box: Tuple[int, int, int, int],
        src_w: int,
        src_h: int,
    ) -> Path:
        """Split-screen: контент сверху (60%), лицо снизу (40%) — как в Klap.
        Оба региона кропятся в 9:16 и масштабируются без искажений."""
        top_h = 1152   # 60% от 1920
        bot_h = 768    # 40% от 1920

        # === Верхняя часть: контент по центру ===
        # Кропим 9:16 область из центра видео
        content_aspect = 1080 / top_h  # целевое соотношение
        # Берём максимально возможную область из исходника
        content_crop_h = src_h
        content_crop_w = int(content_crop_h * content_aspect)
        if content_crop_w > src_w:
            content_crop_w = src_w
            content_crop_h = int(content_crop_w / content_aspect)
        content_x = max(0, (src_w - content_crop_w) // 2)
        content_y = 0

        # === Нижняя часть: кроп вокруг лица ===
        fx1, fy1, fx2, fy2 = face_box
        face_cx = (fx1 + fx2) // 2
        face_cy = (fy1 + fy2) // 2

        # Целевое соотношение для нижней части
        face_aspect = 1080 / bot_h
        face_h = fy2 - fy1

        # Кроп вокруг лица — плотный зум (2x размер лица)
        face_crop_h = max(face_h, 80) * 2
        face_crop_w = int(face_crop_h * face_aspect)

        # Если не влезает — ужимаем, сохраняя соотношение
        if face_crop_w > src_w:
            face_crop_w = src_w
            face_crop_h = int(face_crop_w / face_aspect)
        if face_crop_h > src_h:
            face_crop_h = src_h
            face_crop_w = int(face_crop_h * face_aspect)

        # Центрируем на лице (лицо в верхней трети кропа)
        face_crop_x = int(np.clip(face_cx - face_crop_w // 2, 0, max(0, src_w - face_crop_w)))
        face_crop_y = int(np.clip(face_cy - face_crop_h // 3, 0, max(0, src_h - face_crop_h)))

        filter_complex = (
            f"[0:v]split=2[top][bot];"
            f"[top]crop={content_crop_w}:{content_crop_h}:{content_x}:{content_y},"
            f"scale=1080:{top_h}:force_original_aspect_ratio=decrease,"
            f"pad=1080:{top_h}:(ow-iw)/2:(oh-ih)/2:black[vtop];"
            f"[bot]crop={face_crop_w}:{face_crop_h}:{face_crop_x}:{face_crop_y},"
            f"scale=1080:{bot_h}:force_original_aspect_ratio=decrease,"
            f"pad=1080:{bot_h}:(ow-iw)/2:(oh-ih)/2:black[vbot];"
            f"[vtop][vbot]vstack=inputs=2[out]"
        )

        logger.info(
            f"Split-screen: top={content_crop_w}x{content_crop_h}@{content_x},{content_y} "
            f"bot={face_crop_w}x{face_crop_h}@{face_crop_x},{face_crop_y}"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", str(source),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(f"Split-screen failed, fallback: {stderr.decode()[:300]}")
            return await self.convert_to_vertical(source, output_path)

        return output_path

    async def _get_video_dimensions(self, path: Path) -> Tuple[int, int]:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        parts = stdout.decode().strip().split(",")
        return int(parts[0]), int(parts[1])

    async def _get_video_duration(self, path: Path) -> float:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return float(stdout.decode().strip())

    async def convert_to_vertical_with_footage(
        self,
        source: Path,
        output_path: Path,
        layout: str,
        top_footage: Path | None = None,
        bottom_footage: Path | None = None,
        bg_footage: Path | None = None,
    ) -> Path:
        """Composite a streamer clip with footage filler in top/bottom/both/background mode.

        Layout geometry on a 1080x1920 canvas:
            top:        480px footage  + 1440px streamer
            bottom:     1440px streamer + 480px footage
            background: full 1080x1920 footage + 1080x810 streamer overlay at y=555
            both:       320px footage + 1280px streamer + 320px footage
        """
        clip_dur = await self._get_video_duration(source)

        # Build the input list and filter_complex per layout.
        inputs: list[str] = ["-i", str(source)]
        if layout == "top":
            if top_footage is None:
                raise ValueError("top_footage is required for layout='top'")
            inputs += ["-stream_loop", "-1", "-i", str(top_footage)]
            filter_complex = self._filter_top(clip_dur, top_h=480, mid_h=1440)
        elif layout == "bottom":
            if bottom_footage is None:
                raise ValueError("bottom_footage is required for layout='bottom'")
            inputs += ["-stream_loop", "-1", "-i", str(bottom_footage)]
            filter_complex = self._filter_bottom(clip_dur, mid_h=1440, bot_h=480)
        elif layout == "both":
            if top_footage is None or bottom_footage is None:
                raise ValueError("top_footage and bottom_footage are required for layout='both'")
            inputs += [
                "-stream_loop", "-1", "-i", str(top_footage),
                "-stream_loop", "-1", "-i", str(bottom_footage),
            ]
            filter_complex = self._filter_both(clip_dur, top_h=320, mid_h=1280, bot_h=320)
        elif layout == "background":
            if bg_footage is None:
                raise ValueError("bg_footage is required for layout='background'")
            inputs += ["-stream_loop", "-1", "-i", str(bg_footage)]
            filter_complex = self._filter_background(clip_dur, fg_h=810)
        else:
            raise ValueError(f"Unknown footage layout: {layout!r}")

        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-shortest",
            str(output_path),
        ]

        logger.info(f"Footage composite: layout={layout} dur={clip_dur:.1f}s")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"FFmpeg ошибка при сборке с футажем (layout={layout}): {stderr.decode()[-400:]}"
            )

        return output_path

    @staticmethod
    def _filter_top(clip_dur: float, top_h: int, mid_h: int) -> str:
        # Streamer fills its slot via increase+crop — no black letterbox bars.
        return (
            f"[1:v]scale=1080:{top_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{top_h},trim=duration={clip_dur},setpts=PTS-STARTPTS[topband];"
            f"[0:v]scale=1080:{mid_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{mid_h}[mid];"
            f"[topband][mid]vstack=inputs=2[out]"
        )

    @staticmethod
    def _filter_bottom(clip_dur: float, mid_h: int, bot_h: int) -> str:
        return (
            f"[1:v]scale=1080:{bot_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{bot_h},trim=duration={clip_dur},setpts=PTS-STARTPTS[botband];"
            f"[0:v]scale=1080:{mid_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{mid_h}[mid];"
            f"[mid][botband]vstack=inputs=2[out]"
        )

    @staticmethod
    def _filter_both(clip_dur: float, top_h: int, mid_h: int, bot_h: int) -> str:
        return (
            f"[1:v]scale=1080:{top_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{top_h},trim=duration={clip_dur},setpts=PTS-STARTPTS[topband];"
            f"[2:v]scale=1080:{bot_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{bot_h},trim=duration={clip_dur},setpts=PTS-STARTPTS[botband];"
            f"[0:v]scale=1080:{mid_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{mid_h}[mid];"
            f"[topband][mid][botband]vstack=inputs=3[out]"
        )

    @staticmethod
    def _filter_background(clip_dur: float, fg_h: int) -> str:
        # Background mode: footage fills the canvas. Streamer is scaled to fit
        # within fg_h height (aspect preserved, NO padding) and overlaid centered.
        # The footage is naturally visible above and below the streamer, no black bars.
        return (
            f"[1:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,trim=duration={clip_dur},setpts=PTS-STARTPTS[bg];"
            f"[0:v]scale=1080:{fg_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[out]"
        )
