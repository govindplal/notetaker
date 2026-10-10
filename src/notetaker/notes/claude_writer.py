"""Notes via the Claude Code CLI in headless mode (`claude -p`), on the owner's Claude subscription.

The prompt goes in on stdin: a long lecture's transcript is too big for a command line on Windows.
Frames are image files that Claude views with its Read tool, and that tool is the only tool
allowed. The JSON schema is passed to the CLI, which returns the validated object as
`structured_output`.
"""

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

CLAUDE_TIMEOUT_S = 1800  # a long lecture with many frames can take a while
USAGE_LIMIT_RETRY_S = 3600  # when the plan's usage is used up, wait an hour before trying again
INSTRUCTION = "Follow the instructions on stdin exactly, and answer only with the structured JSON."


class ClaudeError(RuntimeError):
    pass


class UsageLimited(ClaudeError):
    """The Claude plan's usage is used up. The lecture waits; nothing is lost."""

    def __init__(self, message: str, retry_after_s: float = USAGE_LIMIT_RETRY_S):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def _looks_like_limit(text: str) -> bool:
    low = text.lower()
    return "usage limit" in low or "rate limit" in low or "limit reached" in low


class ClaudeWriter:
    def __init__(
        self,
        model: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.model = model
        self._run = runner

    def generate(self, prompt: str, frames: list[Path], schema: dict) -> dict:
        cmd = [
            "claude",
            "-p",
            INSTRUCTION,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--no-session-persistence",
            # Frames are read with Read; without frames Claude gets no tools at all.
            "--tools",
            "Read" if frames else "",
        ]
        for folder in sorted({str(p.parent) for p in frames}):
            cmd += ["--add-dir", folder]
        if self.model:
            cmd += ["--model", self.model]
        proc = self._run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=CLAUDE_TIMEOUT_S,
        )
        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, TypeError):
            msg = f"claude exited {proc.returncode} without JSON: {(proc.stderr or '')[:300]}"
            if _looks_like_limit(proc.stderr or ""):
                raise UsageLimited(msg) from None
            raise ClaudeError(msg) from None
        if data.get("is_error") or proc.returncode != 0:
            text = str(data.get("result") or proc.stderr or "")
            if _looks_like_limit(text):
                raise UsageLimited(text[:300])
            raise ClaudeError(text[:300])
        if data.get("structured_output") is not None:
            return data["structured_output"]
        return json.loads(data["result"])  # no structured output: parse the text (may raise)
