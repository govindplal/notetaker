import pytest

from notetaker import media
from notetaker.capture.state import PageState, classify


def test_busy_page_is_detected():
    text = "Video already playing\nClose this site's video tab on the other device"
    assert classify("https://100xdevs.com/new-courses/23/video/1", text, False) is PageState.BUSY


def test_busy_wins_over_stale_video_element():
    assert classify("https://x/video/1", "Video already playing", True) is PageState.BUSY


def test_ready_when_video_present():
    assert classify("https://x/video/1", "Course content", True) is PageState.READY


def test_unknown_while_loading():
    assert classify("https://x/video/1", "", False) is PageState.UNKNOWN


@pytest.mark.parametrize(
    "url", ["https://100xdevs.com/login", "https://100xdevs.com/auth/callback"]
)
def test_signin_redirect_is_detected(url):
    assert classify(url, "", False) is PageState.NEEDS_SIGNIN


def test_signin_text_is_detected():
    assert classify("https://x/video/1", "Please sign in to continue", False) is (
        PageState.NEEDS_SIGNIN
    )


def test_atempo_single_stage_for_2x():
    assert media.atempo_chain(2) == "atempo=0.500000"


def test_atempo_chains_for_3x():
    chain = media.atempo_chain(3)
    stages = chain.split(",")
    assert stages[0] == "atempo=0.5"
    # 0.5 * x == 1/3 -> x == 2/3
    assert float(stages[1].split("=")[1]) == pytest.approx(2 / 3, rel=1e-4)


def test_atempo_rejects_slowdown():
    with pytest.raises(ValueError):
        media.atempo_chain(0.5)
