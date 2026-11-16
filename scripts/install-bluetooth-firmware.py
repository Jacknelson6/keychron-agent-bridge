#!/usr/bin/env python3
"""Back up and verify factory firmware before an EEPROM-preserving USB flash."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keychron_bridge.integration import stop_bridge
from keychron_bridge.keyboard import Keyboard
from keychron_bridge.paths import data_dir, write_json
from keychron_bridge.service import start_service

FLASH_SIZE = 0x40000
SETTINGS_START, SETTINGS_END = 0x4000, 0x8000


def finish_leave(leave, confirm_application):
    try:
        leave()
    except (OSError, subprocess.TimeoutExpired) as error:
        # macOS can retain the old DFU connection after the application has booted.
        # Accept this only after a fresh, validated USB capability handshake.
        try:
            confirm_application()
        except Exception:
            raise error
        return True
    return False


def validate_backup(rom, factory, settings, logical):
    # dfu-util strips the optional 16-byte file suffix before programming.
    if factory[-8:-5] == b"UFD":
        if factory[-5] != 16 or (zlib.crc32(factory[:-4]) ^ 0xffffffff) != int.from_bytes(factory[-4:], "little"):
            raise ValueError("Factory DFU suffix checksum did not match")
        factory = factory[:-16]
    if len(rom) != FLASH_SIZE or not SETTINGS_END < len(factory) < 0x20000:
        raise ValueError("Unexpected firmware image length")
    if rom[:SETTINGS_START] != factory[:SETTINGS_START] or rom[SETTINGS_END:len(factory)] != factory[SETTINGS_END:]:
        raise ValueError("Installed application differs from the expected factory V1 Max firmware")
    if settings.get("firmware") != "v1.1.1 2025-04-30-10:37:08" or settings.get("macro_size") != 1770:
        raise ValueError("Unexpected factory settings profile")
    if len(logical) != 3072 or logical[:2] != b"\xe6\xfe" or int.from_bytes(logical[15:19], "little") != 477:
        raise ValueError("EEPROM core or keyboard schema did not match")
    if logical[514:517] != bytes([0x25, 0x04, 0x30]):
        raise ValueError("VIA schema date did not match")
    # VIA's direction 0 means counterclockwise; QMK stores clockwise first.
    encoders = bytes(value for layer in settings["encoders"] for direction in reversed(layer) for value in direction)
    if logical[518:1286] != bytes(settings["keymap"]) or logical[1286:1302] != encoders:
        raise ValueError("ROM key mappings differ from the USB settings backup")
    if logical[1302:] != bytes(settings["macros"]):
        raise ValueError("ROM macros differ from the USB settings backup")
    if rom[0x7000:SETTINGS_END] != b"\xff" * 4096:
        raise ValueError("Settings extend beyond the supported backing-store profile")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--factory", type=Path, required=True,
                        help="Expected currently installed application: factory or a previous verified build")
    parser.add_argument("--reader", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True, help="Directory containing the build manifest")
    parser.add_argument("--install", action="store_true", help="Write only after every backup validation passes")
    args = parser.parse_args()
    os.umask(0o077)
    root = data_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / "firmware-backups" / stamp
    destination.mkdir(parents=True, mode=0o700)
    lock = stop_bridge(root)
    try:
        def run(command, name, timeout=120):
            log = destination / (name + ".log")
            with log.open("xb") as stream:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
            if result.returncode:
                raise OSError(f"{name} failed; see {log}")
            return log

        listing = run(["dfu-util", "-d", "0483:df11", "-a", "0", "-l"], "device-list").read_text()
        devices = [line for line in listing.splitlines() if "Found DFU:" in line and "alt=0," in line
                   and "@Internal Flash" in line]
        if len(devices) != 1 or "/0x08000000/04*016Kg,01*064Kg,01*128Kg" not in devices[0]:
            raise ValueError("Connect exactly one STM32F401 256 KB keyboard in DFU mode")
        serial = re.search(r'serial="([A-Za-z0-9]+)"', devices[0])
        if not serial:
            raise ValueError("DFU device has no stable serial identity")
        base = ["dfu-util", "-d", "0483:df11", "-S", serial[1], "-a", "0"]
        backup = destination / "original-full-flash.bin"
        run([*base, "-s", "0x08000000:262144", "-U", str(backup)], "full-flash-backup")
        rom = backup.read_bytes()
        decoded = destination / "original-logical-eeprom.bin"
        run([str(args.reader.resolve()), str(backup), str(decoded), "0x4000"], "decode-eeprom")
        settings = json.loads(args.settings.read_text())
        validate_backup(rom, args.factory.read_bytes(), settings, decoded.read_bytes())
        manifest = json.loads((args.firmware / "manifest.json").read_text())
        image = (args.firmware / "keychron-v1-max-agent-bluetooth.bin").read_bytes()
        expected = {"vendor": 0x3434, "product": 0x0913, "logical_size": 3072,
                    "backing_size": 12288, "eeprom_schema": 477, "macro_bytes": 1770}
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("Unrecognized custom firmware profile")
        if hashlib.sha256(image).hexdigest() != manifest["sha256"] or len(image) != manifest["bytes"]:
            raise ValueError("Custom firmware checksum did not match its build manifest")
        if not SETTINGS_END < len(image) < 0x20000 or image[:4] != rom[:4]:
            raise ValueError("Unexpected custom firmware range or stack vector")
        write_json(destination / "validation.json", {"validated": True,
            "original_sha256": hashlib.sha256(rom).hexdigest(), "custom_sha256": manifest["sha256"],
            "settings_backup": str(args.settings.resolve()), "preserved_sector": ["0x08004000", "0x08008000"]})
        print("Full firmware and settings validated. Private backup:", destination, flush=True)
        if not args.install:
            print("Backup only. No flash writes performed.")
            return

        def segments(data, prefix):
            vectors, program = destination / (prefix + "-vectors.bin"), destination / (prefix + "-program.bin")
            vectors.write_bytes(data[:SETTINGS_START])
            program.write_bytes(data[SETTINGS_END:])
            return vectors, program

        def download(path, address, name, leave=False):
            run([*base, "-s", address + (":leave" if leave else ""), "-D", str(path)], name,
                timeout=30 if leave else 120)

        def confirm_application():
            keyboard = Keyboard.open()
            try:
                keyboard.bluetooth_capability()
            finally:
                keyboard.close()

        vectors, program = segments(image, "custom")
        try:
            download(vectors, "0x08000000", "write-vectors")
            download(program, "0x08008000", "write-program")
            verification = destination / "flashed-readback.bin"
            run([*base, "-s", "0x08000000:262144", "-U", str(verification)], "verify-flash")
            actual = verification.read_bytes()
            if len(actual) != FLASH_SIZE:
                raise OSError("Full application readback was truncated")
            if actual[:SETTINGS_START] != image[:SETTINGS_START] or actual[SETTINGS_END:len(image)] != image[SETTINGS_END:]:
                raise OSError("Custom application flash readback differed")
            if actual[SETTINGS_START:SETTINGS_END] != rom[SETTINGS_START:SETTINGS_END]:
                raise OSError("Protected settings sector changed")
            # Standard DFU exit. Keep this final vector write inside recovery handling.
            detached = finish_leave(lambda: download(vectors, "0x08000000", "leave-dfu", leave=True),
                                    confirm_application)
            if detached:
                print("DFU leave stalled after restart; running custom application verified over USB.", flush=True)
        except Exception:
            # One bounded recovery attempt. Never mass-erase or write the settings sector.
            original_vectors, original_program = segments(rom, "recovery")
            download(original_vectors, "0x08000000", "recover-vectors")
            download(original_program, "0x08008000", "recover-program")
            restored = destination / "recovery-readback.bin"
            run([*base, "-s", "0x08000000:262144", "-U", str(restored)], "verify-recovery")
            if restored.read_bytes() != rom:
                raise OSError("Recovery readback differed; keep the device in DFU mode")
            download(original_vectors, "0x08000000", "leave-recovery", leave=True)
            raise OSError("Installation failed. Original firmware was restored and verified")
        write_json(destination / "installed.json", {"verified": True, "sha256": manifest["sha256"],
                   "eeprom_unchanged": True})
        print("Custom main firmware installed; program readback verified and settings sector unchanged.")
        print("Bluetooth lighting still requires an end-to-end radio test before provisioning.")
    finally:
        lock.close()
        start_service()


if __name__ == "__main__":
    main()
