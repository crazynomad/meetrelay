# 混音模式：本机与同事同时发言

`plan --mode mix` 生成配置方案，不运行音频或安装软件。先确定混音后端；计划的 `planned_only` 与 `untested` 不代表实际连通。

| 目标 | 包含来源 | 排除来源 |
|---|---|---|
| 腾讯 A 麦克风 | 本机麦克风 + 钉钉 B 原声 | 腾讯 A 原声 |
| 钉钉 B 麦克风 | 本机麦克风 + 腾讯 A 原声 | 钉钉 B 原声 |
| 本机耳机 | 腾讯 A 原声 + 钉钉 B 原声 | 本机麦克风监听（默认） |

这种排除接收方原声的混音称为 mix-minus。聚集设备只并列声道，多输出设备只复制声音，都不会自动求和或实现此矩阵。

## 原生混音原型（native）

配套仓库的 `native-mixer/` 是 Swift + Apple Core Audio Tap / AVAudioEngine 原型，要求 macOS 14.4+，通过 `sh native-mixer/build.sh` 构建。它不包含在仅安装 skill 的副本内：执行前先在用户项目中确认源码和已构建应用，缺少时报告依赖未安装，不猜测其位置。运行应用不会自动开始捕获；agent 可按 [CLI 操作](cli.md) 使用结构化 JSON 命令控制同一引擎。旧的只读 `--inventory` 仍可列出设备及音频进程。

原型直接从选定 A/B 音频进程获取原声，使用两张独立虚拟声卡存放混音，不需要再加两张原声声卡。Apple SDK 是系统 SDK；配套原型不依赖 LadioCast 或其他第三方混音软件。不要把 Apple SDK 称为开源 SDK。

```sh
python3 <skill-dir>/scripts/bridge.py plan --mode mix --mixer native \
  --headphones '实际实体耳机名称' --microphone '实际实体麦克风名称' \
  --mix-to-a '实际送 A 虚拟设备' --mix-to-b '实际送 B 虚拟设备' \
  --output /absolute/session-dir/native-mix-plan.json
```

实施顺序：

1. 保存 A/B 输入、输出、静音状态以及旧 Bridge 多输出成员，静音两款客户端。
2. **两款客户端的扬声器均改为实体耳机**，不能保留旧的 Bridge A to B / Bridge B to A 多输出。否则原声与混音会被重复写入同一虚拟设备，可能回授。
3. 在原型中选择两款客户端实际发出音频的独立进程、实体麦克风，以及两个独立混音输出。钉钉主程序与 DingMeeting 子进程可能不同；查看实际 PID 与电平，不将全部系统声音作为兜底来源。原型目前每端只选一个音频进程；多进程捕获完整性未验证。
4. 明确原型将获取所选会议音频与本机麦克风、仅在内存中处理；按实际授权处理 macOS 音频访问提示。启动混音，观察三个输入电平，核实设备隔离后，把 A/B 的麦克风改为各自混音输出。配置完成保持会议静音。
5. 在已授权的测试会议中验证三路单独发言和同时发言；依据下节记录结果。只有电平不能证明对端收听或无回声。

例如设备真实存在且独立时，可令送 A=BlackHole 16ch、送 B=IdeaShare 2ch。名称、UID、声道和实际隔离仍需核实；不能将一个设备的改名或镜像当成第二张声卡。原型输出默认使用声道 1–2，未验证其他客户端的声道选取。

原型默认每路 −9 dB、限制输入幅度，再做求和。界面显示电平、转换错误和缓冲丢弃；出现丢弃/错误或设备变化时，先静音会议并停止排查。它采用有界缓冲和采样率转换，目前没有经过长时间时钟漂移、端到端延迟、掉线恢复或正式会议稳定性验证。不能称为生产可用。

退出：先静音两边，停止原型，然后恢复保存的输入与输出。原生方案改变了客户端扬声器；恢复切换模式时必须同时恢复旧多输出，不能只换麦克风。设备被移除、选定进程退出或 Mac 休眠时原型尝试停止，但不替客户端切换输入或解除静音。

## 外部混音器（LadioCast / Loopback）

只按用户选择使用；用户拒绝 LadioCast 时不再安装或配置它。[LadioCast](https://apps.apple.com/us/app/ladiocast/id411213048?mt=12) 是免费软件，不应称为开源。[Loopback](https://rogueamoeba.com/support/manuals/loopback/?print=true) 的安装或付费许可不包含在选择免费方案的授权中。

外部设备混音方案需要四条独立通路：forward 存 A 原声、return_bus 存 B 原声、mix_to_a / mix_to_b 存两份混音。保留两个多输出设备监听原声；混音器不再监听耳机，也不能输出到原声通路。

```sh
python3 <skill-dir>/scripts/bridge.py plan --mode mix --mixer ladiocast \
  --headphones '实际耳机名称' --microphone '实际麦克风名称' \
  --forward '实际 A 原声设备' --return-bus '实际 B 原声设备' \
  --mix-to-a '实际混音输出 A' --mix-to-b '实际混音输出 B'
```

LadioCast 需要显式提供两个已存在的混音输出；Loopback 可用 `--mixer loopback` 在软件内创建默认 `Bridge Mix to A/B`。后者移除无关 Pass-Thru 与 Monitor；两种后端都按 `mixer.outputs` 配置两份来源，排除接收方原声。每来源从 −9 dB 起步，麦克风默认第 1 声道复制为立体声，实际有声声道需核实。

LadioCast 的两个输出母线分别接 mix_to_a / mix_to_b：本机输入发送到两者；forward 只送 B；return_bus 只送 A。清除其他输入及母线交叉发送，不录音、不推流。各版本具体控件从当前 UI 读取。

[BlackHole 官方仓库](https://github.com/ExistentialAudio/BlackHole) 的常见 Homebrew 包为 2ch、16ch、64ch；不假设存在 `blackhole-128ch` cask。已有改名驱动可能占用标准版本的 UID，安装前检查；缺少独立通路时不提前把会议输入改到空设备。

## 发言与验证

- 同时发言需要两款客户端均解除静音；仅在已授权的测试或发言下执行，方案不会自动开麦。
- 内部讨论只需静音 A；本机与 B 互通，B 继续听 A 的远端声音。
- 静音 B 会同时阻止本机与 A 原声传到 B，不是仅关闭 A 转发。
- 原生与外部后端的恢复路径不同；使用各自实际记录，避免套用旧方案。

按 [会前验证](verification.md) 测试。同事测试端只加入 B；A 的独立远端参与者确认 A 收到的声音。完成双向、同时发言、无回声、静音隔离、稳定性与恢复前，均标明“端到端未验证”。
