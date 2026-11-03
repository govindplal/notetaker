"""The connection to the user's own AI agent (Claude Code first).

Notetaker does not take an API key. Notes are written by the agent CLI the user already pays for,
signed in on this machine. Claude Code is not a background service: it is started per request.
So "make sure the agent is running" means two things here:

* before any work, check that the agent is installed and signed in, and say so plainly if not;
* mark the agent session as started by Notetaker when the worker begins, and tell the user, because
  the agent was not already running. `agent.json` in the data folder holds that state for the page.
"""

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from notetaker.config import Config

NAME = "Claude Code"
COMMAND = "claude"


class AgentUnavailable(RuntimeError):
    """The agent is missing or signed out. The message says what the user should do."""


@dataclass(frozen=True)
class AgentInfo:
    installed: bool
    logged_in: bool
    detail: str


def detect(runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> AgentInfo:
    """Is Claude Code installed, and is it signed in? Costs no model usage."""
    exe = shutil.which(COMMAND)
    if exe is None:
        return AgentInfo(
            False, False, f"{NAME} was not found. Install it from claude.com/claude-code."
        )
    try:
        proc = runner(
            [exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", timeout=30
        )
        data = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError):
        return AgentInfo(True, False, f"{NAME} is installed but its sign-in could not be checked.")
    if data.get("loggedIn"):
        return AgentInfo(True, True, f"{NAME} is signed in ({data.get('authMethod', 'unknown')}).")
    return AgentInfo(True, False, f"{NAME} is not signed in. Run `claude` once and sign in.")


def _state_path(cfg: Config) -> Path:
    return cfg.data_dir / "agent.json"


def read_state(cfg: Config) -> dict:
    try:
        return json.loads(_state_path(cfg).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(cfg: Config, state: dict) -> None:
    _state_path(cfg).write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")


def ensure_agent(
    cfg: Config,
    detector: Callable[[], AgentInfo] = detect,
    now: Callable[[], float] = time.time,
) -> str | None:
    """Make sure the agent is ready and marked running. Returns a notice if Notetaker started it.

    Raises AgentUnavailable when it is missing or signed out. Call this when the worker starts:
    a worker only exists while a run is under way, so a state of "running" left in the file by an
    earlier run is stale, and the agent counts as not running.
    """
    info = detector()
    if not (info.installed and info.logged_in):
        _write_state(cfg, {"agent": NAME, "state": "unavailable", "message": info.detail})
        raise AgentUnavailable(info.detail)
    t = now()
    notice = (
        f"{NAME} was not running, so Notetaker started it at {time.strftime('%H:%M', time.localtime(t))}. "
        "It writes the notes with your own plan."
    )
    _write_state(cfg, {"agent": NAME, "state": "running", "started_at": t, "message": notice})
    return notice


def release_agent(cfg: Config) -> None:
    """The run is over, so the agent is no longer running."""
    state = read_state(cfg)
    if state.get("state") == "running":
        _write_state(cfg, {"agent": NAME, "state": "idle", "message": "No run is under way."})
