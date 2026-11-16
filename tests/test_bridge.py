import copy
import json
import os
import socket
import threading
import tempfile
import uuid
from pathlib import Path

import pytest

from keychron_bridge.broker import Broker
from keychron_bridge.hook import normalize, publish
from keychron_bridge.integration import configure_hooks, merge_hooks
from keychron_bridge.keyboard import ENCODERS, KEYS, Hardware, Keyboard
from keychron_bridge.paths import write_json
from keychron_bridge.prompts import command_arguments, prepare_task
from keychron_bridge.session_lights import FIRMWARE, SessionLights
from keychron_bridge.state import State


def test_hook_does_not_forward_prompt_tool_output_or_secrets():
    value = {"session_id": "123", "hook_event_name": "PostToolUse", "cwd": "/project",
             "prompt": "private prompt", "tool_input": {"token": "secret"},
             "tool_response": {"exit_code": 2, "stdout": "private result"}}
    result = normalize("codex", value)
    assert result["failed"] is True
    assert set(result) == {"kind", "agent", "session_id", "event", "cwd", "failed"}
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)
    assert normalize("claude", {**value, "hook_event_name": "Notification"}) is None
    assert normalize("codex", {**value, "hook_event_name": "PostModelRequest",
                               "tool_response": {}, "status": "failed", "error": None})["failed"]


def test_event_lifecycle_and_concurrent_agent_attention(tmp_path):
    state = State(tmp_path)
    def event(sid, name, **extra):
        state.update({"agent": "codex", "session_id": sid, "event": name, **extra})
    event("a", "UserPromptSubmit")
    assert state.status() == "working"
    event("a", "PermissionRequest")
    event("b", "UserPromptSubmit")
    assert state.status() == "waiting"
    event("a", "PostToolUse")
    assert state.status() == "working"
    event("a", "Stop")
    event("b", "PostToolUse", failed=True)
    assert state.status() == "failed"
    event("b", "PreToolUse")
    assert state.status() == "working"
    event("b", "Stop")
    assert state.status() == "done"
    state.cycle(1)
    assert state.selected == "claude" and state.status() == "idle"
    assert State(tmp_path).status("codex") == "idle"


def test_hook_merge_preserves_other_settings_and_is_reversible(tmp_path):
    source = {"permissions": {"deny": ["Bash(rm *)"]}, "env": {"EXISTING": "value"},
              "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]}}
    expected = copy.deepcopy(source)
    installed = merge_hooks(source, tmp_path, "claude")
    assert source == expected
    assert installed["permissions"] == source["permissions"]
    assert installed["hooks"]["Stop"][0] == source["hooks"]["Stop"][0]
    assert merge_hooks(installed, tmp_path, "claude") == installed
    assert merge_hooks(installed, tmp_path, "claude", remove=True) == source


def test_hook_backups_do_not_duplicate_user_environment_values(tmp_path):
    home = tmp_path / "home"
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    document = {"env": {"PRIVATE_VALUE": "test-secret-value"}, "permissions": {"deny": ["existing"]}}
    settings.write_text(json.dumps(document))
    root = tmp_path / "bridge"
    root.mkdir()
    configure_hooks(root, home)
    assert json.loads(settings.read_text())["env"] == document["env"]
    for backup in (root / "hook-backups").iterdir():
        assert "test-secret-value" not in backup.read_text()
        assert backup.stat().st_mode & 0o777 == 0o600


def test_local_socket_delivery_and_single_instance():
    with tempfile.TemporaryDirectory(prefix="kab-", dir="/tmp") as directory:
        _check_local_socket(Path(directory))


def _check_local_socket(tmp_path):
    received, ready = [], threading.Event()
    def dispatch(message):
        received.append(message)
        ready.set()
    broker = Broker(tmp_path, dispatch)
    broker.start()
    try:
        assert (tmp_path / "control.sock").stat().st_mode & 0o777 == 0o600
        with pytest.raises(RuntimeError):
            Broker(tmp_path, dispatch)
        assert publish({"kind": "action", "action": "panel"}, str(tmp_path))
        assert ready.wait(1)
        assert received == [{"kind": "action", "action": "panel"}]
    finally:
        broker.close()
    assert not (tmp_path / "control.sock").exists()
    assert not publish({"kind": "event"}, str(tmp_path))


def test_overlong_socket_path_has_a_clear_error(tmp_path):
    root = tmp_path / ("x" * 110)
    root.mkdir()
    with pytest.raises(ValueError, match="too long"):
        Broker(root, lambda message: None)


def test_prompt_is_data_not_shell_and_resume_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr("keychron_bridge.prompts.find_cli", lambda agent: "/bin/echo")
    root = tmp_path / "bridge with spaces"
    root.mkdir()
    prompt = "Review `touch SHOULD_NOT_EXIST` and $(touch ALSO_NOT)\n'quoted'"
    launcher = prepare_task(root, "codex", str(tmp_path), prompt)
    assert prompt not in launcher.read_text()
    task = json.loads(next((root / "tasks").glob("*.json")).read_text())
    assert command_arguments(task) == ["/bin/echo", "-C", str(tmp_path), "--", prompt]
    for agent in ("claude", "codex"):
        assert command_arguments({**task, "agent": agent, "prompt": "--help"})[-2:] == ["--", "--help"]
    assert launcher.stat().st_mode & 0o777 == 0o700
    assert next((root / "tasks").glob("*.json")).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        command_arguments({**task, "resume": "not-a-session-id"})


class FakeHID:
    def __init__(self):
        self.keys = {(l, r, c): [0, 58 + c] for l, r, c, _ in KEYS}
        self.encoders = {(l, d): [0, 120 + d] for l, d, _ in ENCODERS}
        self.lights = {1: [170], 2: [5], 3: [127], 4: [0, 255]}
        self.writes = []
        self.fail_next_write = False
        self.firmware = FIRMWARE
        self.rgb_protocol = [1, 0]
        self.led_count = 81
        self.mixed_info = [2, 5]
        self.led_indices = list(range(16))
        self.regions = [int(index % 3 == 0) for index in range(81)]
        duration = list((5000).to_bytes(4, "little"))
        self.effects = [[effect, region * 80, 255, 127, *duration] * 5
                        for region, effect in enumerate((5, 2))]
        self.fail_vendor_once = None

    def write(self, packet):
        data = list(packet)[1:]
        self.writes.append(data.copy())
        if self.fail_next_write and data[0] == 5:
            self.fail_next_write = False
            raise OSError("Simulated USB disconnect")
        if data[0] == 4:
            data[4:6] = self.keys[tuple(data[1:4])]
        elif data[0] == 5:
            self.keys[tuple(data[1:4])] = data[4:6]
        elif data[0] == 0x14:
            data[4:6] = self.encoders[(data[1], data[3])]
        elif data[0] == 0x15:
            self.encoders[(data[1], data[3])] = data[4:6]
        elif data[0] == 8:
            values = self.lights[data[2]]
            data[3:3 + len(values)] = values
        elif data[0] == 7:
            self.lights[data[2]] = data[3:(5 if data[2] == 4 else 4)]
        elif data[0] == 0xA1:
            value = list(self.firmware.encode() + b"\0")
            data[1:1 + len(value)] = value
        elif data[0] == 0xA8:
            command, payload = data[1], data[2:]
            if command == self.fail_vendor_once:
                self.fail_vendor_once = None
                data[2] = 1
                self.response = data
                return
            data[2] = 0
            if command == 1:
                data[3:5] = self.rgb_protocol
            elif command == 5:
                data[3] = self.led_count
            elif command == 6:
                assert payload[0] == 0 and payload[1:4] == [0, 0x1E, 0]
                data[3:19] = self.led_indices
            elif command == 11:
                data[3:5] = self.mixed_info
            elif command in (12, 13):
                start, count = payload[:2]
                assert count <= (29 if command == 12 else 28) and start + count <= 81
                if command == 12:
                    data[3:3 + count] = self.regions[start:start + count]
                else:
                    assert all(value in (0, 1) for value in payload[2:2 + count])
                    self.regions[start:start + count] = payload[2:2 + count]
            elif command in (14, 15):
                region, start, count = payload[:3]
                assert region in (0, 1) and count <= 3 and start + count <= 5
                if command == 14:
                    data[3:3 + count * 8] = self.effects[region][start * 8:(start + count) * 8]
                else:
                    values = payload[3:3 + count * 8]
                    assert all(values[offset] < 24 for offset in range(0, len(values), 8))
                    self.effects[region][start * 8:(start + count) * 8] = values
            else:
                raise AssertionError(f"Unexpected vendor command {command}")
        self.response = data

    def read(self, size, timeout):
        return self.response

    def close(self):
        pass


def test_keyboard_transaction_and_ephemeral_lighting(tmp_path):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    before = keyboard.snapshot()
    backup = tmp_path / "backup.json"
    keyboard.install(backup)
    assert json.loads(backup.read_text()) == before
    assert all(hid.keys[(l, r, c)] == [code >> 8, code & 255] for l, r, c, code in KEYS)
    assert set(hid.keys) == set((l, r, c) for l, r, c, _ in KEYS)
    for packet in hid.writes:
        if packet[0] == 5:
            assert packet[1] in (1, 3) and packet[2] == 0
            assert packet[3] in (9, 10, 11, 12, 15), "Typing and Bluetooth keys must remain untouched"
    for status in ("idle", "working", "waiting", "done", "failed"):
        keyboard.show_status(status)
    assert not any(packet[0] == 9 for packet in hid.writes), "Status updates must not wear EEPROM"
    keyboard.restore(before, mappings=True)
    assert keyboard.snapshot() == before


def test_keyboard_rollback_and_user_edits_are_preserved(tmp_path):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    before = keyboard.snapshot()
    hid.fail_next_write = True
    with pytest.raises(OSError):
        keyboard.install(tmp_path / "backup.json")
    assert keyboard.snapshot() == before
    keyboard.install(tmp_path / "backup.json")
    hid.keys[(1, 0, 9)] = [0, 99]
    with pytest.raises(OSError, match="outside the bridge"):
        keyboard.install(tmp_path / "backup.json")
    assert hid.keys[(1, 0, 9)] == [0, 99]


def test_short_packet_is_never_accepted():
    class Broken(FakeHID):
        def read(self, size, timeout):
            return [4, 1, 0]
    with pytest.raises(OSError):
        Keyboard(Broken()).key(1, 0, 9)


def test_wrong_column_response_is_rejected():
    class Crossed(FakeHID):
        def read(self, size, timeout):
            result = self.response.copy()
            result[3] = 10
            return result
    with pytest.raises(OSError):
        Keyboard(Crossed()).key(1, 0, 9)


def test_resume_candidates_are_scoped_to_selected_project(tmp_path):
    state = State(tmp_path)
    for sid, project in [("first", str(tmp_path / "one")), ("second", str(tmp_path / "two"))]:
        state.update({"agent": "codex", "session_id": sid, "event": "Stop", "cwd": project})
    assert state.latest(project=str(tmp_path / "one")).session_id == "first"


def test_failed_tool_is_not_an_idle_resumable_session(tmp_path):
    state = State(tmp_path)
    identity = {"agent": "claude", "session_id": "abc", "cwd": str(tmp_path)}
    state.update({**identity, "event": "PostToolUseFailure"})
    assert state.status("claude") == "failed"
    assert not state.latest("claude").can_continue
    state.update({**identity, "event": "StopFailure"})
    assert state.latest("claude").can_continue


@pytest.mark.parametrize("brightness", [0, 1, 17, 50, 51, 52, 170, 255])
def test_status_lighting_caps_original_brightness_and_restores_it(tmp_path, monkeypatch, brightness):
    hid = FakeHID()
    hid.lights[1] = [brightness]
    keyboard = Keyboard(hid)
    before = keyboard.snapshot()
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: keyboard))
    hardware = Hardware(tmp_path, lambda: None)
    hardware.connect()
    try:
        for status in ("working", "waiting", "done", "idle", "failed", "interrupted", "idle"):
            hardware.apply_status(status)
            assert hid.lights[1] == [min(brightness, 51)]
        assert not any(packet[0] == 9 for packet in hid.writes), "Runtime lighting must not save EEPROM"
    finally:
        hardware.close()
    assert keyboard.snapshot()["lighting"] == before["lighting"]


def test_status_lighting_accepts_via_brightness_rounding():
    class RoundedBrightness(FakeHID):
        def write(self, packet):
            super().write(packet)
            if list(packet)[1:5] == [7, 3, 1, 51]:
                self.lights[1] = [50]
    hid = RoundedBrightness()
    keyboard = Keyboard(hid)
    baseline = keyboard.lighting()
    for status in ("working", "waiting", "done", "failed", "idle"):
        keyboard.show_status(status, baseline)
        assert hid.lights[1] == [50]


@pytest.mark.parametrize("reference_brightness,reference_mode,current_brightness,current_mode", [
    (170, 0, 170, 0),
    (170, 0, 170, 5),
    (0, 5, 170, 5),
    (170, 5, 0, 5),
    (170, 5, 170, 0),
])
def test_disabled_lighting_has_no_status_writes(reference_brightness, reference_mode,
                                               current_brightness, current_mode):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    reference = keyboard.lighting()
    reference["1"], reference["2"] = [reference_brightness], [reference_mode]
    hid.lights[1], hid.lights[2] = [current_brightness], [current_mode]
    before = copy.deepcopy(hid.lights)
    hid.writes.clear()
    for status in ("idle", "working", "waiting", "done", "failed", "interrupted"):
        keyboard.show_status(status, reference)
    assert hid.lights == before
    assert not any(packet[0] in (7, 9) for packet in hid.writes)


def test_status_lighting_preserves_manual_dimming_since_backup():
    hid = FakeHID()
    keyboard = Keyboard(hid)
    reference = keyboard.lighting()
    hid.lights[1] = [17]
    for status in ("working", "waiting", "done", "failed", "idle"):
        keyboard.show_status(status, reference)
        assert hid.lights[1] == [17]


@pytest.mark.parametrize("agent", ["claude", "codex"])
@pytest.mark.parametrize("event,status,duration", [("Stop", "done", 3), ("StopFailure", "failed", 6)])
def test_completion_light_expires_without_changing_session_status(tmp_path, monkeypatch, agent,
                                                                event, status, duration):
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: 100)
    state = State(tmp_path)
    state.select(agent)
    state.update({"agent": agent, "session_id": "finished", "event": event})
    assert state.light_feedback(now=100) == (status, duration)
    assert state.light_feedback(now=100 + duration - 0.5) == (status, 0.5)
    for now in (100 + duration, 100 + duration + 60):
        assert state.light_feedback(now=now) == ("idle", None)
        assert state.status() == status
        assert state.summary()["agents"][agent] == status
        assert state.latest().event == event


@pytest.mark.parametrize("completion", ["Stop", "StopFailure"])
def test_expired_completion_does_not_hide_concurrent_attention_or_new_work(tmp_path, monkeypatch,
                                                                        completion):
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path)
    def event(sid, name):
        state.update({"agent": "codex", "session_id": sid, "event": name})
    event("finished", completion)
    clock[0] = 101
    event("active", "UserPromptSubmit")
    assert state.light_feedback(now=200) == ("working", None)
    clock[0] = 102
    event("attention", "PermissionRequest")
    assert state.light_feedback(now=200) == ("waiting", None)
    clock[0] = 103
    event("active", "Stop")
    assert state.light_feedback(now=200) == ("waiting", None)
    clock[0] = 104
    event("attention", "PreToolUse")
    assert state.light_feedback(now=200) == ("working", None)
    clock[0] = 105
    event("attention", "Stop")
    assert state.light_feedback(now=106) == ("done", 2)
    assert state.light_feedback(now=108) == ("idle", None)
    assert state.status() == "done"


def test_claude_idle_reminder_preserves_finished_state_and_attention_notifications(tmp_path,
                                                                                 monkeypatch):
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: 100)
    state = State(tmp_path)
    state.select("claude")
    identity = {"session_id": "claude-session", "cwd": str(tmp_path)}
    state.update(normalize("claude", {**identity, "hook_event_name": "Stop"}))
    reminder = normalize("claude", {**identity, "hook_event_name": "Notification",
                                    "notification_type": "idle_prompt"})
    assert reminder is None
    assert state.status() == "done"
    assert state.light_feedback(now=104) == ("idle", None)
    for notification in ("permission_prompt", "elicitation_dialog"):
        message = normalize("claude", {**identity, "hook_event_name": "Notification",
                                       "notification_type": notification})
        assert message is not None
        state.update(message)
        assert state.light_feedback(now=200) == ("waiting", None)


@pytest.mark.parametrize("background,selected", [("claude", "codex"), ("codex", "claude")])
def test_background_agent_lighting_is_independent_of_prompt_selection(tmp_path, monkeypatch,
                                                                     background, selected):
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path)
    state.select(selected)
    identity = {"agent": background, "session_id": "background"}
    state.update({**identity, "event": "UserPromptSubmit"})
    assert state.selected == selected and state.status() == "idle"
    assert state.light_feedback() == ("working", None)
    assert state.activity_session().agent == background
    state.cycle(1)
    assert state.selected == background
    assert state.light_feedback() == ("working", None)
    state.cycle(1)
    clock[0] = 110
    state.update({**identity, "event": "Stop"})
    assert state.light_feedback(now=111) == ("done", 2)
    state.cycle(1)
    assert state.light_feedback(now=111) == ("done", 2)
    state.cycle(1)
    assert state.light_feedback(now=113) == ("idle", None)
    assert state.status(background) == "done"


def test_cross_agent_attention_takes_priority_over_newer_work(tmp_path, monkeypatch):
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path)
    def event(agent, name):
        state.update({"agent": agent, "session_id": "same-id", "event": name})
    event("claude", "PermissionRequest")
    clock[0] = 101
    event("codex", "UserPromptSubmit")
    assert state.activity_session().agent == "claude"
    assert state.light_feedback() == ("waiting", None)
    clock[0] = 102
    event("codex", "PermissionRequest")
    assert state.activity_session().agent == "codex"
    clock[0] = 103
    event("codex", "PreToolUse")
    assert state.activity_session().agent == "claude"
    assert state.light_feedback() == ("waiting", None)
    clock[0] = 104
    event("claude", "PreToolUse")
    assert state.activity_session().agent == "claude"
    assert state.light_feedback() == ("working", None)


@pytest.mark.parametrize("finishing,working", [("claude", "codex"), ("codex", "claude")])
def test_one_agent_finishing_does_not_mask_other_agent_work(tmp_path, monkeypatch, finishing, working):
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path)
    for agent in (finishing, working):
        state.update({"agent": agent, "session_id": "shared-id", "event": "UserPromptSubmit"})
    clock[0] = 110
    state.update({"agent": finishing, "session_id": "shared-id", "event": "Stop"})
    assert state.activity_session().agent == working
    assert state.light_feedback(now=111) == ("working", None)
    clock[0] = 120
    state.update({"agent": working, "session_id": "shared-id", "event": "Stop"})
    assert state.activity_session().agent == working
    assert state.light_feedback(now=121) == ("done", 2)
    assert state.light_feedback(now=123) == ("idle", None)


@pytest.mark.parametrize("background,selected", [("claude", "codex"), ("codex", "claude")])
@pytest.mark.parametrize("result,status,duration", [("Stop", "done", 3), ("StopFailure", "failed", 6)])
def test_other_agent_start_does_not_replace_completion_or_extend_its_timer(tmp_path, monkeypatch,
                                                                         background, selected,
                                                                         result, status, duration):
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path)
    state.select(selected)
    state.update({"agent": background, "session_id": "result", "event": result})
    clock[0] = 102
    state.update({"agent": selected, "session_id": "new-idle", "event": "SessionStart"})
    assert state.latest().session_id == "new-idle"
    activity = state.activity_session()
    assert (activity.agent, activity.session_id, activity.updated) == (background, "result", 100)
    assert state.light_feedback(now=102) == (status, duration - 2)
    assert state.light_feedback(now=100 + duration) == ("idle", None)
    assert state.status(background) == status


def test_activity_without_work_uses_latest_idle_session(tmp_path, monkeypatch):
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path)
    assert state.activity_session() is None
    assert state.light_feedback() == ("idle", None)
    state.update({"agent": "claude", "session_id": "first", "event": "SessionStart"})
    clock[0] = 101
    state.update({"agent": "codex", "session_id": "second", "event": "SessionStart"})
    assert state.activity_session().session_id == "second"
    assert state.light_feedback() == ("idle", None)


def _lighting_mutation(packet):
    return packet[0] in (7, 9) or packet[:2] in ([0xA8, 2], [0xA8, 13], [0xA8, 15])


def test_session_regions_back_up_before_writing_animate_only_counted_keys_and_restore(tmp_path):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    original = (copy.deepcopy(hid.regions), copy.deepcopy(hid.effects), keyboard.lighting())
    backup = tmp_path / "session-backup.json"
    write = hid.write
    def guarded_write(packet):
        if _lighting_mutation(list(packet)[1:]):
            saved = json.loads(backup.read_text())
            assert saved["regions"] == original[0] and saved["effects"] == original[1]
        write(packet)
    hid.write = guarded_write
    lights = SessionLights(keyboard, backup)
    lights.prepare()
    assert not any(_lighting_mutation(packet) for packet in hid.writes)
    assert backup.stat().st_mode & 0o777 == 0o600
    for count in (0, 1, 2, 3, 4, 7, 2, 0):
        lights.show(count, original[2])
        assert [index for index, region in enumerate(hid.regions) if region] == list(range(9, 9 + min(count, 4)))
        assert hid.lights[2] == [24] and hid.lights[1] == original[2]["1"]
        for region, (mode, hue) in enumerate(((1, 170), (2, 20))):
            assert hid.effects[region][:8] == [mode, hue, 255, 64, 136, 19, 0, 0]
            assert hid.effects[region][8:] == [0] * 32
    lights.restore()
    assert hid.regions == original[0] and hid.effects == original[1]
    assert not any(packet[0] == 9 or packet[:2] == [0xA8, 2] for packet in hid.writes)


@pytest.mark.parametrize("field,value", [
    ("firmware", "v1.1.1 different-build"), ("rgb_protocol", [2, 0]),
    ("led_count", 80), ("mixed_info", [3, 5]), ("mixed_info", [2, 4]),
    ("led_indices", [0] * 16),
])
def test_unsupported_session_profile_is_rejected_without_mutation(tmp_path, field, value):
    hid = FakeHID()
    setattr(hid, field, value)
    with pytest.raises(OSError):
        SessionLights(Keyboard(hid), tmp_path / "backup.json").prepare()
    assert not any(_lighting_mutation(packet) for packet in hid.writes)
    assert not (tmp_path / "backup.json").exists()


@pytest.mark.parametrize("status", [1, 255])
def test_vendor_failure_status_is_not_treated_as_echoed_request(status):
    class Rejected(FakeHID):
        def read(self, size, timeout):
            return [0xA8, 12, status] + [0] * 29
    with pytest.raises(OSError, match="rejected"):
        Keyboard(Rejected()).command([0xA8, 12, 28, 28])


def test_session_mode_readback_failure_is_detected(tmp_path):
    class IgnoredMode(FakeHID):
        def write(self, packet):
            super().write(packet)
            if list(packet)[1:5] == [7, 3, 2, 24]:
                self.lights[2] = [5]
    hid = IgnoredMode()
    keyboard = Keyboard(hid)
    baseline = keyboard.lighting()
    lights = SessionLights(keyboard, tmp_path / "backup.json")
    lights.prepare()
    with pytest.raises(OSError, match="enable session"):
        lights.show(1, baseline)


@pytest.mark.parametrize("failed_command", [13, 15])
def test_hardware_rolls_back_partial_session_lighting_transaction(tmp_path, monkeypatch, failed_command):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    original = (copy.deepcopy(hid.regions), copy.deepcopy(hid.effects), keyboard.lighting())
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: keyboard))
    hid.fail_vendor_once = failed_command
    hardware = Hardware(tmp_path, lambda: None)
    hardware.connect()
    assert hardware.keyboard is None
    assert hid.regions == original[0] and hid.effects == original[1]
    assert keyboard.lighting() == original[2]
    assert "rejected" in hardware.message


def test_hardware_session_count_changes_and_shutdown_restore_all_lighting(tmp_path, monkeypatch):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    original = (copy.deepcopy(hid.regions), copy.deepcopy(hid.effects), keyboard.lighting())
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: keyboard))
    hardware = Hardware(tmp_path, lambda: None)
    hardware.connect()
    try:
        for count in (1, 4, 5, 2, 0):
            hardware.apply_sessions(count)
            assert sum(hid.regions) == min(count, 4)
        hardware.apply_sessions(3)
    finally:
        hardware.close()
    assert hid.regions == original[0] and hid.effects == original[1]
    assert keyboard.lighting() == original[2]


@pytest.mark.parametrize("event,extra", [
    ("PostToolUseFailure", {}), ("PostToolUse", {"failed": True}),
    ("PostModelRequest", {"failed": True}),
])
def test_claude_count_includes_active_work_attention_and_transient_failures_only(tmp_path, event, extra):
    state = State(tmp_path)
    for agent, sid, name in (("claude", "work", "UserPromptSubmit"),
                              ("claude", "attention", "PermissionRequest"),
                              ("codex", "work", "UserPromptSubmit"),
                              ("codex", "attention", "PermissionRequest"),
                              ("claude", "idle", "SessionStart")):
        state.update({"agent": agent, "session_id": sid, "event": name})
    state.update({"agent": "claude", "session_id": "tool", "event": event, **extra})
    assert state.claude_session_count() == 3
    state.cycle(1)
    assert state.claude_session_count() == 3
    assert State(tmp_path).claude_session_count() == 0
    for sid, terminal, expected in (("tool", "StopFailure", 2), ("work", "Stop", 1),
                                    ("attention", "SessionEnd", 0)):
        state.update({"agent": "claude", "session_id": sid, "event": terminal})
        assert state.claude_session_count() == expected


@pytest.mark.parametrize("terminal", ["Stop", "StopFailure", "Interrupt", "SessionEnd"])
def test_claude_terminal_events_remove_session_from_count(tmp_path, terminal):
    state = State(tmp_path)
    state.update({"agent": "claude", "session_id": "active", "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1
    state.update({"agent": "claude", "session_id": "active", "event": terminal})
    assert state.claude_session_count() == 0


def test_invalid_saved_session_effect_is_rejected_before_mutation(tmp_path):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    path = tmp_path / "backup.json"
    lights = SessionLights(keyboard, path)
    lights.prepare()
    backup = json.loads(path.read_text())
    backup["effects"][0][0] = 24
    write_json(path, backup)
    hid.writes.clear()
    with pytest.raises(ValueError, match="saved session lighting effect"):
        SessionLights(keyboard, path).prepare()
    assert not any(_lighting_mutation(packet) for packet in hid.writes)


def test_hardware_profile_rejection_does_not_change_lighting(tmp_path, monkeypatch):
    hid = FakeHID()
    hid.firmware = "unsupported"
    keyboard = Keyboard(hid)
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: keyboard))
    hardware = Hardware(tmp_path, lambda: None)
    hardware.connect()
    assert hardware.keyboard is None
    assert not any(_lighting_mutation(packet) for packet in hid.writes)


@pytest.mark.parametrize("count", [-1, True, 1.5])
def test_invalid_session_counts_are_rejected_before_mutation(tmp_path, count):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    baseline = keyboard.lighting()
    with pytest.raises(ValueError):
        SessionLights(keyboard, tmp_path / "backup.json").show(count, baseline)
    assert not any(_lighting_mutation(packet) for packet in hid.writes)


@pytest.mark.parametrize("prior_brightness", [17, 170])
def test_mixed_mode_entry_restores_prior_brightness_on_first_show(tmp_path, prior_brightness):
    class ModeEntryRestoresBrightness(FakeHID):
        def write(self, packet):
            super().write(packet)
            if list(packet)[1:5] == [7, 3, 2, 24]:
                self.lights[1] = [255]
    hid = ModeEntryRestoresBrightness()
    hid.lights[1] = [prior_brightness]
    keyboard = Keyboard(hid)
    baseline = keyboard.lighting()
    lights = SessionLights(keyboard, tmp_path / "backup.json")
    lights.prepare()
    lights.show(1, baseline)
    assert hid.lights[2] == [24]
    assert hid.lights[1] == [prior_brightness]
    mode_write = next(index for index, packet in enumerate(hid.writes) if packet[:4] == [7, 3, 2, 24])
    assert any(packet[:3] == [7, 3, 1] for packet in hid.writes[mode_write + 1:])


def test_rejected_brightness_rolls_back_native_tables_and_via_lighting(tmp_path, monkeypatch):
    class RejectionOfBrightness(FakeHID):
        def write(self, packet):
            data = list(packet)[1:]
            previous = self.lights[1].copy()
            super().write(packet)
            if data[:4] == [7, 3, 2, 24]:
                self.lights[1] = [255]
            elif data[:3] == [7, 3, 1] and self.lights[2] == [24]:
                self.lights[1] = previous
    hid = RejectionOfBrightness()
    keyboard = Keyboard(hid)
    original = (copy.deepcopy(hid.regions), copy.deepcopy(hid.effects), keyboard.lighting())
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: keyboard))
    hardware = Hardware(tmp_path, lambda: None)
    hardware.connect()
    assert hardware.keyboard is None
    assert "accept indicator brightness" in hardware.message
    assert hid.regions == original[0] and hid.effects == original[1]
    assert keyboard.lighting() == original[2]


@pytest.mark.parametrize("baseline_brightness,current_brightness", [
    (255, 255), (254, 254), (170, 170), (170, 17), (17, 170), (0, 170), (170, 0),
])
def test_session_lights_preserve_normal_lower_and_disabled_brightness(tmp_path, baseline_brightness,
                                                                      current_brightness):
    hid = FakeHID()
    keyboard = Keyboard(hid)
    baseline = keyboard.lighting()
    baseline["1"] = [baseline_brightness]
    hid.lights[1] = [current_brightness]
    lights = SessionLights(keyboard, tmp_path / "backup.json")
    lights.prepare()
    hid.writes.clear()
    for count in (0, 1, 4, 2):
        lights.show(count, baseline)
        if not baseline_brightness or not current_brightness:
            assert not any(_lighting_mutation(packet) for packet in hid.writes)
            assert hid.lights[1] == [current_brightness]
        else:
            assert hid.lights[1] == [min(baseline_brightness, current_brightness)]
            assert hid.lights[2] == [24]


def _desktop_metadata(root, number, parent=None, side_session=True, cli_number=None,
                      previous_cli_number=None):
    local_id = "local_" + str(uuid.UUID(int=number))
    cli_id = str(uuid.UUID(int=number if cli_number is None else cli_number))
    path = root / "organization" / "account" / (local_id + ".json")
    document = {"sessionId": local_id, "cliSessionId": cli_id}
    if previous_cli_number is not None:
        document["lastAssistantCliSessionId"] = str(uuid.UUID(int=previous_cli_number))
    if parent is not None:
        document["spawnedFrom"] = {"sessionId": parent, "sideSession": side_session}
    write_json(path, document)
    return local_id, cli_id


def test_desktop_coordinator_and_three_active_children_count_as_three(tmp_path):
    desktop = tmp_path / "desktop"
    parent, parent_cli = _desktop_metadata(desktop, 1)
    children = [_desktop_metadata(desktop, number, parent) for number in (2, 3, 4)]
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (parent_cli, *(cli for _, cli in children)):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 3


def test_desktop_count_moves_from_children_back_to_active_parent(tmp_path):
    desktop = tmp_path / "desktop"
    parent, parent_cli = _desktop_metadata(desktop, 1)
    children = [_desktop_metadata(desktop, number, parent) for number in (2, 3, 4)]
    state = State(tmp_path / "bridge", desktop_root=desktop)
    assert state.claude_session_count() == 0
    state.update({"agent": "claude", "session_id": parent_cli, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1
    for _, cli in children:
        state.update({"agent": "claude", "session_id": cli, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 3
    for (_, cli), expected in zip(children, (2, 1, 1)):
        state.update({"agent": "claude", "session_id": cli, "event": "Stop"})
        assert state.claude_session_count() == expected


def test_unrelated_desktop_sessions_and_ordinary_forks_remain_independent(tmp_path):
    desktop = tmp_path / "desktop"
    parent, parent_cli = _desktop_metadata(desktop, 1)
    _, fork_cli = _desktop_metadata(desktop, 2, parent, side_session=False)
    unrelated = [_desktop_metadata(desktop, number) for number in (3, 4)]
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (parent_cli, fork_cli, *(cli for _, cli in unrelated)):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 4
    state.update({"agent": "claude", "session_id": fork_cli, "event": "Stop"})
    assert state.claude_session_count() == 3


def test_missing_desktop_metadata_keeps_cli_fallback_and_ignores_codex(tmp_path):
    state = State(tmp_path / "bridge", desktop_root=tmp_path / "missing-desktop")
    for number in (1, 2):
        for agent in ("claude", "codex"):
            state.update({"agent": agent, "session_id": str(uuid.UUID(int=number)),
                          "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 2
    assert len(state.sessions) == 4


@pytest.mark.parametrize("contents", ["{invalid", "[]", "null", '{"sessionId": []}'])
def test_malformed_desktop_metadata_does_not_drop_hook_events(tmp_path, contents):
    desktop = tmp_path / "desktop"
    _, parent_cli = _desktop_metadata(desktop, 1)
    path = desktop / "organization/account" / ("local_" + str(uuid.UUID(int=2)) + ".json")
    path.write_text(contents)
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (parent_cli, str(uuid.UUID(int=2))):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 2
    assert len(state.sessions) == 2
    assert all(session.status == "working" for session in state.sessions.values())


def test_cyclic_desktop_parent_metadata_cannot_hide_all_active_sessions(tmp_path):
    desktop = tmp_path / "desktop"
    first = "local_" + str(uuid.UUID(int=1))
    second = "local_" + str(uuid.UUID(int=2))
    _, first_cli = _desktop_metadata(desktop, 1, second)
    _, second_cli = _desktop_metadata(desktop, 2, first)
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (first_cli, second_cli):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 2


def test_desktop_cli_restart_aliases_count_once_and_old_stop_does_not_stop_current_cli(tmp_path,
                                                                                     monkeypatch):
    desktop = tmp_path / "desktop"
    _, old_cli = _desktop_metadata(desktop, 1)
    clock = [100]
    monkeypatch.setattr("keychron_bridge.state.time.time", lambda: clock[0])
    state = State(tmp_path / "bridge", desktop_root=desktop)
    state.update({"agent": "claude", "session_id": old_cli, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1
    _, current_cli = _desktop_metadata(desktop, 1, cli_number=2, previous_cli_number=1)
    clock[0] = 101
    state.update({"agent": "claude", "session_id": current_cli, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1
    clock[0] = 102
    state.update({"agent": "claude", "session_id": old_cli, "event": "Stop"})
    assert state.claude_session_count() == 1
    clock[0] = 103
    state.update({"agent": "claude", "session_id": current_cli, "event": "Stop"})
    assert state.claude_session_count() == 0


def test_desktop_legacy_cli_alias_deduplicates_without_prior_resolver_cache(tmp_path):
    desktop = tmp_path / "desktop"
    _, current_cli = _desktop_metadata(desktop, 1, cli_number=2, previous_cli_number=1)
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (str(uuid.UUID(int=1)), current_cli):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1


def test_finished_current_desktop_cli_does_not_leave_stale_alias_counted(tmp_path):
    desktop = tmp_path / "desktop"
    _, current_cli = _desktop_metadata(desktop, 1, cli_number=2, previous_cli_number=1)
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (str(uuid.UUID(int=1)), current_cli):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1
    state.update({"agent": "claude", "session_id": current_cli, "event": "Stop"})
    assert state.claude_session_count() == 0


def test_nested_side_session_coordinators_leave_only_active_leaf_tasks(tmp_path):
    desktop = tmp_path / "desktop"
    root, root_cli = _desktop_metadata(desktop, 1)
    child, child_cli = _desktop_metadata(desktop, 2, root)
    _, sibling_cli = _desktop_metadata(desktop, 3, root)
    _, leaf_cli = _desktop_metadata(desktop, 4, child)
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (root_cli, child_cli, sibling_cli, leaf_cli):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 2


def test_desktop_resolver_caches_metadata_and_does_not_read_conversation_jsonl(tmp_path, monkeypatch):
    desktop = tmp_path / "desktop"
    parent, parent_cli = _desktop_metadata(desktop, 1)
    _, child_cli = _desktop_metadata(desktop, 2, parent)
    conversation = desktop / "organization/account/conversation.jsonl"
    conversation.write_text("private conversation content must not be read\n")
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (parent_cli, child_cli, str(uuid.UUID(int=3))):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    opened = []
    original = Path.open
    def record_open(path, *args, **kwargs):
        opened.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", record_open)
    assert state.claude_session_count() == 2
    assert opened
    assert all(path.name.startswith("local_") and path.suffix == ".json" for path in opened)
    opened.clear()
    assert state.claude_session_count() == 2
    assert opened == []


def test_desktop_metadata_arriving_after_first_hook_can_be_resolved_on_new_prompt(tmp_path):
    desktop = tmp_path / "desktop"
    parent, parent_cli = _desktop_metadata(desktop, 1)
    child_cli = str(uuid.UUID(int=2))
    state = State(tmp_path / "bridge", desktop_root=desktop)
    for sid in (parent_cli, child_cli):
        state.update({"agent": "claude", "session_id": sid, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 2
    _desktop_metadata(desktop, 2, parent)
    state.update({"agent": "claude", "session_id": child_cli, "event": "UserPromptSubmit"})
    assert state.claude_session_count() == 1
