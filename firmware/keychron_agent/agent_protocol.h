// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    uint8_t raw_leds;
    uint8_t count;
    bool enabled;
} keychron_agent_state_t;

// Caps Lock remains bit 1. Only the unused indicator bits carry status.
static inline bool keychron_agent_receive(keychron_agent_state_t *state, uint8_t raw) {
    state->raw_leds = raw;
    if (raw & 0xe0) return false;
    uint8_t value = (raw & 1) | ((raw >> 1) & 2) | ((raw >> 1) & 4) | ((raw >> 1) & 8);
    if (value >= 8 && value <= 12) {
        state->count = value - 8;
        state->enabled = true;
        return true;
    }
    if (value == 15) {
        state->count = 0;
        state->enabled = false;
        return true;
    }
    return false;
}
