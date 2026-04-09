from pydantic import BaseModel
from typing import Optional, List
from enum import Enum


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
    language: Optional[str] = "auto"
    max_shorts: Optional[int] = 5
    min_duration: Optional[int] = 15
    max_duration: Optional[int] = 60
    caption_style: Optional[str] = "default"
    reframe_mode: Optional[str] = "center"
    add_music: Optional[str] = "none"  # none, upbeat, calm, motivation
    srt_timecodes: Optional[List[dict]] = None  # [{start, end, title?}]
    publish_targets: Optional[List[str]] = None


class CreateBatchRequest(BaseModel):
    urls: List[str]
    language: Optional[str] = "auto"
    max_shorts: Optional[int] = 5
    min_duration: Optional[int] = 15
    max_duration: Optional[int] = 60
    caption_style: Optional[str] = "default"
    reframe_mode: Optional[str] = "center"
    add_music: Optional[str] = "none"
    publish_targets: Optional[List[str]] = None


class BatchResponse(BaseModel):
    batch_id: str
    jobs: List["JobResponse"]
    total: int


class StepInfo(BaseModel):
    id: str
    label: str
    status: str = "pending"  # pending, active, done, error
    detail: Optional[str] = None


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str
    progress: int = 0
    steps: Optional[List[dict]] = None
    shorts: Optional[List[dict]] = None
    error: Optional[str] = None


class VideoMoment(BaseModel):
    start: float
    end: float
    title: str
    description: str
    score: int
    hook: Optional[str] = None
    mood: Optional[str] = None


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: Optional[List[WordTimestamp]] = None
    no_speech_prob: Optional[float] = None
