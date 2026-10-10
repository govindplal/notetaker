"""Write structured notes for one lecture, aware of what earlier lectures already covered."""

from pathlib import Path
from typing import Protocol

from notetaker.notes.model import Concept, LectureNotes

STYLE = (Path(__file__).parent / "style.md").read_text(encoding="utf-8")
MAX_FRAMES = 60
MAX_ATTEMPTS = 3


class NoteWriter(Protocol):
    """Anything that turns a finished prompt (plus frame image files) into notes JSON."""

    def generate(self, prompt: str, frames: list[Path], schema: dict) -> dict:
        """Return a dict matching `schema`. Raises on failure (see notes/claude_writer.py)."""
        ...


PROMPT = """You write study notes for a student taking an online course ("{course}").
You are given the full transcript of one lecture (English with some romanised Hindi, with [mm:ss]
timestamps), slide/board frames from it (image files listed at the end, each with its timestamp; view
them with your Read tool), and the LEDGER: the list
of topics earlier lectures in this course already taught.

{style}

## What to keep
Keep only knowledge a student would want to revise: definitions, concepts, how and why things work,
useful examples, formulas the lecture relies on, procedures, tools and their commands, pitfalls.
Take slide and board content from the frames and put it in the notes as text, tables or a figure. The frames and transcript are thrown away afterwards, so the notes must stand alone: never
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
- Block types: text, bullets, table (comparisons), formula (LaTeX, no $ signs), figure (see below), example, problem (problem plus solution), callout (one thing not to forget), code (a short
  snippet the lecture writes or relies on, with `language`; keep it minimal and runnable). Do not use
  the older `diagram` type.

## Figures (type "figure")
Add a figure wherever a picture teaches faster than words: an architecture or pipeline, how data flows
between parts, a loop, the layers of a network, a before/after sequence. Roughly one figure per two or
three sections is plenty for a visual lecture and none for a talky one. Never force one, and never draw a
figure that only repeats a bulleted list. Put it right after the text it illustrates, with a one-line
`caption`. You do NOT write SVG: you describe the figure as data and it is drawn for you.
- kind "flow": `nodes` (id, short label, optional `sub` line, `accent`) and `edges` (source, target,
  optional short label). At most 12 nodes. Labels are words, not sentences: up to about 24 characters,
  edge labels up to about 20. Edges may loop back to an earlier node. Use `direction` "LR" for a short
  left-to-right pipeline (up to about 5 steps in a row) and "TB" for longer chains.
  `accent`: "highlight" for the one thing to look at, "secondary" for supporting parts, "good" for the
  result, otherwise "none".
- kind "layers": `layers` (label, `units` = number of neurons, optional `sub`, accent) for a neural
  network or any stack of layers. At most 7 layers.
Example flow: nodes User, App, Vector DB, Language model (highlight); edges User->App "query",
App->Vector DB "search", Vector DB->App "chunks", App->Language model "query + chunks", Language
model->User "answer".

## Other fields
- `lead`: 2 to 4 plain sentences opening the notes with the question this lecture answers. `glossary`: up to 10 terms worth a hover definition.
- Write in English. Keep Hindi technical terms as spoken, with the English meaning beside them.
- Never invent content the lecture did not cover.

## LEDGER (earlier lectures)
{ledger}

## Lecture {idx}: {title}
{continuation}Transcript:
{transcript}
"""

CONTINUATION = """This lecture is being noted in parts. Notes for its first part already exist, covering
the sections below (headings and concepts only). The transcript you are given starts where those
notes stopped. Write ONLY what this new part adds: new sections, glossary terms and left_out lines.
Do not repeat or rephrase the existing sections. Their `lead` will not be used, so write any short
text for it.

EXISTING SECTIONS:
{existing}

"""


def _mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def degenerate_text(notes: LectureNotes) -> str | None:
    """Spot a model that got stuck repeating itself: long strings made of few distinct words."""

    def strings(x):
        if isinstance(x, str):
            yield x
        elif isinstance(x, dict):
            for v in x.values():
                yield from strings(v)
        elif isinstance(x, list):
            for v in x:
                yield from strings(v)

    for text in strings(notes.model_dump()):
        words = text.lower().split()
        if len(words) >= 60 and len(set(words)) / len(words) < 0.3:
            return f"repetitive text: {text[:60]!r}"
    return None


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
            key_points=s.key_points,
        )
        for i, s in enumerate(notes.sections)
    ]


def existing_sections(notes: LectureNotes) -> str:
    """Headings and concepts only: enough for the model to avoid repeating them."""
    return "\n".join(f"- {s.heading} ({s.concept})" for s in notes.sections) or "(none yet)"


def merge_notes(earlier: LectureNotes, new: LectureNotes) -> LectureNotes:
    """Append a later part's notes to the notes already written for the lecture."""
    glossary = {g.term.lower(): g for g in earlier.glossary}
    for g in new.glossary:
        glossary.setdefault(g.term.lower(), g)
    return earlier.model_copy(
        update={
            "sections": earlier.sections + new.sections,
            "glossary": list(glossary.values()),
            "left_out": earlier.left_out + new.left_out,
        }
    )


def write_lecture_notes(
    writer: NoteWriter,
    *,
    course: str,
    lecture_idx: int,
    title: str,
    segments: list[dict],
    frames: list[tuple[float, Path]],
    ledger: list[Concept],
    earlier: LectureNotes | None = None,
) -> LectureNotes:
    """Notes for one lecture, or for the part of it after `earlier`'s notes.

    When `earlier` is given, the returned notes hold only the new part's sections (the caller merges
    them with merge_notes). The transcript should then be only the segments after the earlier part.
    """
    transcript = "\n".join(f"[{_mmss(s['start'])}] {s['text']}" for s in segments)
    prompt = PROMPT.format(
        course=course,
        style=STYLE,
        ledger=format_ledger(ledger),
        continuation=(CONTINUATION.format(existing=existing_sections(earlier)) if earlier else ""),
        idx=lecture_idx,
        title=title,
        transcript=transcript,
    )
    picked = pick_frames(frames)
    if picked:
        listing = "\n".join(f"Frame at {_mmss(ts)}: {p}" for ts, p in picked)
        prompt += f"\n## Frames (image files)\n{listing}\n"
    schema = LectureNotes.model_json_schema()
    last: Exception | None = None
    for _attempt in range(MAX_ATTEMPTS):
        try:
            data = writer.generate(prompt, [p for _ts, p in picked], schema)
            notes = LectureNotes.model_validate(data)
            bad = degenerate_text(notes)
            if bad is None:
                return notes
            last = ValueError(bad)
        except ValueError as e:  # malformed JSON, schema mismatch or a missing figure: try again
            last = e
    raise RuntimeError(f"no valid notes after {MAX_ATTEMPTS} attempts: {last}")
