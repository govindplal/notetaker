"""Go/no-go test: record 2 minutes of a lecture at 2x inside Modal with the saved login.

Uses the Volume "notetaker-profile" (filled by modal_login.py) and the project's own
`record_lecture`. Reports whether the sign-in survived, whether the recording has real audio
and real (non-black) video. No tunnel, no GPU. Nothing is kept except the report.

Run:  PYTHONUTF8=1 modal run scripts/modal_capture_test.py
"""

import json
import os
import subprocess
import time
from pathlib import Path

import modal

APP = modal.App("notetaker-capture-test")
PROFILE = modal.Volume.from_name("notetaker-profile")
PROFILE_DIR = "/profile"
LECTURE_URL = "https://100xdevs.com/new-courses/23/video/4150"  # Week 1, ~talking content
START_AT = 60.0
MEDIA_SECONDS = 120.0
SPEED = 2.0

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("chromium", "xvfb", "fluxbox", "ffmpeg", "fonts-liberation")
    .pip_install("playwright==1.*")
    .add_local_dir("src/notetaker", "/root/notetaker")
)


def _ff(*args: str) -> str:
    r = subprocess.run(["ffmpeg", "-hide_banner", *args], capture_output=True, text=True)
    return r.stderr


def analyze(path: Path) -> dict:
    out: dict = {"bytes": path.stat().st_size}
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    out["streams"] = json.loads(probe.stdout or "{}").get("streams", [])
    vol = _ff("-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-")
    for line in vol.splitlines():
        if "mean_volume" in line or "max_volume" in line:
            out[line.split("]")[-1].split(":")[0].strip()] = line.split(":")[-1].strip()
    sig = _ff(
        "-ss",
        "20",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "signalstats,metadata=print",
        "-f",
        "null",
        "-",
    )
    out["video_YAVG"] = next((ln.split("=")[-1] for ln in sig.splitlines() if "YAVG" in ln), None)
    return out


@APP.function(image=image, volumes={PROFILE_DIR: PROFILE}, timeout=1200)
def capture_test() -> dict:
    import sys

    sys.path.insert(0, "/root")
    # A browser killed mid-run leaves lock files that make Chromium refuse the profile.
    prof = Path(PROFILE_DIR) / "chrome-profile"
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        (prof / name).unlink(missing_ok=True)

    procs = [subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x24"])]
    time.sleep(2)
    env = {**os.environ, "DISPLAY": ":99"}
    procs.append(subprocess.Popen(["fluxbox"], env=env))
    procs.append(
        subprocess.Popen(
            [
                "chromium",
                f"--user-data-dir={prof}",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--remote-debugging-port=9222",
                "--autoplay-policy=no-user-gesture-required",
                "--test-type",
                "about:blank",
            ],
            env=env,
        )
    )
    time.sleep(6)
    report: dict = {"lecture": LECTURE_URL}
    try:
        from notetaker.capture.record import (
            DeviceBusy,
            NeedsSignIn,
            RecordingFailed,
            record_lecture,
        )

        out = Path("/tmp/test.webm")
        try:
            rec = record_lecture(
                "http://127.0.0.1:9222",
                LECTURE_URL,
                out,
                speed=SPEED,
                max_media_seconds=MEDIA_SECONDS,
                start_at=START_AT,
            )
            report["recording"] = {
                "lecture_seconds": rec.lecture_seconds,
                "wall_seconds": round(rec.wall_seconds, 1),
                "start_t": rec.start_t,
                "end_t": rec.end_t,
                "duration": rec.duration,
            }
            report["analysis"] = analyze(out)
            report["result"] = "recorded"
        except NeedsSignIn as e:
            report["result"] = f"NEEDS_SIGNIN: {e}"
        except DeviceBusy as e:
            report["result"] = f"DEVICE_BUSY: {e}"
        except RecordingFailed as e:
            report["result"] = f"RECORDING_FAILED: {e}"
    finally:
        for p in procs:
            p.terminate()
        PROFILE.commit()
    return report


@APP.local_entrypoint()
def main() -> None:
    print(json.dumps(capture_test.remote(), indent=2))
