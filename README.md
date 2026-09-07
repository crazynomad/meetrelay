# Meeting Audio Bridge Skill

在 Mac 上通过 BlackHole 桥接腾讯会议与钉钉会议，让异地同事旁听，并通过切换麦克风向正式会议发言。

这是可安装的 Codex skill，附带 Python 标准库实现的设备检查和路由方案生成器。它通过 agent 可用的原生界面工具或用户操作完成配置；**不是运行一个命令就自动接通会议的音频应用**。

## 能做什么

| 模式 | 正式会议 → 同事 | 同事 → 正式会议 | 本机讲话 |
|---|---|---|---|
| 旁听 | 支持 | 不转发 | 使用实体麦克风向正式会议发言 |
| 双向、切换发言人 | 支持 | 腾讯输入切到返程时支持 | 切回实体麦克风后向正式会议发言 |
| 同时发言 | 需要额外混音 | 需要额外混音 | 本仓库提供矩阵设计，未实现混音器 |

旁听和双向基础模式下，同事听不到本机麦克风。内部讨论可临时将钉钉输入切为实体麦克风，同时保持腾讯静音；这会暂停同事旁听正式会议。

## 使用 skill

可直接让 Codex 读取本仓库中的 `skills/meeting-audio-bridge/SKILL.md`。若希望跨项目自动发现，安装到个人 skill 目录（目标存在时不覆盖）：

```sh
python3 - <<'PY'
import os
from pathlib import Path
import shutil
source = Path('skills/meeting-audio-bridge').resolve()
if not (source / 'SKILL.md').is_file():
    raise SystemExit('请先进入本仓库根目录')
destination = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'skills' / source.name
destination.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
print(destination)
PY
```

将以下请求交给能发现该 skill 的 Codex 会话：

> 使用 $meeting-audio-bridge 检查我的 Mac，把腾讯会议转给钉钉同事旁听，并支持切换同事发言。先保存原设置，配置后做会前验证。

当前机器没有 UI 工具时，skill 会提供具体手动步骤并标注未完成项目。安装 skill 不会安装 BlackHole、修改设备、加入会议或录音。

## 单独使用命令行

要求 Python 3.9+。计划生成、测试可在其他平台运行；实时设备枚举仅 macOS。

```sh
python3 skills/meeting-audio-bridge/scripts/bridge.py doctor
python3 skills/meeting-audio-bridge/scripts/bridge.py plan --mode two-way --headphones 'USB Headset' --microphone 'MacBook Pro Microphone'
python3 skills/meeting-audio-bridge/scripts/bridge.py plan --mode two-way --speaker colleague --headphones 'USB Headset' --microphone 'MacBook Pro Microphone'
```

`plan` 输出可读 JSON，包括所需驱动、多输出设备成员、两款软件的输入/输出、信号图和发言切换步骤。`--output /path/to/plan.json` 保存新文件，拒绝覆盖。设备名应使用本机观察到的名称；计划不会检查这些设备是否存在。

`doctor` 只读取 `system_profiler SPAudioDataType -json`；输出 `observed` 不是音频连通证明。空列表、超时或权限限制会标为需 UI 核实，不能据此断定驱动未安装。`--fixture /path/to/profiler.json` 可离线复现枚举问题。

退出码：`0` 已生成计划或观察到设备；`1` 参数/文件错误；`2` 设备枚举需界面确认或平台不支持。都不代表端到端音频测试通过。

## 工作流与验证

- [Skill 入口](skills/meeting-audio-bridge/SKILL.md)
- [Mac 配置、驱动安装和回声排障](skills/meeting-audio-bridge/references/macos.md)
- [远端验证和恢复流程](skills/meeting-audio-bridge/references/verification.md)
- [同时发言所需混音矩阵](skills/meeting-audio-bridge/references/simultaneous.md)

```sh
python3 -m unittest discover -s tests -v
```

测试覆盖数字路由方向、禁止正返程复用、静音切换、旁听限制、空设备/超时的未知状态及文件防覆盖。真实腾讯/钉钉设备选择、声道兼容、网络音频和声学回声需要两端实测，不能用这些单元测试代替。

BlackHole 为外部依赖，本仓库不包含其源码或二进制。路由依据来自 [BlackHole 官方项目](https://github.com/ExistentialAudio/BlackHole) 与其 [多输出设备指南](https://github.com/ExistentialAudio/BlackHole/wiki/Multi-Output-Device)，核对日期 2026-09-07。
