// SPDX-License-Identifier: GPL-2.0-or-later
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "agent_protocol.h"

// Expected wire bytes are explicit, independently of the decoder's bit arithmetic.
static bool expected_packet(uint8_t raw, uint8_t *count, bool *enabled) {
    switch ((uint8_t)(raw & (uint8_t)~0x02)) {
        case 0x10: *count = 0; *enabled = true; return true;
        case 0x11: *count = 1; *enabled = true; return true;
        case 0x14: *count = 2; *enabled = true; return true;
        case 0x15: *count = 3; *enabled = true; return true;
        case 0x18: *count = 4; *enabled = true; return true;
        case 0x1d: *count = 0; *enabled = false; return true;
        default: return false;
    }
}

static void test_all_bytes_from_all_valid_count_states(void) {
    for (unsigned enabled = 0; enabled <= 1; ++enabled) {
        for (unsigned count = 0; count <= 4; ++count) {
            for (unsigned raw = 0; raw <= UINT8_MAX; ++raw) {
                keychron_agent_state_t state = {.raw_leds = 0xff, .count = count, .enabled = enabled};
                uint8_t expected_count = count;
                bool expected_enabled = enabled;
                bool accepted = expected_packet(raw, &expected_count, &expected_enabled);
                assert(keychron_agent_receive(&state, raw) == accepted);
                assert(state.raw_leds == raw);
                assert(state.count == expected_count);
                assert(state.enabled == expected_enabled);
            }
        }
    }
}

static void test_caps_variants_have_identical_protocol_meaning(void) {
    const uint8_t packets[] = {0x10, 0x11, 0x14, 0x15, 0x18, 0x1d};
    for (size_t index = 0; index < sizeof(packets); ++index) {
        keychron_agent_state_t without_caps = {.count = 3, .enabled = true};
        keychron_agent_state_t with_caps = without_caps;
        assert(keychron_agent_receive(&without_caps, packets[index]));
        assert(keychron_agent_receive(&with_caps, packets[index] | 2));
        assert(without_caps.count == with_caps.count);
        assert(without_caps.enabled == with_caps.enabled);
        assert(without_caps.raw_leds == packets[index]);
        assert(with_caps.raw_leds == (packets[index] | 2));
    }
}

static void test_normal_and_rejected_reports_preserve_latched_state(void) {
    keychron_agent_state_t state = {0};
    assert(keychron_agent_receive(&state, 0x18));
    const uint8_t rejected[] = {0, 2, 1, 3, 0x0c, 0x1c, 0x19, 0x1b, 0x20, 0x38, 0x98, 0xff};
    for (size_t index = 0; index < sizeof(rejected); ++index) {
        assert(!keychron_agent_receive(&state, rejected[index]));
        assert(state.count == 4 && state.enabled);
        assert(state.raw_leds == rejected[index]);
    }
    assert(keychron_agent_receive(&state, 0x1f));
    assert(state.count == 0 && !state.enabled && state.raw_leds == 0x1f);
    for (size_t index = 0; index < sizeof(rejected); ++index) {
        assert(!keychron_agent_receive(&state, rejected[index]));
        assert(state.count == 0 && !state.enabled);
        assert(state.raw_leds == rejected[index]);
    }
    assert(keychron_agent_receive(&state, 0x16));
    assert(state.count == 2 && state.enabled && state.raw_leds == 0x16);
}

int main(void) {
    test_all_bytes_from_all_valid_count_states();
    test_caps_variants_have_identical_protocol_meaning();
    test_normal_and_rejected_reports_preserve_latched_state();
    puts("Passed: 2560 exhaustive packets, 6 Caps pairs, and latched-state sequences");
    return 0;
}
