"""Restore verified Claude work once per bridge start, without polling."""

import math
import os
from pathlib import Path
import subprocess

from .desktop_sessions import valid_uuid
from .hook import publish
from .paths import data_dir, read_json, write_json


def busy_records(registry):
    records = []
    for index, path in enumerate(Path(registry).glob("*.json")):
        if index >= 1024:
            break
        if not path.stem.isdecimal() or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 16384:
                continue
            record = read_json(path, {})
            if (isinstance(record, dict) and type(record.get("pid")) is int
                    and record["pid"] > 1 and record["pid"] == int(path.stem)
                    and record.get("pidDomain") == "darwin" and record.get("status") == "busy"
                    and valid_uuid(record.get("sessionId")) and isinstance(record.get("procStart"), str)):
                records.append(record)
        except OSError:
            continue
    return records


def live_processes(pids):
    if not pids:
        return {}
    environment = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}
    result = subprocess.run(["/bin/ps", "-p", ",".join(str(pid) for pid in sorted(set(pids))),
                             "-o", "pid=,lstart=,comm="], capture_output=True, text=True,
                            timeout=5, env=environment)
    processes = {}
    if len(result.stdout) > 1024 * 1024:
        return processes
    for line in result.stdout.splitlines():
        parts = line.split(None, 6)
        if len(parts) == 7 and parts[0].isdecimal() and Path(parts[6]).name == "claude":
            processes[int(parts[0])] = " ".join(parts[1:6])
    return processes


def recover(root, registry=None, processes=None, sender=publish):
    runtime = read_json(root / "runtime.json", {})
    if not isinstance(runtime, dict):
        return 0
    pid, started = runtime.get("pid"), runtime.get("started_at")
    if (type(pid) is not int or pid <= 1 or type(started) not in (int, float)
            or not math.isfinite(started) or started <= 0):
        return 0
    try:
        os.kill(pid, 0)
    except OSError:
        return 0
    identity = {"pid": pid, "started_at": started}
    previous = read_json(root / "session-recovery.json", {})
    if isinstance(previous, dict) and all(previous.get(key) == value for key, value in identity.items()):
        return 0
    registry = registry or Path.home() / ".claude/sessions"
    records = busy_records(registry)
    processes = live_processes([r["pid"] for r in records]) if processes is None else processes
    current = read_json(root / "runtime.json", {})
    if not isinstance(current, dict) or any(current.get(key) != value for key, value in identity.items()):
        return 0
    restored, seen = 0, set()
    for record in records:
        sid = record["sessionId"]
        if sid in seen or processes.get(record["pid"]) != " ".join(record["procStart"].split()):
            continue
        # Check again immediately before delivery, after process verification.
        # Recovery is a startup seed; a lifecycle hook observed since startup wins.
        saved = read_json(root / "sessions.json", [])
        if isinstance(saved, list) and any(isinstance(s, dict) and s.get("agent") == "claude"
                and s.get("session_id") == sid and type(s.get("updated")) in (int, float)
                and s["updated"] >= started for s in saved):
            continue
        # A verified busy registry record maps to the existing working-state
        # event. It carries no prompt, transcript, arguments, or invented tool data.
        if not sender({"kind": "event", "agent": "claude", "event": "PreModelRequest",
                       "session_id": sid, "source": "claude-live-registry"}, str(root)):
            raise OSError("The bridge was not ready for live-session recovery")
        seen.add(sid)
        restored += 1
    write_json(root / "session-recovery.json", {**identity, "restored": restored})
    return restored


def main():
    print("Restored", recover(data_dir()), "verified busy Claude sessions after bridge startup.")


if __name__ == "__main__":
    main()
