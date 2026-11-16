"""Add and remove only this application's hook handlers."""

import copy
import fcntl
import json
import signal
import shlex
import time
from pathlib import Path

from .paths import write_json
from .hook import publish

COMMON = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
          "PermissionRequest", "PostToolUse", "Stop"]
EVENTS = {"claude": COMMON + ["PostToolUseFailure", "StopFailure", "Notification"],
          "codex": COMMON + ["PreModelRequest", "PostModelRequest", "Interrupt"]}


def hook_command(root: Path, agent: str) -> str:
    return shlex.join(["/usr/bin/python3", str(root / "keychron-agent-hook.py"), "--agent", agent])


def merge_hooks(document: dict, root: Path, agent: str, remove=False) -> dict:
    result = copy.deepcopy(document)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Existing hooks must be an object")
    command = hook_command(root, agent)
    for event in EVENTS[agent]:
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"Existing {event} hooks must be a list")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"An existing {event} hook has an unsupported structure")
            remaining = [handler for handler in group["hooks"]
                         if handler.get("command") != command]
            if remaining:
                kept.append({**group, "hooks": remaining})
        if not remove:
            group = {"hooks": [{"type": "command", "command": command, "timeout": 1}]}
            if event == "Notification":
                group["matcher"] = "permission_prompt|idle_prompt|elicitation_dialog"
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if not hooks:
        result.pop("hooks", None)
    return result


def configure_hooks(root: Path, home: Path | None = None, remove=False):
    home = home or Path.home()
    paths = {"claude": home / ".claude/settings.json", "codex": home / ".codex/hooks.json"}
    prepared = []
    for agent, path in paths.items():
        raw = path.read_text() if path.exists() else "{}"
        document = json.loads(raw)
        updated = merge_hooks(document, root, agent, remove)
        if updated != document:
            prepared.append((path, raw, updated))
    # Validate both files before changing either one.
    backups = root / "hook-backups"
    backups.mkdir(exist_ok=True, mode=0o700)
    for path, raw, updated in prepared:
        name = ("claude-settings" if path.name == "settings.json" else "codex-hooks")
        backup = backups / f"{name}-{time.time_ns()}.json"
        original = json.loads(raw)
        # Only hook definitions need a backup. Do not duplicate unrelated user
        # environment values, provider configuration, or credentials.
        write_json(backup, {"path": str(path), "had_hooks": "hooks" in original,
                            "hooks": original.get("hooks", {})})
        write_json(path, updated)
    return [str(path) for path, _, _ in prepared]


def stop_bridge(root: Path, timeout: int = 8):
    """Return the held exclusive app lock after a bounded, non-polling shutdown."""
    lock = open(root / "bridge.lock", "a+")
    publish({"kind": "action", "action": "quit"}, str(root))
    def expired(signum, frame):
        raise TimeoutError("Quit Keychron Agent Bridge from its menu, then try again")
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(timeout)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return lock
    except Exception:
        lock.close()
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
