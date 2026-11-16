# Build your own visuals

Start with a signal and a physical vocabulary: what deserves attention, which
keys should carry it, and how much motion is useful? The supplied layout counts
Claude sessions. The event and keyboard modules can drive other ideas.

## Try a palette without hardware

```sh
python3.12 examples/custom_visual.py --agent claude --status working
python3.12 examples/custom_visual.py --agent codex --status waiting
```

The example's `frame_for(agent, status)` turns semantic state into a VIA effect
and HSV color. It uses blue for Codex, orange for Claude, amber for attention,
green for completion, and red for failures. Default execution only prints JSON.

For a temporary whole-keyboard USB preview, quit the menu-bar bridge first,
connect the verified keyboard, and add `--usb`. Press Return to restore the
original lighting. The example preserves brightness, respects disabled lighting,
does not remap keys, and never saves colors to EEPROM. Use the app's normal
restart shortcut afterward. This example is separate from the four-key layout.

## Follow the event path

1. [hook.py](../keychron_bridge/hook.py) normalizes provider hook input. Keep only
   fields needed for state, not prompts or tool content.
2. [broker.py](../keychron_bridge/broker.py) receives metadata through a private
   local Unix socket.
3. [state.py](../keychron_bridge/state.py) maps lifecycle events into `working`,
   `waiting`, `done`, `failed`, or `interrupted` state. `status("codex")` and
   `status("claude")` are provider-specific; `light_feedback()` considers both.
4. [app.py](../keychron_bridge/app.py), in `BridgeApp.refresh`, chooses a visual.
   The default calls `claude_session_count()` and `Hardware.apply_sessions()`.
5. [keyboard.py](../keychron_bridge/keyboard.py) and
   [session_lights.py](../keychron_bridge/session_lights.py) handle USB transactions,
   restoration, and the default mixed-effect layout.

For a status-based Codex display, replace the refresh method's renderer submission
with a mapping from `self.state.status("codex")` to your frame. Use the example's
palette logic with the USB lighting methods. The existing `Hardware.apply_status`
is another starting point; it uses the `LIGHTS` table and a dim brightness cap.
Changing `LIGHTS` alone does not change the default Claude-count renderer.

Keep the worker-thread boundary, device lock, baseline backup, and restore path.
If a visual has a completion timeout, schedule one transition; do not create
a host animation polling loop. Prefer effects that animate on the keyboard.

## Feed a development instance

Use a separate private data directory so diagnostics do not enter normal state:

```sh
export KEYCHRON_BRIDGE_HOME="$PWD/.demo-data"
python3.12 -m keychron_bridge.app
# In another terminal with the same KEYCHRON_BRIDGE_HOME:
python3.12 -m keychron_bridge.cli event --agent codex --event PreToolUse --session-id demo-codex
python3.12 -m keychron_bridge.cli event --agent codex --event Stop --session-id demo-codex
```

The source app requires macOS/PyObjC. Quit the installed app first if using
hardware. For event-only work, create `.demo-data/config.json` with both
`keyboard_enabled` and `lighting_enabled` set to false before starting it.
Do not change normal provider hooks just to send diagnostics.

## Add a keyboard or agent

The current transport guard is `3434:0913`; its matrix is 6 × 16 with 81 LEDs.
A port needs its own model/firmware identification, Fn coordinates, LED indices,
capacity checks, simulator fixtures, backup/restore validation, and actual hardware
evidence. Keep unverified models unavailable by default. Do not remove the guard
to claim another keyboard works.

Bluetooth is not a generic QMK raw-HID channel. Another model needs descriptor
and transport verification before reusing this protocol. Read the
[firmware guide](../firmware/README.md) before changing that path.

For a new agent, extend allowed providers, labels, hook normalization/configuration,
and state tests together. Normalize real lifecycle signals; count leaf sessions
instead of process names or open windows. Keep the original agent's login, tools,
and approval flow in charge. See [contributing](../CONTRIBUTING.md).
