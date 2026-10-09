from pathlib import Path

from notetaker.notes import writer
from notetaker.notes.model import Block, Concept, GlossaryItem, LectureNotes, Section
from notetaker.offline import load_ledger, save_ledger
from notetaker.render import html as render


def _notes() -> LectureNotes:
    return LectureNotes(
        title="Tokenization",
        lead="Text has to become **numbers** before a model can use it. Tokenization is that step.",
        sections=[
            Section(
                heading="Why tokenize",
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
                    Block(type="code", code="ids = tok.encode(text)", language="python"),
                ],
            ),
            Section(
                heading="BPE",
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
    out = render.render_lecture(_notes(), course_title="AI and ML", lecture_idx=4)
    assert "Why tokenize" in out and "adds to earlier" in out
    assert 'class="mermaid"' in out and "flowchart LR" in out
    assert 'data-latex="x^2"' in out
    assert "<script>alert(1)</script>" not in out  # untrusted markup is escaped
    assert 'data-term="tokenization"' in out  # glossary term linked once
    assert 'data-def="Text to ids."' in out  # hover definition
    assert 'id="glossary"' in out and 'class="faq"' in out
    assert out.count('class="term"') == 1
    assert "Unused" not in out  # glossary terms that never appear are dropped
    assert "Left out" not in out and "Greetings" not in out  # audit list is JSON-only
    assert "On this page" in out and 'href="#s1-why-tokenize"' in out
    assert "builds on Lecture 1" in out
    assert "Text has to become" in out  # the lead paragraph
    assert '<pre class="on"><code>ids = tok.encode(text)</code></pre>' in out


def test_render_uses_one_light_palette_and_no_media():
    out = render.render_lecture(_notes(), course_title="AI and ML", lecture_idx=4)
    assert "#FAF9F5" in out and "#D97757" in out  # reference palette
    assert "prefers-color-scheme" not in out and 'data-theme="dark"' not in out
    # frames, transcripts and timestamps are never shown
    for banned in ("<img", "frames/", "Full transcript", "All frames", "data-frame", "<dialog"):
        assert banned not in out


def test_clean_mermaid_rewrites_list_like_labels():
    src = 'graph TD\n User -->|1. Query| App\n App -->|"2. Retrieve"| DB\n A["3. Step"] --> B'
    out = render.clean_mermaid(src)
    assert "|1 - Query|" in out
    assert '|"2 - Retrieve"|' in out
    assert 'A["3 - Step"]' in out
    plain = "graph LR\n A --> B"
    assert render.clean_mermaid(plain) == plain


def test_consecutive_code_blocks_become_tabs():
    blocks = [
        {"type": "text", "html": "<p>x</p>"},
        {"type": "code", "code": "a = 1", "language": "python", "caption": "train.py"},
        {"type": "code", "code": "b = 2", "language": "python", "caption": None},
        {"type": "text", "html": "<p>y</p>"},
        {"type": "code", "code": "c = 3", "language": "", "caption": None},
    ]
    out = render.group_code(blocks)
    assert [b["type"] for b in out] == ["text", "codegroup", "text", "codegroup"]
    assert [t["label"] for t in out[1]["tabs"]] == ["train.py", "python"]
    assert out[3]["tabs"] == [{"label": "code", "code": "c = 3"}]


def test_figure_block_without_a_figure_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        Block(type="figure", caption="no data")


def test_caption_length_is_capped():
    import pytest

    with pytest.raises(ValueError):
        Block(type="text", text="x", caption="word " * 100)


def test_degenerate_repetition_is_detected_but_normal_text_is_not():
    loop = "nicely cleanly safely properly " * 40
    bad = LectureNotes(
        title="T",
        lead="L",
        sections=[
            Section(
                heading="H",
                status="new",
                concept="c",
                summary="s",
                blocks=[Block(type="text", text=loop)],
            )
        ],
    )
    assert writer.degenerate_text(bad) is not None
    ok = LectureNotes(
        title="T",
        lead="L",
        sections=[
            Section(
                heading="H",
                status="new",
                concept="c",
                summary="s",
                blocks=[Block(type="text", text=" ".join(f"word{i}" for i in range(100)))],
            )
        ],
    )
    assert writer.degenerate_text(ok) is None
    assert writer.degenerate_text(_notes()) is None
