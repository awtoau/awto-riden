#!/usr/bin/env bash
# Build the sigrok stack from the master working trees on /mnt/2tb into
# /usr/local, in dependency order. Logs each stage to tmp/.
#
# Why from source: the last sigrok *release* is libsigrok 0.5.2 (2019); the
# Riden RD60xx driver (rdtech-rd, incl. RD6024) is master-only (1800+ commits
# past 0.5.2). Coexists with the dnf 0.5.2 packages — /usr/local takes PATH/ld
# precedence; nothing is removed.
#
# Usage:  bash scripts/build_sigrok_stack.sh [component ...]
#         (no args = full stack in order)

set -euo pipefail

SRC=/mnt/2tb/git/sigrokproject
PREFIX=/usr/local
LOGDIR="$(cd "$(dirname "$0")/.." && pwd)/tmp"
mkdir -p "$LOGDIR"

JOBS="$(nproc)"
export PKG_CONFIG_PATH="$PREFIX/lib64/pkgconfig:$PREFIX/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
export LD_LIBRARY_PATH="$PREFIX/lib64:$PREFIX/lib:${LD_LIBRARY_PATH:-}"

ORDER=(libserialport libsigrok libsigrokdecode sigrok-cli pulseview)
COMPONENTS=("$@")
[[ ${#COMPONENTS[@]} -eq 0 ]] && COMPONENTS=("${ORDER[@]}")

build_autotools() {
    local name="$1" dir="$SRC/$1" log="$LOGDIR/build_$1.log"
    echo "=== $name (autotools) → $log ==="
    cd "$dir"
    {
        [[ -x ./autogen.sh ]] && ./autogen.sh
        ./configure --prefix="$PREFIX"
        make -j"$JOBS"
        sudo make install
    } >"$log" 2>&1
    sudo ldconfig
    echo "    $name OK"
}

build_cmake() {
    local name="$1" dir="$SRC/$1" log="$LOGDIR/build_$1.log"
    echo "=== $name (cmake) → $log ==="
    cd "$dir"
    {
        rm -rf build && mkdir build && cd build
        cmake -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_BUILD_TYPE=Release ..
        make -j"$JOBS"
        sudo make install
    } >"$log" 2>&1
    sudo ldconfig
    echo "    $name OK"
}

for c in "${COMPONENTS[@]}"; do
    case "$c" in
        pulseview) build_cmake "$c" ;;   # PulseView uses CMake
        *)         build_autotools "$c" ;;  # the libs + cli use autotools
    esac
done

echo
echo "Done. Installed to $PREFIX. Verify: $PREFIX/bin/sigrok-cli --version"
