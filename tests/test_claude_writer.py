import json
import subprocess
from pathlib import Path

import pytest

from notetaker.notes.claude_writer import ClaudeError, ClaudeWriter, UsageLimited
from notetaker.notes.model import Block, Concept, GlossaryItem, LectureNotes, Section
from notetaker.notes.writer import merge_notes, write_lecture_notes

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}}


def _proc(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class _Runner:
    """Records each call and answers with one canned process result."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return self.result


def test_prompt_goes_on_stdin_and_only_frame_folders_are_readable(tmp_path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frame = frames_dir / "a.jpg"
    frame.write_bytes(b"j")
    body = json.dumps({"structured_output": {"title": "T"}, "is_error": False})
    runner = _Runner(_proc(stdout=body))
    out = ClaudeWriter(runner=runner).generate("the prompt", [frame], SCHEMA)
    assert out == {"title": "T"}
    cmd, kwargs = runner.calls[0]
    assert kwargs["input"] == "the prompt"  # long transcripts never go on the command line
    assert cmd[cmd.index("--tools") + 1] == "Read"
    assert cmd[cmd.index("--add-dir") + 1] == str(frames_dir)
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert "--no-session-persistence" in cmd


def test_without_frames_claude_gets_no_tools(tmp_path):
    runner = _Runner(_proc(stdout=json.dumps({"structured_output": {"title": "T"}})))
    ClaudeWriter(runner=runner).generate("p", [], SCHEMA)
    cmd, _ = runner.calls[0]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--add-dir" not in cmd


def test_falls_back_to_parsing_the_text_result():
    body = json.dumps({"result": '{"title": "from text"}'})
    assert ClaudeWriter(runner=_Runner(_proc(stdout=body))).generate("p", [], SCHEMA) == {
        "title": "from text"
    }


def test_a_usage_limit_is_its_own_error_so_the_work_can_wait():
    body = json.dumps({"is_error": True, "result": "Claude usage limit reached. Resets at 9pm."})
    with pytest.raises(UsageLimited) as info:
        ClaudeWriter(runner=_Runner(_proc(stdout=body, code=1))).generate("p", [], SCHEMA)
    assert info.value.retry_after_s == 3600


def test_other_failures_are_plain_errors():
    body = json.dumps({"is_error": True, "result": "invalid API key"})
    with pytest.raises(ClaudeError) as info:
        ClaudeWriter(runner=_Runner(_proc(stdout=body, code=1))).generate("p", [], SCHEMA)
    assert not isinstance(info.value, UsageLimited)


def test_output_that_is_not_json_is_an_error():
    with pytest.raises(ClaudeError):
        ClaudeWriter(runner=_Runner(_proc(stdout="oops", stderr="crash", code=2))).generate(
            "p", [], SCHEMA
        )


def test_the_model_option_is_passed_through_when_set():
    runner = _Runner(_proc(stdout=json.dumps({"structured_output": {"title": "T"}})))
    ClaudeWriter(model="sonnet", runner=runner).generate("p", [], SCHEMA)
    cmd, _ = runner.calls[0]
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def _section(heading):
    return Section(
        heading=heading,
        status="new",
        concept=heading,
        summary="s",
        key_points=["k"],
        blocks=[Block(type="text", text="b")],
    )


class _Writer:
    """Returns canned notes, and records the prompt it was given."""

    def __init__(self, notes_list):
        self.notes = list(notes_list)
        self.prompts = []

    def generate(self, prompt, frames, schema):
        self.prompts.append(prompt)
        return self.notes.pop(0).model_dump()


def test_a_later_part_is_written_as_new_sections_only_and_merged_in():
    first = LectureNotes(
        title="Intro",
        lead="First part.",
        sections=[_section("Tokens")],
        glossary=[GlossaryItem(term="Token", definition="d")],
        left_out=["hi"],
    )
    second = LectureNotes(title="Intro", lead="ignored", sections=[_section("Embeddings")],
                          glossary=[GlossaryItem(term="token", definition="dup"),
                                    GlossaryItem(term="Vector", definition="v")],
                          left_out=["bye"])  # fmt: skip
    writer = _Writer([second])
    part = write_lecture_notes(
        writer,
        course="C",
        lecture_idx=2,
        title="Intro",
        segments=[{"start": 600.0, "end": 605.0, "text": "embeddings are vectors"}],
        frames=[],
        ledger=[Concept(id="L1-c1", lecture_idx=1, title="Tokens", summary="s", key_points=[])],
        earlier=first,
    )
    assert "EXISTING SECTIONS" in writer.prompts[0] and "- Tokens (Tokens)" in writer.prompts[0]
    assert "[10:00] embeddings are vectors" in writer.prompts[0]
    merged = merge_notes(first, part)
    assert [s.heading for s in merged.sections] == ["Tokens", "Embeddings"]
    assert [g.term for g in merged.glossary] == ["Token", "Vector"]  # duplicate term kept once
    assert merged.left_out == ["hi", "bye"] and merged.lead == "First part."


def test_a_first_write_has_no_continuation_block():
    writer = _Writer([LectureNotes(title="T", lead="Lead.", sections=[_section("A")])])
    write_lecture_notes(writer, course="C", lecture_idx=1, title="T",
                        segments=[{"start": 0.0, "end": 1.0, "text": "x"}], frames=[],
                        ledger=[])  # fmt: skip
    assert "EXISTING SECTIONS" not in writer.prompts[0]


def test_frames_are_listed_by_path_and_timestamp(tmp_path):
    f = tmp_path / "f.jpg"
    f.write_bytes(b"j")
    writer = _Writer([LectureNotes(title="T", lead="Lead.", sections=[_section("A")])])
    write_lecture_notes(writer, course="C", lecture_idx=1, title="T",
                        segments=[{"start": 0.0, "end": 1.0, "text": "x"}],
                        frames=[(65.0, f)], ledger=[])  # fmt: skip
    assert f"Frame at 01:05: {f}" in writer.prompts[0]
    assert isinstance(f, Path)
