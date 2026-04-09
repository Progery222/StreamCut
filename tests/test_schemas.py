import pytest
from models.schemas import (
    CreateJobRequest,
    JobResponse,
    JobStatus,
    VideoMoment,
    WordTimestamp,
)
from pydantic import ValidationError


def test_create_job_request_defaults():
    req = CreateJobRequest(url="https://youtube.com/watch?v=test")
    assert req.language == "auto"
    assert req.max_shorts == 5
    assert req.caption_style == "default"
    assert req.reframe_mode == "center"


def test_create_job_request_requires_url():
    with pytest.raises(ValidationError):
        CreateJobRequest()


def test_job_status_values():
    assert JobStatus.PENDING == "pending"
    assert JobStatus.DONE == "done"
    assert JobStatus.ERROR == "error"


def test_job_response_minimum_fields():
    resp = JobResponse(job_id="abc", status=JobStatus.PENDING, message="queued")
    assert resp.progress == 0
    assert resp.shorts is None


def test_video_moment_score_required():
    moment = VideoMoment(start=0.0, end=10.5, title="t", description="d", score=8)
    assert moment.end - moment.start == 10.5


def test_word_timestamp():
    w = WordTimestamp(word="hello", start=0.0, end=0.5)
    assert w.word == "hello"
