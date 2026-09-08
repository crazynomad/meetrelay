# 串个会 · MeetRelay

**会议软件不同，也能聊到一起。**

客户在腾讯会议，团队同事在钉钉。你希望同事能听到客户的问题、直接补充回答，同时你自己也能参与讨论。

串个会让你的 Mac 连接这两场会议：把腾讯的声音送到钉钉，把钉钉的声音送到腾讯，再把你的麦克风加入两边。大家继续使用各自的会议软件。

## 三个人，怎么开这场会？

假设你是小林，客户是小陈，同事是小周：

| 谁 | 加入哪里 | 能和谁说话 |
|---|---|---|
| 小陈，客户 | 只加入腾讯会议 | 小林、小周 |
| 小林，你 | Mac 同时加入腾讯和钉钉，并运行串个会 | 小陈、小周 |
| 小周，同事 | 只加入钉钉会议 | 小陈、小林 |

```mermaid
flowchart LR
    A["客户 · 腾讯会议"] <-->|声音| M["你的 Mac · 串个会"]
    M <-->|声音| B["同事 · 钉钉会议"]
    U["你 · 本机麦克风"] --> M
    M --> H["你的耳机 · 听两边"]
```

客户提问，同事能听见并回答；你也可以一起说话。串个会给两边分别准备一份声音，避免把某场会议的原声再送回它自己。实际有没有回声，仍要在各自的设备上测试。

目前连接的是**声音**。视频和屏幕共享仍留在各自的会议里。串个会本身不保存录音，也不做转写。

## 现在能用到什么程度？

**2026 年 9 月 8 日，腾讯会议 ↔ 钉钉会议的 CLI 混音测试已由使用者确认成功。** 这是一次具体环境中的成功反馈，各项听感和测试时长没有逐项记录。

目前仍是早期版本：首次启动曾出现音频缓冲堵塞，停止重启后完成了测试，根因尚未完全确认。长时间通话、休眠恢复、耳机拔插等情况还需要继续验证。正式使用前，先与参会者做一轮短测试。

飞书、Zoom、Teams 是后续准备验证的方向，目前没有兼容性结论。现有版本一次连接两场会议。

## 开始前，需要准备什么？

- 一台 **macOS 14.4 或更新版本**的 Mac，同时安装并登录两款会议软件。
- 一副耳机。测试人员也戴耳机，避免扬声器的声音又被麦克风收进去。
- 两个独立的虚拟音频设备。可以把它们理解为电脑里的两根“音频连接线”，各负责把一份混音送进一款会议软件。
- 首次安装时允许 macOS 的麦克风和系统音频访问。当前版本需要本地构建；可让 Codex 协助完成。

这次实测使用的是 **BlackHole 16ch** 和 **IdeaShare 2ch**。IdeaShare 是这台测试机器上已有的改名驱动，其他 Mac 不一定有它；不要照抄名字或把同一个设备改名两次。让 agent 检查现有设备后，再决定需要安装什么。

## 最省事的用法：让 Codex 帮你操作

这个项目包含三部分：

| 部分 | 帮你做什么 |
|---|---|
| Skill | 告诉 Codex 怎样检查设备、配置两场会议、测试和恢复 |
| Mac 应用 | 在窗口里选择声音来源和输出，查看电平 |
| CLI | 用命令启动、查看状态和停止，方便 Codex 操作 |

在这个项目中使用 Codex，可以直接说：

> 使用 meeting-audio-bridge 技能，帮我连接腾讯会议和钉钉会议。先检查设备并保存原设置，通过 CLI 启动混音，然后报告状态。

Codex 会检查当前设备和真正发出声音的会议进程，准备配置，再启动混音。遇到系统授权或无法自动操作的客户端菜单，需要你完成对应步骤。安装技能本身不会自动安装驱动或加入会议。

这个项目的名字是“串个会”，技能调用名是 `meeting-audio-bridge`。

<details>
<summary>首次下载项目，怎样准备技能和应用？</summary>

在仓库根目录运行以下命令，将技能安装到当前项目；如果已经存在，脚本会拒绝覆盖：

```sh
python3 - <<'PY'
from pathlib import Path
import shutil
source = Path('skills/meeting-audio-bridge')
target = Path('.agents/skills/meeting-audio-bridge')
shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
print(f'技能已安装到 {target.resolve()}')
PY
```

使用已安装 Xcode Command Line Tools 的 Mac 构建应用与 CLI：

```sh
sh native-mixer/build.sh
```

构建后，图形界面位于 `native-mixer/build/Meeting Bridge.app`，命令入口位于 `native-mixer/build/meeting-bridge`。详细环境要求见 [构建与使用说明](native-mixer/README.md)。

</details>

## 两边会议怎么设置？

以下对应原生混音模式，也是这次实测使用的方式。**配置时先把两边会议静音。**

| 设置 | 腾讯会议 | 钉钉会议 |
|---|---|---|
| 麦克风 | 串个会“送腾讯”的虚拟设备 | 串个会“送钉钉”的虚拟设备 |
| 扬声器 | 你的实体耳机 | 你的实体耳机 |
| 入会自动开麦 | 关闭 | 关闭，或勾选“入会时静音” |

在这台实测机器上，腾讯麦克风选 **BlackHole 16ch**，钉钉麦克风选 **IdeaShare 2ch**，两边扬声器都选 **External Headphones**。你的真实麦克风在串个会里选择，它的声音会被送进两边。

如果之前使用过旧的切换方案，**两边扬声器都要从 `Bridge A to B` / `Bridge B to A` 改回实体耳机**，否则可能重复传声或形成回声。

## 第一次怎么测试？

除了你，再找两位配合的人：一位只加入腾讯，一位只加入钉钉。如果腾讯里已有愿意配合的参会者，就只需再找一位钉钉同事。

1. 让 Codex 启动混音，确认本机状态正常，再解除两边会议静音。
2. 你先说话，让两边分别确认能听见。
3. 腾讯端说话，你和钉钉端确认能听见；再反过来测试。
4. 你和钉钉同事同时说话，请腾讯端确认两个人都能听清，并检查有没有回声或断音。

屏幕上的电平跳动只说明本机收到声音。**以远端参会者实际听到的结果为准。** 出现回声或卡顿，先静音两边，再检查状态。

## 中途想和同事单独聊，或会议结束了呢？

**和钉钉同事单独讨论：**将 Mac 上的腾讯会议静音，钉钉保持可发言。此时你和同事还能互相听见，同事也能继续听到腾讯里的远端声音。要回到共同讨论，再解除腾讯静音。

**会议结束：**先静音两边，再告诉 Codex：

> 停止串个会混音，并按会前记录恢复我的音频设备。

会议进程退出后，CLI 会检测到来源不可用并停止。停止混音与恢复会议软件的设备选择是两件事，结束时需要一并检查。

## 喜欢自己用命令行？

```sh
# 查看设备和正在使用音频的进程
native-mixer/build/meeting-bridge devices
native-mixer/build/meeting-bridge processes

# 查看命令列表
native-mixer/build/meeting-bridge help
```

CLI 提供 `validate`、`run`、`status`、`stop`，输出 JSON。配置文件绑定实际设备和当前会议进程；完整的启动顺序与示例见 [CLI 操作指南](skills/meeting-audio-bridge/references/cli.md)。

## 进一步了解

- [应用与 CLI：构建、实现和已知限制](native-mixer/README.md)
- [给 agent 的技能入口](skills/meeting-audio-bridge/SKILL.md)
- [混音模式：原生方案及外部混音器](skills/meeting-audio-bridge/references/simultaneous.md)
- [设备安装与回声排查](skills/meeting-audio-bridge/references/macos.md)
- [完整测试与恢复步骤](skills/meeting-audio-bridge/references/verification.md)

项目也保留“只旁听”和“切换发言人”的旧模式，用于不同设备条件；上面的快速上手以本机和同事可以同时发言的原生混音模式为主。

开发验证：

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s native-mixer/Tests -p 'test_cli.py' -v
sh native-mixer/test.sh
```

[BlackHole](https://github.com/ExistentialAudio/BlackHole) 是独立的外部音频驱动，本仓库不包含它的源码或安装包。
