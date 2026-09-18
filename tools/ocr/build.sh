#!/usr/bin/env bash
# Build the Vision OCR helper (tools/ocr/ocr.m).
#
# The SDK has to be pinned explicitly. Without -isysroot, clang picks the newest
# SDK in CommandLineTools, which on this machine (macOS 11.7 with a 12.1 SDK
# installed) fails to compile Foundation. So: try the SDK matching the running
# system first, then the rest, and stop at the first one that builds.
#
# usage: build.sh [output-path]
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/ocr}"
sdk_root="/Library/Developer/CommandLineTools/SDKs"

if [ ! -d "$sdk_root" ]; then
    echo "no CommandLineTools SDKs at $sdk_root; install Xcode command line tools" >&2
    exit 1
fi

major="$(sw_vers -productVersion | cut -d. -f1)"
candidates=()
[ -d "$sdk_root/MacOSX${major}.sdk" ] && candidates+=("$sdk_root/MacOSX${major}.sdk")
# Then every other SDK, oldest last: an older SDK still builds fine, a newer one
# may not.
for sdk in $(ls -d "$sdk_root"/MacOSX*.sdk 2>/dev/null | sort -V -r); do
    [ "$sdk" = "$sdk_root/MacOSX${major}.sdk" ] && continue
    candidates+=("$sdk")
done

for sdk in "${candidates[@]}"; do
    if clang -O2 -fobjc-arc -isysroot "$sdk" \
        -framework Foundation -framework AppKit -framework Vision \
        -o "$out" "$here/ocr.m" 2>/dev/null; then
        echo "built $out with $(basename "$sdk")"
        exit 0
    fi
done

echo "could not build the OCR helper with any SDK under $sdk_root" >&2
exit 1
