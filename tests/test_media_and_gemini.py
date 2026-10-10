import json

import pytest
from PIL import Image, ImageDraw

from notetaker import gemini, media


def _slide(path, color, bar_x):
    """A flat colour frame with a dark bar, so different slides hash differently."""
    img = Image.new("RGB", (320, 180), color)
    ImageDraw.Draw(img).rectangle([bar_x, 40, bar_x + 60, 140], fill=(0, 0, 0))
    img.save(path)
    return path


def test_parse_segments_keeps_text_and_drops_empty(tmp_path):
    raw = json.dumps(
        [
            {"start": 0, "end": 4.5, "text": " Aaj hum linear regression dekhenge. "},
            {"start": 4.5, "end": 9, "text": "   "},
            {"start": "9", "end": 12, "text": "Matlab slope."},
        ]
    )
    segs = gemini.parse_segments(raw)
    assert segs == [
        {"start": 0.0, "end": 4.5, "text": "Aaj hum linear regression dekhenge."},
        {"start": 9.0, "end": 12.0, "text": "Matlab slope."},
    ]


def test_parse_segments_rejects_non_list():
    with pytest.raises(ValueError):
        gemini.parse_segments('{"start": 0}')


def test_dedupe_drops_identical_frames_and_keeps_slide_changes(tmp_path):
    a = _slide(tmp_path / "a.jpg", (255, 255, 255), bar_x=20)
    a2 = _slide(tmp_path / "a2.jpg", (255, 255, 255), bar_x=20)
    b = _slide(tmp_path / "b.jpg", (255, 255, 255), bar_x=200)
    kept = media.dedupe_frames([(0.0, a), (2.0, a2), (4.0, b)])
    assert [f.ts for f in kept] == [0.0, 4.0]


def test_dedupe_empty_input():
    assert media.dedupe_frames([]) == []


def _quota_error(model_delay="74681s", code=429):
    from google.genai import errors

    return errors.ClientError(
        code,
        {"error": {"code": code, "message": "Quota exceeded. Please retry in 20h44m41s.", "details": [{"retryDelay": model_delay}]}},
    )  # fmt: skip


def test_retry_after_is_read_from_the_error():
    assert gemini.retry_after_seconds(_quota_error("74681s")) == 74681.0
    assert gemini.retry_after_seconds(Exception("Please retry in 20h44m41.4s")) == pytest.approx(
        74681.4
    )
    assert gemini.retry_after_seconds(Exception("Please retry in 25s")) == 25.0
    assert gemini.retry_after_seconds(Exception("nothing useful")) is None


class _Models:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        result = self.behaviour(model)
        if isinstance(result, Exception):
            raise result
        return result


class _Client:
    def __init__(self, behaviour):
        self.models = _Models(behaviour)


@pytest.fixture(autouse=True)
def _reset_quota_memory(monkeypatch):
    monkeypatch.setattr(gemini, "_exhausted_until", {})
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)


def test_a_model_out_of_daily_quota_is_skipped_for_the_next_one():
    c = _Client(lambda m: _quota_error() if m == "a" else "ok")
    assert gemini.generate(c, "x", models=("a", "b")) == "ok"
    assert gemini.generate(c, "y", models=("a", "b")) == "ok"
    assert c.models.calls == ["a", "b", "b"]  # 'a' is not asked again until its quota returns


def test_every_model_out_of_quota_raises_quota_exhausted_with_the_wait():
    c = _Client(lambda m: _quota_error("7200s"))
    with pytest.raises(gemini.QuotaExhausted) as info:
        gemini.generate(c, "x", models=("a", "b"))
    assert 7000 < info.value.retry_after_s <= 7200
    calls_before = len(c.models.calls)
    with pytest.raises(gemini.QuotaExhausted):
        gemini.generate(c, "x", models=("a", "b"))
    assert len(c.models.calls) == calls_before  # no wasted requests while the quota is gone


def test_a_short_rate_limit_is_retried_not_treated_as_a_daily_quota():
    answers = iter([_quota_error("20s"), "ok"])
    c = _Client(lambda m: next(answers))
    assert gemini.generate(c, "x", models=("a",)) == "ok"
