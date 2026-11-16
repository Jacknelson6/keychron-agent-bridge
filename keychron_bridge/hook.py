#!/usr/bin/python3
"""One-way lifecycle publisher. Always exits successfully and prints nothing.

Installed as a standalone file so agent hooks do not need a virtual environment.
Only lifecycle metadata goes to a private local socket. Prompt text, tool input,
transcripts, credentials, and tool output are never forwarded or stored here.
"""

import argparse
import json
import os
import socket
import sys
from pathlib import Path


def failed_response(value):
    if not isinstance(value, dict):
        return False
    if value.get("is_error") is True or value.get("error"):
        return True
    for key in ("exit_code", "exitCode"):
        code = value.get(key)
        if isinstance(code, int) and not isinstance(code, bool) and code != 0:
            return True
    return False


def normalize(agent, value):
    if agent not in ("claude", "codex") or not isinstance(value, dict):
        return None
    sid = value.get("session_id")
    if not isinstance(sid, str) or not 0 < len(sid) <= 160:
        return None
    event = value.get("hook_event_name", "")
    if event == "Notification" and value.get("notification_type") not in (
            "permission_prompt", "elicitation_dialog"):
        return None
    return {"kind": "event", "agent": agent, "session_id": sid,
            "cwd": str(value.get("cwd", ""))[:4096], "event": event,
            "failed": failed_response(value.get("tool_response")) or bool(value.get("error"))
                      or value.get("status") == "failed"}


def publish(message, root=None):
    if message is None:
        return False
    root = root or os.environ.get("KEYCHRON_BRIDGE_HOME") or str(
        Path.home() / "Library/Application Support/Keychron Agent Bridge")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.2)
            connection.connect(str(Path(root) / "control.sock"))
            connection.sendall((json.dumps(message) + "\n").encode())
        return True
    except (OSError, ValueError):
        return False


def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--agent", choices=("claude", "codex"), required=True)
        args = parser.parse_args()
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) <= 1024 * 1024:
            publish(normalize(args.agent, json.loads(raw)))
    except (Exception, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
