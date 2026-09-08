# 串个会 · MeetRelay — 原生应用与 CLI

Swift + macOS Core Audio Process Tap + AVAudioEngine，源码在本目录，无第三方包依赖。要求 macOS 14.4+ 和 Xcode Command Line Tools。2026-09-08，腾讯 ↔ 钉钉 CLI 混音实测由使用者反馈成功，未逐项记录听感或测试时长。首次启动出现过缓冲堵塞，重启后完成测试，根因尚未完全确认；长时间漂移、延迟和掉线恢复仍待验证。正式使用前先做短测试。面向使用者的场景介绍见 [项目首页](../README.md)。

## 构建与检查

```sh
sh native-mixer/build.sh
sh native-mixer/test.sh
'native-mixer/build/Meeting Bridge.app/Contents/MacOS/MeetingBridge' --inventory
```

从 Finder 打开 `native-mixer/build/Meeting Bridge.app`。首次构建使用本地 ad-hoc 签名，没有公证；后续重建可能需要重新确认 macOS 音频访问权限。应用启动默认停止，不修改会议选择或系统默认音频设备。`--inventory` 只枚举，不创建 Tap、不打开麦克风。

受限执行沙箱可能无法发现 Core Audio 设备或加载 AVAudioEngine 组件；这不证明驱动不存在。离线测试应在允许访问系统音频框架的本地进程运行；它只渲染合成缓冲，不打开硬件音频。

## CLI 版本

构建同时生成 `native-mixer/build/meeting-bridge`，可从 agent 终端运行。与 GUI 共用同一引擎和应用包，无需点击窗口。

```sh
native-mixer/build/meeting-bridge devices
native-mixer/build/meeting-bridge processes
native-mixer/build/meeting-bridge validate --config /private/tmp/meeting-bridge-config.json
native-mixer/build/meeting-bridge run --config /private/tmp/meeting-bridge-config.json --session /private/tmp/meeting-bridge-session --clients-ready --request-permissions
# 从另一个终端或工具调用读取状态与停止：
native-mixer/build/meeting-bridge status --session /private/tmp/meeting-bridge-session
native-mixer/build/meeting-bridge stop --session /private/tmp/meeting-bridge-session
```

启动前必须按下文配置客户端并获得音频访问授权。`run` 保持前台；`--clients-ready` 是对实际设置的声明，不能用于跳过检查。配置格式、JSON 状态、权限行为、停止语义和退出码见 [agent CLI 操作指南](../skills/meeting-audio-bridge/references/cli.md)。

CLI 协议与会话控制测试不启动音频：

```sh
python3 -m unittest discover -s native-mixer/Tests -p 'test_cli.py' -v
```

## 实测使用的设备设置

以下是本次成功测试所用的配置示例，其他机器需按实际设备替换；它不表示此刻客户端仍保持这些选择：

| 项目 | 目标 |
|---|---|
| 腾讯扬声器 | External Headphones |
| 钉钉扬声器 | External Headphones |
| 原型实体麦克风 | MacBook Air Microphone，或实际使用的实体麦克风 |
| 原型送 A / 腾讯麦克风 | BlackHole 16ch |
| 原型送 B / 钉钉麦克风 | IdeaShare 2ch |

先保存旧设备记录并将两款会议静音，**把两边扬声器从旧 Bridge 多输出改回实体耳机**。旧多输出仍写入虚拟声卡，会污染本方案的混音并可能造成回声。

打开两个会议客户端，在原型刷新列表，选择各自实际发出声音的音频进程；钉钉可能是独立 DingMeeting 子进程。默认建议不是进程验证。确认实体耳机输出和静音状态后勾选界面确认项，再开始。macOS 会要求麦克风与系统音频访问；音频只在内存中处理，没有录音文件、转写或网络推流。

确认三个输入电平和输出隔离后，才将客户端麦克风设置为表中对应虚拟设备。测试端同事只加入钉钉，腾讯另需独立参与者确认。配置时保持两边静音；在测试发言时才开麦。不要以本机电平代替远端收听确认。

停止按钮关闭本机混音，**不会恢复客户端设备或控制会议静音**。结束前先静音两边，然后停止并恢复会前记录。返回旧切换模式时，输入和 Bridge 多输出都需恢复。

## 实现与边界

- 每个会议建立一个私有、包含指定进程的 Core Audio Tap，`unmuted` 保留该会议原声到耳机。只为 Tap 创建临时私有聚集设备，停止时释放；不安装驱动。
- 本机麦克风用独立 AVAudioEngine；两路输出各用独立 AVAudioEngine 和两个播放器节点。输入经连续 AVAudioConverter 转为 48 kHz 双声道并限制幅度，每来源 −9 dB，双源峰值理论不超过约 0.71。
- 路由固定为 A←本机+B、B←本机+A，不含本机监听。两个现有独立虚拟声卡只承载混音，原声由 Tap 提供；不需要新增两张原声声卡。
- 原型使用有界缓冲；缓冲拥塞时丢弃并计数。它不具备经过验证的自适应时钟同步、硬实时保证、无缝设备切换或看门狗恢复。输入格式转换错误可在界面看到。
- 每端目前只选择一个音频进程；客户端音频分散在多个子进程时可能漏音。没有“捕获全部系统音频”的自动兜底。设备/PID 消失和睡眠会尝试停止；仍须人工确认静音及恢复。
- 没有自动增益、噪声抑制或声学回声消除；保留客户端处理并用耳机实测。两边同时讲话是否被客户端降噪压制尚未验证。

离线测试真实驱动 AVAudioMixerNode 的手动渲染，检查六种来源/目标组合、左右声道、单声道、非有限值、重采样连续性和余量。它不测试 Core Audio Tap 的实际权限或会议网络传输。

## 选型依据

核对日期：2026-09-08。

| 方案 | 用途与本任务的差别 |
|---|---|
| [FineTune](https://github.com/ronitsingh10/FineTune) | 开源的应用音量控制与路由工具；尚未确认能直接提供本任务的“实体麦克风 + 对侧应用”两份混音。 |
| [miniaudio](https://github.com/mackron/miniaudio) | 轻量 C 音频库，提供设备输入输出等能力；应用级音频获取与桥接界面仍需开发。 |
| [RtAudio](https://github.com/thestk/rtaudio) | C++ 实时音频输入输出抽象，支持 CoreAudio；不会自动实现会议混音矩阵。 |
| [Apple Core Audio Tap](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps) | 直接获取指定进程的输出，适合本机专用工具，因此用于此原型。Apple SDK 本身并非开源 SDK。 |

本目录为本项目编写的实现，没有复制以上第三方项目源码；发布前应单独决定本项目的开源许可证。
