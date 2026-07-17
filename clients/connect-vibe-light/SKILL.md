---
name: connect-vibe-light
description: 连接状态灯 / connect vibe-light / 启动 vibe-light daemon. Triggers when user says "连接状态灯", "开状态灯", "connect status light", "vibe-light daemon", or similar. Establishes the long-connection daemon to ESP32 + WS2812 LED. Does NOT auto-push any LED state — only sets up the connection.
---

# Connect Vibe Light

## 触发语义

仅在用户**显式说出**以下任一短语时执行:
- "连接状态灯"
- "开状态灯"
- "启动 vibe-light"
- "vibe-light daemon"
- "connect status light"
- "connect vibe-light"

**默认不自动调用**. 即便用户在 Claude Code 中工作, 也不会自动连灯 / 自动推状态. 灯的状态变化必须由用户**主动调用** `vl_win` / `vl` 命令触发, 不会被 Claude 隐式驱动.

## 行为

按顺序执行:

### 1. 检测平台 + 找 daemon 脚本

```bash
# Windows
DAEMON="agent/vld_win.py"

# Linux / Mac / Pi 5
DAEMON="agent/vld"
```

优先在**当前工作目录**找; 找不到就搜 `~/vibe-light` / `~/projects/vibe-light` / `D:/SQM/desktop/gowork/vibe-light` (本机已知路径).

### 2. 检查 daemon 是否在跑

```bash
python "$DAEMON" status
```

如果返回 "已运行" → 跳到步骤 4.

### 3. 启动 daemon

```bash
python "$DAEMON" start
```

启动后等 0.5s, 确认 socket 出现 (Windows) 或 `/tmp/vl-daemon.sock` 存在 (Linux).

### 4. 验证 ESP32 连通

```bash
python "$DAEMON" ping      # 走 daemon
# 或
python pi5/vibelight.py --host 192.168.0.236 ping   # 直连
```

如果失败, 检查:
- WiFi (`esp32/config.py` 里 SSID = "SQM")
- ESP32 供电
- `vld_win status` 看 ESP 长连接状态

### 5. 显示状态

```bash
python "$DAEMON" status      # daemon PID, ESP 长连接 ok/down
python "agent/vl_win.py" status    # 当前 LED 状态 (client, state, mode, anim, brightness)
```

### 6. 告知用户

向用户报告:
- ✓ / ✗ daemon 状态
- ✓ / ✗ ESP32 连通
- 当前 LED 显示什么
- **不要自动推任何 state** — 等用户明确指令 (e.g. "vl_win cc.coding")

## 不要做的事

- ❌ 不要自动 `vl_win cc.thinking` / `vl_win cc.coding` 等
- ❌ 不要在用户没要求的情况下推任何状态
- ❌ 不要修改 `agent/vl` / `agent/vld` (Linux 版保持原状)
- ❌ 不要把 daemon 路径写死 (跨平台)

## 解除连接 (用户主动要求时)

```bash
python "$DAEMON" stop
```

## 推状态 (用户主动要求时)

```bash
python "agent/vl_win.py" cc.thinking     # Windows
python "agent/vl" cc.thinking            # Linux
```

### 8 灯效语义

| 灯效 | 语义 | 自动触发 (hook 事件) |
|---|---|---|
| `loading` | 收到用户/系统/内部信息 | UserPromptSubmit, SessionStart, SubagentStart, session.created, tui.prompt.append |
| `thinking` | agent 推理中 | message.updated, session.updated, message.part.* |
| `coding` | 写代码/文件 | session.diff, file.edited |
| `busy` | 调用工具 (WebSearch/Bash) | PreToolUse, PostToolUse, tool.execute.before/after, session.status=busy |
| `waiting` | 等用户决策/输入 (plan mode) | Notification (AskUserQuestion) |
| `success` | 完成 | Stop, session.idle, permission.replied |
| `error` | 失败 | StopFailure, session.error |
| `alarm` | 安全告警/权限请求 | PermissionRequest, permission.asked |

**off 已禁用** — LED 永远保持最后有意义状态.

## 失败处理

| 现象 | 处理 |
|---|---|
| daemon 启动超时 | 看 `~/.cache/vibe-light/vl-daemon-win.log` (Win) 或 `/tmp/vl-daemon.log` (Linux) |
| ESP32 ping 超时 | 检查 WiFi, ESP32 电源, `vld_win status` 看 ESP 连接字段 |
| socket 残留 | `rm -f /tmp/vl-daemon.sock` (Linux) 或重启 daemon (Windows) |
| 端口冲突 | `netstat -an \| grep 8889` (Windows) / `lsof -i :8889` (Linux) |
