# Vibe Light v2 架构

## 目标

树莓派 5 通过 WiFi 控制 ESP32 上的 24 颗 WS2812 环形状态灯，
根据 3 个 AI 编程客户端（OpenClaw / OpenCode / Claude Code）的工作状态切换动画。

## 硬件

- **Pi 5** (10.0.0.35 / 192.168.0.35) — 状态采集 + 转发
- **ESP32** + **WS2812 × 24** 灯环 + Boot 按钮 (GPIO9)
- WiFi 网络：用户路由器（默认 SSID = `SQM`）

## 通信链路

```
AI 客户端 (Claude Code / OpenClaw / OpenCode)
    │ hooks / plugin
    ↓
/tmp/vibe/{client}-{session}.json     ← 状态文件
    │ watchdog 文件监控
    ↓
vibe_watcher.py (Pi5 守护进程)
    │ 聚合 + idle 兜底
    ↓
vl/vl-cli 发现 ESP32: UDP 5000 广播或环境变量
    │
    ├─【启用 daemon】 vld 维护单条 TCP 长连接 → unix socket 接收 vl 命令
    │                (pre-connect 启动时建立, ESP32 同时停止 UDP 广播)
    └─【不走 daemon】vl 临时 UDP 发现 → 直连 TCP 8888

TCP :8888 → ESP32
    │ STATE <client>.<state>
    ↓
ESP32 main.py 渲染 LED
│
└───→ UDP 5000 广播自身 IP:port (无 client 连接时 1 秒 1 次)
```

## ESP32 端（`esp32/`）

### 文件

| 文件 | 作用 |
|---|---|
| `main.py` | 主程序：WiFi / TCP server / **UDP broadcaster** / LED 渲染 / 按钮 |
| `config.py` | WiFi / LED / **UDP 广播端口 + 间隔** / 颜色 / 状态表 |
| `boot.py` | 上电自动 `import main`（WebREPL 容错）|

### 状态机

```
       无 client          收到 STATE/BRIGHT/COLOR
DISCONNECTED  ───────→  CONNECTED  ───────→  ACTIVE
       ↑                       │                  │
       └───────── client 断开 ─┴──── 30s 无更新 ──┘
```

**LED 显示规则**：

| 模式 | D2 (idx 0) | D3-D25 (idx 1-23) |
|---|---|---|
| `disconnected` | 1Hz 闪 client 色 | 全灭 |
| `connected`    | 常亮 client 色    | 全灭 |
| `active`       | 计入动画（全 24 颗参与）| 按 state 渲染 |

### 8 个动画状态

| 状态 | 算法 | 周期 | 备注 |
|---|---|---|---|
| `thinking` | HSV 相位滚动 `hue = i/24*360 - phase*360` | 800ms | 顺时针（idx 增方向）|
| `coding`   | `sin(θ·π)` 映射绿分量，呼吸调制 V | 4s | D14 蓝，两侧渐青 |
| `busy`     | 双 comet：D2→D14 + D25→D14，4 颗拖尾 | 2.4s | 展开 3 帧 + 行进 + 收敛 3 帧 |
| `waiting`  | sin 呼吸（全 24 颗同步）| 2s | 红 |
| `success`  | sin 呼吸（全 24 颗同步）| 2s | 绿 |
| `error`    | 500ms 红/橙闪 + 100ms 切色 + 偶奇位错位 | 500ms | 抖动效果 |
| `alarm`    | 200ms 全闪红/蓝 | 200ms | 无抖动 |
| `off`      | 全灭 | — | — |

亮度全局乘以 `PEAK = 0.5`（峰值），拖尾按 `1 - j/TRAIL` 衰减。

### TCP 协议

```
PING                       → PONG
STATE <client>.<state>     → OK state=<client>.<state> ANIM
CLIENT <oc|oo|cc>          → OK client=<client>
BRIGHT <0-100>             → OK bright=<n>%
COLOR <r> <g> <b>          → OK override=<r>,<g>,<b>
STATUS                     → {"client":"oc","state":"off","mode":"active",...}
HELP                       → 帮助文本
```

**关键 bug 修复**：

- MicroPython 1.28 的 `socket.recv` 超时抛 `OSError` 时字符串不含 `ETIMEDOUT`，需要兼容 `EAGAIN` / `EWOULDBLOCK` / `timed out` 才能正确识别
- `webrepl.start()` 在未 setup 时会抛异常中断 boot，导致 `import main` 不执行 — 必须 try/except

### Boot 按钮（GPIO9）

| 操作 | 行为 |
|---|---|
| 单击 | 循环切换 client：OC → OO → CC → OC |
| 双击 | 切到 off 状态（保留 client）|
| 长按 ≥2s | 软重启 ESP32 |

### UDP 服务发现（port 5000）🆕

ESP32 在 **无 TCP client 连接时** 每秒广播自身 IP:port:

```
vibe-light:v2 ip=192.168.0.236 tcp_port=8888
```

广播时机：

| 状态 | 广播动作 |
|---|---|
| ESP32 启动后 | 默认在广播 |
| 有 client 连接后 | 停广播 |
| 所有 client 断开 | 恢复广播 |

**客户端发现顺序**：

1. 环境变量 `VIBE_HOST` / `VIBE_PORT`
2. 听 UDP 5000 等 3 秒（多个 ESP32 取第一个）
3. fallback 默认 IP `192.168.0.236`

**为什么需要这个**：

ESP32 作为 WiFi client 接家用路由器，IP 由路由器 DHCP 分配，路由器可能随时重发新 IP。
在客户端 hardcode IP 不现实，UDP 广播就解决了这个问题。
仅本 WiFi 有效（UDP 广播不跨路由器）。

## Pi 5 端（`pi5/`）

### 文件

| 文件 | 作用 |
|---|---|
| `vibelight.py` | TCP 客户端库 + CLI（discover / state / bright / status / ping / daemon）|
| `vibe_watcher.py` | 状态文件监控 daemon（/tmp/vibe/*.json）|
| `vibe_daemon.py` | 主守护进程入口（启动 watcher + 发现 ESP32）|
| `install.py` | 一键安装所有 client hooks |
| `install_claude_hooks.py` | Claude Code hooks 专用安装 |
| `test_states.py` | 8 状态回归测试脚本 |

## Agent 端（`agent/`）🆕

跟 `pi5/` 是双层设计：

- `pi5/` 是 daemon / watcher 内部用（Python import 库）
- `agent/` 是 AI agent 快速调（每次 exec ~50 tokens）

### 文件

| 文件 | 作用 |
|---|---|
| `vl`        | 单条命令 CLI + UDP 发现 + daemon unix socket 转发 |
| `vld`       | 长连接守护进程（pre-connect ESP32）|
| `vl-discover` | 独立 UDP 5000 扫描器（debug / 多 ESP32 选用）|

### 接入流程

```
agent 回复开头/结尾
    ↓
vl thinking / vl busy / vl coding
    │
    ├─ vld daemon 在跑 (推荐) → unix socket → daemon 转发 ESP32
    │                            TCP 长连接, ESP32 stop 广播
    │
    └─ vld 不在 → UDP 5000 发现 → 直连 ESP32
                              (ESP32 IP 可变)

agent 回复完成后
    ↓
vl success / vl error / vl off
```

### 安装

```bash
cp agent/{vl,vld,vl-discover} ~/bin/
chmod +x ~/bin/vl ~/bin/vld ~/bin/vl-discover

# 启动 daemon (推荐方式)
vld start

# 手动测试
vl ping
vl thinking
vl off
```

### 自启动

```bash
mkdir -p ~/.config/systemd/user
cp agent/systemd/vld.service.example ~/.config/systemd/user/vld.service

# 修改 ExecStart 路径（如以其它用户名装）
$EDITOR ~/.config/systemd/user/vld.service

systemctl --user daemon-reload
systemctl --user enable vld.service
systemctl --user start vld.service
```
| `test_states.py` | 8 状态回归测试脚本（单长连接保活）|

### 状态文件约定

```
/tmp/vibe/claude-{session_id}.json      → client=cc
/tmp/vibe/opencode-current.json          → client=oo
/tmp/vibe/openclaw-{session_key}.json    → client=oc
```

每文件内容：

```json
{"client": "cc", "state": "thinking", "event": "UserPromptSubmit", "ts": 1700000000, "session": "..."}
```

**聚合策略**：

- 同一 client 多个 session：取最近更新的 state
- 30s 内无任何 session 更新：回退到 idle
- ESP32 不可达时：缓存最近状态，重连后重发

## 客户端 hooks（`clients/`）

| 客户端 | 文件 | 触发 |
|---|---|---|
| **Claude Code** | `claude/vibe-claude-hook.sh` | `UserPromptSubmit`, `PostToolUse`, `Stop` |
| **OpenClaw** | `openclaw/handler.ts` + `HOOK.md` | `agent_run`, `message_received` |
| **OpenCode** | `opencode/vibe-light.ts` | SDK `client.event.listen()` 监听 |

每个 hook 写 JSON 到 `/tmp/vibe/`，由 `vibe_watcher.py` 读取。

## 部署顺序

1. **ESP32 端**（一次性）
   ```bash
   esptool.py ... write_flash 0x1000 micropython.bin
   ampy -p /dev/ttyACM0 put esp32/{main,config,boot}.py /
   ```

2. **Pi5 端**（一次性）
   ```bash
   cd pi5/
   python3 install.py    # 安装所有 hooks
   ```

3. **运行**（持续）
   ```bash
   python3 pi5/vibe_daemon.py
   ```

4. **回归测试**（每次改 LED 代码后）
   ```bash
   cd pi5/
   python3 test_states.py
   ```

## 已知限制

- MicroPython 1.28 socket timeout 字符串识别需手动扩展
- 多客户端并发时需保证 client socket 长连接，避免 client 断开触发 DISCONNECTED
- ESP32 WiFi 断线后 main loop 自动重连，但 TCP 长连接需客户端重连