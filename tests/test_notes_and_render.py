from pathlib import Path

from notetaker.notes import writer
from notetaker.notes.model import Block, Concept, GlossaryItem, LectureNotes, Section
from notetaker.offline import load_ledger, save_ledger
from notetaker.render import html as render


def _notes() -> LectureNotes:
    return LectureNotes(
        title="Tokenization",
        tldr=["Text becomes **numbers** first."],
        sections=[
            Section(
                heading="Why tokenize",
                ts_seconds=75,
                status="new",
                concept="Tokenization",
                summary="Text is converted to token ids before a model sees it.",
                related_ids=["L1-c2"],
                blocks=[
                    Block(type="text", text="Models only understand numbers. -> Tokenization"),
                    Block(type="bullets", items=["character level", "subword"]),
                    Block(type="table", headers=["Approach", "Cost"], rows=[["char", "high"]]),
                    Block(type="formula", latex="x^2"),
                    Block(type="diagram", mermaid="flowchart LR\n A --> B"),
                    Block(type="problem", text="13 steps per word", solution="Use subwords"),
                ],
            ),
            Section(
                heading="BPE",
                ts_seconds=600,
                status="extends",
                concept="BPE",
                summary="Merges frequent pairs.",
                blocks=[Block(type="callout", text="<script>alert(1)</script> stay safe")],
            ),
        ],
        glossary=[
            GlossaryItem(term="Tokenization", definition="Text to ids."),
            GlossaryItem(term="Unused", definition="never mentioned"),
        ],
        left_out=["Greetings", "Re-explains attention (L2-c1)"],
    )


def test_ledger_entries_get_stable_ids():
    entries = writer.ledger_entries(_notes(), 4)
    assert [e.id for e in entries] == ["L4-c1", "L4-c2"]
    assert entries[0].title == "Tokenization" and entries[0].lecture_idx == 4


def test_format_ledger_empty_and_filled():
    assert "first lecture" in writer.format_ledger([])
    c = Concept(id="L1-c1", lecture_idx=1, title="RAG", summary="Retrieve then generate.")
    assert writer.format_ledger([c]) == "L1-c1 | RAG | Retrieve then generate."


def test_pick_frames_caps_and_spreads():
    frames = [(float(i), Path(f"{i}.jpg")) for i in range(200)]
    picked = writer.pick_frames(frames, limit=50)
    assert len(picked) == 50
    assert picked[0][0] == 0.0 and picked[-1][0] > 150
    assert writer.pick_frames(frames[:10], limit=50) == frames[:10]


def test_ledger_roundtrip(tmp_path):
    path = tmp_path / "ledger.json"
    assert load_ledger(path) == []
    entries = writer.ledger_entries(_notes(), 1)
    save_ledger(path, entries)
    assert load_ledger(path) == entries


def test_render_contains_all_parts_and_escapes_html():
    out = render.render_lecture(
        _notes(),
        course_title="AI and ML",
        lecture_idx=4,
        frames=[render.FrameRef(0, "frames/000000.jpg"), render.FrameRef(80, "frames/000080.jpg")],
        transcript=[{"start": 0, "end": 3, "text": "hello there"}],
    )
    assert "Why tokenize" in out and "Adds to earlier" in out
    assert 'class="mermaid"' in out and "flowchart LR" in out
    assert 'data-latex="x^2"' in out
    assert "<script>alert(1)</script>" not in out  # untrusted markup is escaped
    assert 'data-term="tokenization"' in out  # glossary term linked once
    assert out.count('class="term"') == 1
    assert "Unused" not in out  # glossary terms that never appear are dropped
    assert "Left out of these notes (2)" in out
    assert "frames/000080.jpg" in out  # section at 01:15 points at the nearest earlier frame
    assert "hello there" in out
    assert "Builds on: Lecture 1" in out


def test_nearest_frame_picks_latest_not_after_timestamp():
    frames = [render.FrameRef(0, "a"), render.FrameRef(50, "b"), render.FrameRef(100, "c")]
    assert render.nearest_frame(frames, 70).href == "b"
    assert render.nearest_frame(frames, -5).href == "a"
    assert render.nearest_frame([], 10) is None
