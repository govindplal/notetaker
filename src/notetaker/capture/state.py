"""Decide what a lecture page is showing, from its URL, text and whether a <video> exists."""

import os
from enum import StrEnum


class PageState(StrEnum):
    READY = "ready"  # a video element is present and can be played
    BUSY = "busy"  # the site says another device is already playing
    NEEDS_SIGNIN = "needs_signin"
    UNKNOWN = "unknown"  # still loading, or something we don't recognise


def _hints(var: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated overrides from the environment, lower-cased, for sites worded differently."""
    raw = os.environ.get(var, "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip()) or default


# Defaults suit many course sites; set the NOTETAKER_* variables (see .env.example) for yours.
SIGNIN_URL_HINTS = _hints("NOTETAKER_SIGNIN_URL_HINTS", ("/login", "/signin", "/sign-in", "/auth"))
SIGNIN_TEXT_HINTS = _hints(
    "NOTETAKER_SIGNIN_TEXT_HINTS",
    ("sign in to continue", "log in to continue", "login to continue"),
)
BUSY_TEXT = _hints("NOTETAKER_BUSY_TEXT", ("video already playing",))


def classify(url: str, text: str, has_video: bool) -> PageState:
    low_url = url.lower()
    low = text.lower()
    if any(h in low_url for h in SIGNIN_URL_HINTS) or any(h in low for h in SIGNIN_TEXT_HINTS):
        return PageState.NEEDS_SIGNIN
    if any(h in low for h in BUSY_TEXT):
        return PageState.BUSY
    if has_video:
        return PageState.READY
    return PageState.UNKNOWN
