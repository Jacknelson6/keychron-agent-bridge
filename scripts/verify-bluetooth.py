#!/usr/bin/env python3
"""Prove BLE status reaches the main MCU, then enable the Mac BLE backend."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keychron_bridge.bluetooth import BluetoothKeyboard, indicator_byte
from keychron_bridge.integration import stop_bridge
from keychron_bridge.keyboard import Keyboard
from keychron_bridge.paths import data_dir, write_json
from keychron_bridge.service import start_service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision", action="store_true")
    args = parser.parse_args()
    root = data_dir()
    lock = stop_bridge(root)
    keyboard = bluetooth = None
    try:
        keyboard = Keyboard.open()
        before = keyboard.bluetooth_capability()
        if before["transport"] != 2 or before["wireless_state"] != 3:
            raise OSError("Keep USB plugged in, select BT, and connect the keyboard to this Mac")
        bluetooth = BluetoothKeyboard()
        for count in (0, 1, 2, 3, 4, None):
            bluetooth.show(count)
            actual = keyboard.bluetooth_capability()
            if actual["raw"] & ~2 != indicator_byte(count) or actual["enabled"] != (count is not None):
                raise OSError("Main MCU did not acknowledge the Bluetooth indicator message")
            if actual["count"] != (0 if count is None else count):
                raise OSError("Bluetooth session count readback differed")
        print("Radio-to-main-MCU status verified for counts 0–4 and disable.")
        if args.provision:
            write_json(root / "bluetooth-firmware.json", {"verified": True, "protocol": 1,
                       "vendor": 0x3434, "product": 0x0913})
            print("Bluetooth session lighting enabled for this Mac.")
    finally:
        if bluetooth:
            try:
                bluetooth.show(None)
            except OSError:
                pass
            finally:
                bluetooth.close()
        if keyboard:
            keyboard.close()
        lock.close()
        start_service()


if __name__ == "__main__":
    main()
