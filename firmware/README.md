# Bluetooth session lighting

This extension targets only the Keychron V1 Max ANSI encoder, USB 3434:0913,
STM32F401 with 256 KB flash. It is optional. The normal USB lighting backend
continues to work with factory firmware.

The Mac uses shared, output-only IOKit access to Bluetooth HID report 1.
Unused Num Lock, Scroll Lock, Compose, and Kana indicator bits encode counts 0–4
or disable. Caps Lock bit 1 is copied from the Mac's current modifier state.
There are no keyboard input callbacks, event taps, keyboard seizure, or
radio firmware changes. This encoding is intended for this verified Mac setup;
other hosts using those additional lock indicators can overlap the status codes.

macOS requires its **Input Monitoring** permission for the packaged app to open
the Bluetooth keyboard, even though this bridge only sends output reports.
Choose **Bluetooth access…** in the menu and enable Keychron Agent Bridge in
System Settings > Privacy & Security > Input Monitoring, then restart the bridge.
The bridge does not register input callbacks or create an event tap.

The keyboard paints a steady blue base and pulses F9–F12 orange once every four
seconds. It uses the current global brightness, without enabling disabled RGB
or resetting idle timers. The custom overlay draws once before stock Caps Lock,
battery, pairing, and factory indicators. Animation runs on the keyboard.
Disconnecting, selecting another host, or disabling the bridge restores the
previous RAM effect and hue. Status messages never save colors to EEPROM.

## Installation procedure

The build and installer are separate commands. A normal app installation cannot
flash firmware. Use the support virtual environment for the Python commands.

1. Run `scripts/backup-keyboard.py` on USB in Cable mode. It gracefully stops the
   bridge, captures the full VIA keymap, encoders, macros, RGB tables, debounce,
   Snap Click settings, and timeouts, then restarts the bridge. Backups are private.
2. Build with `scripts/build-bluetooth-firmware.py --qmk-root STAGING_CHECKOUT
   --toolchain ARM_BIN_DIRECTORY --qmk-bin QMK_EXECUTABLE`. The staging checkout
   must be a dedicated, disposable checkout of Keychron's `wireless_playground`
   revision `666862cb8123b64a6b96718d739c6203ad99031f`. Initialize its ChibiOS,
   ChibiOS-Contrib, and printf submodules. The tested compiler is Arm GNU
   13.2.Rel1 for macOS arm64. Build output stays under ignored `build/`.
   Build the read-only EEPROM decoder with
   `sh tests/firmware/build_wear_reader.sh STAGING_CHECKOUT build/wear-reader`.
   Use `build/wear-reader/wear-level-reader-4` as the installer's `--reader`.
3. Put the keyboard in USB DFU mode using its reset button while plugging it in.
4. Run `scripts/install-bluetooth-firmware.py --settings PRIVATE_SETTINGS_JSON
   --factory FACTORY_V1_1_1_BIN --reader NATIVE_WEAR_READER --firmware BUILD_DIRECTORY`.
   Without `--install`, this only backs up and validates. Add `--install` for the
   actual flash after reviewing the build and backup.
5. Keep USB plugged in, select BT, and connect the keyboard to this Mac. Run
   `scripts/verify-bluetooth.py --provision`. It reads the custom USB capability,
   sends each Bluetooth count, checks the main MCU's readback, and only then
   initially enables the Bluetooth backend. A failed test cannot initially enable it.
6. Unplug USB and confirm the live indicators work. Reconnect USB for firmware
   recovery or to restore saved mappings.

All firmware backups live under `~/Library/Application Support/Keychron Agent
Bridge/firmware-backups/`. They include private macros, so do not publish them.

## Storage and recovery safeguards

The factory EEPROM profile has 3,072 logical bytes, a 477-byte keyboard schema,
VIA magic at 514, keymaps at 518, encoders at 1,286, and 1,770 macro bytes at
1,302. The extension retains the factory VIA date (2025-04-30) to preserve its
validity marker. A separate `KABT` capability reports the extension protocol.
The version date alone does not identify the custom firmware.

The installer requires an exact match to the expected factory application,
a fresh full 256 KB ROM backup, a native decode using QMK's actual wear-leveling
implementation, matching USB keymaps/encoders/macros, and an unused final 4 KB
of the physical settings sector. The native reader refuses repairs and never
changes its input. See `tests/firmware/build_wear_reader.sh`.

Flash writes are split around the entire settings sector at
0x08004000–0x08007FFF. The installer never mass-erases or writes that sector.
Application bytes and the unchanged settings sector are read back before exiting
DFU. An installation failure triggers one bounded attempt to restore the original
program, again without writing settings. If USB becomes unavailable during a
write, keep the original backup and re-enter DFU for recovery.

The custom firmware needs the factory's dynamic debounce, Snap Click, custom RGB,
wireless, VIA, and encoder-map features. Published sources omit V1 Max custom RGB
default tables; the extension supplies blue defaults for a future factory reset.
Existing EEPROM tables take precedence.
The extension also loads those RGB tables and Snap Click settings at startup,
because the published V1 Max board startup omits the newer common initialization.
Its existing wireless and encoder initialization runs only once.

Source: [Keychron V1 Max](https://github.com/Keychron/qmk_firmware/tree/666862cb8123b64a6b96718d739c6203ad99031f/keyboards/keychron/v1_max).
Firmware additions use GPL-2.0-or-later, matching QMK's licensing.

## Reproduce the source build

Use a disposable upstream checkout. The build script patches it to match the
verified factory storage profile and adds the extension hooks.

```sh
git clone --filter=blob:none --no-checkout https://github.com/Keychron/qmk_firmware.git build/qmk
git -C build/qmk checkout --detach 666862cb8123b64a6b96718d739c6203ad99031f
git -C build/qmk submodule update --init lib/chibios lib/chibios-contrib lib/printf
python3.12 -m venv build/qmk-tools
build/qmk-tools/bin/python -m pip install qmk -r build/qmk/requirements.txt
```

Obtain the [Arm GNU 13.2.Rel1 toolchain](https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads)
for your host, and pass its `bin` directory below. Apple's Command Line Tools
provide the native C compiler used by the read-only decoder.

```sh
python3.12 scripts/build-bluetooth-firmware.py \
  --qmk-root build/qmk \
  --toolchain /path/to/arm-toolchain/bin \
  --qmk-bin build/qmk-tools/bin/qmk
sh tests/firmware/build_wear_reader.sh build/qmk build/wear-reader
```

The resulting manifest records the source revision, storage profile, and image
checksum. Building does not touch hardware. Use the exact expected installed
v1.1.1 application from [Keychron's official V/V Max downloads](https://www.keychron.com/pages/firmware-and-json-files-of-the-keychron-qmk-v-and-v-max-series-keyboards)
for `--factory`. If that version is unavailable, do not substitute another image
or remove the validation checks. A settings/profile mismatch stops installation.
See the [setup guide](../docs/SETUP.md) for app permissions and recovery.
