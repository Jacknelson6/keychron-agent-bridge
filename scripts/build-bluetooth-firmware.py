#!/usr/bin/env python3
"""Build only the pinned V1 Max extension. This command never flashes hardware."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

SOURCE_REVISION = "666862cb8123b64a6b96718d739c6203ad99031f"
BUILD_DATE = "2025-04-30-10:37:08"
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qmk-root", type=Path, required=True)
    parser.add_argument("--toolchain", type=Path, required=True)
    parser.add_argument("--qmk-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "build/bluetooth-firmware")
    args = parser.parse_args()
    qmk = args.qmk_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(qmk), "rev-parse", "HEAD"], text=True).strip()
    if revision != SOURCE_REVISION:
        raise SystemExit("Use a dedicated staging checkout at the pinned Keychron source revision")
    parent = qmk / "keyboards/keychron/v1_max/info.json"
    info = json.loads(parent.read_text())
    info["eeprom"]["wear_leveling"].update(logical_size=3072, backing_size=12288)
    info["build"]["debounce_type"] = "custom"
    parent.write_text(json.dumps(info, indent=4) + "\n")
    board = qmk / "keyboards/keychron/v1_max/ansi_encoder/info.json"
    info = json.loads(board.read_text())
    info["usb"]["device_version"] = "1.1.1"
    board.write_text(json.dumps(info, indent=4) + "\n")
    version = qmk / "lib/python/qmk/cli/generate/version_h.py"
    text = version.read_text()
    old = "current_time = strftime(TIME_FMT)"
    replacement = f'current_time = "{BUILD_DATE}"'
    if old in text:
        version.write_text(text.replace(old, replacement))
    elif replacement not in text:
        raise SystemExit("Unexpected firmware build-date generator")
    task = qmk / "keyboards/keychron/common/keychron_task.c"
    text = task.read_text()
    hook = "bool rgb_matrix_indicators_keychron(void) {"
    inserted = hook + "\n#ifdef KEYCHRON_AGENT_LIGHTS\n    extern void keychron_agent_rgb_overlay(void);\n    keychron_agent_rgb_overlay();\n#endif"
    if "keychron_agent_rgb_overlay" not in text:
        if text.count(hook) != 1:
            raise SystemExit("Unexpected Keychron indicator dispatch")
        task.write_text(text.replace(hook, inserted))
    wireless = qmk / "keyboards/keychron/common/wireless/wireless.c"
    text = wireless.read_text()
    if "keychron_agent_wireless_event" not in text:
        for state, callback in (("WT_CONNECTED", "wireless_enter_connected_kb(host_idx);"),
                                ("WT_DISCONNECTED", "wireless_enter_disconnected_kb(host_idx, reason);"),
                                ("WT_PARING", "wireless_enter_discoverable_kb(host_idx);")):
            if text.count(callback) != 1:
                raise SystemExit("Unexpected Keychron wireless state dispatch")
            text = text.replace(callback, callback + "\n#ifdef KEYCHRON_AGENT_LIGHTS\n"
                "    extern void keychron_agent_wireless_event(uint8_t, uint8_t);\n"
                f"    keychron_agent_wireless_event(host_idx, {state});\n#endif")
        wireless.write_text(text)
    target = qmk / "keyboards/keychron/v1_max/ansi_encoder/keymaps/keychron_agent"
    shutil.copytree(ROOT / "firmware/keychron_agent", target, dirs_exist_ok=True)
    environment = dict(os.environ)
    environment["PATH"] = str(args.toolchain.resolve()) + ":" + str(args.qmk_bin.resolve().parent) + ":" + environment["PATH"]
    environment["QMK_HOME"] = str(qmk)
    command = ["make", "-j2", "keychron/v1_max/ansi_encoder:keychron_agent",
               "KEYCHRON_RGB_ENABLE=yes", "SNAP_CLICK_ENABLE=yes", "DEBOUNCE_TYPE=custom"]
    subprocess.run(command, cwd=qmk, env=environment, check=True)
    stem = "keychron_v1_max_ansi_encoder_keychron_agent"
    binary, elf = qmk / (stem + ".bin"), qmk / ".build" / (stem + ".elf")
    sections = subprocess.check_output([str(args.toolchain / "arm-none-eabi-objdump"), "-h", str(elf)], text=True)
    lines = sections.splitlines()
    for index, line in enumerate(lines[:-1]):
        parts = line.split()
        if len(parts) >= 7 and parts[0].isdigit() and "LOAD" in lines[index + 1]:
            size, address = int(parts[2], 16), int(parts[4], 16)
            if size and address < 0x08008000 and address + size > 0x08004000:
                raise SystemExit("Firmware overlaps the protected settings sector")
    image = binary.read_bytes()
    stack, reset = int.from_bytes(image[:4], "little"), int.from_bytes(image[4:8], "little")
    if not 0x20000000 < stack <= 0x20010000 or not reset & 1 or not 0x08000000 < reset < 0x08040000:
        raise SystemExit("Unexpected STM32F401 application vectors")
    args.output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, args.output / "keychron-v1-max-agent-bluetooth.bin")
    shutil.copy2(elf, args.output / "keychron-v1-max-agent-bluetooth.elf")
    manifest = {"source_revision": revision, "build_date": BUILD_DATE,
                "sha256": hashlib.sha256(image).hexdigest(), "bytes": len(image),
                "vendor": 0x3434, "product": 0x0913, "eeprom_sector": [0x08004000, 0x08008000],
                "logical_size": 3072, "backing_size": 12288, "eeprom_schema": 477,
                "via_magic_address": 514, "macro_bytes": 1770}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output / "sections.txt").write_text(sections)
    print("Built without flashing:", args.output)


if __name__ == "__main__":
    main()
