"""Render structured lecture notes into a self-contained, readable HTML page."""

import html
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from notetaker.notes.model import Block, GlossaryItem, LectureNotes

_md = MarkdownIt("commonmark", {"html": False}).enable("table")
_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)

_TAG = re.compile(r"(<[^>]+>)")


def mmss(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def inline(text: str) -> str:
    return _md.renderInline(text or "")


def block_md(text: str) -> str:
    return _md.render(text or "")


def link_terms(rendered: str, glossary: list[GlossaryItem], seen: set[str]) -> str:
    """Wrap the first mention of each glossary term in a hoverable span, outside HTML tags."""
    if not glossary:
        return rendered
    parts = _TAG.split(rendered)
    for i, part in enumerate(parts):
        if part.startswith("<"):
            continue
        for g in glossary:
            key = g.term.lower()
            if key in seen:
                continue
            pattern = re.compile(rf"(?<![\w-]){re.escape(html.escape(g.term))}(?![\w-])", re.I)
            m = pattern.search(part)
            if m:
                slug = re.sub(r"\W+", "-", key).strip("-")
                span = f'<span class="term" data-term="{slug}">{m.group(0)}</span>'
                part = part[: m.start()] + span + part[m.end() :]
                seen.add(key)
        parts[i] = part
    return "".join(parts)


@dataclass(frozen=True)
class FrameRef:
    ts: float
    href: str  # path relative to the HTML file


def nearest_frame(frames: list[FrameRef], ts: float) -> FrameRef | None:
    before = [f for f in frames if f.ts <= ts + 1]
    return before[-1] if before else (frames[0] if frames else None)


_LIST_LABEL = re.compile(r'(\|"?|\["|\("|\{")(\s*)(\d+)\.\s')


def clean_mermaid(src: str) -> str:
    """Mermaid reads a label starting with "1. " or "1) " as a Markdown list; use "1 - " instead."""
    return _LIST_LABEL.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)} - ", src)


def _render_block(b: Block, glossary: list[GlossaryItem], seen: set[str]) -> dict:
    d = {"type": b.type, "caption": b.caption}
    if b.type in ("text", "example", "callout"):
        d["html"] = link_terms(block_md(b.text or ""), glossary, seen)
    elif b.type == "bullets":
        d["items"] = [link_terms(inline(i), glossary, seen) for i in (b.items or [])]
    elif b.type == "table":
        d["headers"] = [inline(h) for h in (b.headers or [])]
        d["rows"] = [[inline(c) for c in row] for row in (b.rows or [])]
    elif b.type == "formula":
        d["latex"] = b.latex or ""
    elif b.type == "diagram":
        d["mermaid"] = clean_mermaid(b.mermaid or "")
    elif b.type == "problem":
        d["problem"] = link_terms(block_md(b.text or ""), glossary, seen)
        d["solution"] = link_terms(block_md(b.solution or ""), glossary, seen)
    return d


def render_lecture(
    notes: LectureNotes,
    *,
    course_title: str,
    lecture_idx: int,
    lecture_date: str | None = None,
    frames: list[FrameRef] | None = None,
    transcript: list[dict] | None = None,
    prev_href: str | None = None,
    next_href: str | None = None,
    index_href: str = "index.html",
    lecture_titles: dict[int, str] | None = None,
) -> str:
    frames = sorted(frames or [], key=lambda f: f.ts)
    seen: set[str] = set()
    sections = []
    for s in notes.sections:
        fr = nearest_frame(frames, s.ts_seconds)
        sections.append(
            {
                "heading": s.heading,
                "ts": mmss(s.ts_seconds),
                "status": s.status,
                "concept": s.concept,
                "related": [_pointer(r, lecture_titles or {}) for r in s.related_ids],
                "frame": fr.href if fr else None,
                "blocks": [_render_block(b, notes.glossary, seen) for b in s.blocks],
            }
        )
    glossary = [
        {
            "slug": re.sub(r"\W+", "-", g.term.lower()).strip("-"),
            "term": g.term,
            "definition": inline(g.definition),
        }
        for g in notes.glossary
        if g.term.lower() in seen  # only terms that actually appear
    ]
    tpl = _env.get_template("lecture.html")
    return tpl.render(
        notes=notes,
        sections=sections,
        glossary=glossary,
        tldr=[inline(t) for t in notes.tldr],
        left_out=[inline(x) for x in notes.left_out],
        course_title=course_title,
        lecture_idx=lecture_idx,
        lecture_date=lecture_date,
        frames=[{"ts": mmss(f.ts), "href": f.href} for f in frames],
        transcript=[{"ts": mmss(s["start"]), "text": s["text"]} for s in (transcript or [])],
        prev_href=prev_href,
        next_href=next_href,
        index_href=index_href,
    )


def _pointer(concept_id: str, titles: dict[int, str]) -> str:
    """Ledger ids look like 'L3-c2'; show them as 'Lecture 3: Title' (title when known)."""
    m = re.match(r"L(\d+)-", concept_id)
    if not m:
        return concept_id
    n = int(m.group(1))
    return f"Lecture {n}: {titles[n]}" if n in titles else f"Lecture {n}"
