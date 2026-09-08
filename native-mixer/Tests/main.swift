import Foundation
import AVFoundation
import CoreAudio

func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fputs("FAIL: \(message)\n", stderr); exit(1) }
}
func buffer(_ values: [Float], frames: UInt32 = 4096, rate: Double = 48000) -> AVAudioPCMBuffer {
    let format = AVAudioFormat(standardFormatWithSampleRate: rate, channels: AVAudioChannelCount(values.count))!
    let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
    pcm.frameLength = frames
    for c in values.indices { for i in 0..<Int(frames) { pcm.floatChannelData![c][i] = values[c] } }
    return pcm
}
// Drive the same AVAudioMixerNode graph used by the app, using synthetic audio only.
for destination in 0..<2 {
    for source in 0..<3 {
        let engine = AVAudioEngine()
        // Keep one engine per destination. No audio device is started by manual rendering.
        let actual = attachMixPlayers(to: engine)
        try engine.enableManualRenderingMode(.offline, format: mixFormat, maximumFrameCount: 1024)
        for (slot, input) in mixSources[destination].enumerated() {
            actual[slot].scheduleBuffer(buffer(input == source ? [0.8, -0.4] : [0, 0]))
        }
        try engine.start(); actual.forEach { $0.play() }
        let output = AVAudioPCMBuffer(pcmFormat: mixFormat, frameCapacity: 1024)!
        let status = try engine.renderOffline(1024, to: output)
        require(status == .success, "manual rendering failed")
        let allowed = source == 0 || (destination == 0 ? source == 2 : source == 1)
        for i in 0..<Int(output.frameLength) {
            require(abs(output.floatChannelData![0][i] - (allowed ? 0.8 * initialGain : 0)) < 0.0001, "mix-minus left channel")
            require(abs(output.floatChannelData![1][i] - (allowed ? -0.4 * initialGain : 0)) < 0.0001, "mix-minus right channel")
        }
        engine.stop()
    }
}
// Simultaneous full-scale sources exercise the actual summing stage.
do {
    let engine = AVAudioEngine(), sources = [buffer([1, -1]), buffer([1, -1])]
    let players = attachMixPlayers(to: engine)
    try engine.enableManualRenderingMode(.offline, format: mixFormat, maximumFrameCount: 1024)
    for i in 0..<2 { players[i].scheduleBuffer(sources[i]) }
    try engine.start(); players.forEach { $0.play() }
    let output = AVAudioPCMBuffer(pcmFormat: mixFormat, frameCapacity: 1024)!
    let rendered = try engine.renderOffline(1024, to: output)
    require(rendered == .success, "simultaneous rendering")
    for i in 0..<Int(output.frameLength) {
        require(abs(output.floatChannelData![0][i] - 2 * initialGain) < 0.0001, "both sources must be audible together")
        require(abs(output.floatChannelData![1][i] + 2 * initialGain) < 0.0001, "simultaneous negative channel")
    }
    engine.stop()
}
let normalizer = AudioNormalizer()
let bounded = try normalizer.process(buffer([Float.nan, 2]))
require(bounded.floatChannelData![0][0] == 0, "NaN must be silent")
require(bounded.floatChannelData![1][0] == 1, "source must be bounded before sum")
let mono = try AudioNormalizer().process(buffer([0.4]))
require(mono.floatChannelData![0][100] > 0.25, "mono must be audible")
require(abs(mono.floatChannelData![0][100] - mono.floatChannelData![1][100]) < 0.0001, "mono must reach both channels")
let resampler = AudioNormalizer()
var count = 0
for _ in 0..<100 {
    let pcm = try resampler.process(buffer([0.2], frames: 441, rate: 44100))
    count += Int(pcm.frameLength)
}
require(abs(count - 48000) < 128, "continuous resampling lost samples: \(count)")
require(2 * initialGain < 1, "two full-scale sources must not clip")
print("PASS: six source/destination audio renders, simultaneous summing, stereo isolation, mono, finite bounds, continuous resampling and headroom")

let appA = AudioProcess(id: 10, pid: 100, name: "test.A")
let appB = AudioProcess(id: 20, pid: 200, name: "test.B")
let microphone = Device(id: 1, name: "mic", uid: "mic", inputs: 1, outputs: 0, transport: kAudioDeviceTransportTypeBuiltIn)
let toA = Device(id: 2, name: "out A", uid: "A", inputs: 2, outputs: 2, transport: kAudioDeviceTransportTypeVirtual)
let toB = Device(id: 3, name: "out B", uid: "B", inputs: 2, outputs: 2, transport: kAudioDeviceTransportTypeVirtual)
try validateRoutes(a: appA, b: appB, microphone: microphone, toA: toA, toB: toB)
for bad in [toA, Device(id: 4, name: "alias", uid: "A", inputs: 2, outputs: 2, transport: kAudioDeviceTransportTypeVirtual)] {
    do { try validateRoutes(a: appA, b: appB, microphone: microphone, toA: toA, toB: bad); require(false, "output alias accepted") }
    catch { }
}
print("PASS: shared route validation and UID alias rejection")
