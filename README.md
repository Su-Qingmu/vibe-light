# Vibe Light v2

AI 编程客户端状态灯 — 通过 WiFi 控制 24 颗 WS2812 LED 环形灯条。

支持 9 个语义状态、3 个 AI 客户端前缀（OpenClaw / OpenCode / Claude Code），
每个状态在环形 24 颗上跑独立动画。

## ✨ 特性

- **9 个动画状态**：thinking / coding / busy / waiting / success / error / alarm / loading
- **3 个客户端颜色映射**：oc / oo / cc 各有自己的 base 色
- **环形 24 颗**全参与动画（D2 - D25），无静默 LED
- **TCP 长连接保活**：自动识别 timeout 异常（MicroPython 1.28 兼容）
- **WiFi 自动重连**：掉线后 10s 内自动恢复
- **UDP 服务发现** 🆕：ESP32 每秒广播自身 IP:port，ESP32 IP 变了 client 自动发现
- **Boot 按钮**：单击循环 client / 长按 reboot

## 🏗 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Pi 5 (192.168.0.35 / 10.0.0.35)                            │
│  ├─ vibe_watcher.py   监听 /tmp/vibe/*.json                 │
│  ├─ claude hooks      → claude-{session}.json               │
│  ├─ openclaw hook     → openclaw-{session}.json             │
│  └─ opencode plugin   → opencode-current.json               │
│                          ↓ TCP :8888                        │
└──────────────────────────┬──────────────────────────────────┘
                           │ WiFi (SQM)
                           ↓
┌─────────────────────────────────────────────────────────────┐
│  ESP32 + WS2812 × 24 + Boot button                          │
│  ├─ WiFi Station                                          │
│  ├─ TCP server :8888                                       │
│  ├─ Boot button: 单击切 client / 双击关灯 / 长按 reboot    │
│  └─ LED: 环形 24 颗按 state 动画                            │
└─────────────────────────────────────────────────────────────┘
```

## 📂 目录

| 路径 | 作用 |
|---|---|
| `esp32/` | ESP32 MicroPython 固件（main.py / config.py / boot.py）|
| `pi5/` | Pi5 端 daemon + TCP 客户端库 + 测试脚本 |
| `agent/` | 给 AI agent 用的快速调用 CLI（vl / vld daemon）|
| `clients/` | 3 个 AI 客户端的 hooks（Claude / OpenClaw / OpenCode）|
| `docs/` | 架构设计文档 |
| `firmware_backup/` | 历史版本 Python + 原始 Arduino 固件 |

## 🚀 快速上手

### 1. ESP32 烧固件

```bash
# MicroPython 1.28+ 烧入（一次性）
esptool.py --chip esp32 --port /dev/ttyUSB0 \
    --baud 460800 write_flash --flash_size=detect \
    0x1000 esp32-esp32-idf4-20250903-v1.28.0.bin

# 上传 3 个核心文件
ampy -p /dev/ttyACM0 put esp32/main.py   /main.py
ampy -p /dev/ttyACM0 put esp32/config.py /config.py
ampy -p /dev/ttyACM0 put esp32/boot.py   /boot.py

# 软重启
mpremote a0 exec --no-soft-reset  # 或按 ESP32 RST 按钮
```

### 2. Pi5 端跑 daemon

```bash
cd pi5/
python3 install.py    # 一次性安装所有 client hooks
python3 vibe_daemon.py # 启动状态监控守护进程
```

### 3. 手动测试（不走 daemon）

```bash
# 健康检查
nc 192.168.0.236 8888 <<< "PING"

# 切换状态
nc 192.168.0.236 8888 <<< "STATE oc.thinking"
nc 192.168.0.236 8888 <<< "STATE cc.coding"
nc 192.168.0.236 8888 <<< "BRIGHT 80"

# 查询状态
nc 192.168.0.236 8888 <<< "STATUS"
```

### 4. 回归测试

```bash
cd pi5/
python3 test_states.py
# 8 状态各跑 10s，长连接保活（用单 socket 避免 DISCONNECTED 干扰）
```

### 5. Agent 工具（OpenClaw / Claude Code / OpenCode）

```bash
# 安装到 PATH
cp agent/vl agent/vld agent/vl-discover ~/bin/
chmod +x ~/bin/vl ~/bin/vld ~/bin/vl-discover

# 启动 daemon（保持 ESP32 TCP 长连接）
vld start

# 在 agent 回复中调用
vl busy       # 收到任务,开始干活
vl success    # 这轮完成
vl error      # 出错

# 扫描同 WiFi 下所有 ESP32
vl-discover
```

`vl` 默认通过 UDP 5000 自动发现 ESP32，IP 变了也能找到。
启动 daemon 后 ESP32 停广播（避免噪音），关 daemon 后 ESP32 恢复广播。

详见 `agent/README.md`。

### 6. ESP32 IP 自动发现 🆕

ESP32 IP 会因为路由器 DHCP 重发而变化，不需要手动 hardcode IP：

```bash
# 扫描所有可访问的 ESP32
vl-discover
# [
#   {
#     "host": "192.168.0.236",
#     "port": 8888,
#     "version": "v2"
#   }
# ]

# vl 命令自动用 UDP 发现的 IP
vl ping      # → PONG
vl thinking  # 切状态

# 手动指定（debug 用）
vl --host 192.168.0.236 --no-discover ping
```

**UDP 包格式**（ESP32 发出的）：

```
vibe-light:v2 ip=192.168.0.236 tcp_port=8888
```

**广播触发时机**：
- ESP32 启动后默认在广播（无 client 时）
- 有 TCP client 连接后立即停止广播
- 所有 client 断开后恢复广播

**vld daemon** 启动时 `pre-connect` ESP32，所以起 daemon 后 ESP32 立刻停广播。

## 🎨 动画状态表

| 状态 | 描述 | LED 效果 | 周期 |
|---|---|---|---|
| `thinking` | 高速彩虹旋转 | HSV 相位滚动，顺时针 | 0.8s |
| `coding`   | 青→蓝液态渐变呼吸 | 环形对称，D14 附近最蓝 | 4s |
| `busy`     | 黄色双向扫描 | 双 comet 从 D2+D25 → D14，4 颗拖尾 | 2.4s |
| `waiting`  | 红色呼吸 | 全灯同步 sin 呼吸 | 2s |
| `success`  | 绿色呼吸 | 全灯同步 sin 呼吸 | 2s |
| `error`    | 红↔橙快闪 + 抖动 | 偶/奇位错位 + 100ms 切色 | 500ms |
| `alarm`    | 红蓝全闪 | 200ms 全闪 | 200ms |
| `loading`  | 绿色顺时针拖尾 | head=0.50/tail=0.10，先扩到 12 颗再持续旋转 | 140ms/帧 |

**client 颜色前缀**：

| 前缀 | 客户端 | 颜色 |
|---|---|---|
| `oc` | OpenClaw | 红 |
| `oo` | OpenCode | 蓝 |
| `cc` | Claude Code | 橙 |

## 📡 TCP 协议（port 8888）

每条命令以 `\n` 结尾，回复也是。

```
PING                       → PONG
STATE <client>.<state>     → OK state=<client>.<state> ANIM
CLIENT <oc|oo|cc>          → OK client=<client>
BRIGHT <0-100>             → OK bright=<n>%
COLOR <r> <g> <b>          → OK override=<r>,<g>,<b>
STATUS                     → {"client":"oc","state":"idle",...}
HELP                       → 帮助文本
```

> **注意**：动画状态通过 `STATE <client>.<state>` 触发；`COLOR` 是临时覆盖，会被下一次 STATE 冲掉。

### 模式

| Mode | 触发 | 灯效 |
|---|---|---|
| `disconnected` | 无 client 连接 | D2 1Hz 闪 client 色 |
| `connected`    | 有 client 但未发命令 | D2 常亮 client 色 |
| `active`       | 已收到第一条 STATE | 全 24 颗按 state 动画 |

## 🔧 Boot 按钮（GPIO9）

| 操作 | 行为 |
|---|---|
| 单击 | 循环切换 client（OC → OO → CC → OC）|
| 长按 ≥2s | 软重启 ESP32 |

## 📦 部署环境

- **ESP32**：MicroPython v1.28+
- **Pi 5**：Python 3.11+
- **依赖**：`pyserial`（仅手动测试脚本）、`watchdog`（vibe_watcher）

## 📜 License

MIT