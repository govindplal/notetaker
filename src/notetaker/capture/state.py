"""Decide what a lecture page is showing, from its URL, text and whether a <video> exists."""

from enum import StrEnum


class PageState(StrEnum):
    READY = "ready"  # a video element is present and can be played
    BUSY = "busy"  # the site says another device is already playing
    NEEDS_SIGNIN = "needs_signin"
    UNKNOWN = "unknown"  # still loading, or something we don't recognise


SIGNIN_URL_HINTS = ("/login", "/signin", "/sign-in", "/auth")
SIGNIN_TEXT_HINTS = ("sign in to continue", "log in to continue", "login to continue")
BUSY_TEXT = "video already playing"


def classify(url: str, text: str, has_video: bool) -> PageState:
    low_url = url.lower()
    low = text.lower()
    if any(h in low_url for h in SIGNIN_URL_HINTS) or any(h in low for h in SIGNIN_TEXT_HINTS):
        return PageState.NEEDS_SIGNIN
    if BUSY_TEXT in low:
        return PageState.BUSY
    if has_video:
        return PageState.READY
    return PageState.UNKNOWN
