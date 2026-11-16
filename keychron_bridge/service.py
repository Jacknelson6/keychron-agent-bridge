"""Per-user login supervision and a small recovery launcher independent of Python."""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

LABEL = "io.nativz.keychron-agent-bridge"
RECOVERY_NAME = "Restart Keychron Agent Bridge.app"
RECOVERY_ID = LABEL + ".recovery"
SHORTCUT_NAME = "Restart Keyboard Lights.app"
SESSION_RECOVERY_LABEL = LABEL + ".session-recovery"


def session_recovery_configuration(root):
    return {"Label": SESSION_RECOVERY_LABEL,
            "ProgramArguments": [str(root / "venv/bin/python"), "-m", "keychron_bridge.session_recovery"],
            "RunAtLoad": True, "WatchPaths": [str(root / "runtime.json")],
            "ThrottleInterval": 1, "ProcessType": "Background", "Nice": 10, "Umask": 63,
            "EnvironmentVariables": {"PYTHONPATH": str(root / "python"), "KEYCHRON_BRIDGE_HOME": str(root)},
            "StandardOutPath": str(root / "session-recovery.log"),
            "StandardErrorPath": str(root / "session-recovery-error.log")}


def install_session_recovery(root, home=None):
    home = Path(home) if home is not None else Path.home()
    job = home / "Library/LaunchAgents" / (SESSION_RECOVERY_LABEL + ".plist")
    job.parent.mkdir(parents=True, exist_ok=True)
    job.write_bytes(plistlib.dumps(session_recovery_configuration(root)))
    job.chmod(0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", f"{domain}/{SESSION_RECOVERY_LABEL}"],
                   capture_output=True, timeout=15)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(job)],
                   capture_output=True, timeout=15, check=True)
    return job


def remove_session_recovery(home=None):
    home = Path(home) if home is not None else Path.home()
    subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{SESSION_RECOVERY_LABEL}"],
                   capture_output=True, timeout=15)
    (home / "Library/LaunchAgents" / (SESSION_RECOVERY_LABEL + ".plist")).unlink(missing_ok=True)


def launch_configuration(executable, root):
    return {"Label": LABEL, "ProgramArguments": [str(executable)],
            "RunAtLoad": True, "KeepAlive": {"SuccessfulExit": False, "Crashed": True},
            "ThrottleInterval": 10, "Umask": 63,
            "EnvironmentVariables": {"KEYCHRON_BRIDGE_MANAGED": "1"},
            "StandardOutPath": str(root / "launch.log"),
            "StandardErrorPath": str(root / "launch-error.log")}


def start_service(restart=False, home=None):
    home = Path(home) if home is not None else Path.home()
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    def run(arguments, check=True):
        result = subprocess.run(["/bin/launchctl", *arguments], capture_output=True,
                                text=True, timeout=15)
        if check and result.returncode:
            raise RuntimeError("Could not start Keychron Agent Bridge: " + result.stderr.strip()[:400])
        return result
    run(["enable", target])
    if run(["print", target], check=False).returncode:
        job = home / "Library/LaunchAgents" / (LABEL + ".plist")
        if not job.is_file():
            raise FileNotFoundError("The login item is missing. Reinstall Keychron Agent Bridge.")
        run(["bootstrap", domain, str(job)])
    else:
        run(["kickstart", *(["-k"] if restart else []), target])


RECOVERY_SCRIPT = '''#!/bin/sh
set -eu
bridge_domain="gui/$(/usr/bin/id -u)"
bridge_target="$bridge_domain/io.nativz.keychron-agent-bridge"
bridge_job="$HOME/Library/LaunchAgents/io.nativz.keychron-agent-bridge.plist"
/bin/launchctl enable "$bridge_target"
if /bin/launchctl print "$bridge_target" >/dev/null 2>&1; then
    exec /bin/launchctl kickstart -k "$bridge_target"
fi
exec /bin/launchctl bootstrap "$bridge_domain" "$bridge_job"
'''


def install_recovery_helper(home=None):
    home = Path(home) if home is not None else Path.home()
    app = home / "Applications" / RECOVERY_NAME
    info = app / "Contents/Info.plist"
    if app.exists():
        if not info.is_file() or plistlib.loads(info.read_bytes()).get("CFBundleIdentifier") != RECOVERY_ID:
            raise FileExistsError("A different app already uses the recovery launcher's name")
    executable = app / "Contents/MacOS/restart-bridge"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text(RECOVERY_SCRIPT)
    executable.chmod(0o700)
    info.write_bytes(plistlib.dumps({"CFBundleIdentifier": RECOVERY_ID,
        "CFBundleName": "Restart Keychron Agent Bridge", "CFBundlePackageType": "APPL",
        "CFBundleExecutable": executable.name, "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.1.0", "LSUIElement": True}))
    subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app)],
                   capture_output=True, check=True)
    desktop = home / "Desktop"
    desktop.mkdir(exist_ok=True)
    shortcut = desktop / SHORTCUT_NAME
    if not shortcut.exists() and not shortcut.is_symlink():
        shortcut.symlink_to(app)
    elif not shortcut.is_symlink() or shortcut.resolve() != app.resolve():
        shortcut = None  # Preserve an unrelated existing Desktop item.
    return app, shortcut


def remove_recovery_helper(home=None):
    home = Path(home) if home is not None else Path.home()
    app = home / "Applications" / RECOVERY_NAME
    shortcut = home / "Desktop" / SHORTCUT_NAME
    info = app / "Contents/Info.plist"
    if info.is_file() and plistlib.loads(info.read_bytes()).get("CFBundleIdentifier") == RECOVERY_ID:
        if shortcut.is_symlink() and shortcut.resolve() == app.resolve():
            shortcut.unlink()
        shutil.rmtree(app)
