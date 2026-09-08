#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p build/module-cache 'build/Meeting Bridge.app/Contents/MacOS'
xcrun swiftc -swift-version 5 -O -module-cache-path build/module-cache \
  -target "$(uname -m)-apple-macosx14.4" \
  Sources/AudioSupport.swift Sources/Mixer.swift Sources/CLI.swift Sources/main.swift \
  -o 'build/Meeting Bridge.app/Contents/MacOS/MeetingBridge' \
  -framework Cocoa -framework CoreAudio -framework AVFoundation
cp Info.plist 'build/Meeting Bridge.app/Contents/Info.plist'
codesign --force --sign - --identifier local.meetingbridge.mixer 'build/Meeting Bridge.app'
printf '%s\n' "$PWD/build/Meeting Bridge.app"

cat > build/meeting-bridge <<'CLI'
#!/bin/sh
set -eu
bridge_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$bridge_dir/Meeting Bridge.app/Contents/MacOS/MeetingBridge" --cli "$@"
CLI
chmod +x build/meeting-bridge
