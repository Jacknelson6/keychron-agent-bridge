#!/bin/sh
set -eu
umask 077
if [ "$#" -lt 1 ] || [ "$#" -gt 3 ]; then
    printf 'Usage: %s QMK_ROOT [OUTPUT_DIRECTORY [MACOS_SDK]]\n' "$0" >&2
    exit 2
fi
reader_sources=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
reader_root=$(CDPATH= cd -- "$reader_sources/../.." && pwd)
qmk_sources=$1
reader_output=${2:-$reader_root/build/wear-reader}
if [ ! -f "$qmk_sources/quantum/wear_leveling/wear_leveling.c" ] || [ ! -f "$qmk_sources/lib/fnv/hash_64a.c" ]; then
    printf 'QMK_ROOT must contain the QMK wear-leveling and FNV sources.\n' >&2
    exit 2
fi
if [ "$(uname -s)" != Darwin ]; then
    printf 'The wear-level reader build requires macOS.\n' >&2
    exit 2
fi
reader_sdk=${3:-${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path)}}
reader_cc=${CC:-$(xcrun --find clang)}
reader_deployment=${MACOSX_DEPLOYMENT_TARGET:-11.0}
mkdir -p "$reader_output"
for reader_width in 2 4; do
    "$reader_cc" -std=c11 -O1 -g -Wall -Wextra -Werror \
        -isysroot "$reader_sdk" "-mmacosx-version-min=$reader_deployment" \
        -fsanitize=address,undefined -fno-omit-frame-pointer \
        -DBACKING_STORE_WRITE_SIZE="$reader_width" \
        -DWEAR_LEVELING_LOGICAL_SIZE=3072 -DWEAR_LEVELING_BACKING_SIZE=12288 \
        -DWEAR_LEVELING_ASSERTS \
        -I "$qmk_sources/quantum/wear_leveling" -I "$qmk_sources/lib/fnv" \
        "$reader_sources/wear_level_reader.c" \
        "$qmk_sources/quantum/wear_leveling/wear_leveling.c" \
        "$qmk_sources/lib/fnv/hash_64a.c" \
        -o "$reader_output/wear-level-reader-$reader_width"
done
