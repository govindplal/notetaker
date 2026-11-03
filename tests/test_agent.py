import json
import subprocess

import pytest

from notetaker import agent
from notetaker.config import Config
from notetaker.worker import work


def _cfg(tmp_path):
    return Config(data_dir=tmp_path)


def _ok():
    return agent.AgentInfo(True, True, "ok")


def test_detect_not_installed(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _c: None)
    info = agent.detect()
    assert not info.installed and "not found" in info.detail


def test_detect_logged_in_and_out(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda _c: "claude")

    def runner(out):
        return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")

    assert agent.detect(runner(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))).logged_in
    out = agent.detect(runner(json.dumps({"loggedIn": False})))
    assert out.installed and not out.logged_in
    assert not agent.detect(runner("not json")).logged_in


def test_ensure_agent_starts_and_tells(tmp_path):
    cfg = _cfg(tmp_path)
    notice = agent.ensure_agent(cfg, detector=_ok, now=lambda: 0)
    assert "was not running" in notice
    assert agent.read_state(cfg)["state"] == "running"
    agent.release_agent(cfg)
    assert agent.read_state(cfg)["state"] == "idle"


def test_ensure_agent_unavailable(tmp_path):
    cfg = _cfg(tmp_path)
    bad = lambda: agent.AgentInfo(True, False, "not signed in")  # noqa: E731
    with pytest.raises(agent.AgentUnavailable):
        agent.ensure_agent(cfg, detector=bad)
    assert agent.read_state(cfg)["state"] == "unavailable"


def test_worker_logs_notice_and_releases(tmp_path, tmp_conn=None):
    from notetaker.db import connect

    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    logs = []
    reason = work(
        conn,
        cfg,
        log=logs.append,
        agent=lambda c: agent.ensure_agent(c, detector=_ok, now=lambda: 0),
        agent_release=agent.release_agent,
    )
    assert reason == "finished"
    assert any("was not running" in m for m in logs)
    assert agent.read_state(cfg)["state"] == "idle"


def test_worker_stops_when_agent_unavailable(tmp_path):
    from notetaker.db import connect

    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    logs = []

    def boom(_c):
        raise agent.AgentUnavailable("sign in to Claude Code")

    assert work(conn, cfg, log=logs.append, agent=boom) == "agent_unavailable"
    assert logs == ["sign in to Claude Code"]
