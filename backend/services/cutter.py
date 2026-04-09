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
