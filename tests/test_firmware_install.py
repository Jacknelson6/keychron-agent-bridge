import copy
import importlib.util
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def installer():
    path = Path(__file__).resolve().parents[1] / "scripts/install-bluetooth-firmware.py"
    spec = importlib.util.spec_from_file_location("firmware_installer_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot():
    factory = bytes((index * 13 + 7) % 256 for index in range(0x10000))
    rom = bytearray(b"\xff" * 0x40000)
    rom[:len(factory)] = factory
    rom[0x4000:0x7000] = bytes(12288)
    rom[0x7000:0x8000] = b"\xff" * 4096
    keymap = [(index * 7) % 256 for index in range(768)]
    encoders = [[[layer, direction + 100] for direction in range(2)] for layer in range(4)]
    macros = [(index * 11) % 256 for index in range(1770)]
    settings = {"firmware": "v1.1.1 2025-04-30-10:37:08", "macro_size": 1770,
                "keymap": keymap, "encoders": encoders, "macros": macros}
    logical = bytearray(3072)
    logical[:2] = b"\xe6\xfe"
    logical[15:19] = (477).to_bytes(4, "little")
    logical[514:517] = bytes([0x25, 0x04, 0x30])
    logical[518:1286] = bytes(keymap)
    for layer, (counterclockwise, clockwise) in enumerate(encoders):
        logical[1286 + layer * 4:1290 + layer * 4] = bytes(clockwise + counterclockwise)
    logical[1302:] = bytes(macros)
    return [rom, factory, settings, logical]


def test_valid_factory_backup_passes_without_changing_input_snapshots(installer, snapshot):
    before = copy.deepcopy(snapshot)
    installer.validate_backup(*snapshot)
    assert snapshot == before
    # Factory image settings are intentionally not used to overwrite user settings.
    assert snapshot[0][0x4000:0x7000] != snapshot[1][0x4000:0x7000]


@pytest.mark.parametrize("region", [0, 0x3fff, 0x8000, 0xffff])
def test_changed_factory_application_is_rejected(installer, snapshot, region):
    snapshot[0][region] ^= 1
    before = copy.deepcopy(snapshot)
    with pytest.raises(ValueError, match="application differs"):
        installer.validate_backup(*snapshot)
    assert snapshot == before


@pytest.mark.parametrize("argument,size", [(0, 0x40000 - 1), (0, 0x40000 + 1),
                                            (1, 0x8000), (1, 0x20000)])
def test_truncated_or_wrong_sized_images_are_rejected(installer, snapshot, argument, size):
    snapshot[argument] = bytes(size)
    with pytest.raises(ValueError, match="image length"):
        installer.validate_backup(*snapshot)


@pytest.mark.parametrize("offset,reason", [(0, "core or keyboard schema"), (15, "core or keyboard schema"),
                                           (514, "VIA schema date"), (518, "key mappings"),
                                           (1286, "key mappings"), (1302, "macros"), (3071, "macros")])
def test_logical_schema_keymap_encoder_and_macro_mismatches_are_rejected(installer, snapshot,
                                                                        offset, reason):
    snapshot[3][offset] ^= 1
    before = copy.deepcopy(snapshot)
    with pytest.raises(ValueError, match=reason):
        installer.validate_backup(*snapshot)
    assert snapshot == before


@pytest.mark.parametrize("key,value", [("firmware", "v1.0.1 another-build"), ("macro_size", 746)])
def test_wrong_factory_settings_profile_is_rejected(installer, snapshot, key, value):
    snapshot[2][key] = value
    with pytest.raises(ValueError, match="settings profile"):
        installer.validate_backup(*snapshot)


def test_short_logical_eeprom_is_rejected(installer, snapshot):
    snapshot[3] = snapshot[3][:-1]
    with pytest.raises(ValueError, match="core or keyboard schema"):
        installer.validate_backup(*snapshot)


@pytest.mark.parametrize("offset", [0x7000, 0x7fff])
def test_vendor_log_outside_supported_backing_profile_is_rejected(installer, snapshot, offset):
    snapshot[0][offset] = 0
    with pytest.raises(ValueError, match="beyond the supported backing-store"):
        installer.validate_backup(*snapshot)


def test_settings_sector_is_never_treated_as_factory_application(installer, snapshot):
    snapshot[0][0x4000] = 123
    snapshot[0][0x6fff] = 45
    before = copy.deepcopy(snapshot)
    installer.validate_backup(*snapshot)
    assert snapshot == before


def _with_dfu_suffix(application, suffix_length=16):
    footer = (0x0111).to_bytes(2, "little") + (0x0913).to_bytes(2, "little")
    footer += (0x3434).to_bytes(2, "little") + (0x011A).to_bytes(2, "little")
    footer += b"UFD" + bytes([suffix_length])
    payload = bytes(application) + footer
    # DFU CRC uses reflected CRC32 with an initial all-ones state and no final XOR.
    # This reference intentionally does not use the implementation's zlib expression.
    crc = 0xffffffff
    for value in payload:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ (0xedb88320 if crc & 1 else 0)
    return payload + crc.to_bytes(4, "little")


def test_valid_dfu_suffix_is_checked_and_excluded_from_application_comparison(installer, snapshot):
    snapshot[1] = _with_dfu_suffix(snapshot[1])
    before = copy.deepcopy(snapshot)
    installer.validate_backup(*snapshot)
    assert snapshot == before


@pytest.mark.parametrize("offset", [-1, -4])
def test_corrupt_dfu_suffix_crc_is_rejected_without_mutating_backup(installer, snapshot, offset):
    factory = bytearray(_with_dfu_suffix(snapshot[1]))
    factory[offset] ^= 1
    snapshot[1] = factory
    before = copy.deepcopy(snapshot)
    with pytest.raises(ValueError, match="DFU suffix checksum"):
        installer.validate_backup(*snapshot)
    assert snapshot == before


@pytest.mark.parametrize("length", [0, 15, 17, 255])
def test_wrong_dfu_suffix_length_is_rejected_even_with_matching_crc(installer, snapshot, length):
    snapshot[1] = _with_dfu_suffix(snapshot[1], suffix_length=length)
    with pytest.raises(ValueError, match="DFU suffix checksum"):
        installer.validate_backup(*snapshot)


def test_valid_dfu_suffix_does_not_hide_changed_application_bytes(installer, snapshot):
    factory = bytearray(snapshot[1])
    factory[0x8000] ^= 1
    snapshot[1] = _with_dfu_suffix(factory)
    with pytest.raises(ValueError, match="application differs"):
        installer.validate_backup(*snapshot)


def test_truncated_dfu_suffix_is_rejected(installer, snapshot):
    snapshot[1] = _with_dfu_suffix(snapshot[1])[:-1]
    with pytest.raises(ValueError):
        installer.validate_backup(*snapshot)


def test_encoder_usb_directions_are_compared_to_clockwise_first_eeprom(installer, snapshot):
    snapshot[2]["encoders"][0] = [[0x00, 0xaa], [0x00, 0xa9]]
    snapshot[3][1286:1290] = bytes([0x00, 0xa9, 0x00, 0xaa])
    before = copy.deepcopy(snapshot)
    installer.validate_backup(*snapshot)
    assert snapshot == before
    snapshot[3][1286:1290] = bytes([0x00, 0xaa, 0x00, 0xa9])
    with pytest.raises(ValueError, match="key mappings"):
        installer.validate_backup(*snapshot)


def test_successful_dfu_leave_does_not_require_or_invoke_fallback_handshake(installer):
    calls = []
    def leave():
        calls.append("leave")
    def confirm():
        raise AssertionError("Fallback handshake should not run after a successful leave")
    assert installer.finish_leave(leave, confirm) is False
    assert calls == ["leave"]


@pytest.mark.parametrize("error", [OSError("DFU disconnected"), subprocess.TimeoutExpired("dfu-util", 30)])
def test_failed_dfu_leave_is_accepted_only_after_new_application_handshake(installer, error):
    calls = []
    def leave():
        calls.append("leave")
        raise error
    def confirm():
        calls.append("fresh USB capability verified")
    assert installer.finish_leave(leave, confirm) is True
    assert calls == ["leave", "fresh USB capability verified"]


@pytest.mark.parametrize("leave_error", [OSError("original DFU failure"),
                                         subprocess.TimeoutExpired("dfu-util", 30)])
@pytest.mark.parametrize("probe_error", [OSError("USB unavailable"), ValueError("Not bridge firmware")])
def test_failed_application_handshake_preserves_exact_original_leave_exception(installer, leave_error,
                                                                              probe_error):
    calls = []
    def leave():
        calls.append("leave")
        raise leave_error
    def confirm():
        calls.append("probe")
        raise probe_error
    with pytest.raises(type(leave_error)) as raised:
        installer.finish_leave(leave, confirm)
    assert raised.value is leave_error
    assert calls == ["leave", "probe"]


def test_unexpected_leave_failure_is_not_hidden_by_application_probe(installer):
    def leave():
        raise ValueError("Invalid download arguments")
    def confirm():
        raise AssertionError("Unexpected errors must not be interpreted as successful detach")
    with pytest.raises(ValueError, match="Invalid download arguments"):
        installer.finish_leave(leave, confirm)
