"""Write structured notes for one lecture, aware of what earlier lectures already covered."""

from pathlib import Path

from google import genai
from google.genai import types

from notetaker import gemini
from notetaker.notes.model import Concept, LectureNotes

STYLE = (Path(__file__).parent / "style.md").read_text(encoding="utf-8")
MAX_FRAMES = 60

PROMPT = """You write study notes for a student taking an online course ("{course}").
You are given the full transcript of one lecture (English with some romanised Hindi, with [mm:ss]
timestamps), slide/board frames from it (each preceded by its timestamp), and the LEDGER: the list
of topics earlier lectures in this course already taught.

{style}

## What to keep
Keep only knowledge a student would want to revise: definitions, concepts, how and why things work,
useful examples, formulas the lecture relies on, procedures, tools and their commands, pitfalls.
Take slide and board content from the frames and put it in the notes as text, tables or a Mermaid
diagram. The frames and transcript are thrown away afterwards, so the notes must stand alone: never
mention timestamps, "the slide", "the video" or "the instructor".

## What to drop (put one short line for each in `left_out`)
- greetings, logistics, announcements, deadlines, attendance, "can you hear me", jokes, off-topic talk
- screens that are not course content (recording tools, terminals or editors for unrelated work, blank canvases)
- Q&A or chat that does not teach something about the course subject. If an answer does teach
  something, keep only that knowledge.
- the instructor repeating or re-explaining something within this same lecture. Write it once.
- ANYTHING the LEDGER already contains, even if it is worded differently or shown with a different
  example. The LEDGER lists, per earlier topic, the specific facts and examples already taught.
  If a point, definition, example or diagram here matches one of them, drop it. Mention it in
  `left_out` with the earlier id, for example "Re-explains tokenization (L3-c1)".
- It is correct, and common, for a lecture to produce zero or few sections when it mostly repeats
  or recaps earlier lectures. Never pad.

## Building on earlier lectures
Use status "extends" only when this lecture adds a fact, example, mechanism, formula or detail that
is NOT in the LEDGER. Then fill `what_is_new` with one line naming exactly what is new, write ONLY
that new part, and put the earlier id in `related_ids`. If you cannot name something genuinely new,
do not write the section. Use status "new" only for topics absent from the LEDGER. Use related_ids
for new sections too when they build on earlier ones.

## Format
- Sections in teaching order. Usually 5 to 12 sections for a long lecture. Fill `key_points` for every section.
- Block types: text, bullets, table (comparisons), formula (LaTeX, no $ signs), diagram (valid
  Mermaid, one statement per line; quote any label containing brackets, colons or commas; never start
  a label with a number and a dot like "1. Query", write "1 - Query" instead), example, problem (problem plus
  solution), callout (one thing not to forget), code (a short snippet the lecture
  writes or relies on, with `language`; keep it minimal and runnable). Use a diagram only when a flow or structure helps.
- `lead`: 2 to 4 plain sentences opening the notes with the question this lecture answers. `glossary`: up to 10 terms worth a hover definition.
- Write in English. Keep Hindi technical terms as spoken, with the English meaning beside them.
- Never invent content the lecture did not cover.

## LEDGER (earlier lectures)
{ledger}

## Lecture {idx}: {title}
Transcript:
{transcript}
"""


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def format_ledger(ledger: list[Concept]) -> str:
    if not ledger:
        return "(empty: this is the first lecture, so everything is new)"
    return "\n".join(f"{c.id} | {c.title} | {c.summary}" for c in ledger)


def pick_frames(frames: list[tuple[float, Path]], limit: int = MAX_FRAMES):
    """Spread at most `limit` frames evenly over the lecture. All frames are still stored."""
    if len(frames) <= limit:
        return frames
    step = len(frames) / limit
    return [frames[int(i * step)] for i in range(limit)]


def ledger_entries(notes: LectureNotes, lecture_idx: int) -> list[Concept]:
    """One ledger row per section, so later lectures can tell what was already taught."""
    return [
        Concept(
            id=f"L{lecture_idx}-c{i + 1}",
            lecture_idx=lecture_idx,
            title=s.concept,
            summary=s.summary,
        )
        for i, s in enumerate(notes.sections)
    ]


def write_lecture_notes(
    c: genai.Client,
    *,
    course: str,
    lecture_idx: int,
    title: str,
    segments: list[dict],
    frames: list[tuple[float, Path]],
    ledger: list[Concept],
) -> LectureNotes:
    transcript = "\n".join(f"[{_mmss(s['start'])}] {s['text']}" for s in segments)
    prompt = PROMPT.format(
        course=course,
        style=STYLE,
        ledger=format_ledger(ledger),
        idx=lecture_idx,
        title=title,
        transcript=transcript,
    )
    contents: list = []
    for ts, path in pick_frames(frames):
        contents.append(f"Frame at {_mmss(ts)}:")
        contents.append(types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg"))
    contents.append(prompt)
    resp = gemini.generate(
        c,
        contents,
        types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=LectureNotes,
            temperature=0.2,
        ),
    )
    return LectureNotes.model_validate_json(resp.text)
