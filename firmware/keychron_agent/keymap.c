// SPDX-License-Identifier: GPL-2.0-or-later
// Keep the stock Keychron VIA layout, encoder actions, pairing, and reset keys.
#include "../via/keymap.c"
#include "agent_protocol.h"
#include "color.h"
#include "rgb_matrix.h"
#include "transport.h"
#include "wireless.h"
#include "via.h"
#include "eeprom.h"
#include "lib/lib8tion/lib8tion.h"

#if RGB_MATRIX_LED_COUNT != 81 || MATRIX_ROWS != 6 || MATRIX_COLS != 16
#    error This extension is only for the V1 Max ANSI encoder model
#endif

_Static_assert(EECONFIG_SIZE == 514, "Factory EEPROM schema must remain unchanged");
_Static_assert(WEAR_LEVELING_LOGICAL_SIZE == 3072, "Factory macro capacity must remain unchanged");
// Published V1 Max sources omit these tables required by custom RGB support.
// Existing EEPROM colors take precedence. These apply only to a factory reset.
HSV default_per_key_led[RGB_MATRIX_LED_COUNT] = {[0 ... RGB_MATRIX_LED_COUNT - 1] = {170, 255, 255}};
uint8_t default_region[RGB_MATRIX_LED_COUNT];

static keychron_agent_state_t agent_state;
static uint8_t agent_host = 0xff;
static uint8_t agent_previous_mode;
static HSV agent_previous_hsv;

void keyboard_post_init_user(void) {
    // This board initializes wireless itself but omits the newer common settings.
    // Load only those settings; never initialize the radio or encoder twice.
    extern void snap_click_init(void);
    extern void eeconfig_init_custom_rgb(void);
    snap_click_init();
    eeconfig_init_custom_rgb();
}

static void agent_restore(void) {
    rgb_matrix_mode_noeeprom(agent_previous_mode);
    rgb_matrix_sethsv_noeeprom(agent_previous_hsv.h, agent_previous_hsv.s, rgb_matrix_get_val());
}

bool led_update_user(led_t leds) {
    if (get_transport() == TRANSPORT_BLUETOOTH && wireless_get_state() == WT_CONNECTED) {
        bool was_enabled = agent_state.enabled;
        if (keychron_agent_receive(&agent_state, leds.raw) && !was_enabled && agent_state.enabled) {
            agent_previous_mode = rgb_matrix_get_mode();
            agent_previous_hsv = (HSV){rgb_matrix_get_hue(), rgb_matrix_get_sat(), rgb_matrix_get_val()};
            // A cable can leave the USB mixed effect active. Use a steady base on BT.
            rgb_matrix_mode_noeeprom(RGB_MATRIX_SOLID_COLOR);
            rgb_matrix_sethsv_noeeprom(170, 255, rgb_matrix_get_val());
        }
        if (was_enabled && !agent_state.enabled) agent_restore();
    }
    return true;
}

void keychron_agent_wireless_event(uint8_t host, uint8_t state) {
    if (state != WT_CONNECTED || agent_host != host) {
        if (agent_state.enabled) agent_restore();
        agent_state.enabled = false;
    }
    if (state == WT_CONNECTED) agent_host = host;
}

// A single hook before stock Caps Lock, pairing, battery, and factory overlays.
// The normal user RGB hook runs twice in Keychron's wireless indicator stack.
void keychron_agent_rgb_overlay(void) {
    if (!agent_state.enabled || get_transport() != TRANSPORT_BLUETOOTH || wireless_get_state() != WT_CONNECTED) return;
    uint8_t brightness = rgb_matrix_get_val();
    RGB blue = hsv_to_rgb((HSV){170, 255, brightness});
    uint8_t phase = (uint32_t)(timer_read32() % 4000) * 256 / 4000;
    RGB orange = hsv_to_rgb((HSV){20, 255, scale8(sin8(phase), brightness)});
    for (uint8_t index = 0; index < RGB_MATRIX_LED_COUNT; ++index) {
        rgb_matrix_set_color(index, blue.r, blue.g, blue.b);
    }
    for (uint8_t slot = 0; slot < agent_state.count; ++slot) {
        uint8_t index = g_led_config.matrix_co[0][9 + slot];
        if (index < RGB_MATRIX_LED_COUNT) rgb_matrix_set_color(index, orange.r, orange.g, orange.b);
    }
}

void via_custom_value_command_kb(uint8_t *data, uint8_t length) {
    if (length != 32 || data[0] != id_custom_get_value || data[1] != 0x7e || data[2] != 1) {
        data[0] = id_unhandled;
        return;
    }
    memcpy(&data[3], "KABT", 4);
    data[7] = 1;
    data[8] = 4;
    data[9] = RGB_MATRIX_LED_COUNT;
    data[10] = agent_state.raw_leds;
    data[11] = agent_state.count;
    data[12] = agent_state.enabled;
    data[13] = get_transport();
    data[14] = wireless_get_state();
    data[15] = EECONFIG_SIZE >> 8;
    data[16] = EECONFIG_SIZE & 255;
    data[17] = WEAR_LEVELING_LOGICAL_SIZE >> 8;
    data[18] = WEAR_LEVELING_LOGICAL_SIZE & 255;
    data[19] = WEAR_LEVELING_BACKING_SIZE >> 8;
    data[20] = WEAR_LEVELING_BACKING_SIZE & 255;
    for (uint8_t slot = 0; slot < 4; ++slot) data[21 + slot] = g_led_config.matrix_co[0][9 + slot];
}
