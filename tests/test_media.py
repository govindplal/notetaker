import pytest
from PIL import Image, ImageDraw

from notetaker import media


def _slide(path, color, bar_x):
    """A flat colour frame with a dark bar, so different slides hash differently."""
    img = Image.new("RGB", (320, 180), color)
    ImageDraw.Draw(img).rectangle([bar_x, 40, bar_x + 60, 140], fill=(0, 0, 0))
    img.save(path)
    return path


def test_dedupe_drops_identical_frames_and_keeps_slide_changes(tmp_path):
    a = _slide(tmp_path / "a.jpg", (255, 255, 255), bar_x=20)
    a2 = _slide(tmp_path / "a2.jpg", (255, 255, 255), bar_x=20)
    b = _slide(tmp_path / "b.jpg", (255, 255, 255), bar_x=200)
    kept = media.dedupe_frames([(0.0, a), (2.0, a2), (4.0, b)])
    assert [f.ts for f in kept] == [0.0, 4.0]


def test_dedupe_empty_input():
    assert media.dedupe_frames([]) == []


def test_atempo_chain_slows_by_the_speed_in_stages_of_at_least_half():
    assert media.atempo_chain(2.0) == "atempo=0.500000"
    assert media.atempo_chain(4.0) == "atempo=0.5,atempo=0.500000"
    with pytest.raises(ValueError):
        media.atempo_chain(0.5)
