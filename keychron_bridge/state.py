"""Provider-independent lifecycle state. No transcript or prompt is collected."""

import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import read_json, write_json
from .desktop_sessions import DesktopSessions

AGENTS = ("claude", "codex")
LABELS = {"claude": "Claude Code", "codex": "Codex"}
STATUS_LABELS = {"idle": "Idle", "working": "Working", "waiting": "Needs you",
                 "done": "Finished", "failed": "Error", "interrupted": "Stopped"}
LIGHTS = {"idle": (1, 170, 255), "working": (2, 170, 255),
          "waiting": (1, 32, 255), "done": (1, 85, 255),
          "failed": (1, 0, 255), "interrupted": (1, 170, 255)}


@dataclass
class Session:
    agent: str
    session_id: str
    cwd: str
    status: str = "idle"
    updated: float = 0
    event: str = ""

    @property
    def can_continue(self) -> bool:
        return self.status not in ("working", "waiting") and self.event in (
            "SessionStart", "SessionEnd", "Stop", "StopFailure", "Interrupt")


class State:
    def __init__(self, root: Path, desktop_root: Path | None = None):
        self.root = root
        self.desktop = DesktopSessions(desktop_root)
        self.config = {"selected_agent": "codex", "project": "",
                       "keyboard_enabled": True, "lighting_enabled": True}
        saved = read_json(root / "config.json", {})
        if isinstance(saved, dict):
            self.config.update({k: v for k, v in saved.items() if k in self.config})
        if self.config["selected_agent"] not in AGENTS:
            self.config["selected_agent"] = "codex"
        self.sessions: dict[tuple[str, str], Session] = {}
        saved_sessions = read_json(root / "sessions.json", [])
        for item in saved_sessions if isinstance(saved_sessions, list) else []:
            try:
                session = Session(**item)
                if session.agent in AGENTS and isinstance(session.session_id, str):
                    # A restarted bridge cannot know whether an old process is still working.
                    session.status = "idle"
                    self.sessions[(session.agent, session.session_id)] = session
            except (TypeError, ValueError):
                pass

    @property
    def selected(self) -> str:
        return self.config["selected_agent"]

    def select(self, agent: str) -> None:
        if agent not in AGENTS:
            raise ValueError("Unknown agent")
        self.config["selected_agent"] = agent
        self.save_config()

    def cycle(self, step: int) -> None:
        self.select(AGENTS[(AGENTS.index(self.selected) + step) % len(AGENTS)])

    def save_config(self) -> None:
        write_json(self.root / "config.json", self.config)

    def update(self, event: dict) -> None:
        agent, sid = event.get("agent"), event.get("session_id")
        if agent not in AGENTS or not isinstance(sid, str) or not 0 < len(sid) <= 160:
            raise ValueError("Invalid lifecycle identity")
        name = event.get("event", "")
        if agent == "claude" and name in ("SessionStart", "UserPromptSubmit"):
            self.desktop.note_start(sid)
        key = (agent, sid)
        session = self.sessions.get(key, Session(agent, sid, ""))
        cwd = event.get("cwd")
        if isinstance(cwd, str) and len(cwd) <= 4096:
            session.cwd = cwd
        status = {
            "SessionStart": "idle", "SessionEnd": "idle",
            "UserPromptSubmit": "working", "PreToolUse": "working",
            "PreModelRequest": "working", "PostModelRequest": "working",
            "PostToolUse": "working", "PostToolUseFailure": "failed",
            "PermissionRequest": "waiting", "Notification": "waiting",
            "Stop": "done", "StopFailure": "failed", "Interrupt": "interrupted",
            "LaunchFailed": "failed",
        }.get(name)
        if status is None:
            return
        if name in ("PostToolUse", "PostModelRequest") and event.get("failed"):
            status = "failed"
        session.status, session.event, session.updated = status, name, time.time()
        self.sessions[key] = session
        newest = sorted(self.sessions.values(), key=lambda s: s.updated, reverse=True)[:128]
        self.sessions = {(s.agent, s.session_id): s for s in newest}
        write_json(self.root / "sessions.json", [asdict(s) for s in newest])

    def latest(self, agent: str | None = None, project: str | None = None) -> Session | None:
        candidates = [s for s in self.sessions.values() if s.agent == (agent or self.selected)]
        if project:
            matching = []
            for session in candidates:
                try:
                    if session.cwd and Path(session.cwd).resolve() == Path(project).resolve():
                        matching.append(session)
                except (OSError, ValueError):
                    pass
            candidates = matching
        return max(candidates, key=lambda s: s.updated, default=None)

    def status(self, agent: str | None = None) -> str:
        candidates = [s for s in self.sessions.values() if s.agent == (agent or self.selected)]
        # Attention takes priority over other simultaneous sessions.
        for status in ("waiting", "working"):
            if any(s.status == status for s in candidates):
                return status
        latest = self.latest(agent)
        return latest.status if latest else "idle"

    def summary(self) -> dict:
        return {"selected_agent": self.selected, "project": self.config["project"],
                "agents": {a: self.status(a) for a in AGENTS}}

    def light_feedback(self, now=None) -> tuple[str, float | None]:
        """Follow all agents automatically, independent of prompt destination or focus."""
        latest = self.activity_session()
        status = latest.status if latest else "idle"
        duration = {"done": 3, "failed": 6}.get(status)
        if duration is None or latest is None:
            return status, None
        remaining = latest.updated + duration - (time.time() if now is None else now)
        return (status, remaining) if remaining > 0 else ("idle", None)

    def activity_session(self) -> Session | None:
        """Attention, then ongoing work, then the most recent result across sessions."""
        sessions = list(self.sessions.values())
        for status in ("waiting", "working"):
            active = [session for session in sessions if session.status == status]
            if active:
                return max(active, key=lambda session: session.updated)
        results = [session for session in sessions if session.status != "idle"]
        return max(results or sessions, key=lambda session: session.updated, default=None)

    def claude_session_count(self) -> int:
        active = {session.session_id for session in self.sessions.values() if session.agent == "claude" and (
            session.status in ("working", "waiting") or
            session.status == "failed" and session.event in (
                "PostToolUseFailure", "PostToolUse", "PostModelRequest"))}
        inactive = {session.session_id for session in self.sessions.values() if session.agent == "claude"} - active
        return self.desktop.count(active, known_inactive=inactive)
