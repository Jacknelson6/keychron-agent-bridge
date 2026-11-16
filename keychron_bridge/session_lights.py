"""V1 Max RAM-only lighting regions. Animation runs on the keyboard, not the Mac."""

from .paths import read_json, write_json

LED_COUNT = 81
INDICES = (9, 10, 11, 12)
MIXED_MODE = 24  # v1.1.1: 22 standard effects, per-key 23, mixed 24.
FIRMWARE = "v1.1.1 2025-04-30-10:37:08"


class SessionLights:
    def __init__(self, keyboard, backup_path):
        self.keyboard = keyboard
        self.backup_path = backup_path
        self.original = None
        self.configured = False
        self.modified = False

    def command(self, command, *payload):
        if command not in (1, 5, 6, 11, 12, 13, 14, 15):
            raise ValueError("Unsupported session lighting command")
        return self.keyboard.command([0xA8, command, *payload])

    def regions(self):
        result = []
        for start in range(0, LED_COUNT, 28):
            count = min(28, LED_COUNT - start)
            result.extend(self.command(12, start, count)[3:3 + count])
        return result

    def effects(self, region):
        result = []
        for start in range(0, 5, 3):
            count = min(3, 5 - start)
            result.extend(self.command(14, region, start, count)[3:3 + count * 8])
        return result

    def set_regions(self, values):
        for start in range(0, LED_COUNT, 28):
            chunk = values[start:start + 28]
            self.command(13, start, len(chunk), *chunk)
        if self.regions() != values:
            raise OSError("Session indicator regions could not be verified")

    def set_effects(self, region, values):
        for start in range(0, 5, 3):
            count = min(3, 5 - start)
            self.command(15, region, start, count, *values[start * 8:(start + count) * 8])
        if self.effects(region) != values:
            raise OSError("Session indicator animation could not be verified")

    @staticmethod
    def desired_effects():
        duration = list((5000).to_bytes(4, "little"))
        # Blue background is solid. Orange indicators breathe about once per four seconds.
        return [[mode, hue, 255, 64, *duration] + [0] * 32
                for mode, hue in ((1, 170), (2, 20))]

    @staticmethod
    def desired_regions(count):
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Invalid Claude session count")
        result = [0] * LED_COUNT
        for index in INDICES[:min(count, 4)]:
            result[index] = 1
        return result

    @staticmethod
    def validate(backup):
        if not isinstance(backup, dict) or backup.get("profile") != FIRMWARE:
            raise ValueError("Invalid session lighting backup")
        regions, effects = backup.get("regions"), backup.get("effects")
        if not isinstance(regions, list) or len(regions) != LED_COUNT or any(
                type(value) is not int or value not in (0, 1) for value in regions):
            raise ValueError("Invalid session lighting regions")
        if not isinstance(effects, list) or len(effects) != 2 or any(
                not isinstance(row, list) or len(row) != 40 or any(
                    type(value) is not int or not 0 <= value <= 255 for value in row)
                for row in effects):
            raise ValueError("Invalid session lighting effects")
        if any(row[index] >= MIXED_MODE for row in effects for index in range(0, 40, 8)):
            raise ValueError("Invalid saved session lighting effect")

    def prepare(self):
        firmware = self.keyboard.command([0xA1])
        version = bytes(firmware[1:]).split(b"\0")[0].decode(errors="replace")
        if version != FIRMWARE or self.command(1)[3:5] != [1, 0]:
            raise OSError("Session indicators need the verified V1 Max v1.1.1 firmware profile")
        if self.command(5)[3] != LED_COUNT or self.command(11)[3:5] != [2, 5]:
            raise OSError("Session indicator lighting layout is unsupported")
        indices = self.command(6, 0, 0, 0x1E, 0)[3:19]
        if indices[9:13] != list(INDICES):
            raise OSError("The F9–F12 LED mapping did not match this keyboard")
        current = {"profile": FIRMWARE, "regions": self.regions(),
                   "effects": [self.effects(region) for region in range(2)]}
        self.validate(current)
        original = read_json(self.backup_path, None)
        if original is None:
            write_json(self.backup_path, current)  # Durable before any lighting mutation.
            original = current
        self.validate(original)
        ours = self.desired_effects()
        # Accept original, installed, or interrupted transactions, preserving unrelated custom edits.
        for index, value in enumerate(current["regions"]):
            if value not in {original["regions"][index], 0, int(index in INDICES)}:
                raise OSError("Lighting regions changed outside the bridge")
        for region in range(2):
            for offset in range(0, 40, 8):
                if current["effects"][region][offset:offset + 8] not in (
                        original["effects"][region][offset:offset + 8], ours[region][offset:offset + 8]):
                    raise OSError("Lighting effects changed outside the bridge")
        self.original = original

    def show(self, count, baseline):
        desired = self.desired_regions(count)
        current = self.keyboard.lighting()
        if any(values[key] == [0] for values in (baseline, current) for key in ("1", "2")):
            return False
        if not self.configured:
            for region, values in enumerate(self.desired_effects()):
                if self.effects(region) != values:
                    self.modified = True
                    self.set_effects(region, values)
            self.configured = True
        if self.regions() != desired:
            self.modified = True
            self.set_regions(desired)
        if current["2"] != [MIXED_MODE]:
            self.modified = True
            self.keyboard.set_light(2, [MIXED_MODE])
        # Mode entry can reset brightness on this firmware.
        # Set and verify the brightness after mode entry, not before it.
        entered = self.keyboard.lighting()
        brightness = min(baseline["1"][0], current["1"][0])
        if entered["1"] != [brightness]:
            self.modified = True
            self.keyboard.set_light(1, [brightness])
        verified = self.keyboard.lighting()
        if verified["2"] != [MIXED_MODE]:
            raise OSError("The keyboard did not enable session indicator mode")
        if verified["1"][0] > brightness:
            raise OSError("The keyboard did not accept indicator brightness")
        return True

    def restore(self):
        if self.original:
            if self.regions() != self.original["regions"]:
                self.set_regions(self.original["regions"])
            for region, values in enumerate(self.original["effects"]):
                if self.effects(region) != values:
                    self.set_effects(region, values)
        self.configured = False
        self.modified = False
