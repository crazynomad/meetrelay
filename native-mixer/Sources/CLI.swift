import Foundation
import CoreAudio
import AVFoundation
import AppKit
import Darwin

private struct ProcessSelection: Codable { let pid: Int32; let bundle_id: String? }
private struct CLIConfig: Codable {
    let schema_version: Int
    let a: ProcessSelection
    let b: ProcessSelection
    let microphone_uid: String
    let to_a_uid: String
    let to_b_uid: String
}
private struct ResolvedConfig {
    let a: AudioProcess, b: AudioProcess
    let mic: Device, toA: Device, toB: Device
}
private func resolve(_ config: CLIConfig) throws -> ResolvedConfig {
    guard config.schema_version == 1 else { throw BridgeError(message: "Unsupported config schema_version") }
    let allProcesses = try processes(), allDevices = try devices()
    func process(_ selection: ProcessSelection) throws -> AudioProcess {
        let matches = allProcesses.filter { $0.pid == selection.pid && (selection.bundle_id == nil || selection.bundle_id == $0.name) }
        guard selection.pid > 0, matches.count == 1 else {
            throw BridgeError(message: "Audio process missing or ambiguous: PID \(selection.pid). Refresh processes; never reuse a stale PID.")
        }
        return matches[0]
    }
    func device(_ uid: String) throws -> Device {
        let matches = allDevices.filter { !$0.uid.isEmpty && $0.uid == uid }
        guard matches.count == 1 else { throw BridgeError(message: "Device UID missing or ambiguous: \(uid)") }
        return matches[0]
    }
    let result = try ResolvedConfig(a: process(config.a), b: process(config.b), mic: device(config.microphone_uid),
                                    toA: device(config.to_a_uid), toB: device(config.to_b_uid))
    try validateRoutes(a: result.a, b: result.b, microphone: result.mic, toA: result.toA, toB: result.toB)
    return result
}
private func json(_ object: [String: Any]) -> Data {
    // All callers provide only finite JSON primitives.
    (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{\"error\":\"encoding_failed\"}".utf8)
}
private func emitJSON(_ object: [String: Any]) { FileHandle.standardOutput.write(json(object) + Data([10])) }
private func readObject(_ url: URL) throws -> [String: Any] {
    guard let result = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any] else {
        throw BridgeError(message: "Expected JSON object: \(url.path)")
    }
    return result
}
private func permissions() -> String {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

// Session control never sends a signal to a PID from a file. A UUID-scoped stop
// request is consumed by the owner of a held flock, preventing PID-reuse kills.
private final class CLISession {
    let directory: URL
    let lock: Int32
    init(path: String, create: Bool) throws {
        guard path.hasPrefix("/") else { throw BridgeError(message: "--session must be an absolute directory outside the repository") }
        directory = URL(fileURLWithPath: path, isDirectory: true)
        if create && !FileManager.default.fileExists(atPath: path) {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                    attributes: [.posixPermissions: 0o700])
        }
        var info = stat()
        guard lstat(path, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == geteuid(), (info.st_mode & 0o077) == 0 else {
            throw BridgeError(message: "Session must be a private directory owned by the current user (chmod 700), not a symlink")
        }
        lock = open(directory.appendingPathComponent("lock").path, (create ? O_CREAT | O_RDWR : O_RDONLY) | O_NOFOLLOW, 0o600)
        guard lock >= 0 else { throw BridgeError(message: "Cannot open session lock") }
        var lockInfo = stat()
        guard fstat(lock, &lockInfo) == 0, (lockInfo.st_mode & S_IFMT) == S_IFREG,
              lockInfo.st_uid == geteuid(), (lockInfo.st_mode & 0o077) == 0 else {
            close(lock); throw BridgeError(message: "Invalid session lock")
        }
    }
    func acquire() throws {
        guard flock(lock, LOCK_EX | LOCK_NB) == 0 else { throw BridgeError(message: "Session already has a running owner") }
    }
    func hasOwner() -> Bool {
        if flock(lock, LOCK_EX | LOCK_NB) == 0 { flock(lock, LOCK_UN); return false }
        return errno == EWOULDBLOCK
    }
    func write(_ value: [String: Any]) throws {
        try json(value).write(to: directory.appendingPathComponent("status.json"), options: .atomic)
    }
    func read() throws -> [String: Any] {
        var result = try readObject(directory.appendingPathComponent("status.json"))
        let alive = hasOwner()
        result["owner_lock_held"] = alive
        if !alive && ["starting", "running", "stopping"].contains(result["state"] as? String ?? "") {
            result["state"] = "interrupted"
        } else if alive, let heartbeat = result["updated_at"] as? Double,
                  Date().timeIntervalSince1970 - heartbeat > 5 {
            result["state"] = "unresponsive"
        }
        return result
    }
    func stopURL(_ token: String) throws -> URL {
        guard UUID(uuidString: token) != nil else { throw BridgeError(message: "Invalid session instance token") }
        return directory.appendingPathComponent("stop-\(token)")
    }
    deinit { close(lock) }
}

func runBridgeCLI(_ arguments: [String]) -> Int32 {
    do {
        let command = arguments.first ?? "help"
        if command == "help" || command == "--help" {
            emitJSON(["schema_version": 1, "commands": [
                "devices", "processes", "validate --config FILE",
                "run --config FILE --session ABSOLUTE_DIR --clients-ready --request-permissions",
                "status --session ABSOLUTE_DIR", "stop --session ABSOLUTE_DIR"],
                "notes": "run stays in foreground; use an agent terminal session. JSON stdout. No client settings changes. No recording."])
            return 0
        }
        let allowed: [String: Set<String>] = ["devices": [], "processes": [], "validate": ["--config"],
            "run": ["--config", "--session", "--clients-ready", "--request-permissions"],
            "status": ["--session"], "stop": ["--session"]]
        guard let options = allowed[command] else { throw BridgeError(message: "Unknown command: \(command)") }
        var parsed: [String: String] = [:], i = 1
        while i < arguments.count {
            let option = arguments[i]
            guard options.contains(option), parsed[option] == nil else { throw BridgeError(message: "Unknown or repeated option: \(option)") }
            if ["--clients-ready", "--request-permissions"].contains(option) { parsed[option] = "true"; i += 1 }
            else {
                guard i + 1 < arguments.count, !arguments[i+1].hasPrefix("--") else { throw BridgeError(message: "Missing value: \(option)") }
                parsed[option] = arguments[i+1]; i += 2
            }
        }
        func required(_ key: String) throws -> String {
            guard let value = parsed[key], !value.isEmpty else { throw BridgeError(message: "Missing \(key)") }; return value
        }
        if command == "devices" {
            let values = try devices()
            emitJSON(["schema_version": 1, "state": values.isEmpty ? "needs_host_verification" : "observed",
                      "devices": values.map { ["id": $0.id, "uid": $0.uid, "name": $0.name,
                        "input_channels": $0.inputs, "output_channels": $0.outputs, "virtual": $0.isVirtual] as [String: Any] },
                      "end_to_end": "untested"])
            return values.isEmpty ? 2 : 0
        }
        if command == "processes" {
            let values = try processes()
            emitJSON(["schema_version": 1, "state": values.isEmpty ? "not_observed" : "observed",
                      "processes": values.map { ["id": $0.id, "pid": $0.pid, "bundle_id": $0.name] as [String: Any] }])
            return values.isEmpty ? 2 : 0
        }
        if command == "status" || command == "stop" {
            let session = try CLISession(path: required("--session"), create: false)
            let state = try session.read()
            if command == "status" { emitJSON(state); return 0 }
            guard state["owner_lock_held"] as? Bool == true else { emitJSON(state); return 0 }
            guard let instance = state["instance_id"] as? String else { throw BridgeError(message: "Missing instance_id; not stopping an unidentified process") }
            let stopFile = try session.stopURL(instance)
            try Data().write(to: stopFile, options: .atomic)
            let deadline = Date().addingTimeInterval(8)
            while Date() < deadline {
                let latest = try session.read()
                if latest["instance_id"] as? String != instance || latest["owner_lock_held"] as? Bool == false {
                    emitJSON(["schema_version": 1, "state": "stop_completed", "stopped_instance": instance, "session": latest]); return 0
                }
                Thread.sleep(forTimeInterval: 0.1)
            }
            emitJSON(["schema_version": 1, "state": "stop_pending", "instance_id": instance,
                      "note": "Owner has not exited; no PID signal was sent. Check status."])
            return 2
        }
        let config = try JSONDecoder().decode(CLIConfig.self, from: Data(contentsOf: URL(fileURLWithPath: required("--config"))))
        let selection = try resolve(config)
        if command == "validate" {
            emitJSON(["schema_version": 1, "state": "configuration_valid", "microphone_permission": permissions(),
                      "system_audio_permission": "not_probed", "client_settings": "not_inspected", "end_to_end": "untested",
                      "devices": ["microphone": selection.mic.name, "to_a": selection.toA.name, "to_b": selection.toB.name]])
            return 0
        }
        guard parsed["--clients-ready"] != nil else {
            throw BridgeError(message: "Verify both clients muted and both speakers on physical headphones, then pass --clients-ready")
        }
        let session = try CLISession(path: required("--session"), create: true)
        try session.acquire()
        let instance = UUID().uuidString
        let stopFile = try session.stopURL(instance)
        let mixer = BridgeMixer()
        var state = "starting", reason = "", finished = false, exitCode: Int32 = 0
        var snapshot: [String: Any] { ["schema_version": 1, "instance_id": instance, "pid": getpid(),
            "state": state, "reason": reason, "updated_at": Date().timeIntervalSince1970,
            "metrics": mixer.metrics(), "end_to_end": "untested", "client_settings": "asserted_by_operator"] }
        try session.write(snapshot)
        func finish(_ message: String, failure: Bool = false) {
            guard !finished else { return }
            state = "stopping"; reason = message
            try? session.write(snapshot)
            mixer.stop(); state = failure ? "failed" : "stopped"; finished = true; exitCode = failure ? 2 : 0
            try? session.write(snapshot); emitJSON(snapshot)
        }
        signal(SIGINT, SIG_IGN); signal(SIGTERM, SIG_IGN)
        let signals = [SIGINT, SIGTERM].map { number -> DispatchSourceSignal in
            let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
            source.setEventHandler { finish("signal_\(number)") }; source.resume(); return source
        }
        let sleepObserver = NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.willSleepNotification,
                        object: nil, queue: .main) { _ in finish("system_sleep") }
        defer { signals.forEach { $0.cancel() }; NSWorkspace.shared.notificationCenter.removeObserver(sleepObserver); mixer.stop() }
        do {
            if permissions() == "not_determined", parsed["--request-permissions"] != nil {
                var answered = false, granted = false
                AVCaptureDevice.requestAccess(for: .audio) { allowed in DispatchQueue.main.async { granted = allowed; answered = true } }
                let deadline = Date().addingTimeInterval(60)
                while !answered && !finished && Date() < deadline {
                    if FileManager.default.fileExists(atPath: stopFile.path) { finish("stop_requested") }
                    try session.write(snapshot)
                    RunLoop.main.run(until: Date().addingTimeInterval(0.1))
                }
                if finished { return exitCode }
                guard answered && granted else { throw BridgeError(message: "Microphone permission denied or timed out; complete permission in the host app and retry") }
            }
            guard permissions() == "authorized" else { throw BridgeError(message: "Microphone permission is \(permissions()). Use --request-permissions only with user authorization.") }
            guard parsed["--request-permissions"] != nil else {
                throw BridgeError(message: "Core Audio Tap can trigger system-audio consent. run requires --request-permissions to explicitly allow that prompt.")
            }
            try mixer.start(a: selection.a, b: selection.b, microphone: selection.mic, toA: selection.toA, toB: selection.toB)
            state = "running"; try session.write(snapshot); emitJSON(snapshot)
            var lastCheck = Date.distantPast
            while !finished {
                if FileManager.default.fileExists(atPath: stopFile.path) { finish("stop_requested"); break }
                if Date().timeIntervalSince(lastCheck) >= 1 {
                    let fresh = try resolve(config)
                    guard fresh.a.id == selection.a.id, fresh.b.id == selection.b.id,
                          fresh.mic.id == selection.mic.id, fresh.toA.id == selection.toA.id, fresh.toB.id == selection.toB.id else {
                        throw BridgeError(message: "Device or process identity changed; stopping")
                    }
                    try session.write(snapshot); lastCheck = Date()
                }
                RunLoop.main.run(until: Date().addingTimeInterval(0.1))
            }
        } catch { finish(error.localizedDescription, failure: true) }
        return exitCode
    } catch {
        emitJSON(["schema_version": 1, "state": "error", "message": error.localizedDescription])
        return 1
    }
}
