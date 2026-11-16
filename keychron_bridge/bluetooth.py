"""Shared, output-only BLE indicators. No input callbacks or keyboard seizure."""

import ctypes as C

from .native import bind
from .paths import read_json


def indicator_byte(count, caps_lock=False):
    """Encode one atomic status report, preserving macOS's Caps Lock bit."""
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        raise ValueError("Session count must be a nonnegative integer")
    value = 15 if count is None else 8 + min(count, 4)
    return (value & 1) | ((value & 14) << 1) | (2 if caps_lock else 0)


def provisioned(root):
    config = read_json(root / "bluetooth-firmware.json", {})
    return (isinstance(config, dict) and config.get("verified") is True
            and type(config.get("protocol")) is int and config.get("protocol") == 1 and config.get("vendor") == 0x3434
            and config.get("product") == 0x0913)


class BluetoothKeyboard:
    def __init__(self):
        import Foundation as F
        import objc
        self.io = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.manager = self.devices = self.device = None
        self.opened = False
        P, U, I, L = C.c_void_p, C.c_uint32, C.c_int32, C.c_long
        self.release = bind(self.cf, "CFRelease", [P], None)
        self.manager = bind(self.io, "IOHIDManagerCreate", [P, U], P)(None, 8)
        if not self.manager:
            raise OSError("Cannot create shared Bluetooth output access")
        self.match = F.NSDictionary.dictionaryWithDictionary_({
            "VendorID": 0x3434, "ProductID": 0x0913, "PrimaryUsagePage": 1,
            "PrimaryUsage": 6, "Transport": "Bluetooth Low Energy"})
        try:
            bind(self.io, "IOHIDManagerSetDeviceMatching", [P, P], None)(self.manager, objc.pyobjc_id(self.match))
            # Discover without opening all matched input devices. Only the
            # selected device's output interface is opened below, in shared mode.
            self.devices = bind(self.io, "IOHIDManagerCopyDevices", [P], P)(self.manager)
            count = bind(self.cf, "CFSetGetCount", [P], L)(self.devices) if self.devices else 0
            if count != 1:
                raise OSError("Connect one paired V1 Max over Bluetooth")
            values = (P * count)()
            bind(self.cf, "CFSetGetValues", [P, P], None)(self.devices, C.cast(values, P))
            self.device = values[0]
            status = bind(self.io, "IOHIDDeviceOpen", [P, U], I)(self.device, 0)
            if status & 0xffffffff == 0xe00002e2:
                raise PermissionError("Allow Keychron Agent Bridge in Privacy & Security > Input Monitoring for Bluetooth lighting")
            if status:
                raise OSError(f"Cannot open shared Bluetooth output ({status})")
            self.opened = True
            self.send = bind(self.io, "IOHIDDeviceSetReport", [P, U, L, P, L], I)
        except Exception:
            self.close()
            raise

    def show(self, count):
        import AppKit as A
        caps = bool(A.NSEvent.modifierFlags() & A.NSEventModifierFlagCapsLock)
        # Numbered macOS reports include ID 1 in the buffer as well as the API argument.
        payload = (C.c_uint8 * 2)(1, indicator_byte(count, caps))
        status = self.send(self.device, 1, 1, C.cast(payload, C.c_void_p), 2)
        if status:
            raise OSError(f"Bluetooth status output failed ({status})")

    def close(self):
        P, U, I = C.c_void_p, C.c_uint32, C.c_int32
        if self.opened:
            bind(self.io, "IOHIDDeviceClose", [P, U], I)(self.device, 0)
            self.opened = False
        if self.devices:
            self.release(self.devices)
            self.devices = None
        if self.manager:
            self.release(self.manager)
            self.manager = None
