import os
import plistlib
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from keychron_bridge import service


def test_launch_policy_recovers_crashes_without_requiring_keyboard_or_network(tmp_path):
    executable = tmp_path / "Applications with spaces/Bridge.app/Contents/MacOS/bridge"
    root = tmp_path / "Application Support/Keychron Agent Bridge"
    config = service.launch_configuration(executable, root)
    assert config["Label"] == service.LABEL
    assert config["ProgramArguments"] == [str(executable)]
    assert config["RunAtLoad"] is True
    assert config["KeepAlive"] == {"SuccessfulExit": False, "Crashed": True}
    assert config["ThrottleInterval"] == 10
    assert config["EnvironmentVariables"] == {"KEYCHRON_BRIDGE_MANAGED": "1"}
    assert config["StandardOutPath"] == str(root / "launch.log")
    assert config["StandardErrorPath"] == str(root / "launch-error.log")
    assert config["Umask"] == 0o77
    assert not set(config) & {"WatchPaths", "QueueDirectories", "StartOnMount", "Sockets"}
    assert plistlib.loads(plistlib.dumps(config)) == config


def _launchctl(monkeypatch, results):
    calls = []
    def run(arguments, **kwargs):
        calls.append(arguments)
        assert kwargs == {"capture_output": True, "text": True, "timeout": 15}
        result = results[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(returncode=result, stderr="launch failure")
    monkeypatch.setattr(service.subprocess, "run", run)
    monkeypatch.setattr(service.os, "getuid", lambda: 501)
    return calls


@pytest.mark.parametrize("restart", [False, True])
def test_loaded_service_start_vs_restart_never_bootstraps(tmp_path, monkeypatch, restart):
    calls = _launchctl(monkeypatch, [0, 0, 0])
    service.start_service(restart=restart, home=tmp_path)
    target = "gui/501/" + service.LABEL
    assert calls == [["/bin/launchctl", "enable", target], ["/bin/launchctl", "print", target],
                     ["/bin/launchctl", "kickstart", *(["-k"] if restart else []), target]]


def test_unloaded_service_bootstraps_existing_plist_with_spaces(tmp_path, monkeypatch):
    home = tmp_path / "user home with spaces"
    job = home / "Library/LaunchAgents" / (service.LABEL + ".plist")
    job.parent.mkdir(parents=True)
    job.write_bytes(plistlib.dumps({"Label": service.LABEL}))
    calls = _launchctl(monkeypatch, [0, 113, 0])
    service.start_service(restart=True, home=home)
    assert calls[-1] == ["/bin/launchctl", "bootstrap", "gui/501", str(job)]
    assert not any("kickstart" in call for call in calls)


def test_missing_login_item_reports_reinstallation_without_bootstrap(tmp_path, monkeypatch):
    calls = _launchctl(monkeypatch, [0, 113])
    with pytest.raises(FileNotFoundError, match="Reinstall"):
        service.start_service(home=tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize("results", [[1], [0, 0, 1], [0, 113, 1]])
def test_launchctl_failures_propagate(tmp_path, monkeypatch, results):
    job = tmp_path / "Library/LaunchAgents" / (service.LABEL + ".plist")
    job.parent.mkdir(parents=True)
    job.write_bytes(plistlib.dumps({"Label": service.LABEL}))
    calls = _launchctl(monkeypatch, results)
    with pytest.raises(RuntimeError, match="launch failure"):
        service.start_service(home=tmp_path)
    assert len(calls) == len(results)


def test_launchctl_timeout_propagates_without_retry_loop(tmp_path, monkeypatch):
    calls = _launchctl(monkeypatch, [subprocess.TimeoutExpired("launchctl", 15)])
    with pytest.raises(subprocess.TimeoutExpired):
        service.start_service(home=tmp_path)
    assert len(calls) == 1


def _codesign(monkeypatch):
    calls = []
    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(service.subprocess, "run", run)
    return calls


def test_recovery_helper_is_standalone_signed_and_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home with spaces"
    home.mkdir()
    calls = _codesign(monkeypatch)
    app, shortcut = service.install_recovery_helper(home=home)
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    executable = app / "Contents/MacOS" / info["CFBundleExecutable"]
    assert info["CFBundleIdentifier"] == service.RECOVERY_ID
    assert info["CFBundlePackageType"] == "APPL" and info["LSUIElement"] is True
    assert executable.stat().st_mode & 0o777 == 0o700
    assert executable.read_text() == service.RECOVERY_SCRIPT
    assert "python" not in executable.read_text().lower() and "Terminal" not in executable.read_text()
    assert shortcut.is_symlink() and shortcut.resolve() == app.resolve()
    assert calls == [(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
                      {"capture_output": True, "check": True})]
    assert service.install_recovery_helper(home=home) == (app, shortcut)
    service.remove_recovery_helper(home=home)
    assert not app.exists() and not shortcut.is_symlink()
    service.remove_recovery_helper(home=home)


@pytest.mark.parametrize("collision", ["file", "symlink"])
def test_existing_unrelated_desktop_item_is_preserved(tmp_path, monkeypatch, collision):
    _codesign(monkeypatch)
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    shortcut = desktop / service.SHORTCUT_NAME
    if collision == "file":
        shortcut.write_text("unrelated")
    else:
        shortcut.symlink_to(tmp_path / "unrelated missing target")
    app, installed_shortcut = service.install_recovery_helper(home=tmp_path)
    assert installed_shortcut is None
    service.remove_recovery_helper(home=tmp_path)
    assert not app.exists()
    if collision == "file":
        assert shortcut.read_text() == "unrelated"
    else:
        assert shortcut.is_symlink()


def test_existing_unrelated_app_and_its_shortcut_are_preserved(tmp_path, monkeypatch):
    calls = _codesign(monkeypatch)
    app = tmp_path / "Applications" / service.RECOVERY_NAME
    info = app / "Contents/Info.plist"
    info.parent.mkdir(parents=True)
    document = {"CFBundleIdentifier": "example.unrelated-app"}
    info.write_bytes(plistlib.dumps(document))
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    shortcut = desktop / service.SHORTCUT_NAME
    shortcut.symlink_to(app)
    with pytest.raises(FileExistsError, match="different app"):
        service.install_recovery_helper(home=tmp_path)
    assert calls == []
    service.remove_recovery_helper(home=tmp_path)
    assert plistlib.loads(info.read_bytes()) == document
    assert shortcut.is_symlink()


def test_codesign_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(service.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        service.install_recovery_helper(home=tmp_path)
    assert not (tmp_path / "Desktop" / service.SHORTCUT_NAME).is_symlink()


@pytest.mark.parametrize("loaded", [True, False])
def test_recovery_shell_quotes_home_and_target_without_real_launchctl(tmp_path, loaded):
    tools = tmp_path / "mock tools with spaces"
    tools.mkdir()
    args_file = tmp_path / "arguments"
    fake_launchctl = tools / "launchctl"
    fake_launchctl.write_text('''#!/bin/sh
printf 'CALL\\0' >> "$SERVICE_TEST_ARGUMENTS"
printf '%s\\0' "$@" >> "$SERVICE_TEST_ARGUMENTS"
if [ "$1" = print ]; then exit "$SERVICE_TEST_PRINT_STATUS"; fi
''')
    fake_id = tools / "id"
    fake_id.write_text("#!/bin/sh\nprintf '501\\n'\n")
    fake_launchctl.chmod(0o700)
    fake_id.chmod(0o700)
    script = tmp_path / "recovery test.sh"
    source = service.RECOVERY_SCRIPT.replace("/bin/launchctl", shlex.quote(str(fake_launchctl)))
    source = source.replace("/usr/bin/id", shlex.quote(str(fake_id)))
    assert "/bin/launchctl" not in source and "/usr/bin/id" not in source
    script.write_text(source)
    home = tmp_path / "user home with spaces and 'quotes' $(not-a-command)"
    result = subprocess.run(["/bin/sh", str(script)], capture_output=True, text=True, timeout=5,
                            env={**os.environ, "HOME": str(home), "SERVICE_TEST_ARGUMENTS": str(args_file),
                                 "SERVICE_TEST_PRINT_STATUS": "0" if loaded else "113"})
    assert result.returncode == 0, result.stderr
    calls = [part.rstrip("\0").split("\0") for part in args_file.read_text().split("CALL\0")[1:]]
    target = "gui/501/" + service.LABEL
    assert calls[:2] == [["enable", target], ["print", target]]
    assert calls[2] == (["kickstart", "-k", target] if loaded else
                        ["bootstrap", "gui/501", str(home / "Library/LaunchAgents" / (service.LABEL + ".plist"))])
