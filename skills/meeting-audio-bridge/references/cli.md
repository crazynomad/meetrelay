# 原生混音 CLI（供 agent 调用）

使用原生后端且已具备配套仓库时，优先用 CLI 控制混音进程；会议客户端设备选择仍通过原生界面核实。CLI 不代替客户端 API，不会加入会议、替客户端静音或修改其输入输出。

## 找到并构建

在用户指定项目中确认 `native-mixer/build.sh` 与 `native-mixer/Sources/CLI.swift`；没有源码时说明依赖缺失，不在技能安装目录猜测应用路径。构建：

```sh
sh /absolute/project/native-mixer/build.sh
```

CLI 位于 `/absolute/project/native-mixer/build/meeting-bridge`，与 GUI 共用应用包内同一个签名可执行文件和音频引擎。不要只复制 wrapper；旁边的 `Meeting Bridge.app` 也是必需文件。仅安装此 skill 不会安装或构建 CLI。

下文 `<cli>` 换成已核实的绝对路径。标准输出均为 JSON，`run` 输出起止事件，实时指标通过 `status` 获取；系统框架日志可能在 stderr。

## 只读发现与配置

```sh
<cli> help
<cli> devices
<cli> processes
```

`devices` 提供名称、UID、声道数；配置使用 UID，不以列表下标或设备名称绑定。`processes` 提供 Core Audio 对象 ID、PID 和 bundle_id。选真实发声进程，尤其注意独立会议子进程；不能用同一软件的任意进程凑配置。每次启动前刷新 PID，建议同时保存 bundle_id，防止错配。端点目前仍限两款客户端、每端一个音频进程。

在仓库之外保存配置，例如 `/private/tmp/meeting-bridge-config.json`：

```json
{
  "schema_version": 1,
  "a": {"pid": 123, "bundle_id": "实际 A 音频进程标识"},
  "b": {"pid": 456, "bundle_id": "实际 B 音频进程标识"},
  "microphone_uid": "实际实体麦克风 UID",
  "to_a_uid": "实际送 A 虚拟声卡 UID",
  "to_b_uid": "实际送 B 虚拟声卡 UID"
}
```

这是格式示例，PID 和 UID 必须换成当前枚举值。此配置与 Python `bridge.py plan` 的路由计划是两种文件：前者绑定运行时进程/UID，后者描述目标设备关系，不能互换。

```sh
<cli> validate --config /private/tmp/meeting-bridge-config.json
```

校验实际进程与 UID 是否可解析、两端独立、实体输入和虚拟输出声道，并报告麦克风权限状态。它不创建 Tap、不触发授权、不检查客户端扬声器选择。`configuration_valid` 不是声音已连通。空设备列表标为 `needs_host_verification`；若是沙箱看不到 Core Audio，在允许的宿主执行环境重试，不据此判定驱动缺失。

## 启动与控制

先保存会前设备记录，确认两边会议静音、两边扬声器均为实体耳机，移除当前选择中的旧 Bridge 多输出。使用已获授权的本机麦克风与所选会议音频；macOS 原生权限仍需用户允许，CLI 不能绕过它。

```sh
<cli> run --config /private/tmp/meeting-bridge-config.json \
  --session /private/tmp/meeting-bridge-session \
  --clients-ready --request-permissions
```

- `--clients-ready` 是操作方对刚核实的客户端设置的声明，不是自动检测结果；不要为了让命令通过而盲目添加。
- `--request-permissions` 允许请求麦克风访问，并声明已准备处理 Core Audio Tap 可能出现的系统音频授权提示。每次 `run` 显式传入；它本身不授予权限，不应因 CLI 开发请求就自动开始采集。
- `run` 保持前台运行。agent 使用可持续运行的终端会话，保存 session ID；不要把尚未返回当作失败，不重复启动。此版本不安装后台服务、开机启动或定时任务。
- `--session` 为绝对目录。若不存在，父目录需已存在，CLI 创建权限 700 的目录；现有目录须属于当前用户、权限 700 且不是符号链接。同目录只能有一个实例。
- 会话目录保存锁、状态和 UUID 停止请求，不保存音频。原始客户端恢复记录另外保留。

另一个工具调用中执行：

```sh
<cli> status --session /private/tmp/meeting-bridge-session
<cli> stop --session /private/tmp/meeting-bridge-session
```

`status` 给出 `state`、`instance_id`、心跳、锁状态及三路输入峰值/帧数、输出缓冲丢弃、转换错误。`running` 只代表本机引擎启动；`end_to_end` 始终为 `untested`，远端确认另行记录。

`stop` 发送限定当前 UUID 的请求并等待最多 8 秒；它不会对文件里的 PID 发信号。`stop_completed` 表示该实例已释放会话；`stop_pending` 说明尚未退出，继续检查，不宣称停止成功。`interrupted` 表示记录仍写着运行但实例锁已释放；`unresponsive` 表示持锁且心跳超过 5 秒，需排查。Ctrl-C / SIGTERM 也会触发资源清理。SIGKILL 无法执行清理，后续以锁和状态识别中断。

停止混音不会恢复客户端设备。先静音会议，停止后按会前记录恢复；若回到旧切换模式，须同时恢复旧多输出和输入。不要在混音进程运行时手动删除会话目录，也不要在停止另一个实例时复用旧目录记录。

## 退出码与验证

- `0`：查询、校验或控制请求已完成；仍需读取 JSON 的状态字段。
- `1`：参数/配置/会话错误，或启动前置条件不满足。
- `2`：设备/进程未观察到、运行失败或停止仍待完成。

真实音频沿用 [混音配置](simultaneous.md) 和 [会前验证](verification.md)。当前原型的时钟漂移、实际会议捕获、回声和长时间稳定性限制同 GUI；增加 CLI 不会自动解决这些问题。
