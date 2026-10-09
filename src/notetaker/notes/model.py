"""Structured lecture notes. Gemini fills this schema; the HTML renderer consumes it."""

from typing import Literal

from pydantic import BaseModel, Field

BlockType = Literal[
    "text", "bullets", "table", "formula", "diagram", "example", "problem", "callout", "code"
]
SectionStatus = Literal["new", "extends"]


class Block(BaseModel):
    type: BlockType
    # text / example / callout: markdown in `text`. problem: the problem in `text`, the fix in `solution`.
    text: str | None = None
    solution: str | None = None
    items: list[str] | None = None  # bullets (markdown per item)
    headers: list[str] | None = None  # table
    rows: list[list[str]] | None = None  # table
    latex: str | None = None  # formula, without $ delimiters
    mermaid: str | None = None  # diagram source
    code: str | None = None  # code snippet
    language: str | None = None  # code language, e.g. python
    caption: str | None = None


class Section(BaseModel):
    heading: str
    status: SectionStatus = Field(
        description="new: first time in the course. extends: adds something new to a topic covered "
        "earlier (write ONLY the new part)."
    )
    concept: str = Field(description="Short name of the concept this section teaches.")
    summary: str = Field(
        description="One line, max 25 words, saying what this section establishes. Used to "
        "recognise this topic in later lectures."
    )
    key_points: list[str] = Field(
        default_factory=list,
        description="The specific facts, examples and terms this section states, up to 8, each under "
        "15 words. Stored in the ledger so later lectures can tell what was already taught.",
    )
    what_is_new: str | None = Field(
        default=None,
        description="Required when status is extends: one line naming exactly what this lecture adds "
        "that the ledger does not already contain.",
    )
    related_ids: list[str] = Field(
        default_factory=list,
        description="Ids from the course ledger of earlier concepts this builds on or repeats.",
    )
    blocks: list[Block]


class GlossaryItem(BaseModel):
    term: str
    definition: str


class LectureNotes(BaseModel):
    title: str
    lead: str = Field(
        description="2 to 4 plain sentences that open the notes: the question or problem this "
        "lecture answers and where it lands. No bullet points."
    )
    sections: list[Section]
    glossary: list[GlossaryItem] = Field(default_factory=list)
    left_out: list[str] = Field(
        default_factory=list,
        description="One short line per chunk of the lecture skipped, with the reason "
        "(chit-chat, logistics, off-topic Q&A, already covered in Lecture N). Kept in the JSON "
        "for auditing only; the page does not show it.",
    )


class Concept(BaseModel):
    """One row of the course ledger: something an earlier lecture already taught."""

    id: str
    lecture_idx: int
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
