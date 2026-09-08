import Cocoa
import AVFoundation
import CoreAudio

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let a = NSPopUpButton(), b = NSPopUpButton(), mic = NSPopUpButton()
    let outA = NSPopUpButton(), outB = NSPopUpButton()
    let ready = NSButton(checkboxWithTitle: "我已将两边扬声器改为实体耳机，并将两边会议静音", target: nil, action: nil)
    let status = NSTextField(wrappingLabelWithString: "已停止 · 尚未进行远端验证")
    let startButton = NSButton(title: "开始混音", target: nil, action: nil)
    let stopButton = NSButton(title: "停止混音", target: nil, action: nil)
    let mixer = BridgeMixer()
    var appList: [AudioProcess] = [], microphones: [Device] = [], virtuals: [Device] = []
    var running = false, starting = false
    var requestID = 0
    var monitor: Timer?
    var configurationObservers: [NSObjectProtocol] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu(), item = NSMenuItem(), appMenu = NSMenu()
        appMenu.addItem(withTitle: "退出 Meeting Bridge", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu; menu.addItem(item); NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 740, height: 550),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Meeting Bridge · 原生混音原型"
        let root = NSStackView(); root.orientation = .vertical; root.alignment = .leading; root.spacing = 16
        root.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(root)
        NSLayoutConstraint.activate([root.leadingAnchor.constraint(equalTo: window.contentView!.leadingAnchor, constant: 24),
            root.trailingAnchor.constraint(equalTo: window.contentView!.trailingAnchor, constant: -24),
            root.topAnchor.constraint(equalTo: window.contentView!.topAnchor, constant: 24)])
        let title = NSTextField(labelWithString: "同时发言，两路独立混音")
        title.font = .boldSystemFont(ofSize: 23); root.addArrangedSubview(title)
        root.addArrangedSubview(NSTextField(wrappingLabelWithString:
            "送腾讯 = 本机麦克风 + 钉钉声音；送钉钉 = 本机麦克风 + 腾讯声音。\n直接获取所选进程的音频，声音仅在内存中流转。原型未完成实际会议验证。"))
        for (label, popup) in [("A · 腾讯音频进程", a), ("B · 钉钉音频进程", b), ("本机实体麦克风", mic),
                               ("送 A · 腾讯麦克风设备", outA), ("送 B · 钉钉麦克风设备", outB)] {
            let row = NSStackView(); row.orientation = .horizontal
            let field = NSTextField(labelWithString: label); field.widthAnchor.constraint(equalToConstant: 195).isActive = true
            row.addArrangedSubview(field); row.addArrangedSubview(popup)
            popup.widthAnchor.constraint(equalToConstant: 455).isActive = true
            root.addArrangedSubview(row)
        }
        root.addArrangedSubview(ready)
        let controls = NSStackView()
        let refreshButton = NSButton(title: "刷新进程和设备", target: self, action: #selector(refresh))
        startButton.target = self; startButton.action = #selector(start)
        stopButton.target = self; stopButton.action = #selector(stop)
        controls.addArrangedSubview(refreshButton); controls.addArrangedSubview(startButton); controls.addArrangedSubview(stopButton)
        root.addArrangedSubview(controls); root.addArrangedSubview(status)
        status.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        refresh()
        monitor = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            guard let self, self.running else { return }
            self.status.stringValue = "运行中 · 请保持耳机连接\n" + self.mixer.status()
            // Fail closed after process/device loss. Re-selection requires an explicit start.
            guard let present = try? devices(), let current = try? processes(),
                  self.selectedDevices.allSatisfy({ id in present.contains { $0.id == id } }),
                  self.selectedProcesses.allSatisfy({ selected in current.contains { $0.id == selected.id && $0.pid == selected.pid } }) else {
                self.stop(); self.status.stringValue = "设备或会议进程已退出，混音已停止；请先静音会议，再刷新。"; return
            }
        }
        configurationObservers.append(NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.willSleepNotification, object: nil, queue: .main) { [weak self] _ in
                self?.stop(); self?.status.stringValue = "电脑休眠，混音已停止。唤醒后请重新检查设备。"
            })
        window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    var selectedDevices: [AudioDeviceID] = []
    var selectedProcesses: [AudioProcess] = []
    @objc func refresh() {
        guard !running, !starting else { return }
        do {
            appList = try processes().sorted { $0.name < $1.name }
            let all = try devices()
            microphones = all.filter { $0.inputs > 0 && !$0.isVirtual && $0.transport != kAudioDeviceTransportTypeAggregate }
            virtuals = all.filter { $0.isVirtual && $0.inputs >= 2 && $0.outputs >= 2 }
            for popup in [a, b] { popup.removeAllItems(); popup.addItems(withTitles: appList.map { "\($0.name) [PID \($0.pid)]" }); popup.selectItem(at: -1) }
            mic.removeAllItems(); mic.addItems(withTitles: microphones.map { $0.name })
            for popup in [outA, outB] { popup.removeAllItems(); popup.addItems(withTitles: virtuals.map { $0.name }); popup.selectItem(at: -1) }
            // Known names are suggestions only. Nothing starts or changes client settings here.
            if let i = appList.firstIndex(where: { ($0.name.lowercased().contains("tencent") && $0.name.lowercased().contains("meeting")) }) { a.selectItem(at: i) }
            if let i = appList.firstIndex(where: { $0.name.lowercased().contains("dingtalk.meeting") }) { b.selectItem(at: i) }
            if let i = microphones.firstIndex(where: { $0.name.contains("MacBook") }) { mic.selectItem(at: i) }
            if let i = virtuals.firstIndex(where: { $0.name == "BlackHole 16ch" }) { outA.selectItem(at: i) }
            if let i = virtuals.firstIndex(where: { $0.name == "IdeaShare 2ch" }) { outB.selectItem(at: i) }
            status.stringValue = "已停止 · 发现 \(appList.count) 个音频进程、\(virtuals.count) 个虚拟设备\n先核实进程；仅安装技能时，本应用不随技能一起安装。"
        } catch { status.stringValue = error.localizedDescription }
    }
    @objc func start() {
        guard !running, !starting else { return }
        guard ready.state == .on else { status.stringValue = "开始前，请确认两边使用实体耳机输出并保持静音。旧的 Bridge 多输出会把原声再次写入混音声卡。"; return }
        guard appList.indices.contains(a.indexOfSelectedItem), appList.indices.contains(b.indexOfSelectedItem),
              microphones.indices.contains(mic.indexOfSelectedItem), virtuals.indices.contains(outA.indexOfSelectedItem),
              virtuals.indices.contains(outB.indexOfSelectedItem) else { status.stringValue = "请完整选择两端进程、麦克风和输出设备。"; return }
        requestID += 1; let request = requestID
        starting = true; startButton.isEnabled = false; status.stringValue = "等待麦克风授权；系统音频访问也可能弹出授权提示。"
        AVCaptureDevice.requestAccess(for: .audio) { [weak self] allowed in
            DispatchQueue.main.async {
                guard let self, self.requestID == request else { return }; self.starting = false; self.startButton.isEnabled = true
                guard allowed else { self.status.stringValue = "麦克风授权被拒绝，未启动混音。"; return }
                do {
                    let a = self.appList[self.a.indexOfSelectedItem], b = self.appList[self.b.indexOfSelectedItem]
                    let mic = self.microphones[self.mic.indexOfSelectedItem]
                    let toA = self.virtuals[self.outA.indexOfSelectedItem], toB = self.virtuals[self.outB.indexOfSelectedItem]
                    try self.mixer.start(a: a, b: b, microphone: mic, toA: toA, toB: toB)
                    self.selectedProcesses = [a, b]; self.selectedDevices = [mic.id, toA.id, toB.id]
                    self.running = true
                    for popup in [self.a, self.b, self.mic, self.outA, self.outB] { popup.isEnabled = false }
                    self.ready.isEnabled = false
                } catch { self.status.stringValue = error.localizedDescription }
            }
        }
    }
    @objc func stop() {
        requestID += 1; starting = false; startButton.isEnabled = true
        mixer.stop(); running = false; ready.state = .off
        for popup in [a, b, mic, outA, outB] { popup.isEnabled = true }
        ready.isEnabled = true
        status.stringValue = "已停止混音 · 客户端设备保持原选择，请在静音状态恢复会前设备。"
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) { monitor?.invalidate(); mixer.stop() }
}

if CommandLine.arguments.dropFirst().first == "--cli" {
    exit(runBridgeCLI(Array(CommandLine.arguments.dropFirst(2))))
} else if CommandLine.arguments.contains("--inventory") {
    do {
        for d in try devices() { print("DEVICE \(d.id) | \(d.name) | in=\(d.inputs) out=\(d.outputs) | uid=\(d.uid) | virtual=\(d.isVirtual)") }
        for p in try processes() { print("PROCESS \(p.id) | pid=\(p.pid) | \(p.name)") }
    } catch { fputs("\(error.localizedDescription)\n", stderr); exit(1) }
} else {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.regular)
    app.run()
}
