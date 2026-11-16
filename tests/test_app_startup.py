import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


class ProcessExit(BaseException):
    def __init__(self, code):
        self.code = code


@pytest.fixture
def startup(tmp_path, monkeypatch):
    events = []
    center = SimpleNamespace(addObserver_selector_name_object_=lambda *args: events.append("observe-wake"))
    status_bar = SimpleNamespace(statusItemWithLength_=lambda value: events.append("status-item") or object())
    workspace = SimpleNamespace(notificationCenter=lambda: center,
                                openURL_=lambda url: events.append(("settings-url", url)))
    appkit = SimpleNamespace(
        NSStatusBar=SimpleNamespace(systemStatusBar=lambda: status_bar), NSVariableStatusItemLength=-1,
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
        NSWorkspaceDidWakeNotification="wake-notification")
    helper = SimpleNamespace(callAfter=lambda *args: None)
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", SimpleNamespace(
        NSObject=object, NSURL=SimpleNamespace(URLWithString_=lambda value: value)))
    monkeypatch.setitem(sys.modules, "objc", SimpleNamespace(python_method=lambda function: function))
    monkeypatch.setitem(sys.modules, "PyObjCTools", SimpleNamespace(AppHelper=helper))
    path = Path(__file__).resolve().parents[1] / "keychron_bridge/app.py"
    spec = importlib.util.spec_from_file_location("keychron_bridge._startup_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def unexpected_permission_request():
        raise AssertionError("Startup must not request keyboard permission automatically")
    monkeypatch.setattr(module, "request_keyboard_access", unexpected_permission_request)
    def exit_process(code):
        events.append(("exit", code))
        raise ProcessExit(code)
    monkeypatch.setattr(module, "os", SimpleNamespace(
        _exit=exit_process, getpid=lambda: 4321, environ={"KEYCHRON_BRIDGE_MANAGED": "1"}))
    monkeypatch.setattr(module, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(module, "State", lambda root: SimpleNamespace(
        config={"keyboard_enabled": True, "lighting_enabled": True}))
    class Worker:
        def __init__(self, **kwargs):
            self.submitted = []
        def submit(self, function, *args):
            self.submitted.append((function, args))
            events.append("submit")
    monkeypatch.setattr(module, "ThreadPoolExecutor", Worker)
    monkeypatch.setattr(module, "Hardware", lambda root, changed: SimpleNamespace(connect=lambda: None,
                                                                                  reconnect=lambda: None))
    class Broker:
        def __init__(self, root, dispatch):
            events.append("broker-created")
        def start(self):
            events.append("broker-started")
    monkeypatch.setattr(module, "Broker", Broker)
    monkeypatch.setattr(module, "Hotkeys", lambda callback: events.append("hotkeys") or
                        SimpleNamespace(references=list(range(14))))
    monkeypatch.setattr(module, "USBNotifications", lambda *callbacks: events.append("device-notifications"))
    application = module.BridgeApp()
    application.refresh = lambda: events.append("refresh")
    return SimpleNamespace(module=module, app=application, events=events, root=tmp_path)


def test_successful_startup_records_readiness_only_after_native_initialization(startup):
    startup.app.applicationDidFinishLaunching_(None)
    assert startup.events == ["broker-created", "broker-started", "status-item", "hotkeys",
                              "device-notifications", "observe-wake", "refresh", "submit"]
    runtime = json.loads((startup.root / "runtime.json").read_text())
    assert runtime["pid"] == 4321 and runtime["shortcuts"] == 14 and runtime["managed"] is True
    assert runtime["started_at"] > 0
    assert len(startup.app.worker.submitted) == 1
    assert startup.app.worker.submitted[0][0] is startup.app.hardware.connect


def test_singleton_lock_contention_exits_cleanly_before_registering_native_controls(startup,
                                                                                  monkeypatch):
    def locked(root, dispatch):
        raise RuntimeError("Keychron Agent Bridge is already running")
    monkeypatch.setattr(startup.module, "Broker", locked)
    with pytest.raises(ProcessExit) as stopped:
        startup.app.applicationDidFinishLaunching_(None)
    assert stopped.value.code == 0
    assert startup.events == [("exit", 0)]
    assert not (startup.root / "runtime.json").exists()
    assert startup.app.worker.submitted == []


@pytest.mark.parametrize("stage", ["data_dir", "USBNotifications"])
def test_unexpected_startup_failure_exits_nonzero_for_launchd_recovery(startup, monkeypatch, caplog, stage):
    def fail(*args):
        raise OSError("Synthetic startup failure")
    monkeypatch.setattr(startup.module, stage, fail)
    with pytest.raises(ProcessExit) as stopped:
        startup.app.applicationDidFinishLaunching_(None)
    assert stopped.value.code == 1
    assert startup.events[-1] == ("exit", 1)
    assert "Bridge startup failed" in caplog.text
    assert "Synthetic startup failure" in caplog.text
    assert not (startup.root / "runtime.json").exists()


def test_unexpected_broker_error_is_not_misclassified_as_another_running_instance(startup, monkeypatch):
    def fail(root, dispatch):
        raise PermissionError("Cannot bind private socket")
    monkeypatch.setattr(startup.module, "Broker", fail)
    with pytest.raises(ProcessExit) as stopped:
        startup.app.applicationDidFinishLaunching_(None)
    assert stopped.value.code == 1
    assert startup.events == [("exit", 1)]
    assert not (startup.root / "runtime.json").exists()


@pytest.mark.parametrize("granted", [False, True])
def test_explicit_bluetooth_access_action_requests_permission_then_reconnects_or_opens_settings(startup,
                                                                                             monkeypatch,
                                                                                             granted):
    startup.app.applicationDidFinishLaunching_(None)
    startup.events.clear()
    def request():
        startup.events.append("request-access")
        return granted
    monkeypatch.setattr(startup.module, "request_keyboard_access", request)
    startup.app.action("allow-bluetooth")
    if granted:
        assert startup.events == ["request-access", "submit"]
        assert startup.app.worker.submitted[-1][0] is startup.app.hardware.reconnect
    else:
        assert startup.events == ["request-access", ("settings-url",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent")]
        assert len(startup.app.worker.submitted) == 1
