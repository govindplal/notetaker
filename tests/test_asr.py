import sys
import types

import numpy as np
import pytest

from notetaker import asr


class _FakeModel:
    """Stands in for faster-whisper's WhisperModel."""

    fail_on = set()  # devices that cannot load
    loaded = []

    def __init__(self, name, device, compute_type):
        if device in _FakeModel.fail_on:
            raise RuntimeError("cublas64_12.dll not found")
        _FakeModel.loaded.append((name, device, compute_type))

    def transcribe(self, samples, vad_filter, beam_size):
        seg = types.SimpleNamespace
        out = [seg(start=0.0, end=2.0, text=" namaste "), seg(start=2.0, end=3.0, text="   ")]
        return iter(out), None


@pytest.fixture
def fake_whisper(monkeypatch):
    _FakeModel.fail_on = set()
    _FakeModel.loaded = []
    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FakeModel)
    )
    return _FakeModel


def test_falls_back_to_cpu_when_the_gpu_cannot_load(fake_whisper):
    fake_whisper.fail_on = {"cuda"}
    t = asr.WhisperTranscriber("large-v3-turbo")
    assert t.device == "cpu"
    assert fake_whisper.loaded == [("large-v3-turbo", "cpu", "int8")]


def test_uses_the_gpu_when_it_can(fake_whisper):
    t = asr.WhisperTranscriber("large-v3-turbo")
    assert t.device == "cuda"


def test_no_device_at_all_is_an_error(fake_whisper):
    fake_whisper.fail_on = {"cuda", "cpu"}
    with pytest.raises(RuntimeError, match="no usable Whisper device"):
        asr.WhisperTranscriber("large-v3-turbo")


def test_segments_are_shifted_to_lecture_time_and_blank_text_is_dropped(fake_whisper):
    t = asr.WhisperTranscriber("large-v3-turbo")
    segs = list(t.transcribe(np.zeros(16000, dtype=np.float32), offset_s=600.0))
    assert segs == [{"start": 600.0, "end": 602.0, "text": "namaste"}]


def test_decode_recording_slows_the_audio_and_cuts_it_to_natural_length(monkeypatch):
    calls = []

    class Done:
        stdout = np.zeros(4, dtype=np.float32).tobytes()

    def fake_run(args, **kwargs):
        calls.append(args)
        return Done()

    monkeypatch.setattr(asr.subprocess, "run", fake_run)
    out = asr.decode_recording(asr.Path("part.webm"), speed=2.0, natural_s=600.0)
    args = calls[0]
    assert "atempo=0.500000" in args
    assert args[args.index("-t") + 1] == "600.000"
    assert args[args.index("-ar") + 1] == "16000" and args[-1] == "-"
    assert out.dtype == np.float32 and len(out) == 4
