// SPDX-License-Identifier: GPL-2.0-or-later
// Native, read-only adapter for the pinned QMK wear-leveling implementation.
// Usage: wear-level-reader INPUT OUTPUT [SECTOR_OFFSET], offset accepts 0x notation.
// INPUT is never modified. OUTPUT must not exist and is created with mode 0600.

#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "fnv.h"
#include "wear_leveling.h"
#include "wear_leveling_internal.h"

static uint8_t physical[WEAR_LEVELING_BACKING_SIZE];
static bool loaded;
static unsigned blocked_mutations;

bool backing_store_init(void) { return loaded; }
bool backing_store_lock(void) { return true; }

bool backing_store_unlock(void) {
    ++blocked_mutations;
    return false;
}

bool backing_store_erase(void) {
    ++blocked_mutations;
    return false;
}

bool backing_store_write(uint32_t address, backing_store_int_t value) {
    (void)address;
    (void)value;
    ++blocked_mutations;
    return false;
}

bool backing_store_read(uint32_t address, backing_store_int_t *value) {
    if (!loaded || address % BACKING_STORE_WRITE_SIZE ||
        address > sizeof(physical) - sizeof(*value)) return false;
    *value = 0;
    for (size_t index = 0; index < sizeof(*value); ++index) {
        *value |= (backing_store_int_t)((uint8_t)~physical[address + index]) << (index * 8);
    }
    return true;
}

static bool load_input(const char *path, uint64_t offset) {
    int input = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
    if (input < 0) return false;
    struct stat info;
    if (fstat(input, &info) || !S_ISREG(info.st_mode) || info.st_size < 0 ||
        offset > (uint64_t)info.st_size || sizeof(physical) > (uint64_t)info.st_size - offset) {
        close(input);
        errno = EINVAL;
        return false;
    }
    size_t done = 0;
    while (done < sizeof(physical)) {
        ssize_t received = pread(input, physical + done, sizeof(physical) - done, (off_t)(offset + done));
        if (received < 0 && errno == EINTR) continue;
        if (received <= 0) {
            int error = received ? errno : EIO;
            close(input);
            errno = error;
            return false;
        }
        done += (size_t)received;
    }
    if (close(input)) return false;
    loaded = true;
    return true;
}

static bool save_output(const char *path, const uint8_t *logical) {
    int output = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (output < 0) return false;
    size_t done = 0;
    bool ok = true;
    while (done < WEAR_LEVELING_LOGICAL_SIZE) {
        ssize_t written = write(output, logical + done, WEAR_LEVELING_LOGICAL_SIZE - done);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) {
            ok = false;
            break;
        }
        done += (size_t)written;
    }
    if (ok && fsync(output)) ok = false;
    int error = errno;
    if (close(output) && ok) {
        ok = false;
        error = errno;
    }
    if (!ok) {
        unlink(path); // Only the incomplete output exclusively created above.
        errno = error;
    }
    return ok;
}

int main(int argc, char **argv) {
    if (argc != 3 && argc != 4) {
        fprintf(stderr, "Usage: %s INPUT OUTPUT [SECTOR_OFFSET]\n", argv[0]);
        return 2;
    }
    uint64_t offset = 0;
    if (argc == 4) {
        char *end = NULL;
        errno = 0;
        offset = strtoull(argv[3], &end, 0);
        if (errno || argv[3][0] == '-' || !argv[3][0] || !end || *end) {
            fprintf(stderr, "Invalid sector offset\n");
            return 2;
        }
    }
    if (!load_input(argv[1], offset)) {
        perror("Read-only input");
        return 3;
    }
    uint8_t consolidated[WEAR_LEVELING_LOGICAL_SIZE];
    uint64_t stored = 0;
    bool erased_base = true;
    for (size_t index = 0; index < sizeof(consolidated); ++index) {
        consolidated[index] = (uint8_t)~physical[index];
        if (consolidated[index]) erased_base = false;
    }
    for (size_t index = 0; index < 8; ++index) {
        stored |= (uint64_t)((uint8_t)~physical[WEAR_LEVELING_LOGICAL_SIZE + index]) << (index * 8);
    }
    uint64_t expected = fnv_64a_buf(consolidated, sizeof(consolidated), FNV1A_64_INIT);
    bool checksum_match = stored == expected;
    fprintf(stderr, "write_size=%d logical_size=%d backing_size=%d checksum=%s\n",
            BACKING_STORE_WRITE_SIZE, WEAR_LEVELING_LOGICAL_SIZE, WEAR_LEVELING_BACKING_SIZE,
            checksum_match ? "match" : erased_base && !stored ? "erased-log-base" : "mismatch");
    if (!checksum_match && !(erased_base && !stored)) {
        fprintf(stderr, "Refusing nonempty consolidated data with an invalid checksum\n");
        return 4;
    }
    wear_leveling_status_t status = wear_leveling_init();
    uint8_t logical[WEAR_LEVELING_LOGICAL_SIZE];
    if (status != WEAR_LEVELING_SUCCESS || blocked_mutations ||
        wear_leveling_read(0, logical, sizeof(logical)) != WEAR_LEVELING_SUCCESS) {
        fprintf(stderr, "QMK decode failed: status=%d blocked_mutations=%u\n", status, blocked_mutations);
        return 4;
    }
    if (!save_output(argv[2], logical)) {
        perror("Exclusive output");
        return 5;
    }
    fprintf(stderr, "Decoded %zu bytes; input unchanged; output mode 0600\n", sizeof(logical));
    return 0;
}
