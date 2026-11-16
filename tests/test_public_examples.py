import copy
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location("public_example_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_dry_run_prints_plan_without_creating_or_installing(tmp_path, monkeypatch, capsys, existing):
    module = load("scripts/bootstrap.py")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--dry-run"])
    monkeypatch.setattr(module, "sys", SimpleNamespace(executable="python3.12", platform="linux", version_info=(3, 12)))
    python = tmp_path / "Library/Application Support/Keychron Agent Bridge/venv/bin/python"
    if existing:
        python.parent.mkdir(parents=True)
        python.write_text("existing interpreter")
    def forbidden(*args, **kwargs):
        raise AssertionError("Dry run must not execute installation commands")
    monkeypatch.setattr(module.subprocess, "run", forbidden)
    module.main()
    assert python.exists() is existing
    output = capsys.readouterr().out
    assert "pip install -r" in output and "requirements.lock" in output
    assert ("-m venv" in output) is not existing


@pytest.mark.parametrize("platform,version", [("linux", (3, 12)), ("win32", (3, 12)),
                                             ("darwin", (3, 11)), ("darwin", (3, 13))])
def test_bootstrap_rejects_wrong_os_or_python_before_installation(tmp_path, monkeypatch, platform, version):
    module = load("scripts/bootstrap.py")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap"])
    monkeypatch.setattr(module, "sys", SimpleNamespace(executable="python", platform=platform, version_info=version))
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("Installation on unsupported host"))
    with pytest.raises(SystemExit, match="macOS with python3.12"):
        module.main()
    assert not (tmp_path / "Library").exists()


def test_bootstrap_prepares_only_dependencies_without_installing_app_hooks_or_services(tmp_path, monkeypatch):
    module = load("scripts/bootstrap.py")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap"])
    monkeypatch.setattr(module, "sys", SimpleNamespace(executable="python3.12", platform="darwin", version_info=(3, 12)))
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    module.main()
    python = tmp_path / "Library/Application Support/Keychron Agent Bridge/venv/bin/python"
    assert calls == [(["python3.12", "-m", "venv", str(python.parent.parent)], {"check": True}),
                     ([str(python), "-m", "pip", "install", "-r", str(module.ROOT / "requirements.lock")], {"check": True})]


class PreviewKeyboard:
    def __init__(self, firmware):
        self.firmware = firmware
        self.original = {"1": [170], "2": [5], "3": [127], "4": [110, 220]}
        self.current = copy.deepcopy(self.original)
        self.writes = []
        self.closed = False
        self.fail_preview_key = None

    def command(self, packet):
        assert packet == [0xa1]
        return [0xa1, *self.firmware.encode(), 0]

    def lighting(self):
        return copy.deepcopy(self.current)

    def set_light(self, key, values):
        self.writes.append((key, list(values)))
        if key == self.fail_preview_key:
            self.fail_preview_key = None
            raise OSError("Simulated preview write failure")
        self.current[str(key)] = list(values)

    def close(self):
        self.closed = True


@pytest.fixture
def preview(monkeypatch):
    from keychron_bridge.keyboard import Keyboard
    from keychron_bridge.session_lights import FIRMWARE
    module = load("examples/custom_visual.py")
    keyboard = PreviewKeyboard(FIRMWARE)
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: keyboard))
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    return module, keyboard


def test_palette_preview_without_usb_never_opens_hardware(preview, monkeypatch, capsys):
    from keychron_bridge.keyboard import Keyboard
    module, keyboard = preview
    monkeypatch.setattr(sys, "argv", ["preview", "--agent", "codex"])
    monkeypatch.setattr(Keyboard, "open", classmethod(lambda cls: pytest.fail("Preview accessed hardware")))
    module.main()
    assert "mode" in capsys.readouterr().out
    assert keyboard.writes == []


def test_usb_preview_restores_effect_and_color_without_brightness_or_eeprom_writes(preview, monkeypatch):
    module, keyboard = preview
    monkeypatch.setattr(sys, "argv", ["preview", "--usb"])
    module.main()
    assert keyboard.closed and keyboard.current == keyboard.original
    assert keyboard.writes and all(key in (2, 4) for key, values in keyboard.writes)
    assert keyboard.writes[-2:] == [(2, keyboard.original["2"]), (4, keyboard.original["4"])]


@pytest.mark.parametrize("key", [2, 4])
def test_preview_restores_original_lighting_after_write_failure(preview, monkeypatch, key):
    module, keyboard = preview
    keyboard.fail_preview_key = key
    monkeypatch.setattr(sys, "argv", ["preview", "--usb"])
    with pytest.raises(OSError, match="preview write failure"):
        module.main()
    assert keyboard.closed and keyboard.current == keyboard.original
    assert all(key in (2, 4) for key, values in keyboard.writes)


@pytest.mark.parametrize("disabled_key", ["1", "2"])
def test_usb_preview_leaves_disabled_lights_completely_untouched(preview, monkeypatch, disabled_key):
    module, keyboard = preview
    keyboard.current[disabled_key] = [0]
    before = copy.deepcopy(keyboard.current)
    monkeypatch.setattr(sys, "argv", ["preview", "--usb"])
    module.main()
    assert keyboard.closed and keyboard.writes == [] and keyboard.current == before


def test_usb_preview_rejects_unsupported_firmware_before_any_mutation(preview, monkeypatch):
    module, keyboard = preview
    keyboard.firmware = "unverified firmware"
    monkeypatch.setattr(sys, "argv", ["preview", "--usb"])
    with pytest.raises(OSError, match="verified V1 Max"):
        module.main()
    assert keyboard.closed and keyboard.writes == []


def test_preview_attempts_color_restore_after_mode_restore_fails_then_closes(preview, monkeypatch):
    module, keyboard = preview
    monkeypatch.setattr(sys, "argv", ["preview", "--usb"])
    def finish_preview(prompt):
        keyboard.fail_preview_key = 2
        return ""
    monkeypatch.setattr("builtins.input", finish_preview)
    with pytest.raises(OSError, match="preview write failure"):
        module.main()
    assert keyboard.writes[-2:] == [(2, keyboard.original["2"]), (4, keyboard.original["4"])]
    assert keyboard.current["4"] == keyboard.original["4"]
    assert keyboard.closed
