# Contributing

Help make physical agent displays useful, quiet, and reliable. Good first changes
include visual presets, provider fixtures, setup improvements, and verified
keyboard ports. Discuss a new model or firmware approach in an issue first.

## Develop without a keyboard

```sh
git clone https://github.com/Jacknelson6/keychron-agent-bridge.git
cd keychron-agent-bridge
python3.12 -m venv .venv
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
sh tests/firmware/run_agent_protocol_tests.sh
.venv/bin/python examples/custom_visual.py --agent codex --status working
```

Pure Python fixtures and the native decoder run on Linux or macOS. That does not
make the Mac app available on Linux. UI calls are mocked in tests. App/hardware
work needs macOS and the dependencies in the setup guide. Native scripts use the
active compiler/SDK; `CC`, `SDKROOT`, and `MACOSX_DEPLOYMENT_TARGET` can override them.

Keep changes focused. Preserve backup, readback, and restoration paths. Tests
should demonstrate behavior and meaningful failures, not perform hardware writes.
Do not install hooks, start login jobs, or flash firmware in automated tests.

## Hardware reports

Include exact model, ANSI/ISO/JIS layout, knob variant, VID/PID, firmware,
operating system, transport, and checks run. Record typing, pairing, Caps Lock,
brightness, disabled lighting, idle timeout, backup/restore, and reconnect results.
Use simulated fixtures first. Source similarity is not proof of hardware support.

Do not commit dumps, macros, private configuration, provider sessions, credentials,
or unsanitized logs. Use [private reporting](SECURITY.md) for security problems.

## License and artwork

Contributions are GPL-2.0-or-later. Preserve upstream notices. The blue/orange
artwork and generation prompts are in [assets](assets/README.md). Use branding
without implying endorsement by a keyboard or AI vendor.
