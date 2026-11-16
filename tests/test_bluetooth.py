import ctypes
import sys
from types import SimpleNamespace

import pytest

from keychron_bridge import bluetooth
from keychron_bridge.keyboard import Hardware, Keyboard
from keychron_bridge.paths import write_json


@pytest.mark.parametrize("caps", [False, True])
@pytest.mark.parametrize("count,expected", [(0, 16), (1, 17), (2, 20), (3, 21),
                                           (4, 24), (5, 24), (1000, 24), (None, 29)])
def test_indicator_byte_has_atomic_count_encoding_and_preserves_caps(count, expected, caps):
    assert bluetooth.indicator_byte(count, caps) == expected | (2 if caps else 0)


@pytest.mark.parametrize("count", [-1, True, False, 1.5, "1", [], {}])
def test_indicator_byte_rejects_invalid_counts(count):
    with pytest.raises(ValueError):
        bluetooth.indicator_byte(count)


@pytest.mark.parametrize("config", [None, {}, {"verified": False, "protocol": 1, "vendor": 0x3434, "product": 0x0913},
                                    {"verified": True, "protocol": True, "vendor": 0x3434, "product": 0x0913},
                                    {"verified": True, "protocol": 2, "vendor": 0x3434, "product": 0x0913},
                                    {"verified": True, "protocol": 1, "vendor": 0, "product": 0x0913},
                                    {"verified": True, "protocol": 1, "vendor": 0x3434, "product": 0}])
def test_provision_gate_rejects_missing_or_wrong_firmware_identity(tmp_path, config):
    if config is not None:
        write_json(tmp_path / "bluetooth-firmware.json", config)
    assert not bluetooth.provisioned(tmp_path)


@pytest.mark.parametrize("caps", [False, True])
def test_output_report_contains_id_and_preserved_caps_without_opening_device(monkeypatch, caps):
    monkeypatch.setitem(sys.modules, "AppKit", SimpleNamespace(
        NSEvent=SimpleNamespace(modifierFlags=lambda: 0x10000 if caps else 0),
        NSEventModifierFlagCapsLock=0x10000))
    keyboard = bluetooth.BluetoothKeyboard.__new__(bluetooth.BluetoothKeyboard)
    keyboard.device = 123
    sent = []
    def send(device, report_type, report_id, payload, length):
        sent.append((device, report_type, report_id, ctypes.string_at(payload, length)))
        return 0
    keyboard.send = send
    keyboard.show(3)
    keyboard.show(None)
    assert sent == [(123, 1, 1, bytes([1, 21 | (2 if caps else 0)])),
                    (123, 1, 1, bytes([1, 29 | (2 if caps else 0)]))]
    keyboard.send = lambda *args: -1
    with pytest.raises(OSError, match="status output failed"):
        keyboard.show(0)


class FakeBluetooth:
    def __init__(self, events):
        self.events = events
        self.sent = []
        self.fail_counts = set()
        self.closed = False

    def show(self, count):
        self.events.append(("bluetooth", count))
        if count in self.fail_counts:
            raise OSError("Bluetooth write failed")
        self.sent.append(count)

    def close(self):
        self.closed = True
        self.events.append(("bluetooth-close",))


class FakeUSB:
    def __init__(self, events):
        self.events = events
        self.closed = False
        self.baseline = {"1": [170], "2": [5], "3": [127], "4": [0, 255]}

    def install(self, path):
        self.events.append(("usb-install",))
        return {"lighting": self.baseline}

    def close(self):
        self.closed = True
        self.events.append(("usb-close",))

    def set_light(self, key, value):
        self.events.append(("usb-set-light", key, value))

    def restore(self, original, mappings=False):
        self.events.append(("usb-restore", mappings))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    events, backends = [], []
    usb = {"available": None}
    def open_usb(cls):
        if usb["available"] is None:
            raise OSError("USB unavailable")
        return usb["available"]
    def open_bluetooth():
        backend = FakeBluetooth(events)
        backends.append(backend)
        return backend
    class SessionLights:
        def __init__(self, keyboard, path):
            self.modified = False
        def prepare(self):
            events.append(("usb-prepare",))
        def show(self, count, baseline):
            events.append(("usb-count", count))
            return True
        def restore(self):
            events.append(("usb-restore-regions",))
    monkeypatch.setattr(Keyboard, "open", classmethod(open_usb))
    monkeypatch.setattr(bluetooth, "BluetoothKeyboard", open_bluetooth)
    monkeypatch.setattr("keychron_bridge.session_lights.SessionLights", SessionLights)
    write_json(tmp_path / "bluetooth-firmware.json", {
        "verified": True, "protocol": 1, "vendor": 0x3434, "product": 0x0913})
    changed = []
    hardware = Hardware(tmp_path, lambda: changed.append(True))
    return SimpleNamespace(root=tmp_path, hardware=hardware, backends=backends, usb=usb,
                           events=events, changed=changed)


def test_usb_failure_falls_back_only_when_custom_firmware_is_provisioned(rig):
    (rig.root / "bluetooth-firmware.json").unlink()
    rig.hardware.connect()
    assert rig.backends == [] and rig.hardware.bluetooth is None
    write_json(rig.root / "bluetooth-firmware.json", {
        "verified": True, "protocol": 1, "vendor": 0x3434, "product": 0x0913})
    rig.hardware.session_count = 3
    rig.hardware.connect()
    assert rig.hardware.keyboard is None
    assert rig.hardware.bluetooth is rig.backends[0]
    assert rig.backends[0].sent == [3]


def test_bluetooth_cache_suppresses_duplicate_counts_but_reconnect_resends(rig):
    rig.hardware.session_count = 2
    rig.hardware.connect()
    first = rig.backends[0]
    rig.hardware.apply_sessions(2)
    assert first.sent == [2]
    rig.hardware.reconnect()
    assert first.closed
    assert len(rig.backends) == 2 and rig.backends[1].sent == [2]
    assert rig.hardware.last_light == ("sessions", 2)


def test_usb_takes_preference_and_disables_existing_bluetooth_overlay(rig):
    rig.hardware.session_count = 2
    rig.hardware.connect()
    first = rig.backends[0]
    rig.usb["available"] = usb = FakeUSB(rig.events)
    rig.hardware.connect()
    assert first.sent == [2, None] and first.closed
    assert rig.hardware.keyboard is usb and rig.hardware.bluetooth is None
    assert ("usb-count", 2) in rig.events and not usb.closed
    assert len(rig.backends) == 1


def test_stale_bluetooth_disable_failure_does_not_discard_usable_usb(rig):
    rig.hardware.connect()
    first = rig.backends[0]
    first.fail_counts.add(None)
    rig.usb["available"] = usb = FakeUSB(rig.events)
    rig.hardware.connect()
    assert rig.hardware.keyboard is usb and not usb.closed
    assert rig.hardware.bluetooth is None and first.closed
    assert len(rig.backends) == 1


def test_usb_removal_falls_back_and_resends_same_count(rig):
    rig.usb["available"] = usb = FakeUSB(rig.events)
    rig.hardware.session_count = 4
    rig.hardware.connect()
    assert rig.backends == []
    rig.usb["available"] = None
    rig.hardware.disconnect()
    assert usb.closed and rig.hardware.keyboard is None
    assert rig.backends[0].sent == [4]


@pytest.mark.parametrize("setting", ["enabled", "lighting_enabled"])
def test_disabled_controls_or_lighting_never_send_counts(rig, setting):
    setattr(rig.hardware, setting, False)
    rig.hardware.connect()
    rig.hardware.apply_sessions(3)
    assert all(not backend.sent for backend in rig.backends)
    setattr(rig.hardware, setting, True)
    rig.hardware.apply_sessions(3)
    assert rig.backends[-1].sent == [3]


def test_disable_and_reenable_restore_then_resend_unchanged_count(rig):
    rig.hardware.session_count = 3
    rig.hardware.connect()
    backend = rig.backends[0]
    rig.hardware.lighting_enabled = False
    rig.hardware.restore_lighting()
    rig.hardware.apply_sessions(3)
    assert backend.sent == [3, None] and rig.hardware.last_light is None
    rig.hardware.lighting_enabled = True
    rig.hardware.apply_sessions(3)
    assert backend.sent == [3, None, 3]
    rig.hardware.close()
    assert backend.sent == [3, None, 3, None] and backend.closed
    assert rig.hardware.bluetooth is None


def test_failed_bluetooth_count_write_closes_backend_and_clears_cache(rig):
    rig.hardware.connect()
    backend = rig.backends[0]
    backend.fail_counts.add(3)
    rig.hardware.apply_sessions(3)
    assert backend.closed and rig.hardware.bluetooth is None
    assert rig.hardware.last_light is None and "write failed" in rig.hardware.message


def test_failed_bluetooth_disable_write_also_closes_backend_and_clears_cache(rig):
    rig.hardware.connect()
    backend = rig.backends[0]
    backend.fail_counts.add(None)
    try:
        rig.hardware.restore_lighting()
    except OSError:
        pass
    assert backend.closed and rig.hardware.bluetooth is None
    assert rig.hardware.last_light is None


def test_close_still_releases_backend_if_disable_packet_fails(rig):
    rig.hardware.connect()
    backend = rig.backends[0]
    backend.fail_counts.add(None)
    rig.hardware.close()
    assert backend.closed and rig.hardware.bluetooth is None


@pytest.mark.parametrize("open_status", [0, -536870174, 0xe00002e2, -1])
def test_bluetooth_discovery_uses_independent_devices_and_opens_only_selected_output(monkeypatch, open_status):
    calls = []
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSDictionary=SimpleNamespace(dictionaryWithDictionary_=lambda value: value)))
    monkeypatch.setitem(sys.modules, "objc", SimpleNamespace(pyobjc_id=lambda value: 200))
    monkeypatch.setattr(bluetooth.C, "CDLL", lambda path: object())
    def bind(library, name, arguments, result):
        assert name not in ("IOHIDManagerOpen", "IOHIDManagerClose")
        def invoke(*values):
            calls.append((name, values))
            if name == "IOHIDManagerCreate":
                return 100
            if name == "IOHIDManagerCopyDevices":
                return 300
            if name == "CFSetGetCount":
                return 1
            if name == "CFSetGetValues":
                ctypes.cast(values[1], ctypes.POINTER(ctypes.c_void_p))[0] = 400
            if name == "IOHIDDeviceOpen":
                return open_status
            return 0
        return invoke
    monkeypatch.setattr(bluetooth, "bind", bind)
    if open_status:
        expected = PermissionError if open_status in (-536870174, 0xe00002e2) else OSError
        with pytest.raises(expected) as rejected:
            bluetooth.BluetoothKeyboard()
        assert type(rejected.value) is expected
        if expected is PermissionError:
            assert "Privacy & Security > Input Monitoring" in str(rejected.value)
        assert ("IOHIDDeviceOpen", (400, 0)) in calls
        assert not any(call[0] == "IOHIDDeviceClose" for call in calls)
        assert [call for call in calls if call[0] == "CFRelease"] == [("CFRelease", (300,)), ("CFRelease", (100,))]
        return
    keyboard = bluetooth.BluetoothKeyboard()
    assert ("IOHIDManagerCreate", (None, 8)) in calls
    assert ("IOHIDDeviceOpen", (400, 0)) in calls
    assert keyboard.opened and keyboard.device == 400
    assert keyboard.match["Transport"] == "Bluetooth Low Energy"
    assert keyboard.match["VendorID"] == 0x3434 and keyboard.match["ProductID"] == 0x0913
    keyboard.close()
    keyboard.close()
    assert [call for call in calls if call[0] == "IOHIDDeviceClose"] == [("IOHIDDeviceClose", (400, 0))]
    assert [call for call in calls if call[0] == "CFRelease"] == [("CFRelease", (300,)), ("CFRelease", (100,))]


@pytest.mark.parametrize("granted", [False, True])
def test_public_permission_helper_only_requests_listen_access_without_input_tap(monkeypatch, granted):
    from keychron_bridge import native
    calls = []
    def load(path):
        calls.append(path)
        return "fake-graphics"
    def bind(library, name, arguments, result):
        assert library == "fake-graphics" and name == "CGRequestListenEventAccess"
        assert arguments == [] and result is ctypes.c_bool
        return lambda: calls.append(name) or granted
    monkeypatch.setattr(native.C, "CDLL", load)
    monkeypatch.setattr(native, "bind", bind)
    assert native.request_keyboard_access() is granted
    assert calls == ["/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics", "CGRequestListenEventAccess"]


def test_explicit_access_cli_action_publishes_to_native_app_without_requesting_itself(monkeypatch):
    from keychron_bridge import cli
    messages = []
    monkeypatch.setattr(sys, "argv", ["keychron-bridge", "action", "allow-bluetooth"])
    monkeypatch.setattr(cli, "publish", lambda message: messages.append(message) or True)
    assert cli.main() == 0
    assert messages == [{"kind": "action", "action": "allow-bluetooth"}]
