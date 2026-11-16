import json
import plistlib
import subprocess
import uuid
from types import SimpleNamespace

import pytest

from keychron_bridge import service, session_recovery as recovery
from keychron_bridge.paths import read_json, write_json


START = "Mon Nov 16 18:37:52 2026"


def record(registry, pid=12, sid=None, **extra):
    registry.mkdir(parents=True, exist_ok=True)
    data = {"pid": pid, "sessionId": sid or str(uuid.UUID(int=pid)), "pidDomain": "darwin",
            "procStart": START, "status": "busy", "name": "private", "cwd": "/private/project"}
    data.update(extra)
    write_json(registry / f"{pid}.json", data)
    return data


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root, registry = tmp_path / "bridge", tmp_path / "registry"
    write_json(root / "runtime.json", {"pid": 4321, "started_at": 100})
    registry.mkdir()
    monkeypatch.setattr(recovery.os, "kill", lambda pid, signal: None)
    sent = []
    return SimpleNamespace(root=root, registry=registry, sent=sent,
                           sender=lambda message, path: sent.append((message, path)) or True)


def test_verified_busy_records_recover_with_allowlisted_metadata_only(rig):
    item = record(rig.registry)
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 1
    assert rig.sent == [({"kind": "event", "agent": "claude", "event": "PreModelRequest",
                         "session_id": item["sessionId"], "source": "claude-live-registry"}, str(rig.root))]
    marker = rig.root / "session-recovery.json"
    assert read_json(marker, {}) == {"pid": 4321, "started_at": 100, "restored": 1}
    assert marker.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("changes", [{"status": "idle"}, {"status": "unknown"}, {"pidDomain": "linux"},
                                     {"sessionId": "not-a-uuid"}, {"pid": True}, {"procStart": None}])
def test_unknown_idle_and_malformed_identity_records_are_ignored(rig, changes):
    record(rig.registry, **changes)
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 0
    assert rig.sent == []


@pytest.mark.parametrize("processes", [{}, {12: "Mon Nov 16 19:00:00 2026"}])
def test_dead_or_reused_pid_cannot_restore_stale_work(rig, processes):
    record(rig.registry)
    assert recovery.recover(rig.root, rig.registry, processes, rig.sender) == 0
    assert rig.sent == []


def test_symlink_oversized_malformed_and_mismatched_filename_records_are_skipped(rig):
    outside = rig.root / "outside.json"
    write_json(outside, record(rig.registry))
    (rig.registry / "12.json").unlink()
    (rig.registry / "12.json").symlink_to(outside)
    (rig.registry / "13.json").write_text("x" * 16385)
    (rig.registry / "14.json").write_text("{invalid")
    write_json(rig.registry / "15.json", {"pid": 99, "pidDomain": "darwin", "status": "busy",
                                         "sessionId": str(uuid.UUID(int=99)), "procStart": START})
    assert recovery.busy_records(rig.registry) == []


def test_ps_uses_batched_numeric_pids_utc_and_command_name_without_arguments(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=f"12 {START} /safe/bin/claude\n30 {START} /safe/bin/other\n", returncode=0)
    monkeypatch.setattr(recovery.subprocess, "run", run)
    assert recovery.live_processes([30, 12, 12]) == {12: START}
    command, options = calls[0]
    assert command == ["/bin/ps", "-p", "12,30", "-o", "pid=,lstart=,comm="]
    assert options["env"]["LC_ALL"] == "C" and options["env"]["TZ"] == "UTC"
    assert options["timeout"] == 5 and options["capture_output"] and options["text"]
    assert recovery.live_processes([]) == {} and len(calls) == 1


def test_newer_stop_hook_wins_over_recovery_seed(rig):
    item = record(rig.registry)
    write_json(rig.root / "sessions.json", [{"agent": "claude", "session_id": item["sessionId"],
                                            "event": "Stop", "status": "done", "updated": 101}])
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 0
    assert rig.sent == []


def test_stop_arriving_during_process_validation_wins_recovery_race(rig):
    item = record(rig.registry)
    class ProcessCheck(dict):
        def get(self, pid, default=None):
            write_json(rig.root / "sessions.json", [{"agent": "claude", "session_id": item["sessionId"],
                                                    "event": "Stop", "status": "done", "updated": 101}])
            return START
    assert recovery.recover(rig.root, rig.registry, ProcessCheck({12: START}), rig.sender) == 0
    assert rig.sent == []


def test_runtime_change_during_recovery_cancels_without_marking_old_start(rig, monkeypatch):
    record(rig.registry)
    original = recovery.read_json
    runtime_reads = []
    def read(path, default):
        if path.name == "runtime.json":
            runtime_reads.append(True)
            if len(runtime_reads) > 1:
                return {"pid": 5432, "started_at": 110}
        return original(path, default)
    monkeypatch.setattr(recovery, "read_json", read)
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 0
    assert rig.sent == [] and not (rig.root / "session-recovery.json").exists()


def test_recovery_is_idempotent_per_runtime_but_new_start_can_recover(rig):
    record(rig.registry)
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 1
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 0
    write_json(rig.root / "runtime.json", {"pid": 4321, "started_at": 110})
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 1
    assert len(rig.sent) == 2


def test_failed_delivery_never_records_success_marker(rig):
    record(rig.registry)
    with pytest.raises(OSError, match="not ready"):
        recovery.recover(rig.root, rig.registry, {12: START}, lambda *args: False)
    assert not (rig.root / "session-recovery.json").exists()
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 1


def test_duplicate_process_records_for_same_session_publish_only_once(rig):
    item = record(rig.registry)
    record(rig.registry, pid=13, sid=item["sessionId"])
    assert recovery.recover(rig.root, rig.registry, {12: START, 13: START}, rig.sender) == 1
    assert len(rig.sent) == 1


def test_dead_bridge_runtime_cannot_receive_recovery(rig, monkeypatch):
    record(rig.registry)
    def dead(*args):
        raise ProcessLookupError()
    monkeypatch.setattr(recovery.os, "kill", dead)
    assert recovery.recover(rig.root, rig.registry, {12: START}, rig.sender) == 0
    assert rig.sent == [] and not (rig.root / "session-recovery.json").exists()


def test_recovery_job_is_event_driven_and_uses_support_python_with_private_settings(tmp_path):
    root = tmp_path / "Application Support/Keyboard Bridge"
    config = service.session_recovery_configuration(root)
    assert config["Label"] == service.SESSION_RECOVERY_LABEL
    assert config["ProgramArguments"] == [str(root / "venv/bin/python"), "-m", "keychron_bridge.session_recovery"]
    assert config["RunAtLoad"] is True and config["WatchPaths"] == [str(root / "runtime.json")]
    assert not set(config) & {"KeepAlive", "StartInterval", "StartCalendarInterval"}
    assert config["Umask"] == 0o77 and config["ProcessType"] == "Background"
    assert config["EnvironmentVariables"] == {"PYTHONPATH": str(root / "python"), "KEYCHRON_BRIDGE_HOME": str(root)}


def test_install_and_remove_recovery_job_touch_only_its_own_launch_agent(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess, "run", run)
    monkeypatch.setattr(service.os, "getuid", lambda: 501)
    directory = tmp_path / "Library/LaunchAgents"
    directory.mkdir(parents=True)
    unrelated = directory / "example.unrelated.plist"
    unrelated.write_text("untouched")
    job = service.install_session_recovery(tmp_path / "support with spaces", home=tmp_path)
    assert plistlib.loads(job.read_bytes())["Label"] == service.SESSION_RECOVERY_LABEL
    assert job.stat().st_mode & 0o777 == 0o600
    assert calls[0][0] == ["/bin/launchctl", "bootout", "gui/501/" + service.SESSION_RECOVERY_LABEL]
    assert calls[1][0] == ["/bin/launchctl", "bootstrap", "gui/501", str(job)]
    assert calls[1][1]["check"] is True
    assert all(options["timeout"] == 15 for _, options in calls)
    service.remove_session_recovery(home=tmp_path)
    assert not job.exists() and unrelated.read_text() == "untouched"
    assert calls[-1][0] == ["/bin/launchctl", "bootout", "gui/501/" + service.SESSION_RECOVERY_LABEL]
