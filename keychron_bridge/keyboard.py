"""Verified VIA transactions for the V1 Max ANSI knob model.

Only the two Fn layers and their encoder mappings are changed. Runtime colors
are deliberately not saved to EEPROM on each agent event.
"""

import logging
import threading
from pathlib import Path

from .paths import read_json, write_json
from .state import LIGHTS

VENDOR, PRODUCT = 0x3434, 0x0913
# Fn+F9..F12 -> F13..F16; Fn+knob -> F19. Mac and Windows Fn layers.
KEYS = [(layer, 0, column, code) for layer in (1, 3)
        for column, code in ((9, 0x68), (10, 0x69), (11, 0x6A), (12, 0x6B), (15, 0x6E))]
# Fn+turn left/right -> F17/F18.
ENCODERS = [(layer, direction, code) for layer in (1, 3)
            for direction, code in ((0, 0x6C), (1, 0x6D))]


class Keyboard:
    def __init__(self, handle):
        self.handle = handle

    @classmethod
    def open(cls):
        import hid
        devices = [d for d in hid.enumerate(VENDOR, PRODUCT)
                   if d["usage_page"] == 0xFF60 and d["usage"] == 0x61
                   and d.get("bus_type", 1) == 1]
        if len(devices) != 1:
            raise OSError("Connect one V1 Max ANSI knob keyboard by USB")
        handle = hid.device()
        handle.open_path(devices[0]["path"])
        return cls(handle)

    def command(self, data):
        if len(data) > 32:
            raise ValueError("Invalid HID packet length")
        self.handle.write(bytes([0] + data + [0] * (32 - len(data))))
        response = list(self.handle.read(32, 1000))
        if data[0] == 0xA8:
            prefix = data[:2]
        elif data[0] == 0xA1:
            prefix = data[:1]
        else:
            prefix = data[:4] if data[0] in (4, 5, 0x14, 0x15) else data[:3]
        if len(response) != 32 or response[:len(prefix)] != prefix:
            raise OSError(f"The keyboard did not accept VIA command {data[:3]}")
        if data[0] == 0xA8 and response[2] != 0:
            raise OSError("The keyboard rejected a session lighting command")
        return response

    def key(self, layer, row, column):
        return self.command([4, layer, row, column])[4:6]

    def set_key(self, layer, row, column, code):
        self.command([5, layer, row, column] + code)

    def encoder(self, layer, direction):
        return self.command([0x14, layer, 0, direction])[4:6]

    def set_encoder(self, layer, direction, code):
        self.command([0x15, layer, 0, direction] + code)

    def lighting(self):
        return {str(i): self.command([8, 3, i])[3:(5 if i == 4 else 4)] for i in range(1, 5)}

    def bluetooth_capability(self):
        reply = self.command([8, 0x7e, 1])
        if reply[3:10] != [*b"KABT", 1, 4, 81] or reply[15:25] != [2, 2, 12, 0, 48, 0, 9, 10, 11, 12]:
            raise OSError("Bluetooth lighting firmware profile was not verified")
        return {"raw": reply[10], "count": reply[11], "enabled": bool(reply[12]),
                "transport": reply[13], "wireless_state": reply[14]}

    def set_light(self, value_id, values):
        self.command([7, 3, value_id] + values)

    def snapshot(self):
        return {"vendor": VENDOR, "product": PRODUCT,
                "keys": [{"layer": l, "row": r, "column": c, "code": self.key(l, r, c)}
                         for l, r, c, _ in KEYS],
                "encoders": [{"layer": l, "direction": d, "code": self.encoder(l, d)}
                             for l, d, _ in ENCODERS], "lighting": self.lighting()}

    def install(self, backup_path: Path):
        current = self.snapshot()
        original = read_json(backup_path, None)
        if original is None:
            original = current
            write_json(backup_path, original)  # Durable backup precedes the first device write.
        self.validate_backup(original)
        expected = {(l, r, c): [code >> 8, code & 255] for l, r, c, code in KEYS}
        expected_encoders = {(l, d): [code >> 8, code & 255] for l, d, code in ENCODERS}
        # Accept originals, installed mappings, or a partially completed installation.
        # Preserve custom edits made by the user after this bridge was installed.
        for now, old in zip(current["keys"], original["keys"]):
            identity = (now["layer"], now["row"], now["column"])
            if now["code"] not in (old["code"], expected[identity]):
                raise OSError("A keyboard shortcut changed outside the bridge. Restore or review it first.")
        for now, old in zip(current["encoders"], original["encoders"]):
            if now["code"] not in (old["code"], expected_encoders[(now["layer"], now["direction"])]):
                raise OSError("An encoder mapping changed outside the bridge. Restore or review it first.")
        try:
            for l, r, c, code in KEYS:
                value = [code >> 8, code & 255]
                if self.key(l, r, c) != value:
                    self.set_key(l, r, c, value)
                if self.key(l, r, c) != value:
                    raise OSError("A prompt shortcut could not be verified")
            for l, d, code in ENCODERS:
                value = [code >> 8, code & 255]
                if self.encoder(l, d) != value:
                    self.set_encoder(l, d, value)
                if self.encoder(l, d) != value:
                    raise OSError("An agent selector mapping could not be verified")
        except Exception:
            try:
                self.restore(original, mappings=True)
            except OSError:
                pass  # Original backup remains available for recovery on the next connection.
            raise
        return original

    @staticmethod
    def validate_backup(backup):
        if not isinstance(backup, dict) or (backup.get("vendor"), backup.get("product")) != (VENDOR, PRODUCT):
            raise ValueError("Invalid keyboard backup")
        if [(k["layer"], k["row"], k["column"]) for k in backup.get("keys", [])] != [x[:3] for x in KEYS]:
            raise ValueError("Backup does not match the shortcut layout")
        if [(e["layer"], e["direction"]) for e in backup.get("encoders", [])] != [x[:2] for x in ENCODERS]:
            raise ValueError("Backup does not match the encoder layout")

    def restore(self, backup, mappings=False):
        self.validate_backup(backup)
        if mappings:
            for item in backup["keys"]:
                self.set_key(item["layer"], item["row"], item["column"], item["code"])
                if self.key(item["layer"], item["row"], item["column"]) != item["code"]:
                    raise OSError("Could not verify restored prompt shortcuts")
            for item in backup["encoders"]:
                self.set_encoder(item["layer"], item["direction"], item["code"])
                if self.encoder(item["layer"], item["direction"]) != item["code"]:
                    raise OSError("Could not verify restored encoder mappings")
        current_lighting = self.lighting()
        for key, values in backup["lighting"].items():
            # VIA's brightness scale can round down. Do not rewrite an untouched
            # brightness value every time the app quits or reconnects.
            if current_lighting.get(key) != values:
                self.set_light(int(key), values)

    def show_status(self, status, baseline=None):
        mode, hue, saturation = LIGHTS[status]
        current = self.lighting()
        reference = baseline or current
        # VIA reports disabled RGB as effect 0 even when brightness is nonzero.
        if any(values[key] == [0] for values in (reference, current) for key in ("1", "2")):
            return
        # Keep feedback quiet and never brighten a keyboard the user dimmed or disabled.
        brightness = min(51, reference["1"][0], current["1"][0])
        if current["1"] != [brightness]:
            self.set_light(1, [brightness])
        self.set_light(2, [mode])
        self.set_light(4, [hue, saturation])
        current = self.lighting()
        if current["2"] != [mode] or current["4"] != [hue, saturation]:
            raise OSError("The keyboard lighting did not match the requested status")

    def close(self):
        self.handle.close()


class Hardware:
    def __init__(self, root, changed):
        self.root = root
        self.backup_path = root / "keyboard-backup-3434-0913.json"
        self.changed = changed
        self.keyboard = None
        self.original = None
        self.lock = threading.RLock()
        self.message = "Waiting for V1 Max over USB"
        self.status = "idle"
        self.enabled = True
        self.lighting_enabled = True
        self.last_light = None
        self.session_count = 0
        self.session_lights = None
        self.bluetooth = None

    def connect(self):
        with self.lock:
            if self.keyboard:
                return
            try:
                self.keyboard = Keyboard.open()
                if self.bluetooth:
                    try:
                        self.bluetooth.show(None)
                    except OSError:
                        pass  # A stale wireless output must not discard usable USB.
                    finally:
                        self.bluetooth.close()
                        self.bluetooth = None
                if self.enabled:
                    self.original = self.keyboard.install(self.backup_path)
                    self.message = "Keyboard controls installed and verified"
                else:
                    self.original = read_json(self.backup_path, None)
                    self.message = "Keyboard controls disabled"
                    if self.original:
                        self.keyboard.restore(self.original, mappings=True)
                self.last_light = None
                self.apply_sessions(self.session_count)
            except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
                self.message = str(error)
                self._close()
                self._connect_bluetooth()
            self.changed()

    def _connect_bluetooth(self):
        from .bluetooth import BluetoothKeyboard, provisioned
        if not provisioned(self.root):
            return
        try:
            self.bluetooth = BluetoothKeyboard()
            self.last_light = None
            self.apply_sessions(self.session_count)
        except (OSError, ImportError) as error:
            self.message = str(error)
            logging.warning("Bluetooth lighting connection unavailable: %s", error)

    def reconnect(self):
        with self.lock:
            self._close()
            self.connect()

    def disconnect(self):
        with self.lock:
            self._close()
            self.message = "USB lighting offline; saved controls work over Bluetooth"
            self._connect_bluetooth()
            self.changed()

    def _close(self):
        if self.keyboard:
            self.keyboard.close()
        self.keyboard = None
        if self.bluetooth:
            self.bluetooth.close()
        self.bluetooth = None
        self.last_light = None
        self.session_lights = None

    def apply_sessions(self, count):
        from .session_lights import SessionLights
        with self.lock:
            self.session_count = count
            if not self.lighting_enabled or not self.enabled:
                return
            if self.last_light == ("sessions", min(count, 4)):
                return
            if self.bluetooth:
                try:
                    self.bluetooth.show(count)
                    self.last_light = ("sessions", min(count, 4))
                    self.message = "F9–F12 Claude session indicators connected over Bluetooth"
                    logging.info("Sent Bluetooth Claude session count %d (%d indicators)", count, min(count, 4))
                except OSError as error:
                    self.message = str(error)
                    self._close()
                    self.changed()
                return
            if not self.keyboard:
                return
            try:
                if self.session_lights is None:
                    self.session_lights = SessionLights(
                        self.keyboard, self.backup_path.with_name("session-lights-backup-3434-0913.json"))
                    self.session_lights.prepare()
                visible = self.session_lights.show(count, self.original["lighting"])
                self.last_light = ("sessions", min(count, 4))
                self.message = ("F9–F12 Claude session indicators connected" if visible
                                else "Keyboard backlighting is off")
                if visible:
                    logging.info("Applied Claude session count %d (%d indicators)", count, min(count, 4))
            except (OSError, ValueError) as error:
                self.message = str(error)
                if self.session_lights and self.session_lights.modified:
                    try:
                        self.restore_lighting()
                    except OSError:
                        pass
                self._close()
                self.changed()

    def restore_lighting(self):
        with self.lock:
            if self.bluetooth:
                try:
                    self.bluetooth.show(None)
                except OSError:
                    self._close()
                    raise
                finally:
                    self.last_light = None
            if self.keyboard and self.original:
                # Exit animated regions before restoring their previous contents.
                self.keyboard.set_light(2, self.original["lighting"]["2"])
                if self.session_lights:
                    self.session_lights.restore()
                self.keyboard.restore(self.original)
                self.last_light = None

    def apply_status(self, status):
        with self.lock:
            self.status = status
            if self.keyboard and self.lighting_enabled and self.enabled and self.last_light != status:
                try:
                    self.keyboard.show_status(status, self.original["lighting"] if self.original else None)
                    self.last_light = status
                except OSError as error:
                    self.message = str(error)
                    self._close()
                    self.changed()

    def restore_controls(self):
        with self.lock:
            self.enabled = False
            if self.bluetooth:
                self.restore_lighting()
            if self.keyboard and self.original:
                self.restore_lighting()
                self.keyboard.restore(self.original, mappings=True)
                self.message = "Original keyboard mappings restored"
            else:
                self.message = "Connect USB to finish restoring keyboard mappings"
            self.changed()

    def close(self):
        with self.lock:
            if self.bluetooth or (self.keyboard and self.original):
                try:
                    self.restore_lighting()
                except OSError:
                    pass
            self._close()
