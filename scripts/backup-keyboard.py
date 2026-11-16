#!/usr/bin/env python3
"""Read-only full VIA/settings backup before a custom main-firmware installation."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keychron_bridge.integration import stop_bridge
from keychron_bridge.keyboard import Keyboard
from keychron_bridge.paths import data_dir, write_json
from keychron_bridge.service import start_service
from keychron_bridge.session_lights import SessionLights


def main():
    root = data_dir()
    lock = stop_bridge(root)
    keyboard = None
    try:
        keyboard = Keyboard.open()
        def query(data):
            keyboard.handle.write(bytes([0, *data, *([0] * (32 - len(data)))]))
            reply = list(keyboard.handle.read(32, 1000))
            if len(reply) != 32 or reply[0] != data[0]:
                raise OSError("A full-settings backup command was rejected")
            if data[0] in (0xA7, 0xA8) and (reply[1] != data[1] or reply[2] != 0):
                raise OSError("A vendor settings backup command was rejected")
            return reply
        def buffer(command, size):
            result = []
            for start in range(0, size, 28):
                count = min(28, size - start)
                request = [command, start >> 8, start & 255, count]
                reply = query(request)
                if reply[:4] != request:
                    raise OSError("A settings buffer response did not match its offset")
                result.extend(reply[4:4 + count])
            return result
        layers = query([0x11])[1]
        macro_size = int.from_bytes(bytes(query([0x0D])[1:3]), "big")
        if layers != 4 or not 0 < macro_size <= 4096:
            raise OSError("Unexpected V1 Max storage profile")
        lights = SessionLights(keyboard, root / "session-lights-backup-3434-0913.json")
        colors = []
        for start in range(0, 81, 9):
            count = min(9, 81 - start)
            colors.extend(query([0xA8, 9, start, count])[3:3 + 3 * count])
        document = {"vendor": 0x3434, "product": 0x0913,
            "firmware": bytes(query([0xA1])[1:]).split(b"\0")[0].decode(),
            "via_protocol": query([1])[1:3], "features": query([0xA2])[1:3],
            "layers": layers, "rows": 6, "columns": 16,
            "keymap": buffer(0x12, layers * 6 * 16 * 2),
            "encoders": [[keyboard.encoder(layer, direction) for direction in (0, 1)] for layer in range(layers)],
            "macro_count": query([0x0C])[1], "macro_size": macro_size,
            "macros": buffer(0x0E, macro_size), "layout_options": query([2, 2])[2:6],
            "lighting": keyboard.lighting(), "regions": lights.regions(),
            "effects": [lights.effects(region) for region in (0, 1)],
            "per_key_type": query([0xA8, 7])[3], "per_key_colors": colors,
            "os_indicators": query([0xA8, 3])[3:8],
            "debounce": query([0xA7, 5])[3:6], "snap_count": query([0xA7, 7])[3],
            "snap_groups": [query([0xA7, 8, start, min(9, 20 - start)])[3:3 + min(9, 20 - start) * 3]
                            for start in range(0, 20, 9)],
            "wireless_timeouts": query([0xA7, 11])[3:7]}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        target = root / "firmware-backups" / stamp / "settings.json"
        write_json(target, document)
        print("Private full settings backup:", target)
        print("Profile:", document["firmware"], "features", document["features"],
              "macro bytes", macro_size, "snap groups", document["snap_count"])
    finally:
        if keyboard:
            keyboard.close()
        lock.close()
        start_service()


if __name__ == "__main__":
    main()
