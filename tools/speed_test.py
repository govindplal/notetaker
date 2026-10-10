"""Find how fast a lecture can be recorded before the transcription gets worse.

Records the same stretch of one lecture at several playback speeds, transcribes each recording
(as recorded, and slowed back to normal pace), and compares every transcript with a 1x reference.
Two 1x transcriptions of the same audio give a noise floor: the difference between two runs of the
same model on identical audio, so real degradation can be told apart from run-to-run variation.

    uv run python tools/speed_test.py --url <lecture url> --start 1800 --seconds 180 --speeds 1 2 3 4 6 8

Needs the recorder Chrome (signed in) and GEMINI_API_KEY. Recordings and transcripts are deleted
afterwards; only the metrics are kept (docs/speed-test.json).
"""

import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from notetaker import gemini, media
from notetaker.capture.record import DeviceBusy, record_lecture
from notetaker.config import load_dotenv

HINDI_MARKERS = {
    "hai", "hain", "kya", "toh", "to", "ka", "ki", "ke", "ko", "se", "me", "mein", "yeh", "ye", "woh",
    "vo", "hum", "aap", "nahi", "nahin", "bhi", "ek", "kuch", "jo", "matlab", "isliye", "lekin", "aur",
    "agar", "kyunki", "jab", "tab", "abhi", "phir", "ab", "bahut", "kaise", "kyun", "wala", "wali",
}  # fmt: skip
STOP = {"about", "because", "should", "would", "could", "there", "their", "which", "where", "these", "those", "being", "really", "actually"}  # fmt: skip


def words(segments: list[dict]) -> list[str]:
    text = " ".join(s["text"] for s in segments).lower()
    return re.findall(r"[a-z0-9']+", text)


def wer(ref: list[str], hyp: list[str]) -> float:
    """Word error rate: edit distance between the word lists over the reference length."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def term_recall(ref: list[str], hyp: list[str]) -> float:
    """Share of the reference's distinctive words (6+ letters) that also appear in the hypothesis."""
    terms = {w for w in ref if len(w) >= 6 and w not in STOP}
    return len(terms & set(hyp)) / max(len(terms), 1)


def hindi_recall(ref: list[str], hyp: list[str]) -> tuple[int, float]:
    """(Hindi function words in the reference, share of them kept by the hypothesis)."""
    from collections import Counter

    cr = Counter(w for w in ref if w in HINDI_MARKERS)
    ch = Counter(w for w in hyp if w in HINDI_MARKERS)
    total = sum(cr.values())
    kept = sum(min(n, ch[w]) for w, n in cr.items())
    return total, kept / max(total, 1)


def extract_audio(recording: Path, out: Path, speed: float | None) -> Path:
    """16 kHz mono MP3. With `speed` set, slow it back to natural pace first."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(recording),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
    ]
    if speed and speed > 1:
        cmd += ["-af", media.atempo_chain(speed)]
    subprocess.run([*cmd, "-b:a", "64k", str(out)], check=True)
    return out


def record(url: str, out: Path, speed: float, start: float, seconds: float, cdp: str) -> dict:
    for attempt in range(4):
        try:
            t0 = time.time()
            rec = record_lecture(
                cdp, url, out, speed=speed, max_media_seconds=seconds, start_at=start
            )
            wall = time.time() - t0
            return {
                "wall_s": round(wall, 1),
                "media_s": rec.lecture_seconds,
                "effective_speed": round(rec.lecture_seconds / wall, 2),
                "mb": round(out.stat().st_size / 1e6, 1),
            }
        except DeviceBusy:
            print(f"  player busy, waiting 30 s (attempt {attempt + 1})", flush=True)
            time.sleep(30)
    raise SystemExit("the player stayed busy; close the course on other devices and retry")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--url", required=True)
    p.add_argument("--start", type=float, default=1800)
    p.add_argument("--seconds", type=float, default=180)
    p.add_argument("--speeds", type=float, nargs="+", default=[1, 2, 3, 4, 6, 8])
    p.add_argument("--cdp", default="http://127.0.0.1:9222")
    p.add_argument("--work", type=Path, default=Path("data/speedtest"))
    p.add_argument("--out", type=Path, default=Path("docs/speed-test.json"))
    a = p.parse_args()
    a.work.mkdir(parents=True, exist_ok=True)
    client = gemini.client()

    recs: dict[float, dict] = {}
    for s in a.speeds:
        print(f"recording {s}x ...", flush=True)
        path = a.work / f"rec_{s:g}x.webm"
        recs[s] = {"path": path, **record(a.url, path, s, a.start, a.seconds, a.cdp)}
        print(
            f"  {recs[s]['wall_s']} s wall for {recs[s]['media_s']:.0f} s of lecture (effective {recs[s]['effective_speed']}x), {recs[s]['mb']} MB",
            flush=True,
        )

    transcripts: dict[str, list[str]] = {}
    jobs = [("ref_a", 1.0, None), ("ref_b", 1.0, None)]
    for s in a.speeds:
        if s > 1:
            jobs += [(f"{s:g}x_raw", s, None), (f"{s:g}x_slowed", s, s)]
    for name, s, slow in jobs:
        src = recs[1.0 if name.startswith("ref") else s]["path"]
        audio = extract_audio(src, a.work / f"{name}.mp3", slow)
        print(f"transcribing {name} ...", flush=True)
        transcripts[name] = words(gemini.transcribe(client, audio))
        print(f"  {len(transcripts[name])} words", flush=True)

    ref = transcripts["ref_a"]
    h_total, _ = hindi_recall(ref, ref)
    results = {
        "sample": {
            "url": a.url,
            "start_s": a.start,
            "seconds": a.seconds,
            "reference_words": len(ref),
            "hindi_marker_words_in_reference": h_total,
        },
        "noise_floor": {
            "wer": round(wer(ref, transcripts["ref_b"]), 3),
            "term_recall": round(term_recall(ref, transcripts["ref_b"]), 3),
        },
        "speeds": {},
    }
    for s in a.speeds:
        if s == 1:
            continue
        row = {k: v for k, v in recs[s].items() if k != "path"}
        for variant in ("raw", "slowed"):
            hyp = transcripts[f"{s:g}x_{variant}"]
            n_h, rec_h = hindi_recall(ref, hyp)
            row[variant] = {
                "words": len(hyp),
                "wer": round(wer(ref, hyp), 3),
                "term_recall": round(term_recall(ref, hyp), 3),
                "hindi_recall": round(rec_h, 3),
            }
        results["speeds"][f"{s:g}x"] = row
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    shutil.rmtree(a.work, ignore_errors=True)  # recordings and transcripts are not kept
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
