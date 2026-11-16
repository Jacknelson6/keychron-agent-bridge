// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

// Factory v1.1.1 storage profile, confirmed by full VIA capacity and ROM decoding.
#define DYNAMIC_KEYMAP_LAYER_COUNT 4
#define WEAR_LEVELING_LOGICAL_SIZE 3072
#define WEAR_LEVELING_BACKING_SIZE 12288
#define WEAR_LEVELING_EFL_FIRST_SECTOR 1

// Define the complete factory schema before any translation unit loads eeconfig.h.
#include "eeconfig_kb.h"
