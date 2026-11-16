# Keychron Agent Bridge

A local Mac companion for a Keychron V1 Max ANSI knob keyboard, Claude Code,
and Codex. It uses the original interactive CLIs and native lifecycle hooks.
No additional model account or API key is needed.

## Controls

| Keyboard control | Action |
| --- | --- |
| Fn + turn knob left/right | Select Claude Code or Codex |
| Fn + press knob | Open prompt controls |
| Fn + F9 | Review changes |
| Fn + F10 | Explain clipboard |
| Fn + F11 | Run project checks |
| Fn + F12 | Summarize session |

The normal knob remains volume/mute. Normal typing, the Mac/Windows base layers,
and Fn + 1/2/3 Bluetooth selection remain unchanged. The four listed Fn function
keys, Fn knob press, and Fn encoder actions are replaced while controls are installed.

Without the keyboard, Control + Option + Command + 1 through 4 opens the four
prompts; 5/6 selects the previous/next agent; 7 opens controls.

Prompt shortcuts open an editable draft. Choose a project and click **Open in
Claude Code** or **Open in Codex** to open the official interactive CLI in the
Mac's default application for `.command` files, normally Terminal. Existing login,
project instructions, permissions, and approval prompts remain the CLI's responsibility.
You can also copy a prompt into an existing chat. To summarize existing history,
select **Continue last session** when a matching idle session is available.
New sessions cannot summarize history from another session.

## Status lighting

| Active Claude sessions | Keyboard lighting |
| --- | --- |
| 0 | Steady blue |
| 1 | F9 slowly flashes orange |
| 2 | F9–F10 slowly flash orange |
| 3 | F9–F11 slowly flash orange |
| 4 or more | F9–F12 slowly flash orange |

Only Claude Code sessions count toward orange indicators. Working sessions and
sessions waiting for input count, including background sessions. Finished, idle,
and interrupted sessions do not count. A failed tool still counts until the turn
ends or resumes; a failed turn stops counting. Codex never adds orange keys.
The menu bar shows the full count when more than four sessions are active.
When a parent coordinates active child sessions, the child sessions each get an
indicator and the parent is not counted again. A parent with three active children
therefore shows three indicators. Desktop chat identities also prevent a resumed
CLI process from becoming an extra session.
You do not need to open a session, select an agent, choose a project, or launch
tasks through this app. The knob and agent picker only choose where prompts go.

Lighting uses your original backlight brightness, preserving a lower current
setting and leaving disabled backlighting off. Only occupied F9–F12 indicators pulse,
roughly once every four seconds. All other keys remain steady blue. Animation
runs on the keyboard with no host animation timer or polling. Delayed idle
reminders do not add sessions. **Test session indicators** in the menu previews
zero through four sessions; the next real lifecycle event restores the live count.

Claude Code hooks also target local sessions in Claude Desktop's **Code** tab.
Start a new local Code session after installation to load its hooks. Regular
Chat, Cowork, and remote sessions are not connected by this
local integration.

Live RGB works over USB with the original v1.1.1 firmware. Optional Bluetooth
lighting requires the custom main-firmware extension and a successful radio
verification before the app enables it. The separate Bluetooth radio firmware
is unchanged. See [Bluetooth setup and recovery](../firmware/README.md).
Saved prompt and encoder mappings work over both connections.
Runtime status colors are not saved to EEPROM. Original region assignments and
effect lists are backed up before USB changes. Quitting restores original lighting
on the connected transport. Normal brightness, disabled lighting, pairing indicators,
Caps Lock, battery warnings, and the keyboard's idle timeout remain available.

## First setup

1. Launch `~/Applications/Keychron Agent Bridge.app`.
2. Lighting follows activity automatically. Choose a project in the prompt controls
   only if you want to use prompt shortcuts.
3. In Codex, review the Keychron status hooks in **Settings > Hooks**, or use
   `/hooks` in Codex CLI. Trust is deliberately left to Codex's normal review UI.
4. Start a new Claude Code session to load its status hooks.
5. Connect one V1 Max ANSI knob keyboard by USB, with its switch on Cable.
   The app backs up affected settings, installs controls, and reads them back.
   You can then return to Bluetooth.

Status hooks collect only provider, session ID, project path, lifecycle event,
and an error flag. Parent/child grouping reads local Claude Desktop session
identity metadata, without reading conversation JSONL files. Only session identity
and parent relationships are retained. Hooks do not collect prompts, transcripts, tool arguments,
tool output, or credentials. They use a user-private local Unix socket and always
exit successfully with no output. There is no network server or status polling.

The installed bridge is a menu-bar app and runs at login. macOS restarts it after
a crash or an error exit, with a ten-second restart throttle. It stays running
while the keyboard is unplugged, and reconnects through USB and Bluetooth device
notifications. A Mac wake notification also rediscovers the keyboard and resends status.
Choosing **Quit** intentionally leaves it stopped until you start it again or log in.

After startup or crash recovery, a small event-driven helper restores busy Claude
sessions from Claude's own local process registry. It verifies each process ID,
process start time, and executable name, ignores idle or dead workers, and
skips sessions that already received a new lifecycle event. It runs once per bridge start, without a timer
or polling, so restarting the bridge does not clear indicators for existing work.
This recovery reads session identity and process status, never conversation files.

To recover manually, double-click **Restart Keyboard Lights** on the Desktop,
or search Spotlight for **Restart Keychron Agent Bridge**. The small recovery
app uses macOS's service manager directly, without opening Terminal or relying on
the Python virtual environment. Opening the main packaged app also starts the
supervised login service. Terminal alternatives are `keychron-bridge start` and
`keychron-bridge restart`.

The bridge can monitor sessions without a cable. Wireless live lighting is enabled
only after the optional firmware installation and radio test succeed. USB is preferred
while attached; unplugging USB automatically selects the verified Bluetooth backend.

Its local files live in
`~/Library/Application Support/Keychron Agent Bridge`. Backups are private files.
Normal agent sessions are monitored, not just tasks opened by the bridge.

## Restore and uninstall

Uncheck **Install keyboard controls on USB connection** in the menu to restore
the original affected mappings. If disconnected, connect USB to finish restoring.
Other custom keyboard edits are preserved during installation; unexpected changes
to an affected mapping stop automatic installation instead of overwriting them.

To remove the app, connect the keyboard by USB and run `scripts/uninstall.py`
using the application virtual environment. It restores affected mappings, removes
only this app's hook handlers and login item, and retains backups. Existing agent
permissions and unrelated hooks are preserved.

## Development

Use Python 3.12 on macOS. The runtime dependencies are Cocoa's PyObjC bindings
and hidapi. `requirements-build.txt` includes the test and packaging tools.

```sh
uv venv --python python3.12 "$HOME/Library/Application Support/Keychron Agent Bridge/venv"
uv pip sync --python "$HOME/Library/Application Support/Keychron Agent Bridge/venv/bin/python" requirements.lock
"$HOME/Library/Application Support/Keychron Agent Bridge/venv/bin/python" -m pytest -q
sh scripts/build.sh
"$HOME/Library/Application Support/Keychron Agent Bridge/venv/bin/python" scripts/install.py
```

The hardware tests use a VIA simulator and cover readback, restoration, interrupted
installation, user-edited mappings, and avoiding EEPROM writes for live lighting.

Protocol references: [Codex hooks](https://learn.chatgpt.com/docs/hooks),
[Claude Code hooks](https://code.claude.com/docs/en/hooks), and
[Keychron V1 Max source](https://github.com/Keychron/qmk_firmware/tree/wireless_playground/keyboards/keychron/v1_max).
