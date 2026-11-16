#!/usr/bin/env python3
"""Restore keyboard mappings before removing this app's hooks and login item."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from keychron_bridge.hook import publish
from keychron_bridge.integration import configure_hooks, stop_bridge
from keychron_bridge.keyboard import Keyboard
from keychron_bridge.paths import data_dir, read_json, write_json
from keychron_bridge.session_lights import SessionLights
from keychron_bridge.service import remove_recovery_helper, remove_session_recovery


def main():
    root = data_dir()
    remove_session_recovery()
    try:
        app_lock = stop_bridge(root)
    except TimeoutError as error:
        raise SystemExit(str(error))
    label = "io.nativz.keychron-agent-bridge"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)
    backup = read_json(root / "keyboard-backup-3434-0913.json", None)
    if backup:
        try:
            keyboard = Keyboard.open()
        except OSError:
            raise SystemExit("Connect the V1 Max by USB first so its original controls can be restored.")
        try:
            keyboard.restore(backup, mappings=True)
            lighting_backup = root / "session-lights-backup-3434-0913.json"
            if lighting_backup.exists():
                lights = SessionLights(keyboard, lighting_backup)
                lights.prepare()
                lights.restore()
        finally:
            keyboard.close()
    config = read_json(root / "config.json", {})
    config["keyboard_enabled"] = False
    write_json(root / "config.json", config)
    configure_hooks(root, remove=True)
    (Path.home() / "Library/LaunchAgents" / f"{label}.plist").unlink(missing_ok=True)
    (Path.home() / ".local/bin/keychron-bridge").unlink(missing_ok=True)
    app = Path.home() / "Applications/Keychron Agent Bridge.app"
    if app.exists():
        shutil.rmtree(app)
    remove_recovery_helper()
    app_lock.close()
    print("Removed the app, login item, and only this app's hook handlers.")
    print("Settings and keyboard backups retained at", root)


if __name__ == "__main__":
    main()
