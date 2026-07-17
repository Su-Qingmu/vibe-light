# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Vibe Light v2

AI 编程客户端状态灯 — Pi 5 通过 WiFi (TCP :8888) 控制 ESP32 + 24 颗 WS2812 环形灯条。
根据 3 个 AI 客户端（OpenClaw / OpenCode / Claude Code）的工作状态切换动画。

## 常用命令

### 烧 ESP32 固件 (一次性)

```bash
# 烧 MicroPython 1.28+ 到 ESP32-C3
esptool.py --chip esp32 --port /dev/ttyUSB0 --baud 460800 \
    write_flash --flash_size=detect 0x1000 esp32-esp32-idf4-20250903-v1.28.0.bin

# 上传 3 个核心文件
ampy -p /dev/ttyACM0 put esp32/main.py   /main.py
ampy -p /dev/ttyACM0 put esp32/config.py /config.py
ampy -p /dev/ttyACM0 put esp32/boot.py   /boot.py
```

### Pi 5 端 (一次性安装 + 持续运行)

```bash
cd pi5/
python3 install.py          # 装 Claude/OpenClaw/OpenCode hooks
vibelight --discover        # 扫描子网找 ESP32, 保存到 ~/.config/vibe-light/esp32.json
python3 vibe_daemon.py      # 启动状态监控 daemon (Ctrl-C 退出)
```

### 手动测试 (不走 daemon)

```bash
# 健康检查
nc 192.168.0.236 8888 <<< "PING"

# 切状态 / 亮度
nc 192.168.0.236 8888 <<< "STATE oc.thinking"
nc 192.168.0.236 8888 <<< "STATE cc.coding"
nc 192.168.0.236 8888 <<< "BRIGHT 80"

# 查询
nc 192.168.0.236 8888 <<< "STATUS"
```

### 回归测试 (改 LED 代码后跑)

```bash
cd pi5/ && python3 test_states.py
# 8 状态各跑 10s, 用单 socket 长连接避免 ESP32 切回 DISCONNECTED
```

### Agent CLI (OpenClaw / Claude Code / OpenCode 调用)

```bash
cp agent/vl agent/vld ~/bin/ && chmod +x ~/bin/vl ~/bin/vld

vld start       # 后台 daemon, 维护到 ESP32 的 TCP 长连接
vld status      # 查看 daemon 状态
vld stop        # 停止 daemon

vl thinking     # 切状态 (默认 oc client)
vl cc.coding    # 指定 client
vl off          # 全灭
vl bright 80    # 亮度
vl status       # 查 ESP32 状态
```

## 架构

```
AI 客户端 (Claude Code / OpenClaw / OpenCode)
    │ hooks / plugin
    ↓ 写 JSON
/tmp/vibe/{client}-{session}.json
    │ watchdog 文件监控
    ↓
vibe_watcher.py (Pi5)
    │ 聚合 + 30s idle 兜底
    ↓
TCP :8888  ──────→  ESP32 (main.py)  ──────→  WS2812 × 24
                                              + Boot 按钮 (GPIO9)
```

### 关键文件

| 路径 | 作用 |
|---|---|
| `esp32/main.py` | ESP32 主程序 (WiFi / TCP server / LED 渲染 / 按钮状态机) |
| `esp32/config.py` | WiFi 凭据 / LED 配置 / 颜色表 / 状态白名单 |
| `esp32/boot.py` | 启动后 `import main` (webrepl 容错 try/except) |
| `pi5/vibelight.py` | TCP 客户端库 + CLI (state/client/color/bright/status/ping) |
| `pi5/vibe_watcher.py` | 状态文件监控 + 聚合 + TCP 推送 |
| `pi5/vibe_daemon.py` | daemon 入口 (discover → start watcher) |
| `pi5/test_states.py` | 8 状态回归测试 (单长连接) |
| `agent/vl` | agent 用 CLI (单条命令, 优先走 vld daemon) |
| `agent/vld` | unix socket daemon (保持 ESP32 TCP 长连接) |
| `clients/claude/vibe-claude-hook.sh` | Claude Code hooks → 写 /tmp/vibe/claude-*.json |
| `clients/openclaw/handler.ts` | OpenClaw hook → /tmp/vibe/openclaw-*.json |
| `clients/opencode/vibe-light.ts` | OpenCode plugin → /tmp/vibe/opencode-current.json |

## 状态机

```
       无 client            收到 STATE/BRIGHT/COLOR
DISCONNECTED  ──────→  CONNECTED  ──────→  ACTIVE
       ↑                      │                  │
       └──── client 断开 ─────┴── 30s 无更新 ────┘
```

| 模式 | D2 (idx 0) | D3-D25 (idx 1-23) |
|---|---|---|
| `disconnected` | 1Hz 闪 client 色 | 全灭 |
| `connected` | 常亮 client 色 | 全灭 |
| `active` | 计入动画 (全 24 颗参与) | 按 state 渲染 |

## 8 动画状态

| 状态 | 算法 | 周期 |
|---|---|---|
| `thinking` | HSV 相位滚动, 顺时针 | 800ms |
| `coding` | sin 渐变 + 呼吸 (D14 蓝, 两侧青) | 4s |
| `busy` | 双 comet D2↔D25→D14, 4 颗拖尾 | 2.4s |
| `waiting` / `success` | sin 呼吸 (全 24 颗同步) | 2s |
| `error` | 红/橙 500ms 闪 + 100ms 切色 + 偶奇位错位 | 500ms |
| `alarm` | 红蓝全闪 | 200ms |
| `off` | 全灭 | — |

亮度全局乘 `PEAK = 0.5` (在 `esp32/config.py`), 拖尾按 `1 - j/TRAIL` 衰减.

颜色前缀: `oc` (OpenClaw 红) / `oo` (OpenCode 蓝) / `cc` (Claude Code 橙).

## TCP 协议 (port 8888, `\n` 结尾)

```
PING                       → PONG
STATE <client>.<state>     → OK state=<client>.<state> ANIM
CLIENT <oc|oo|cc>          → OK client=<client>
BRIGHT <0-100>             → OK bright=<n>%
COLOR <r> <g> <b>          → OK override=<r>,<g>,<b>    # 临时, 下次 STATE 冲掉
STATUS                     → {"client":..,"state":..,"mode":..,..}
HELP                       → 帮助文本
```

## 状态文件约定 (`/tmp/vibe/`)

```
claude-{session_id}.json        → client=cc
opencode-current.json           → client=oo (单文件)
openclaw-{session_key}.json     → client=oc
```

内容: `{"client": "cc", "state": "thinking", "event": "...", "ts": 1700000000, "session": "..."}`
写入用 `tmpfile + mv` 原子替换 (避免 watcher 读到半行 JSON).

## Boot 按钮 (GPIO9)

| 操作 | 行为 |
|---|---|
| 单击 | 循环切 client (OC → OO → CC) |
| 双击 | 切到 off 状态 |
| 长按 ≥1.5s | 软重启 ESP32 (白闪 3 次) |

## 已知坑点

- **MicroPython 1.28 socket timeout 字符串识别**: `socket.recv` 超时抛 `OSError`, 字符串可能是 `"timed out"` / `"EAGAIN"` / `"EWOULDBLOCK"`, 需要兼容匹配 (`esp32/main.py:626`)
- **`webrepl.start()` 在未 setup 时会抛异常**中断 boot, 导致 `import main` 不执行 — 必须 try/except (`esp32/boot.py:6`)
- **多客户端并发必须保持 TCP 长连接**: client 断开会触发 ESP32 切回 DISCONNECTED 模式. 用 `vld` daemon 解决
- **`vibelight --discover` 保存 IP 到** `~/.config/vibe-light/esp32.json` (CONFIG_DIR), 不传 `--host` 时 fallback 用这个
- **`PEAK = 0.5`** 在 `esp32/config.py` 是动画峰值乘数, 改它会影响所有 LED 亮度

## 部署

- ESP32: MicroPython v1.28+
- Pi 5: Python 3.11+, 依赖 `watchdog` (vibe_watcher) / `pyserial` (可选, 手动测试)
- 外部依赖: `jq` (claude hook) / `node` (openclaw hook 编译)
