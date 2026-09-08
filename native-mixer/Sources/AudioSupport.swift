import Foundation
import CoreAudio
import AVFoundation

struct BridgeError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}
func check(_ status: OSStatus, _ operation: String) throws {
    if status != noErr { throw BridgeError(message: "\(operation): Core Audio \(status)") }
}
func address(_ selector: AudioObjectPropertySelector,
             _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
func objectIDs(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) throws -> [AudioObjectID] {
    var a = address(selector), size: UInt32 = 0
    try check(AudioObjectGetPropertyDataSize(object, &a, 0, nil, &size), "枚举大小")
    if size == 0 { return [] }
    var values = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    try values.withUnsafeMutableBytes { bytes in
        try check(AudioObjectGetPropertyData(object, &a, 0, nil, &size, bytes.baseAddress!), "枚举设备")
    }
    return values
}
func stringProperty(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var a = address(selector), value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(object, &a, 0, nil, &size, &value) == noErr else { return "" }
    return value?.takeRetainedValue() as String? ?? ""
}
func scalar<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector, initial: T,
               scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) throws -> T {
    var a = address(selector, scope), result = initial, size = UInt32(MemoryLayout<T>.size)
    try withUnsafeMutablePointer(to: &result) { pointer in
        try check(AudioObjectGetPropertyData(object, &a, 0, nil, &size, pointer), "读取属性")
    }
    return result
}
struct Device: Identifiable {
    let id: AudioDeviceID
    let name: String
    let uid: String
    let inputs: Int
    let outputs: Int
    let transport: UInt32
    var isVirtual: Bool { transport == kAudioDeviceTransportTypeVirtual }
}
func channelCount(_ id: AudioDeviceID, _ scope: AudioObjectPropertyScope) -> Int {
    var a = address(kAudioDevicePropertyStreamConfiguration, scope), size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &a, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let memory = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
    defer { memory.deallocate() }
    guard AudioObjectGetPropertyData(id, &a, 0, nil, &size, memory) == noErr else { return 0 }
    return UnsafeMutableAudioBufferListPointer(memory.assumingMemoryBound(to: AudioBufferList.self))
        .reduce(0) { $0 + Int($1.mNumberChannels) }
}
func devices() throws -> [Device] {
    try objectIDs(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDevices).map {
        Device(id: $0, name: stringProperty($0, kAudioObjectPropertyName),
               uid: stringProperty($0, kAudioDevicePropertyDeviceUID),
               inputs: channelCount($0, kAudioDevicePropertyScopeInput),
               outputs: channelCount($0, kAudioDevicePropertyScopeOutput),
               transport: (try? scalar($0, kAudioDevicePropertyTransportType, initial: UInt32(0))) ?? 0)
    }
}
struct AudioProcess: Identifiable {
    let id: AudioObjectID
    let pid: pid_t
    let name: String
}
func processes() throws -> [AudioProcess] {
    try objectIDs(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyProcessObjectList).compactMap { id in
        let pid = try scalar(id, kAudioProcessPropertyPID, initial: pid_t(0))
        guard pid != getpid(), pid > 0 else { return nil }
        let bundle = stringProperty(id, kAudioProcessPropertyBundleID)
        return AudioProcess(id: id, pid: pid, name: bundle.isEmpty ? "PID \(pid)" : bundle)
    }
}
func selectDevice(_ unit: AudioUnit?, _ id: AudioDeviceID) throws {
    guard let unit else { throw BridgeError(message: "音频单元不可用") }
    var device = id
    try check(AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
                                   kAudioUnitScope_Global, 0, &device,
                                   UInt32(MemoryLayout<AudioDeviceID>.size)), "选择设备")
}

// Original mix-minus matrix: source 0 = local mic, 1 = A, 2 = B.
// Destination 0 = A, 1 = B. Neither destination ever receives its own app.
let mixSources = [[0, 2], [0, 1]]
let initialGain: Float = 0.3548134 // -9 dB; two bounded sources peak below 0.71.
let mixFormat = AVAudioFormat(standardFormatWithSampleRate: 48_000, channels: 2)!

final class AudioNormalizer {
    private var converter: AVAudioConverter?
    func process(_ input: AVAudioPCMBuffer) throws -> AVAudioPCMBuffer {
        if converter?.inputFormat != input.format {
            converter = AVAudioConverter(from: input.format, to: mixFormat)
            converter?.primeMethod = .none
        }
        guard let converter else { throw BridgeError(message: "不支持输入音频格式") }
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * 48_000 / input.format.sampleRate)) + 64
        let result = AVAudioPCMBuffer(pcmFormat: mixFormat, frameCapacity: capacity)!
        var supplied = false, error: NSError?
        let status = converter.convert(to: result, error: &error) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true; state.pointee = .haveData; return input
        }
        if status == .error { throw error ?? BridgeError(message: "采样率转换失败") as NSError }
        if let channels = result.floatChannelData {
            for c in 0..<2 { for i in 0..<Int(result.frameLength) {
                let x = channels[c][i]
                channels[c][i] = x.isFinite ? max(-1, min(1, x)) : 0
            } }
        }
        return result
    }
}

func attachMixPlayers(to engine: AVAudioEngine) -> [AVAudioPlayerNode] {
    (0..<2).map { _ in
        let player = AVAudioPlayerNode()
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: mixFormat)
        player.volume = initialGain
        return player
    }
}

func validateRoutes(a: AudioProcess, b: AudioProcess, microphone: Device, toA: Device, toB: Device) throws {
        guard a.id != b.id, a.pid != b.pid, toA.id != toB.id, toA.uid != toB.uid,
              microphone.id != toA.id, microphone.id != toB.id,
              !microphone.isVirtual, microphone.transport != kAudioDeviceTransportTypeAggregate, microphone.inputs > 0,
              toA.isVirtual, toB.isVirtual, toA.inputs >= 2, toA.outputs >= 2,
              toB.inputs >= 2, toB.outputs >= 2 else {
            throw BridgeError(message: "请选择不同会议进程、实体麦克风和两张独立虚拟声卡")
        }
}
