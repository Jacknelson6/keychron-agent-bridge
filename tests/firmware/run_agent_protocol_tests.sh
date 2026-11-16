#!/bin/sh
set -eu
umask 077
if [ "$#" -gt 2 ]; then
    printf 'Usage: %s [OUTPUT_DIRECTORY [MACOS_SDK]]\n' "$0" >&2
    exit 2
fi
protocol_tests=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
protocol_root=$(CDPATH= cd -- "$protocol_tests/../.." && pwd)
protocol_output=${1:-$protocol_root/build/protocol-tests}
protocol_sdk_override=${2:-}
set -- -std=c11 -O1 -g -Wall -Wextra -Werror \
    -fsanitize=address,undefined -fno-omit-frame-pointer
case $(uname -s) in
    Darwin)
        protocol_sdk=${protocol_sdk_override:-${SDKROOT:-$(xcrun --sdk macosx --show-sdk-path)}}
        protocol_cc=${CC:-$(xcrun --find clang)}
        set -- "$@" -isysroot "$protocol_sdk" "-mmacosx-version-min=${MACOSX_DEPLOYMENT_TARGET:-11.0}"
        ;;
    Linux)
        protocol_cc=${CC:-cc}
        ;;
    *)
        printf 'Protocol tests support macOS and Linux.\n' >&2
        exit 2
        ;;
esac
mkdir -p "$protocol_output"
"$protocol_cc" "$@" \
    -I "$protocol_root/firmware/keychron_agent" \
    "$protocol_tests/test_agent_protocol.c" -o "$protocol_output/test-agent-protocol"
"$protocol_output/test-agent-protocol"
