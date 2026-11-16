#!/usr/bin/env python3
"""Install the built local app, original-CLI launchers, and status hooks."""

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keychron_bridge.integration import configure_hooks, stop_bridge
from keychron_bridge.paths import data_dir, write_json
from keychron_bridge.service import launch_configuration, install_recovery_helper, install_session_recovery


def main():
    root = data_dir()
    source = ROOT / "dist/Keychron Agent Bridge.app"
    if not source.is_dir():
        raise SystemExit("Build the app with scripts/build.sh first.")
    venv_python = root / "venv/bin/python"
    if not venv_python.is_file():
        raise SystemExit("Create the application virtual environment first.")
    applications = Path.home() / "Applications"
    applications.mkdir(exist_ok=True)
    target = applications / source.name
    staging = applications / ".Keychron Agent Bridge.installing.app"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging, symlinks=True)
    executable = plistlib.loads((staging / "Contents/Info.plist").read_bytes())["CFBundleExecutable"]
    app_lock = stop_bridge(root)
    label = "io.nativz.keychron-agent-bridge"
    domain = f"gui/{os.getuid()}"
    # Only this application's own launchd job is touched.
    subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
    if target.exists():
        previous = applications / ".Keychron Agent Bridge.previous.app"
        if previous.exists():
            shutil.rmtree(previous)
        target.rename(previous)
    staging.rename(target)
    python_sources = root / "python/keychron_bridge"
    shutil.copytree(ROOT / "keychron_bridge", python_sources, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "keychron_bridge/hook.py", root / "keychron-agent-hook.py")
    (root / "keychron-agent-hook.py").chmod(0o600)
    (root / "run-task.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / 'python'))\n"
        "from keychron_bridge.prompts import run_task\n"
        "sys.exit(run_task(sys.argv[1]))\n")
    (root / "run-task.py").chmod(0o600)
    local_bin = Path.home() / ".local/bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    wrapper = local_bin / "keychron-bridge"
    wrapper.write_text("#!/bin/sh\nPYTHONPATH=" + shlex.quote(str(root / "python")) + " exec " +
                       shlex.quote(str(venv_python)) + ' -m keychron_bridge.cli "$@"\n')
    wrapper.chmod(0o700)
    changed = configure_hooks(root)
    launch_agents = Path.home() / "Library/LaunchAgents"
    launch_agents.mkdir(exist_ok=True)
    job = launch_agents / f"{label}.plist"
    job.write_bytes(plistlib.dumps(launch_configuration(target / "Contents/MacOS" / executable, root)))
    job.chmod(0o600)
    recovery, shortcut = install_recovery_helper()
    write_json(root / "installation.json", {"version": "0.1.0", "app": str(target),
        "job": str(job), "source": str(ROOT), "hooks": changed,
        "recovery_app": str(recovery), "recovery_shortcut": str(shortcut) if shortcut else None})
    app_lock.close()
    result = subprocess.run(["launchctl", "bootstrap", domain, str(job)], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("App installed, but login launch failed: " + result.stderr.strip())
    install_session_recovery(root)
    print("Installed:", target)
    print("Login startup and automatic crash recovery enabled.")
    print("Recovery launcher:", shortcut or recovery)
    print("Lifecycle hooks added without changing existing agent permissions.")
    print("Codex hooks require review in Settings > Hooks or /hooks.")
    print("Connect the V1 Max by USB to install the saved keyboard controls.")


if __name__ == "__main__":
    main()
