from enum import Enum
from typing import Literal

from pydantic import BaseModel

FootageLayout = Literal["none", "background", "footage_top", "footage_bottom"]
CaptionPosition = Literal["auto", "fixed_bottom"]


class JobStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    ANALYZING = "analyzing"
    CUTTING = "cutting"
    RENDERING = "rendering"
    PUBLISHING = "publishing"
    DONE = "done"
    ERROR = "error"


class CreateJobRequest(BaseModel):
    url: str
    language: str | None = "auto"
    max_shorts: int | None = 5
    min_duration: int | None = 15
    max_duration: int | None = 60
    caption_style: str | None = "default"
    reframe_mode: str | None = "center"
    add_music: str | None = "none"  # none, upbeat, calm, motivation
    footage_layout: FootageLayout = "none"
    footage_category: str | None = None  # filter footage library by category
    caption_position: CaptionPosition = "auto"  # auto → follows layout; fixed_bottom → always y≈1420
    add_watermark: bool = True  # add rumble.com watermark to video
    srt_timecodes: list[dict] | None = None  # [{start, end, title?}]
    publish_targets: list[str] | None = None


class CreateBatchRequest(BaseModel):
    urls: list[str]
    language: str | None = "auto"
    max_shorts: int | None = 5
    min_duration: int | None = 15
    max_duration: int | None = 60
    caption_style: str | None = "default"
    reframe_mode: str | None = "center"
    add_music: str | None = "none"
    footage_layout: FootageLayout = "none"
    footage_category: str | None = None
    caption_position: CaptionPosition = "auto"
    add_watermark: bool = True
    publish_targets: list[str] | None = None


class BatchResponse(BaseModel):
    batch_id: str
    jobs: list["JobResponse"]
    total: int


class StepInfo(BaseModel):
    id: str
    label: str
    status: str = "pending"  # pending, active, done, error
    detail: str | None = None


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    progress: int = 0
    steps: list[dict] | None = None
    shorts: list[dict] | None = None
    error: str | None = None


class VideoMoment(BaseModel):
    start: float
    end: float
    title: str
    description: str
    score: int
    hook: str | None = None
    mood: str | None = None


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[WordTimestamp] | None = None
    no_speech_prob: float | None = None
