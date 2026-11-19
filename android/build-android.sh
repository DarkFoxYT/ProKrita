#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Run on Linux with the official Krita Android SDK/NDK prerequisites installed.
set -euo pipefail
port_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "$(uname -s)" != Linux ]]; then
    echo 'A Linux host is required for a complete Krita APK build.' >&2
    echo 'Windows can compile the geometry core, but the upstream Krita Android dependency toolchain needs Linux.' >&2
    exit 2
fi
for command in git python3 cmake ninja java javac; do
    command -v "$command" >/dev/null || { echo "Missing build prerequisite: $command" >&2; exit 2; }
done
: "${KDECI_ANDROID_SDK_ROOT:?Set KDECI_ANDROID_SDK_ROOT to the Android SDK (platform 35, build tools 35)}"
: "${KDECI_ANDROID_NDK_ROOT:?Set KDECI_ANDROID_NDK_ROOT to the Krita-compatible Android NDK (27.3.13750724)}"
[[ -f "$KDECI_ANDROID_NDK_ROOT/build/cmake/android.toolchain.cmake" ]] || { echo 'Missing Android NDK toolchain' >&2;exit 2; }
[[ -d "$KDECI_ANDROID_NDK_ROOT/sources/android/cpufeatures" ]] || { echo 'This Krita branch needs an NDK with sources/android/cpufeatures.' >&2;exit 2; }
export ANDROID_HOME="$KDECI_ANDROID_SDK_ROOT"
export KDECI_ANDROID_ABI="${KDECI_ANDROID_ABI:-arm64-v8a}"
case "$KDECI_ANDROID_ABI" in arm64-v8a|armeabi-v7a|x86_64) ;; *) echo 'Unsupported ABI' >&2;exit 2;; esac
work="${PROKRITA_ANDROID_WORK:-$port_root/work/$KDECI_ANDROID_ABI}"
mkdir -p "$work"
work="$(cd -- "$work" && pwd)"
source_dir="$work/krita"
revision=7d9aefc93f15998eca13458bb9a065e2cca77c02
if [[ ! -d "$source_dir/.git" ]]; then
    git clone --depth 1 --branch v5.2.16 https://github.com/KDE/krita.git "$source_dir"
fi
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$revision" ]] || { echo 'Wrong Krita source revision' >&2;exit 2; }
python3 "$port_root/apply-port.py" "$source_dir"
deps="$source_dir/krita-deps-management"
if [[ ! -d "$deps/.git" ]]; then
    git clone https://invent.kde.org/packaging/krita-deps-management.git "$deps"
    git -C "$deps" checkout --detach 5b58b1970635d2c2825c682173028a2a3021a764
fi
if [[ ! -d "$deps/ci-utilities/.git" ]]; then
    git clone https://invent.kde.org/packaging/krita-ci-utilities.git "$deps/ci-utilities"
    git -C "$deps/ci-utilities" checkout --detach b591f0ef834d061329707e8841b97ff6a0b3c48b
fi
python3 -m venv "$work/venv"
source "$work/venv/bin/activate"
python -m pip install -r "$deps/requirements.txt"
cd "$source_dir"
python "$deps/tools/setup-env.py" --root "$source_dir" --shared-install "$source_dir/_install" \
    --venv "$work/venv" --android-abi "$KDECI_ANDROID_ABI" --generate-deps-file
set +u # Upstream environment activation reads unset variables to save old values.
source "$source_dir/base-env"
set -u
# The pinned upstream setup helper omits Qt5/Shared from its full-env path;
# use the same full platform name as Krita's actual Android CI jobs.
unset KDECI_GLOBAL_CONFIG_OVERRIDE_PATH
python "$deps/ci-utilities/run-ci-build.py" --project krita --branch master \
    --platform "Android/$KDECI_ANDROID_ABI/Qt5/Shared" --only-env --env env --skip-publishing
set +u
source "$source_dir/env"
set -u
export KDECI_ANDROID_ABI
cmake -S "$port_root/tests" -B "$work/geometry-host" -G Ninja
cmake --build "$work/geometry-host"
ctest --test-dir "$work/geometry-host" --output-on-failure
cmake -S "$source_dir" -B "$source_dir/_build" -G Ninja \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo -DBUILD_TESTING=OFF -DHIDE_SAFE_ASSERTS=OFF \
    -DCMAKE_INSTALL_PREFIX="$source_dir/_install" \
    -DCMAKE_TOOLCHAIN_FILE="$deps/tools/android-toolchain-krita.cmake" \
    -DANDROID_ENABLE_STDIO_FORWARDING=ON
cmake --build "$source_dir/_build" --parallel "${PROKRITA_BUILD_JOBS:-4}"
cmake --install "$source_dir/_build"
export KRITACI_ANDROID_PACKAGE_TYPE=debug
python "$source_dir/build-tools/ci-scripts/build-android-package.py"
out="$port_root/../dist/android"
mkdir -p "$out"
shopt -s nullglob
packages=("$source_dir"/_packaging/*.apk)
(( ${#packages[@]} > 0 )) || { echo 'Build produced no APK' >&2;exit 1; }
for apk in "${packages[@]}"; do
    cp -- "$apk" "$out/ProKrita-Android-$KDECI_ANDROID_ABI.apk"
done
printf 'APK built: %s\n' "$out/ProKrita-Android-$KDECI_ANDROID_ABI.apk"
