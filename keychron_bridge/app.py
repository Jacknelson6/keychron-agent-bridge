"""Native menu-bar controls and editable prompt drafts."""

import logging
import logging.handlers
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import AppKit as A
import Foundation as F
import objc
from PyObjCTools import AppHelper

from .broker import Broker
from .keyboard import Hardware
from .native import Hotkeys, USBNotifications, request_keyboard_access
from .paths import data_dir, write_json
from .prompts import PRESETS, prepare_task
from .state import AGENTS, LABELS, STATUS_LABELS, State


def label(text, frame, size=13):
    view = A.NSTextField.labelWithString_(text)
    view.setFrame_(frame)
    view.setFont_(A.NSFont.systemFontOfSize_(size))
    return view


class BridgeApp(F.NSObject):
    def applicationDidFinishLaunching_(self, notification):
        try:
            self.initialize_bridge()
        except Exception:
            # PyObjC otherwise logs launch errors and leaves a half-started app
            # alive, preventing launchd's crash recovery from running.
            logging.exception("Bridge startup failed")
            os._exit(1)

    @objc.python_method
    def initialize_bridge(self):
        self.root = data_dir()
        self.state = State(self.root)
        self.window = None
        self.active_preset = "review"
        self.light_override = None
        self.shutting_down = False
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="keyboard-usb")
        self.hardware = Hardware(self.root, lambda: AppHelper.callAfter(self.refresh))
        self.hardware.enabled = bool(self.state.config["keyboard_enabled"])
        self.hardware.lighting_enabled = bool(self.state.config["lighting_enabled"])
        try:
            self.broker = Broker(self.root, lambda message: AppHelper.callAfter(self.receive, message))
        except RuntimeError:
            # An installer can own the singleton lock while replacing firmware.
            # Exit cleanly; its final service start will launch a fresh instance.
            logging.info("Another bridge instance or installer owns the app lock")
            os._exit(0)
        self.broker.start()
        self.status_item = A.NSStatusBar.systemStatusBar().statusItemWithLength_(A.NSVariableStatusItemLength)
        self.hotkeys = Hotkeys(self.action)
        self.usb = USBNotifications(lambda: self.worker.submit(self.hardware.connect),
                                    lambda: self.worker.submit(self.hardware.reconnect))
        self.workspace_notifications = A.NSWorkspace.sharedWorkspace().notificationCenter()
        self.workspace_notifications.addObserver_selector_name_object_(
            self, "workspaceDidWake:", A.NSWorkspaceDidWakeNotification, None)
        self.refresh()
        # One startup discovery, followed exclusively by IOKit device notifications.
        self.worker.submit(self.hardware.connect)
        logging.info("Bridge started; %d shortcuts registered", len(self.hotkeys.references))
        write_json(self.root / "runtime.json", {"pid": os.getpid(), "started_at": time.time(),
            "shortcuts": len(self.hotkeys.references),
            "managed": os.environ.get("KEYCHRON_BRIDGE_MANAGED") == "1"})

    @objc.python_method
    def item(self, menu, title, selector=None, represented=None, checked=False):
        item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
        if selector:
            item.setTarget_(self)
        if represented is not None:
            item.setRepresentedObject_(represented)
        if checked:
            item.setState_(A.NSControlStateValueOn)
        menu.addItem_(item)
        return item

    @objc.python_method
    def refresh(self):
        if self.shutting_down:
            return
        selected = self.state.selected
        count = self.state.claude_session_count()
        title = f"C · {count} running"
        self.status_item.button().setTitle_(title)
        self.status_item.button().setToolTip_(f"{count} active Claude Code sessions; F9–F12 show up to four")
        menu = A.NSMenu.alloc().initWithTitle_("Keychron Agent Bridge")
        self.item(menu, "Keychron Agent Bridge")
        self.item(menu, "Prompt shortcuts go to:")
        for agent in AGENTS:
            self.item(menu, f"{LABELS[agent]}: {STATUS_LABELS[self.state.status(agent)]}",
                      "chooseAgent:", agent, selected == agent)
        menu.addItem_(A.NSMenuItem.separatorItem())
        self.item(menu, "Open prompt controls…", "openControls:")
        for key, preset in PRESETS.items():
            self.item(menu, preset.title + "…", "choosePreset:", key)
        menu.addItem_(A.NSMenuItem.separatorItem())
        self.item(menu, self.hardware.message)
        self.item(menu, "Install keyboard controls on USB connection", "toggleKeyboard:",
                  checked=bool(self.state.config["keyboard_enabled"]))
        self.item(menu, "Live status lighting", "toggleLighting:",
                  checked=bool(self.state.config["lighting_enabled"]))
        self.item(menu, f"Claude sessions: {count}" + (" (showing 4)" if count > 4 else ""))
        self.item(menu, "Reconnect keyboard lighting", "retryUSB:")
        self.item(menu, "Bluetooth access…", "allowBluetooth:")
        lights = A.NSMenu.alloc().initWithTitle_("Test session indicators")
        for number in range(5):
            self.item(lights, f"{number} Claude sessions", "testLight:", str(number))
        self.item(lights, "Follow live Claude sessions", "testLight:", "live")
        light_item = self.item(menu, "Test session indicators")
        light_item.setEnabled_(True)
        light_item.setSubmenu_(lights)
        self.item(menu, "Setup and shortcuts…", "showSetup:")
        if self.hotkeys.errors:
            self.item(menu, "Some shortcuts are already in use. See setup.")
        menu.addItem_(A.NSMenuItem.separatorItem())
        self.item(menu, "Quit Keychron Agent Bridge", "quit:")
        self.status_item.setMenu_(menu)
        self.worker.submit(self.hardware.apply_sessions,
                           count if self.light_override is None else self.light_override)
        if self.window and self.window.isVisible():
            self.update_window()

    @objc.python_method
    def receive(self, message):
        if self.shutting_down:
            return
        try:
            if message.get("kind") == "event":
                self.state.update(message)
                self.light_override = None
            elif message.get("kind") == "action":
                self.action(message.get("action", ""))
                return
            self.refresh()
        except (ValueError, TypeError, KeyError):
            logging.warning("Ignored invalid lifecycle message")

    @objc.python_method
    def action(self, name):
        if name in ("previous", "next"):
            self.state.cycle(-1 if name == "previous" else 1)
            self.light_override = None
            self.refresh()
        elif name == "panel":
            self.show_window(None)
        elif name in PRESETS:
            self.show_window(name)
        elif name == "quit":
            A.NSApp.terminate_(None)
        elif name == "allow-bluetooth":
            self.allowBluetooth_(None)

    def chooseAgent_(self, sender):
        self.state.select(str(sender.representedObject()))
        self.light_override = None
        self.refresh()

    def choosePreset_(self, sender):
        self.show_window(str(sender.representedObject()))

    def openControls_(self, sender):
        self.show_window(None)

    def retryUSB_(self, sender):
        self.worker.submit(self.hardware.reconnect)

    def allowBluetooth_(self, sender):
        if request_keyboard_access():
            self.worker.submit(self.hardware.reconnect)
        else:
            A.NSWorkspace.sharedWorkspace().openURL_(F.NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"))

    def workspaceDidWake_(self, notification):
        if not self.shutting_down:
            self.worker.submit(self.hardware.reconnect)

    def toggleKeyboard_(self, sender):
        enabled = not self.state.config["keyboard_enabled"]
        self.state.config["keyboard_enabled"] = enabled
        self.state.save_config()
        if enabled:
            self.hardware.enabled = True
            def reconnect():
                self.hardware.close()
                self.hardware.connect()
            self.worker.submit(reconnect)
        else:
            self.worker.submit(self.hardware.restore_controls)
        self.refresh()

    def toggleLighting_(self, sender):
        self.state.config["lighting_enabled"] = not self.state.config["lighting_enabled"]
        self.state.save_config()
        self.hardware.lighting_enabled = self.state.config["lighting_enabled"]
        if not self.hardware.lighting_enabled:
            self.worker.submit(self.hardware.restore_lighting)
        self.refresh()

    def testLight_(self, sender):
        value = str(sender.representedObject())
        self.light_override = None if value == "live" else int(value)
        self.refresh()

    @objc.python_method
    def button(self, content, title, selector, frame):
        button = A.NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(A.NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(selector)
        content.addSubview_(button)
        return button

    @objc.python_method
    def build_window(self):
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (640, 590)), A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered, False)
        self.window.setTitle_("Keychron Agent Bridge")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        content = self.window.contentView()
        content.addSubview_(label("Choose an agent and review the prompt before opening its CLI.",
                                 ((24, 546), (592, 22)), 13))
        self.agent_picker = A.NSSegmentedControl.alloc().initWithFrame_(((24, 504), (310, 30)))
        self.agent_picker.setSegmentCount_(2)
        for index, agent in enumerate(AGENTS):
            self.agent_picker.setLabel_forSegment_(LABELS[agent], index)
            self.agent_picker.setWidth_forSegment_(150, index)
        self.agent_picker.setTarget_(self)
        self.agent_picker.setAction_("agentChanged:")
        content.addSubview_(self.agent_picker)
        self.agent_status = label("", ((350, 507), (266, 22)))
        content.addSubview_(self.agent_status)
        self.project_label = label("Choose a project folder", ((24, 462), (470, 24)))
        self.project_label.setLineBreakMode_(A.NSLineBreakByTruncatingMiddle)
        content.addSubview_(self.project_label)
        self.button(content, "Choose…", "chooseProject:", ((514, 458), (104, 30)))
        self.button(content, "Use last agent project", "useLastProject:", ((20, 423), (185, 28)))
        self.preset_picker = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(((24, 379), (280, 30)), False)
        self.preset_picker.addItemsWithTitles_([preset.title for preset in PRESETS.values()])
        self.preset_picker.setTarget_(self)
        self.preset_picker.setAction_("presetChanged:")
        content.addSubview_(self.preset_picker)
        self.resume = A.NSButton.checkboxWithTitle_target_action_("Continue last session", self, "resumeChanged:")
        self.resume.setFrame_(((332, 383), (280, 24)))
        content.addSubview_(self.resume)
        scroll = A.NSScrollView.alloc().initWithFrame_(((24, 115), (592, 250)))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(A.NSBezelBorder)
        self.editor = A.NSTextView.alloc().initWithFrame_(((0, 0), (570, 250)))
        self.editor.setRichText_(False)
        self.editor.setFont_(A.NSFont.monospacedSystemFontOfSize_weight_(12, A.NSFontWeightRegular))
        self.editor.setTextContainerInset_((10, 10))
        scroll.setDocumentView_(self.editor)
        content.addSubview_(scroll)
        self.note = label("", ((24, 67), (592, 40)), 11)
        self.note.setMaximumNumberOfLines_(2)
        content.addSubview_(self.note)
        self.open_button = self.button(content, "Open in Codex", "sendPrompt:", ((420, 20), (200, 34)))
        self.open_button.setKeyEquivalent_("\r")
        self.open_button.setKeyEquivalentModifierMask_(A.NSEventModifierFlagCommand)
        self.button(content, "Copy prompt", "copyPrompt:", ((20, 20), (130, 34)))

    @objc.python_method
    def show_window(self, preset):
        if not self.window:
            self.build_window()
        if preset is not None or not self.editor.string():
            self.active_preset = preset or self.active_preset
            self.fill_preset()
        self.update_window()
        if preset == "summary" and self.resumable_session():
            self.resume.setState_(A.NSControlStateValueOn)
        A.NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def fill_preset(self):
        clipboard = ""
        if self.active_preset == "explain":
            clipboard = str(A.NSPasteboard.generalPasteboard().stringForType_(A.NSPasteboardTypeString) or "")[:50000]
        self.editor.setString_(PRESETS[self.active_preset].prompt.replace("{clipboard}", clipboard))
        self.preset_picker.selectItemAtIndex_(list(PRESETS).index(self.active_preset))
        self.resume.setState_(A.NSControlStateValueOff)

    @objc.python_method
    def resumable_session(self):
        project = self.state.config["project"]
        session = self.state.latest(project=project) if project else None
        if not session or not session.can_continue or not project:
            return None
        try:
            uuid.UUID(session.session_id)
            if Path(session.cwd).resolve() == Path(project).resolve():
                return session
        except (ValueError, OSError):
            pass
        return None

    @objc.python_method
    def update_window(self):
        selected = self.state.selected
        self.agent_picker.setSelectedSegment_(AGENTS.index(selected))
        self.agent_status.setStringValue_(STATUS_LABELS[self.state.status()])
        project = self.state.config["project"]
        self.project_label.setStringValue_(project or "Choose a project folder")
        self.open_button.setTitle_("Open in " + LABELS[selected])
        self.open_button.setEnabled_(bool(project and Path(project).is_dir()))
        resumable = self.resumable_session()
        self.resume.setEnabled_(resumable is not None)
        if not resumable:
            self.resume.setState_(A.NSControlStateValueOff)
        note = "Opens the original interactive CLI with its normal permission prompts."
        if self.active_preset == "summary" and not resumable:
            note = "No idle session is available for this project. A new session can only summarize visible project changes."
        self.note.setStringValue_(note)

    def agentChanged_(self, sender):
        self.state.select(AGENTS[int(sender.selectedSegment())])
        self.resume.setState_(A.NSControlStateValueOff)
        self.refresh()

    def presetChanged_(self, sender):
        self.active_preset = list(PRESETS)[int(sender.indexOfSelectedItem())]
        self.fill_preset()
        self.update_window()

    def resumeChanged_(self, sender):
        pass

    def chooseProject_(self, sender):
        panel = A.NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        if panel.runModal() == A.NSModalResponseOK:
            self.state.config["project"] = str(panel.URL().path())
            self.state.save_config()
            self.update_window()

    def useLastProject_(self, sender):
        session = self.state.latest()
        if session and session.cwd and Path(session.cwd).is_dir():
            self.state.config["project"] = session.cwd
            self.state.save_config()
            self.update_window()
        else:
            self.alert("Choose a project", "No recent project has arrived from this agent's hooks yet.")

    def sendPrompt_(self, sender):
        try:
            session = self.resumable_session() if self.resume.state() == A.NSControlStateValueOn else None
            launcher = prepare_task(self.root, self.state.selected, self.state.config["project"],
                                    str(self.editor.string()), session.session_id if session else None)
            opened = A.NSWorkspace.sharedWorkspace().openURL_(F.NSURL.fileURLWithPath_(str(launcher)))
            if not opened:
                raise OSError("macOS could not open the CLI launcher. Check the default application for .command files.")
            self.note.setStringValue_("Opened in the agent's CLI. Lifecycle hooks will update its status here.")
        except (OSError, ValueError, KeyError) as error:
            self.alert("Could not open the agent", str(error))

    def copyPrompt_(self, sender):
        board = A.NSPasteboard.generalPasteboard()
        board.clearContents()
        board.setString_forType_(str(self.editor.string()), A.NSPasteboardTypeString)
        self.note.setStringValue_("Prompt copied. You can paste it into an existing agent chat.")

    @objc.python_method
    def alert(self, title, detail):
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(detail)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    def showSetup_(self, sender):
        detail = ("Hold Fn and turn the knob to select Claude Code or Codex. Fn + knob opens prompt controls.\n\n"
                  "Fn + F9: Review changes\nFn + F10: Explain clipboard\nFn + F11: Run project checks\nFn + F12: Summarize session\n\n"
                  "Without the keyboard: Control + Option + Command + 1–4 opens those prompts; 5/6 selects an agent; 7 opens controls.\n\n"
                  "F9–F12 show active Claude sessions: one slowly flashing orange key per session, up to four. "
                  "The rest stay blue at your normal backlight brightness; disabled lighting stays off.\n\n"
                  "Working sessions and sessions waiting for your input count automatically, even in the background. "
                  "Codex and idle or finished Claude sessions do not add orange keys. "
                  "The knob selects where prompt shortcuts go; it does not select lighting.\n\n"
                  "Codex: review the new Keychron hooks in Settings > Hooks or /hooks. "
                  "Claude Code, including local Desktop Code sessions: start a new session to load its hooks.\n\n"
                  "Connect one V1 Max ANSI knob keyboard by USB to install mappings automatically. They also work over Bluetooth afterward. "
                  "Bluetooth lighting requires the verified bridge firmware. USB works with the original firmware. "
                  "Disable keyboard controls in this menu to restore the original mappings.")
        if self.hotkeys.errors:
            detail += "\n\nUnavailable shortcuts:\n" + "\n".join(self.hotkeys.errors)
        self.alert("Keychron Agent Bridge setup", detail)

    def quit_(self, sender):
        A.NSApp.terminate_(None)

    def applicationShouldTerminateAfterLastWindowClosed_(self, application):
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, application, visible):
        self.show_window(None)
        return True

    def applicationShouldTerminate_(self, application):
        self.shutting_down = True
        self.hotkeys.close()
        self.usb.close()
        self.workspace_notifications.removeObserver_(self)
        self.worker.shutdown(wait=True, cancel_futures=True)
        self.hardware.close()
        self.broker.close()  # Releasing the app lock also confirms the HID handle is closed.
        return A.NSTerminateNow


def main():
    if getattr(sys, "frozen", False) and os.environ.get("KEYCHRON_BRIDGE_MANAGED") != "1":
        # Finder launches should also run under crash supervision.
        from .service import start_service
        start_service()
        return
    root = data_dir()
    handler = logging.handlers.RotatingFileHandler(root / "bridge.log", maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    application = A.NSApplication.sharedApplication()
    application.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    delegate = BridgeApp.alloc().init()
    application.setDelegate_(delegate)
    AppHelper.runEventLoop()
