# Setup

Use macOS with Python 3.12 and Apple's Command Line Tools. This release was
hardware-tested with the V1 Max ANSI knob model, `3434:0913`, on Apple Silicon.
The simulated test suite does not establish support for other keyboards.

## Prepare and build

```sh
git clone https://github.com/Jacknelson6/keychron-agent-bridge.git
cd keychron-agent-bridge
python3.12 scripts/bootstrap.py --dry-run
python3.12 scripts/bootstrap.py
sh scripts/build.sh
```

Bootstrap prepares a Python environment under
`~/Library/Application Support/Keychron Agent Bridge/venv` and installs the pinned
build dependencies. It does not install hooks, touch a keyboard, or flash firmware.
The build produces `dist/Keychron Agent Bridge.app`, signed locally with an ad-hoc
signature. This first release is source-based, not a notarized app download.

## Review and install

On the first verified USB connection, the app backs up and replaces Fn + F9–F12,
Fn + knob press, and Fn encoder actions on both Fn layers. Base typing,
normal volume/mute, and Fn + 1/2/3 pairing remain unchanged. It also applies
temporary lighting. Review the [controls](USAGE.md) before continuing.

```sh
"$HOME/Library/Application Support/Keychron Agent Bridge/venv/bin/python" scripts/install.py
```

This installs the menu-bar app in `~/Applications`, adds its own Claude/Codex
hook handlers while preserving unrelated settings, and registers login services.
It starts the app. Connect one supported keyboard in Cable mode for USB setup.
Installation never flashes keyboard firmware.

For Claude Code, start a new session to load the hooks. Local Claude Desktop
**Code** sessions are included. Chat, Cowork, and remote sessions are not.
In Codex, review the hooks under **Settings > Hooks** or `/hooks` in Codex CLI.
Normal provider login, project instructions, and approval rules still apply.

## Check the lights

Choose **Test session indicators** in the bridge menu to preview zero through
four sessions. Choose **Follow live Claude sessions** afterward. The next real
lifecycle event also clears a preview.

The default counts working and input-waiting Claude sessions. A coordinating
parent is not an extra light when its active children already have indicators.
Codex hooks and prompt controls work, but the default indicator layout counts
Claude only. Agent selection changes where prompts go, not which sessions count.

## Bluetooth

Prompt mappings work wirelessly after USB installation. Live Bluetooth lighting
also needs the [model-specific firmware procedure](../firmware/README.md).
The source build, backup validation, flash, and radio verification are separate.
Do not use this firmware on a different model or layout.

macOS requires **Input Monitoring** for the packaged app's raw Bluetooth-device
access, even though the bridge only sends LED reports. Choose **Bluetooth access…**
in the bridge menu, enable **Keychron Agent Bridge** in System Settings > Privacy
& Security > Input Monitoring, then quit and reopen it if macOS requests that.
It does not register keyboard input-report callbacks or create an event tap.

If an older locally signed build remains in that list and access stays denied,
remove the entry and add the current `~/Applications/Keychron Agent Bridge.app`.
Rebuilding an ad-hoc signed app can change the identity macOS remembers.

USB is preferred while plugged in. Unplugging it selects the verified Bluetooth
backend. Normal battery-saving idle timeouts still apply; tap a key to wake RGB.

## Startup and recovery

The main LaunchAgent starts at login and restarts failed or crashed runs, with
a restart throttle. A separate, low-priority one-shot job restores busy Claude
sessions after each bridge start. It validates the local registry's PID and
process-start time against the running `claude` process. Newer observed lifecycle
events take precedence. This is a best-effort startup seed, not a status poller.

Double-click **Restart Keyboard Lights** on the Desktop, use Spotlight for
**Restart Keychron Agent Bridge**, or run:

```sh
"$HOME/.local/bin/keychron-bridge" restart
```

Choosing **Quit** intentionally leaves the app stopped until started again.

## Privacy and restore

Local metadata includes provider, session identity, project path, lifecycle event,
and error state. Desktop grouping also reads session identity/parent metadata;
startup recovery reads Claude's local process registry. Conversation JSONL,
prompts, transcripts, tool arguments, tool output, and credentials are not collected.

Files and backups live under `~/Library/Application Support/Keychron Agent Bridge`.
Backups can contain your private keyboard macros. Never attach that directory or
firmware dumps to a public issue. Share sanitized error messages and model details.

To restore mappings, uncheck **Install keyboard controls on USB connection** and
connect USB. To uninstall, connect the keyboard in Cable mode and run:

```sh
"$HOME/Library/Application Support/Keychron Agent Bridge/venv/bin/python" scripts/uninstall.py
```

This restores affected mappings, removes this project's hooks and login jobs,
and retains backups. Other hook handlers and provider permissions are preserved.
