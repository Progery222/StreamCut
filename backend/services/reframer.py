import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CropKeyframe:
    time: float
    x_center: int


class SmartReframer:
    def __init__(self, sample_fps: int = 3, ema_alpha: float = 0.15):
        self.sample_fps = sample_fps
        self.ema_alpha = ema_alpha
        self._yolo_model = None
        self._face_detector = None

    def _load_yolo(self):
        if self._yolo_model is None:
            from ultralytics import YOLO
            self._yolo_model = YOLO("yolov8n.pt")
        return self._yolo_model

    def _load_face_detector(self, force_new: bool = False):
        if self._face_detector is None or force_new:
            if self._face_detector is not None:
                self._face_detector.close()
            import mediapipe as mp
            self._face_detector = mp.solutions.face_detection.FaceDetection(
                model_selection=1, min_detection_confidence=0.5
            )
        return self._face_detector

    def _detect_persons_yolo(self, frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
        model = self._load_yolo()
        results = model(frame, verbose=False, classes=[0])  # class 0 = person
        boxes = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                conf = float(box.conf[0])
                if conf > 0.4:
                    boxes.append((x1, y1, x2, y2))
        return boxes

    def _detect_faces_mediapipe(
        self, frame: np.ndarray
    ) -> List[Tuple[int, int, int, int]]:
        detector = self._load_face_detector()
        if frame is None or frame.size == 0:
            return []
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        try:
            results = detector.process(rgb)
        except ValueError:
            return []
        boxes = []
        if results.detections:
            for det in results.detections:
                bb = det.location_data.relative_bounding_box
                x1 = int(bb.xmin * w)
                y1 = int(bb.ymin * h)
                x2 = int((bb.xmin + bb.width) * w)
                y2 = int((bb.ymin + bb.height) * h)
                boxes.append((x1, y1, x2, y2))
        return boxes

    def _dominant_x_center(
        self, boxes: List[Tuple[int, int, int, int]]
    ) -> Optional[int]:
        if not boxes:
            return None
        areas = [(x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in boxes]
        largest_idx = np.argmax(areas)
        x1, _, x2, _ = boxes[largest_idx]
        return (x1 + x2) // 2

    def _smooth_keyframes(
        self, keyframes: List[CropKeyframe], src_w: int, crop_w: int
    ) -> List[CropKeyframe]:
        if not keyframes:
            return keyframes

        smoothed = []
        prev = keyframes[0].x_center
        half = crop_w // 2

        for kf in keyframes:
            val = self.ema_alpha * kf.x_center + (1 - self.ema_alpha) * prev
            val = int(np.clip(val, half, src_w - half))
            smoothed.append(CropKeyframe(time=kf.time, x_center=val))
            prev = val

        return smoothed

    async def compute_crop_trajectory(
        self, video_path: Path, src_w: int, src_h: int
    ) -> List[CropKeyframe]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._compute_sync, video_path, src_w, src_h
        )

    def _compute_sync(
        self, video_path: Path, src_w: int, src_h: int
    ) -> List[CropKeyframe]:
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        sample_interval = max(1, int(fps / self.sample_fps))

        crop_w = int(src_h * 9 / 16)
        default_center = src_w // 2

        logger.info(
            f"Reframer: {total_frames} frames, sample every {sample_interval}, "
            f"crop_w={crop_w}"
        )

        keyframes = []
        frame_idx = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % sample_interval == 0:
                time_sec = frame_idx / fps

                boxes = self._detect_persons_yolo(frame)
                if not boxes:
                    boxes = self._detect_faces_mediapipe(frame)

                x_center = self._dominant_x_center(boxes)
                if x_center is None:
                    x_center = default_center

                keyframes.append(CropKeyframe(time=time_sec, x_center=x_center))

            frame_idx += 1

        cap.release()

        if not keyframes:
            return [CropKeyframe(time=0, x_center=default_center)]

        smoothed = self._smooth_keyframes(keyframes, src_w, crop_w)
        logger.info(f"Reframer: {len(smoothed)} keyframes computed")
        return smoothed

    def detect_face_region(self, video_path: Path) -> Optional[Tuple[int, int, int, int]]:
        """Находит среднюю позицию лица на нескольких кадрах."""
        try:
            return self._detect_face_region_inner(video_path)
        except Exception as e:
            logger.warning(f"detect_face_region error: {e}")
            return None

    def _detect_face_region_inner(self, video_path: Path) -> Optional[Tuple[int, int, int, int]]:
        cap = cv2.VideoCapture(str(video_path))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        sample_frames = [int(total_frames * p) for p in [0.05, 0.2, 0.4, 0.6, 0.8]]
        all_faces = []

        for frame_num in sample_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue

            self._load_face_detector(force_new=True)
            faces = self._detect_faces_mediapipe(frame)
            if not faces:
                # Fallback: YOLO person detection — берём верхнюю часть bbox как "лицо"
                persons = self._detect_persons_yolo(frame)
                for px1, py1, px2, py2 in persons:
                    head_h = (py2 - py1) // 3  # верхняя треть = голова
                    faces.append((px1, py1, px2, py1 + head_h))

            if faces:
                all_faces.extend(faces)

        cap.release()

        if not all_faces:
            logger.info("Face detection: лицо не найдено ни на одном кадре")
            return None

        avg_x1 = int(np.mean([f[0] for f in all_faces]))
        avg_y1 = int(np.mean([f[1] for f in all_faces]))
        avg_x2 = int(np.mean([f[2] for f in all_faces]))
        avg_y2 = int(np.mean([f[3] for f in all_faces]))

        logger.info(f"Face region: ({avg_x1},{avg_y1})-({avg_x2},{avg_y2})")
        return (avg_x1, avg_y1, avg_x2, avg_y2)

    def is_talking_head(self, video_path: Path) -> bool:
        """Определяет: talking head (лицо крупно) или скринкаст (лицо мелко в углу)."""
        try:
            cap = cv2.VideoCapture(str(video_path))
            src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_area = src_w * src_h
            if frame_area == 0:
                cap.release()
                return False

            samples = [int(total_frames * p) for p in [0.15, 0.4, 0.65, 0.85]]
            face_ratios = []

            for frame_num in samples:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                # Пересоздаём детектор для каждого кадра (избегаем timestamp mismatch)
                self._load_face_detector(force_new=True)
                faces = self._detect_faces_mediapipe(frame)
                if faces:
                    largest = max(faces, key=lambda f: (f[2]-f[0]) * (f[3]-f[1]))
                    face_area = (largest[2]-largest[0]) * (largest[3]-largest[1])
                    face_ratios.append(face_area / frame_area)

            cap.release()

            if not face_ratios:
                return False

            avg_ratio = np.mean(face_ratios)
            is_th = avg_ratio > 0.03
            logger.info(f"Face ratio: {avg_ratio:.4f} -> {'talking_head' if is_th else 'screencast'}")
        except Exception as e:
            logger.warning(f"is_talking_head error: {e}")
            return True  # fallback: assume talking head (safer — no crop)
        return is_th

    def generate_crop_filter(
        self,
        keyframes: List[CropKeyframe],
        src_w: int,
        src_h: int,
    ) -> str:
        crop_w = int(src_h * 9 / 16)
        half = crop_w // 2

        if len(keyframes) <= 1:
            x = keyframes[0].x_center - half if keyframes else (src_w - crop_w) // 2
            x = max(0, min(x, src_w - crop_w))
            return f"crop={crop_w}:{src_h}:{x}:0,scale=1080:1920"

        # Build FFmpeg expression with linear interpolation between keyframes
        parts = []
        for i in range(len(keyframes) - 1):
            kf1 = keyframes[i]
            kf2 = keyframes[i + 1]
            x1 = kf1.x_center - half
            x2 = kf2.x_center - half
            t1 = kf1.time
            t2 = kf2.time

            x1 = max(0, min(x1, src_w - crop_w))
            x2 = max(0, min(x2, src_w - crop_w))

            if t2 - t1 < 0.001:
                lerp = str(x1)
            else:
                lerp = f"{x1}+({x2}-{x1})*(t-{t1:.3f})/({t2:.3f}-{t1:.3f})"

            parts.append(f"between(t\\,{t1:.3f}\\,{t2:.3f})*({lerp})")

        last_x = max(0, min(keyframes[-1].x_center - half, src_w - crop_w))
        expr = "+".join(parts) + f"+gte(t\\,{keyframes[-1].time:.3f})*{last_x}"

        return f"crop={crop_w}:{src_h}:'({expr})':0,scale=1080:1920"
