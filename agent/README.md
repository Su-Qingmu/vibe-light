# Vibe Light Agent Tools

给 AI agent (OpenClaw / Claude Code / OpenCode) 用的快速调用工具集。

跟 `pi5/vibelight.py`（TCP 客户端库）的区别：
- **`pi5/vibelight.py`**：Python import 库，给 daemon / watcher 用
- **`agent/vl` / `agent/vld` / `agent/vl-discover`**：shell 命令，给 agent 在回复中快速调用（每次 exec ~50 tokens）

## `vl` — 单条命令 CLI + UDP 发现

```bash
vl thinking                       # 切到 thinking (默认 oc client, 自动 UDP 发现 ESP32)
vl oc.thinking                    # 指定 client
vl busy                           # 切到 busy
vl off                            # 全灭
vl bright 80                      # 设亮度
vl status                         # 查询 ESP32 状态
vl ping                           # 健康检查
vl --host 192.168.0.236 --no-discover ping  # 手动指定 IP,跳过发现
VIBE_HOST=10.0.0.100 vl ping     # 环境变量覆盖
vl help                           # 帮助
```

**UDP 自动发现** 🆕：默认会听 UDP 5000 3 秒，发现 ESP32 后直连。
适合 **ESP32 IP 变化**（路由器 DHCP 重发、换 WiFi 等场景）。
多个 ESP32 在同一 WiFi 时只连第一个。

发现顺序：**环境变量 `VIBE_HOST` → UDP 5000 广播 → 默认 IP `192.168.0.236`**

**实现要点**：vl 单进程内复用 socket，但每次 `vl` exec 都是新进程，
所以**单靠 vl 会让 ESP32 在 CONNECTED ↔ ACTIVE 之间跳**。
解决：`vld` daemon。

## `vl-discover` — 独立 ESP32 扫描器

监听 UDP 5000 3 秒，打印所有看到的 vibe-light 设备：

```bash
vl-discover
# Listening on UDP 5000 for vibe-light broadcasts (timeout=3.0s)...
# [
#   {
#     "host": "192.168.0.236",
#     "port": 8888,
#     "version": "v2",
#     "first_seen": 1784193733.0665832
#   }
# ]

vl-discover --timeout 10    # 听 10 秒
vl-discover --once          # 第一个包就到就退出
```

输出是 JSON，适合被其他脚本处理。

**注意**：daemon 在跑时 ESP32 停止广播，scanner 会扫不到。
需先 `vld stop` 再 `vl-discover`。

## `vld` — 长连接守护进程

`vld` 维护一条到 ESP32 的 TCP 长连接，通过 unix socket 接受本地命令。
所有 `vl` 调用都优先走 daemon，没有 daemon 时 fallback 直连。
**启动 daemon 时 `pre-connect` ESP32** 🆕——所以开 daemon 后 ESP32 停止广播。

```bash
vld start           # 后台启动, pre-connect ESP32
vld stop            # 停止, ESP32 恢复广播
vld status          # 状态 + 长连接是否活着
```

Unix socket: `/tmp/vl-daemon.sock`
PID file: `/tmp/vl-daemon.pid`
日志: `/tmp/vld.log`

### 开机自启

```bash
mkdir -p ~/.config/systemd/user
cp agent/systemd/vld.service.example ~/.config/systemd/user/vld.service

# 修改 ExecStart 路径（如果不是 /home/<user>/bin/vld）
# 然后:
systemctl --user daemon-reload
systemctl --user enable vld.service
systemctl --user start vld.service
systemctl --user status vld.service
```

## 接入 agent 的方式

### OpenClaw agent

在 agent 的回复中：

```bash
# 收到 user 消息,准备干活
vl busy

# 写代码时
vl coding

# 长任务 / spawn 子 agent
vl busy

# 这轮完成
vl success

# 出错
vl error
```

每轮回复调 1-2 次 vl（开头 + 结尾），保持 token 成本可控。

### Claude Code

在 `~/.claude/settings.json` 的 hooks 里：

```json
{
  "hooks": {
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "vl busy"}]}],
    "Stop":              [{"hooks": [{"type": "command", "command": "vl success"}]}],
    "PostToolUseFailure": [{"hooks": [{"type": "command", "command": "vl error"}]}]
  }
}
```

### OpenCode

在 plugin 里直接 import `agent/vl.py` 或者 exec `vl` 命令。

## 协议参考

跟 `esp32/main.py` 一致：

```
PING                       → PONG
STATE <client>.<state>     → OK state=<client>.<state> ANIM
CLIENT <oc|oo|cc>          → OK client=<client>
BRIGHT <0-100>             → OK bright=<n>%
COLOR <r> <g> <b>          → OK override=<r>,<g>,<b>
STATUS                     → {client, state, mode, anim, brightness, override}
HELP                       → 帮助文本
```

`<client>` ∈ {`oc`(OpenClaw), `oo`(OpenCode), `cc`(Claude Code)}
`<state>` 见 README.md 状态表

## 环境变量

- `VIBE_HOST`：ESP32 IP（默认 `192.168.0.236`）
- `VIBE_PORT`：ESP32 端口（默认 `8888`）

```bash
VIBE_HOST=10.0.0.100 vl status
```

## 故障排查

| 现象 | 原因 | 修法 |
|---|---|---|
| status 显示 `mode=connected` 不是 `active` | vl 直连,每次 exec 新 socket | 启动 `vld` daemon |
| `vl ping` 超时 | ESP32 离线 / IP 变了 | 先 `vl-discover` 看能不能扫到，再调 IP |
| ESP32 IP 忘了 / 变了 | DHCP 重发 | 用 `vl-discover` 扫描后接 IP |
| vl-discover 扫不到 | daemon 在跑,ESP32 停广播 | `vld stop`, 再扫 |
| 灯一直不亮 | vl daemon 断了 / ESP32 在 disconnected | `vld restart` 或重启 ESP32 |
| vl 命令 hang | daemon 不响应 | `vld stop && vld start` 看 `/tmp/vld.log` |