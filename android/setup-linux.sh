#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Install the Linux build prerequisites, keeping SDK/builds in the Linux filesystem.
set -euo pipefail
[[ "$(uname -s)" == Linux ]] || { echo 'Run this inside Ubuntu/WSL.' >&2; exit 2; }
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build git python3-venv python3-dev \
    pkg-config unzip curl ca-certificates gettext bison flex gperf patchelf zstd \
    openjdk-17-jdk-headless openjdk-21-jdk-headless libgl1 libegl1 libopengl0 \
    libx11-xcb1 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
    libxcb-render-util0 libxcb-xfixes0 libxcb-shape0 libxcb-sync1 libxcb-xkb1 \
    libxkbcommon-x11-0 libxcb-cursor0 libxcb-xinerama0
build_host_root="${PROKRITA_TOOLCHAIN_ROOT:-$HOME/.local/share/prokrita-android}"
mkdir -p "$build_host_root/downloads" "$build_host_root/sdk/cmdline-tools"
archive="$build_host_root/downloads/commandlinetools-linux-15859902_latest.zip"
if [[ ! -f "$archive" ]]; then
    curl --fail --location --retry 3 https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip -o "$archive"
fi
printf '%s  %s\n' 4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583 "$archive" | sha256sum --check
if [[ ! -x "$build_host_root/sdk/cmdline-tools/latest/bin/sdkmanager" ]]; then
    unzip -q "$archive" -d "$build_host_root/sdk/cmdline-tools"
    mv -- "$build_host_root/sdk/cmdline-tools/cmdline-tools" "$build_host_root/sdk/cmdline-tools/latest"
fi
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
manager="$build_host_root/sdk/cmdline-tools/latest/bin/sdkmanager"
# yes exits with SIGPIPE once sdkmanager finishes; check sdkmanager's own status.
set +o pipefail
yes | "$manager" --sdk_root="$build_host_root/sdk" --licenses
set -o pipefail
"$manager" --sdk_root="$build_host_root/sdk" 'platform-tools' 'platforms;android-35' \
    'build-tools;35.0.0' 'ndk;27.3.13750724'
env_file="$build_host_root/environment.sh"
{
    printf 'export JAVA_HOME=%q\n' /usr/lib/jvm/java-17-openjdk-amd64
    printf 'export KDECI_ANDROID_SDK_ROOT=%q\n' "$build_host_root/sdk"
    printf 'export KDECI_ANDROID_NDK_ROOT=%q\n' "$build_host_root/sdk/ndk/27.3.13750724"
    printf 'export KDECI_ANDROID_ABI=arm64-v8a\n'
    printf 'export PROKRITA_ANDROID_WORK=%q\n' "$build_host_root/work/arm64-v8a"
    printf 'export PATH="$JAVA_HOME/bin:$PATH"\n'
} > "$env_file"
printf 'Build tools installed. Run: source %q\n' "$env_file"
printf 'Then run bash android/build-android.sh from the ProKrita project.\n'
