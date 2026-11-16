"""Carbon hotkeys and IOKit device notifications, without key logging."""

import ctypes as C
import sys

HOTKEYS = {105: "review", 107: "explain", 113: "checks", 106: "summary",
           64: "previous", 79: "next", 80: "panel"}


def bind(library, name, arguments, result):
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result
    return function


def request_keyboard_access():
    """Ask macOS for raw keyboard-device access, without creating an input tap."""
    graphics = C.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    return bool(bind(graphics, "CGRequestListenEventAccess", [], C.c_bool)())


class HotkeyID(C.Structure):
    _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]


class EventType(C.Structure):
    _fields_ = [("event_class", C.c_uint32), ("kind", C.c_uint32)]


class Hotkeys:
    def __init__(self, action):
        self.carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self.action, self.references, self.actions, self.errors = action, [], {}, []
        P, U = C.c_void_p, C.c_uint32
        self.callback_type = C.CFUNCTYPE(C.c_int32, P, P, P)
        self.callback = self.callback_type(self._handle)
        self.target = bind(self.carbon, "GetApplicationEventTarget", [], P)()
        install = bind(self.carbon, "InstallEventHandler", [P, self.callback_type, U,
                       C.POINTER(EventType), P, C.POINTER(P)], C.c_int32)
        self.get_parameter = bind(self.carbon, "GetEventParameter", [P, U, U, C.POINTER(U),
                                 U, C.POINTER(U), P], C.c_int32)
        self.unregister = bind(self.carbon, "UnregisterEventHotKey", [P], C.c_int32)
        self.handler = P()
        event = EventType(0x6B657962, 6)  # kEventClassKeyboard, kEventHotKeyPressed
        status = install(self.target, self.callback, 1, C.byref(event), None, C.byref(self.handler))
        if status:
            raise OSError(f"Cannot install global shortcut handler ({status})")
        register = bind(self.carbon, "RegisterEventHotKey", [U, U, HotkeyID, P, U, C.POINTER(P)], C.c_int32)
        entries = [(key, 0, action) for key, action in HOTKEYS.items()]
        entries += [(key, 0x1900, action) for key, action in zip(
            (18, 19, 20, 21, 23, 22, 26),
            ("review", "explain", "checks", "summary", "previous", "next", "panel"))]
        for identifier, (key, modifiers, name) in enumerate(entries, 1):
            reference = P()
            status = register(key, modifiers, HotkeyID(0x4B414252, identifier),
                              self.target, 0, C.byref(reference))
            if status:
                self.errors.append(f"{name} shortcut unavailable ({status})")
            else:
                self.references.append(reference)
                self.actions[identifier] = name

    def _handle(self, next_handler, event, context):
        try:
            identity = HotkeyID()
            result = self.get_parameter(event, 0x2D2D2D2D, 0x686B6964, None,
                                        C.sizeof(identity), None, C.byref(identity))
            if result == 0 and identity.signature == 0x4B414252 and identity.id in self.actions:
                self.action(self.actions[identity.id])
                return 0
        except Exception:
            import logging
            logging.exception("Global shortcut failed")
        return -9874  # eventNotHandledErr

    def close(self):
        for reference in self.references:
            self.unregister(reference)
        if self.handler:
            bind(self.carbon, "RemoveEventHandler", [C.c_void_p], C.c_int32)(self.handler)


class USBNotifications:
    def __init__(self, attached, removed):
        import Foundation
        import objc
        self.iokit = C.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        P = C.c_void_p
        self.callback_type = C.CFUNCTYPE(None, P, C.c_int32, P, P)
        self.on_add = self.callback_type(lambda *_: attached())
        self.on_remove = self.callback_type(lambda *_: removed())
        self.manager = bind(self.iokit, "IOHIDManagerCreate", [P, C.c_uint32], P)(None, 8)
        if not self.manager:
            raise OSError("Cannot create the USB device monitor")
        usb = {
            "VendorID": 0x3434, "ProductID": 0x0913, "PrimaryUsagePage": 0xFF60,
            "PrimaryUsage": 0x61, "Transport": "USB"}
        bluetooth = {"VendorID": 0x3434, "ProductID": 0x0913, "PrimaryUsagePage": 1,
                     "PrimaryUsage": 6, "Transport": "Bluetooth Low Energy"}
        self.match = Foundation.NSArray.arrayWithArray_([usb, bluetooth])
        bind(self.iokit, "IOHIDManagerSetDeviceMatchingMultiple", [P, P], None)(self.manager, objc.pyobjc_id(self.match))
        for name, callback in [("IOHIDManagerRegisterDeviceMatchingCallback", self.on_add),
                               ("IOHIDManagerRegisterDeviceRemovalCallback", self.on_remove)]:
            bind(self.iokit, name, [P, self.callback_type, P], None)(self.manager, callback, None)
        self.loop = bind(self.cf, "CFRunLoopGetMain", [], P)()
        self.mode = P.in_dll(self.cf, "kCFRunLoopDefaultMode")
        bind(self.iokit, "IOHIDManagerScheduleWithRunLoop", [P, P, P], None)(self.manager, self.loop, self.mode)
        # Discovery notifications need no input-device open. Opening a manager
        # that matches a BLE keyboard requests Input Monitoring on macOS.

    def close(self):
        if self.manager:
            P = C.c_void_p
            bind(self.iokit, "IOHIDManagerUnscheduleFromRunLoop", [P, P, P], None)(self.manager, self.loop, self.mode)
            bind(self.cf, "CFRelease", [P], None)(self.manager)
            self.manager = None
