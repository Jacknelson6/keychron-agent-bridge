"""Local command-line controls for diagnostics and optional alternate launchers."""

import argparse
import json
import sys

from .hook import publish
from .paths import data_dir
from .state import State


def main():
    parser = argparse.ArgumentParser(prog="keychron-bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("start")
    commands.add_parser("restart")
    action = commands.add_parser("action")
    action.add_argument("name", choices=("review", "explain", "checks", "summary", "previous", "next", "panel", "quit", "allow-bluetooth"))
    event = commands.add_parser("event")
    event.add_argument("--agent", choices=("claude", "codex"), required=True)
    event.add_argument("--event", required=True)
    event.add_argument("--session-id", default="manual-light-test")
    event.add_argument("--cwd", default="")
    args = parser.parse_args()
    if args.command in ("start", "restart"):
        from .service import start_service
        try:
            start_service(restart=args.command == "restart")
        except (OSError, RuntimeError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print("Keychron Agent Bridge " + ("restarted." if args.command == "restart" else "started."))
        return 0
    if args.command == "status":
        # Do not instantiate State here: startup recovery resets in-memory status.
        root = data_dir()
        from .paths import read_json
        print(json.dumps({"config": read_json(root / "config.json", {}),
                          "sessions": read_json(root / "sessions.json", [])}, indent=2))
        return 0
    message = ({"kind": "action", "action": args.name} if args.command == "action" else
               {"kind": "event", "agent": args.agent, "event": args.event,
                "session_id": args.session_id, "cwd": args.cwd})
    if not publish(message):
        print("Keychron Agent Bridge is not running.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
