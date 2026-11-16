#!/usr/bin/env python3
"""Preview a provider-aware palette; use --usb for a temporary hardware demo."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def frame_for(agent, status):
    hue = {"claude": 20, "codex": 170}[agent]
    hue = {"waiting": 32, "done": 85, "failed": 0}.get(status, hue)
    return {"mode": 1, "hue": hue, "saturation": 255}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("claude", "codex"), default="claude")
    parser.add_argument("--status", choices=("working", "waiting", "done", "failed"), default="working")
    parser.add_argument("--usb", action="store_true", help="Temporarily display this frame on the verified V1 Max")
    args = parser.parse_args()
    frame = frame_for(args.agent, args.status)
    print(json.dumps(frame, indent=2))
    if not args.usb:
        return
    from keychron_bridge.keyboard import Keyboard
    from keychron_bridge.session_lights import FIRMWARE
    keyboard = Keyboard.open()  # Quit the menu-bar bridge first; it owns this interface.
    original = None
    modified = False
    try:
        firmware = bytes(keyboard.command([0xA1])[1:]).split(b"\0")[0].decode()
        if firmware != FIRMWARE:
            raise OSError("This preview requires the verified V1 Max v1.1.1 firmware")
        original = keyboard.lighting()
        if original["1"] == [0] or original["2"] == [0]:
            print("Backlighting is disabled. Leaving it off.")
            return
        modified = True
        keyboard.set_light(4, [frame["hue"], frame["saturation"]])
        keyboard.set_light(2, [frame["mode"]])
        input("Temporary RAM-only preview. Press Return to restore your lighting: ")
    finally:
        try:
            if modified:
                restore_error = None
                for key in ("2", "4"):
                    try:
                        keyboard.set_light(int(key), original[key])
                    except OSError as error:
                        restore_error = restore_error or error
                if restore_error:
                    raise restore_error
        finally:
            keyboard.close()


if __name__ == "__main__":
    main()
