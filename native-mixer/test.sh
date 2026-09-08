#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build/module-cache
xcrun swiftc -swift-version 5 -module-cache-path build/module-cache \
  -target "$(uname -m)-apple-macosx14.4" Sources/AudioSupport.swift Tests/main.swift \
  -o build/audio-tests -framework AVFoundation -framework CoreAudio
build/audio-tests
