"""Record a lecture by playing it in a real browser and capturing the player's own stream.

The browser is a long-lived Chrome that a person signed into once. We attach to it over CDP,
open the lecture page, press the player's own Play button (a real click, since scripts cannot
start playback), set the player to `speed`x, and record `video.captureStream()` with
MediaRecorder inside the page. Chunks are streamed to a .webm file on our side.

Timeline note: at speed S the recording is S times shorter than the lecture. Call
`media.normalize_speed(path, S)` to get a 1x file before transcription.
"""

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from notetaker.capture.state import PageState, classify


class NeedsSignIn(Exception):
    pass


class DeviceBusy(Exception):
    """The site says another device is playing. Pause the course and retry later."""


class RecordingFailed(Exception):
    pass


@dataclass(frozen=True)
class Recording:
    path: Path
    speed: float
    lecture_seconds: float  # lecture length from the player
    wall_seconds: float


START_JS = """
(speed) => {
  const v = document.querySelector('video');
  v.playbackRate = speed;
  const stream = v.captureStream();
  const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus')
    ? 'video/webm;codecs=vp8,opus' : 'video/webm';
  const rec = new MediaRecorder(stream, {
    mimeType: mime, videoBitsPerSecond: 800000, audioBitsPerSecond: 64000,
  });
  window.__nt = {done: false, error: null, pending: 0, ended: false};
  const send = (blob) => new Promise((resolve) => {
    const r = new FileReader();
    // The MIME type can contain commas (codecs=vp8,opus), so cut after ";base64,".
    r.onloadend = () => {
      const s = String(r.result);
      window.nt_chunk(s.slice(s.indexOf(';base64,') + 8)).then(resolve);
    };
    r.readAsDataURL(blob);
  });
  // Chunks must reach the file in order, so send them one after another through a queue.
  window.__nt.queue = Promise.resolve();
  rec.ondataavailable = (e) => {
    if (!e.data.size) return;
    window.__nt.pending++;
    window.__nt.queue = window.__nt.queue
      .then(() => send(e.data))
      .finally(() => { window.__nt.pending--; });
  };
  rec.onstop = () => { window.__nt.stopped = true; };
  rec.onerror = (e) => { window.__nt.error = String(e.error || e); };
  v.addEventListener('ended', () => { window.__nt.ended = true; rec.stop(); });
  rec.start(2000);
  window.__nt.rec = rec;
  return {mime, duration: v.duration};
}
"""

STATUS_JS = """
() => {
  const v = document.querySelector('video');
  const n = window.__nt || {};
  return {
    t: v ? v.currentTime : null, dur: v ? v.duration : null, paused: v ? v.paused : null,
    rate: v ? v.playbackRate : null, ended: !!n.ended, stopped: !!n.stopped,
    pending: n.pending || 0, error: n.error || null,
  };
}
"""


def page_state(page: Page) -> PageState:
    text = page.evaluate("document.body ? document.body.innerText : ''")
    has_video = page.evaluate("!!document.querySelector('video')")
    return classify(page.url, text, has_video)


def _wait_ready(page: Page, timeout_s: float = 30) -> PageState:
    deadline = time.time() + timeout_s
    state = PageState.UNKNOWN
    while time.time() < deadline:
        state = page_state(page)
        if state is not PageState.UNKNOWN:
            return state
        page.wait_for_timeout(1000)
    return state


def record_lecture(
    cdp_url: str,
    lecture_url: str,
    out_path: Path,
    speed: float = 2.0,
    max_media_seconds: float | None = None,
) -> Recording:
    """Record one lecture. `max_media_seconds` stops early (used for short test recordings)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            with out_path.open("wb") as f:

                def on_chunk(b64: str) -> None:
                    f.write(base64.b64decode(b64))

                page.expose_function("nt_chunk", on_chunk)
                page.goto(lecture_url, wait_until="domcontentloaded")
                state = _wait_ready(page)
                if state is PageState.NEEDS_SIGNIN:
                    raise NeedsSignIn(page.url)
                if state is PageState.BUSY:
                    raise DeviceBusy("another device is playing this account's video")
                if state is not PageState.READY:
                    raise RecordingFailed(
                        f"page never became ready (state={state}, url={page.url})"
                    )

                _press_play(page)
                page.wait_for_timeout(1500)
                info = page.evaluate(START_JS, speed)
                lecture_s = float(info["duration"])
                # The player may resume from a saved position, so measure from where we start.
                start_t = float(page.evaluate("document.querySelector('video').currentTime"))
                remaining = lecture_s - start_t
                limit_media = min(remaining, max_media_seconds or remaining)
                deadline = time.time() + limit_media / speed * 1.5 + 90
                last_t, last_move = -1.0, time.time()

                while True:
                    st = page.evaluate(STATUS_JS)
                    if st["error"]:
                        raise RecordingFailed(st["error"])
                    if st["stopped"] and st["pending"] == 0:
                        break
                    if st["t"] is not None and st["t"] - start_t >= limit_media - 0.5:
                        page.evaluate(
                            "window.__nt.rec.state !== 'inactive' && window.__nt.rec.stop()"
                        )
                    if page_state(page) is PageState.BUSY:
                        raise DeviceBusy("playback taken over by another device")
                    if st["t"] is not None and st["t"] > last_t + 0.2:
                        last_t, last_move = st["t"], time.time()
                    elif time.time() - last_move > 60:
                        raise RecordingFailed(f"playback stalled at {st['t']}s")
                    if st["paused"] and not st["ended"]:
                        page.evaluate("document.querySelector('video').play().catch(() => {})")
                    if time.time() > deadline:
                        raise RecordingFailed("recording timed out")
                    page.wait_for_timeout(2000)  # keeps the nt_chunk callbacks flowing
                page.evaluate("document.querySelector('video').pause()")
        finally:
            page.close()
    return Recording(
        path=out_path,
        speed=speed,
        lecture_seconds=limit_media,
        wall_seconds=time.time() - started,
    )


def _press_play(page: Page) -> None:
    btn = page.get_by_role("button", name="Play video")
    if btn.count():
        btn.first.click()
    else:
        page.locator("video").first.click()
