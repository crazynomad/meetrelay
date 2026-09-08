import Foundation
import CoreAudio
import AVFoundation

// Prototype: bounded scheduled PCM buffers, not a hard real-time render engine.
// All conversion/scheduling runs on this serial queue; it never writes audio to disk.
final class MixOutput {
    let engine = AVAudioEngine()
    var players: [AVAudioPlayerNode] = []
    var pending = [0, 0]
    var dropped = 0
    let queue: DispatchQueue
    init(device: AudioDeviceID, queue: DispatchQueue) throws {
        self.queue = queue
        try selectDevice(engine.outputNode.audioUnit, device)
        players = attachMixPlayers(to: engine)
        engine.mainMixerNode.outputVolume = 1
        try engine.start()
        players.forEach { $0.play() }
    }
    func push(_ buffer: AVAudioPCMBuffer, slot: Int) {
        guard buffer.frameLength > 0 else { return }
        // Bound queue growth if a device stalls; dropped buffers are visible in the UI.
        if pending[slot] >= 8 { dropped += 1; return }
        pending[slot] += 1
        players[slot].scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            self.queue.async { self.pending[slot] = max(0, self.pending[slot] - 1) }
        }
    }
    func stop() { players.forEach { $0.stop() }; engine.stop() }
    deinit { stop() }
}

final class ProcessCapture {
    var tap: AudioObjectID = 0
    var aggregate: AudioObjectID = 0
    var proc: AudioDeviceIOProcID?
    private let queue: DispatchQueue
    init(process: AudioObjectID, label: String, queue: DispatchQueue,
         receive: @escaping (AVAudioPCMBuffer) -> Void) throws {
        self.queue = queue
        do {
            let description = CATapDescription(stereoMixdownOfProcesses: [process])
            description.name = "Meeting Bridge \(label)"
            description.isPrivate = true
            description.muteBehavior = .unmuted // Original app audio continues to headphones.
            try check(AudioHardwareCreateProcessTap(description, &tap), "创建 \(label) 音频 Tap")
            var asbd = try scalar(tap, kAudioTapPropertyFormat, initial: AudioStreamBasicDescription())
            guard let format = AVAudioFormat(streamDescription: &asbd) else {
                throw BridgeError(message: "Tap 音频格式不可用")
            }
            let config: [String: Any] = [
                kAudioAggregateDeviceNameKey: "Meeting Bridge private \(label)",
                kAudioAggregateDeviceUIDKey: UUID().uuidString,
                kAudioAggregateDeviceIsPrivateKey: true,
                kAudioAggregateDeviceTapAutoStartKey: true,
                kAudioAggregateDeviceTapListKey: [[
                    kAudioSubTapUIDKey: description.uuid.uuidString,
                    kAudioSubTapDriftCompensationKey: true
                ]]
            ]
            try check(AudioHardwareCreateAggregateDevice(config as CFDictionary, &aggregate), "创建私有 Tap 设备")
            try check(AudioDeviceCreateIOProcIDWithBlock(&proc, aggregate, queue) { _, input, _, _, _ in
                let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: input))
                guard let first = buffers.first, asbd.mBytesPerFrame > 0 else { return }
                let frames = AVAudioFrameCount(first.mDataByteSize / asbd.mBytesPerFrame)
                guard frames > 0, let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return }
                pcm.frameLength = frames
                let target = UnsafeMutableAudioBufferListPointer(pcm.mutableAudioBufferList)
                guard target.count == buffers.count else { return }
                for index in buffers.indices {
                    guard let from = buffers[index].mData, let to = target[index].mData else { return }
                    memcpy(to, from, min(Int(target[index].mDataByteSize), Int(buffers[index].mDataByteSize)))
                }
                receive(pcm)
            }, "创建音频回调")
            try check(AudioDeviceStart(aggregate, proc), "启动 \(label) 音频 Tap")
        } catch { stop(); throw error }
    }
    func stop() {
        if let proc {
            AudioDeviceStop(aggregate, proc)
            AudioDeviceDestroyIOProcID(aggregate, proc)
            self.proc = nil
        }
        if aggregate != 0 { AudioHardwareDestroyAggregateDevice(aggregate); aggregate = 0 }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap); tap = 0 }
    }
    deinit { stop() }
}

final class BridgeMixer {
    let queue = DispatchQueue(label: "meeting.bridge.audio", qos: .userInteractive)
    private var outputs: [MixOutput] = []
    private var taps: [ProcessCapture] = []
    private var mic: AVAudioEngine?
    private var micTapInstalled = false
    private var normalizers = [AudioNormalizer(), AudioNormalizer(), AudioNormalizer()]
    private var active = false // accessed on queue
    private var generation = 0
    private var peak = [Float](repeating: 0, count: 3)
    private var convertedFrames = [Int](repeating: 0, count: 3)
    private var errors = 0
    private let micSlots = DispatchSemaphore(value: 8)

    func start(a: AudioProcess, b: AudioProcess, microphone: Device, toA: Device, toB: Device) throws {
        try validateRoutes(a: a, b: b, microphone: microphone, toA: toA, toB: toB)
        // Match process identity again: never silently capture a replacement process after refresh.
        let current = try processes()
        guard current.contains(where: { $0.id == a.id && $0.pid == a.pid }),
              current.contains(where: { $0.id == b.id && $0.pid == b.pid }) else {
            throw BridgeError(message: "会议进程已变化，请刷新后重新选择")
        }
        stop()
        do {
            let outA = try MixOutput(device: toA.id, queue: queue)
            let outB = try MixOutput(device: toB.id, queue: queue)
            queue.sync { outputs = [outA, outB]; active = true; generation += 1; peak = [0,0,0]; convertedFrames = [0,0,0]; errors = 0; normalizers = [AudioNormalizer(), AudioNormalizer(), AudioNormalizer()] }
            let epoch = queue.sync { generation }
            for (index, process) in [a, b].enumerated() {
                taps.append(try ProcessCapture(process: process.id, label: index == 0 ? "A" : "B", queue: queue) { [weak self] pcm in
                    self?.receive(pcm, source: index + 1, epoch: epoch)
                })
            }
            let engine = AVAudioEngine()
            mic = engine
            try selectDevice(engine.inputNode.audioUnit, microphone.id)
            let format = engine.inputNode.outputFormat(forBus: 0)
            guard format.channelCount > 0, format.sampleRate > 0 else { throw BridgeError(message: "麦克风不可用或未授权") }
            engine.inputNode.installTap(onBus: 0, bufferSize: 512, format: format) { [weak self] pcm, _ in
                guard let self, self.micSlots.wait(timeout: .now()) == .success else { return }
                guard let copy = AVAudioPCMBuffer(pcmFormat: pcm.format, frameCapacity: pcm.frameLength) else { self.micSlots.signal(); return }
                copy.frameLength = pcm.frameLength
                let src = UnsafeMutableAudioBufferListPointer(pcm.mutableAudioBufferList)
                let dst = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
                for i in src.indices { if let from = src[i].mData, let to = dst[i].mData { memcpy(to, from, Int(src[i].mDataByteSize)) } }
                self.queue.async { self.receive(copy, source: 0, epoch: epoch); self.micSlots.signal() }
            }
            micTapInstalled = true
            try engine.start()
        } catch { stop(); throw error }
    }
    private func receive(_ buffer: AVAudioPCMBuffer, source: Int, epoch: Int) {
        guard active, generation == epoch else { return }
        do {
            let pcm = try normalizers[source].process(buffer)
            convertedFrames[source] += Int(pcm.frameLength)
            var value: Float = 0
            for c in 0..<2 { for i in 0..<Int(pcm.frameLength) { value = max(value, abs(pcm.floatChannelData![c][i])) } }
            peak[source] = max(value, peak[source] * 0.9)
            for destination in 0..<2 {
                if let slot = mixSources[destination].firstIndex(of: source) { outputs[destination].push(pcm, slot: slot) }
            }
        } catch { errors += 1 }
    }
    func metrics() -> [String: Any] {
        queue.sync { ["active": active, "source_order": ["local", "A", "B"], "input_peak_dbfs": peak.map { 20 * log10(max($0, 0.00001)) },
            "microphone_engine_running": mic?.isRunning ?? false, "outputs": outputs.map { ["engine_running": $0.engine.isRunning, "pending": $0.pending, "players_playing": $0.players.map { $0.isPlaying }, "dropped": $0.dropped] as [String: Any] },
            "input_frames": convertedFrames, "dropped_buffers": outputs.reduce(0) { $0 + $1.dropped }, "conversion_errors": errors] }
    }
    func status() -> String {
        queue.sync {
            let levels = peak.map { String(format: "%.0f dB", 20 * log10(max($0, 0.00001))) }
            return "本机 \(levels[0]) · A \(levels[1]) · B \(levels[2])\n缓冲丢弃 \(outputs.reduce(0) { $0 + $1.dropped }) · 转换错误 \(errors)\n输入帧数 \(convertedFrames)（电平不代表远端验证）"
        }
    }
    func stop() {
        queue.sync { active = false; generation += 1 }
        if let mic { if micTapInstalled { mic.inputNode.removeTap(onBus: 0) }; mic.stop() }; mic = nil; micTapInstalled = false
        taps.forEach { $0.stop() }; taps = []
        queue.sync { outputs.forEach { $0.stop() }; outputs = [] }
    }
    deinit { stop() }
}
