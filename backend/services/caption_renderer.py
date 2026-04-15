import asyncio
import logging
from pathlib import Path

from models.schemas import TranscriptSegment

logger = logging.getLogger(__name__)

CAPTION_STYLES = {
    "default": {
        "fontsize": 52,
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 3,
        "box": 0,
    },
    "highlight": {
        "fontsize": 56,
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 4,
        "box": 1,
        "boxcolor": "black@0.5",
        "boxborderw": 8,
    },
    "minimal": {
        "fontsize": 44,
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 2,
        "box": 0,
    },
    "karaoke": {
        "fontsize": 72,
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 5,
        "highlight_color": "&H0000FFFF",
        "box": 0,
    },
    "glow": {
        "fontsize": 64,
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 4,
        "highlight_color": "&H00FF88FF",
        "box": 0,
    },
    "bold": {
        "fontsize": 80,
        "fontcolor": "white",
        "bordercolor": "black",
        "borderw": 6,
        "highlight_color": "&H004080FF",
        "box": 0,
    },
}


class CaptionRenderer:
    def _format_ass_time(self, seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int((seconds % 1) * 100)
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    @staticmethod
    def _margin_v_for_layout(footage_layout: str, caption_position: str) -> int:
        """Pick MarginV (distance from bottom, PlayResY=1920) so captions sit near the
        bottom of the streamer's visible slot.

        caption_position='fixed_bottom' → always 500 (legacy behavior, y≈1420).
        caption_position='auto' → adjust per layout so subtitles stay inside the streamer zone:
            none / footage_top    → 500  (y≈1420, inside full-screen or bottom-half streamer)
            footage_bottom        → 1070 (y≈850, near bottom of 0..960 streamer slot)
            background            → 690  (y≈1230, near bottom of 640..1280 streamer slot)
        """
        if caption_position == "fixed_bottom":
            return 500
        if footage_layout == "footage_bottom":
            return 1070
        if footage_layout == "background":
            return 690
        # none, footage_top → default
        return 500

    def _ass_header(
        self, style_name: str, fontsize: int, borderw: int, secondary_color: str = "&H000000FF", margin_v: int = 500
    ) -> str:
        return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {style_name},Arial,{fontsize},&H00FFFFFF,{secondary_color},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{borderw},0,2,20,20,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def _create_ass_subtitles(
        self,
        segments: list[TranscriptSegment],
        output_path: Path,
        style: str = "default",
        video_start: float = 0.0,
        margin_v: int = 500,
    ) -> Path:
        if style in ("karaoke", "glow", "bold"):
            return self._create_ass_karaoke(segments, output_path, video_start, style, margin_v)

        s = CAPTION_STYLES.get(style, CAPTION_STYLES["default"])
        fontsize = s["fontsize"]
        borderw = s["borderw"]

        lines = [self._ass_header("Default", fontsize, borderw, margin_v=margin_v)]

        for seg in segments:
            seg_start = seg.start - video_start
            seg_end = seg.end - video_start

            # Сегмент полностью до начала клипа — пропускаем
            if seg_end <= 0:
                continue

            # Сегмент начинается до клипа, но перекрывает — обрезаем до 0
            start_fmt = self._format_ass_time(max(0, seg_start))
            end_fmt = self._format_ass_time(seg_end)

            text = seg.text.strip()
            words = text.split()
            if len(words) > 6:
                mid = len(words) // 2
                text = " ".join(words[:mid]) + "\\N" + " ".join(words[mid:])

            lines.append(f"Dialogue: 0,{start_fmt},{end_fmt},Default,,0,0,0,,{text}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return output_path

    def _create_ass_karaoke(
        self,
        segments: list[TranscriptSegment],
        output_path: Path,
        video_start: float = 0.0,
        style_name: str = "karaoke",
        margin_v: int = 500,
    ) -> Path:
        """Klap-style: показывает 2-3 слова за раз, текущее слово выделено цветом."""
        s = CAPTION_STYLES.get(style_name, CAPTION_STYLES["karaoke"])
        # Два стиля: обычный (белый) и подсвеченный (жёлтый)
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Montserrat ExtraBold,{s["fontsize"]},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,2,0,1,{s["borderw"]},3,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines = [header]

        # Собираем все слова из всех сегментов
        all_words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    ws = w.start - video_start
                    we = w.end - video_start
                    if we > 0:
                        all_words.append((max(0, ws), we, w.word.upper()))
            else:
                ws = seg.start - video_start
                we = seg.end - video_start
                if we > 0:
                    for word in seg.text.strip().split():
                        all_words.append((max(0, ws), we, word.upper()))

        if not all_words:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(header)
            return output_path

        # Группируем по 2-3 слова
        groups = []
        i = 0
        while i < len(all_words):
            chunk_size = min(3, len(all_words) - i)
            group = all_words[i : i + chunk_size]
            groups.append(group)
            i += chunk_size

        # Для каждой группы: показываем все слова, но подсвечиваем текущее
        for group in groups:
            for word_idx, (ws, we, _word) in enumerate(group):
                # Строим текст: все слова группы, текущее — жёлтым
                parts = []
                for j, (_, _, w) in enumerate(group):
                    if j == word_idx:
                        hl = s.get("highlight_color", "&H0000FFFF")
                        parts.append(f"{{\\c{hl}\\b1}}{w}{{\\c&HFFFFFF&\\b0}}")
                    else:
                        parts.append(w)

                text = " ".join(parts)
                start_fmt = self._format_ass_time(max(0, ws))
                end_fmt = self._format_ass_time(we)
                lines.append(f"Dialogue: 0,{start_fmt},{end_fmt},Word,,0,0,0,,{text}\n")

        with open(output_path, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return output_path

    async def render_captions(
        self,
        video_path: Path,
        segments: list[TranscriptSegment],
        output_path: Path,
        style: str = "default",
        video_start: float = 0.0,
        add_music: str = "none",
        hook_text: str = None,
        footage_layout: str = "none",
        caption_position: str = "auto",
    ) -> Path:
        ass_path = video_path.parent / f"{video_path.stem}.ass"

        margin_v = self._margin_v_for_layout(footage_layout, caption_position)

        self._create_ass_subtitles(
            segments=segments,
            output_path=ass_path,
            style=style,
            video_start=video_start,
            margin_v=margin_v,
        )

        # Добавляем hook-текст в первые 3 секунды
        if hook_text:
            hook_line = (
                f"Dialogue: 1,0:00:00.00,0:00:03.00,Hook,,0,0,0,,"
                f"{{\\fad(300,300)\\an8\\fs48\\b1\\c&H00FFFF&}}{hook_text.upper()}\n"
            )
            with open(ass_path, encoding="utf-8") as f:
                content = f.read()
            # Добавляем стиль Hook если нет
            if "Style: Hook" not in content:
                hook_style = (
                    "Style: Hook,Montserrat ExtraBold,96,&H0000FFFF,&H000000FF,"
                    "&H00000000,&H64000000,-1,0,0,0,100,100,3,0,1,5,4,8,40,40,320,1\n"
                )
                content = content.replace("[Events]", hook_style + "\n[Events]")
            content += hook_line
            with open(ass_path, "w", encoding="utf-8") as f:
                f.write(content)

        music_tracks = {
            "upbeat": Path("/app/music/upbeat.mp3"),
            "calm": Path("/app/music/calm.mp3"),
            "motivation": Path("/app/music/motivation.mp3"),
        }
        music_path = music_tracks.get(add_music)
        use_music = music_path is not None and music_path.exists()
        logger.info(f"Render: style={style}, music={add_music}, use={use_music}")

        # Config for Watermark (professional style: centered logo and text)
        logo_path = Path("/app/storage/rumble_logo.png")
        use_logo = logo_path.exists()
        
        wm_x = 180
        wm_y = 910
        # Vertical center for 70px bar: 910 + 35 = 945
        # Logo (48x48): 945 - 24 = 921
        # Text (40px): 945 - 20 = 925
        # Horizontal: Logo at 220, spacing 10px, Text at 278
        wm_vf = (
            f"drawbox=x={wm_x}:y={wm_y}:w=720:h=70:color=black@0.6:t=fill,"
            f"drawtext=fontfile='/app/fonts/Montserrat-ExtraBold.ttf':text='rumble.com/PhilGodlewski':fontsize=40:fontcolor=white:x=278:y=925"
        )

        if use_music:
            ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
            fonts_dir = "/app/fonts"
            
            # Base text overlay + ASS subtitles
            v_chain = f"[0:v]{wm_vf},ass='{ass_escaped}':fontsdir='{fonts_dir}'[v_txt];"
            
            # If we have a logo, overlay it
            if use_logo:
                v_chain += f"[v_txt][1:v]overlay=x=220:y=921[vout];"
            else:
                v_chain += f"[v_txt]copy[vout];"

            # Music mixing
            a_chain = f"[0:a]volume=1.0[voice];[{2 if use_logo else 1}:a]volume=0.15[music];[voice][music]amix=inputs=2:duration=first[aout]"
            
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path)
            ]
            
            if use_logo:
                cmd.extend(["-i", str(logo_path)])
                
            cmd.extend([
                "-stream_loop", "-1", "-i", str(music_path),
                "-filter_complex", v_chain + a_chain,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k", "-shortest",
                str(output_path)
            ])
        else:
            ass_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
            v_chain = f"{wm_vf},ass='{ass_escaped}':fontsdir=/app/fonts"

            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path)
            ]
            
            if use_logo:
                cmd.extend(["-i", str(logo_path)])
                filter_complex = f"[0:v]{v_chain}[v_txt];[v_txt][1:v]overlay=x=220:y=921[vout]"
                cmd.extend([
                    "-filter_complex", filter_complex,
                    "-map", "[vout]", "-map", "0:a?",
                ])
            else:
                cmd.extend([
                    "-vf", v_chain,
                ])
                
            cmd.extend([
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "copy",
                str(output_path)
            ])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            if use_music:
                logger.warning(f"Music mix failed, retrying without: {stderr.decode()[:200]}")
                return await self.render_captions(
                    video_path, segments, output_path, style, video_start, add_music=False
                )
            raise RuntimeError(f"FFmpeg ошибка при рендеринге субтитров: {stderr.decode()}")

        ass_path.unlink(missing_ok=True)

        return output_path
