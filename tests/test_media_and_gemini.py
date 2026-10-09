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
