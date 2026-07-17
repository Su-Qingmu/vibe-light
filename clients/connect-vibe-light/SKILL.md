---
name: connect-vibe-light
description: 连接状态灯 / connect vibe-light / 启动 vibe-light daemon. Triggers when user says "连接状态灯", "开状态灯", "connect status light", "vibe-light daemon", or similar. Establishes the **persistent** long-connection daemon to ESP32 + WS2812 LED — once connected, it stays connected until user explicitly says "断开状态灯" or until network/TCP breaks (auto-reconnects). Does NOT auto-push any LED state — only sets up the connection.
---

# Connect Vibe Light (持续连接)

## 触发语义

仅在用户**显式说出**以下任一短语时执行:
- "连接状态灯"
- "开状态灯"
- "启动 vibe-light"
- "vibe-light daemon"
- "connect status light"
- "connect vibe-light"

**默认不自动调用**. 即便用户在 Claude Code 中工作, 也不会自动连灯 / 自动推状态. 灯的状态变化必须由用户**主动调用** `vl_win` / `vl` 命令触发.

---

## 🔌 连接策略（核心规则）

**一旦连接，永不断开**——daemon 启动后常驻后台, 维护与 ESP32 的 TCP 长连接:

| 情况 | 行为 |
|---|---|
| 正常运行 | daemon 持续监听本地 socket, 任何 `vl` 命令即时转发到 ESP32 |
| ESP32 短暂掉线 / WiFi 抖动 | daemon 自动重连 (`send_to_esp` 捕获 `BrokenPipeError`/`ConnectionResetError` 后重建 socket) |
| 网络彻底断 / ESP32 关机 | daemon 持续尝试重连, 网络恢复后立即建立连接 |
| 用户**显式说** "断开状态灯" / "停掉灯" / "stop daemon" | daemon 优雅退出, 释放 socket |

**❌ daemon 不会做的事**:
- 不会因闲置超时断开（"没用半小时就断"❌）
- 不会因开关 app 断开（只要 daemon 进程在跑）
- 不会因用户沉默断开（除非用户**明确**说断开）

**❌ agent 不要做的事**:
- 不要在用户没要求时主动 `vld stop` / 停掉 daemon
- 不要建议"用完了记得关"——daemon 设计就是常驻
- 不要在每次任务完成时关掉连接

---

## 行为

按顺序执行:

### 1. 检测平台 + 找 daemon 脚本

```bash
# Windows
DAEMON="agent/vld_win.py"

# Linux / Mac / Pi 5
DAEMON="agent/vld"
```

优先在**当前工作目录**找; 找不到就搜 `~/vibe-light` / `~/projects/vibe-light` / `D:/SQM/desktop/gowork/vibe-light`.

### 2. 检查 daemon 是否在跑

```bash
python "$DAEMON" status
```

如果返回 "已运行" → 跳到步骤 5 (验证 ESP32).

### 3. 启动 daemon（持久运行）

```bash
python "$DAEMON" start
```

启动后等 0.5s, daemon 会:
1. 创建 unix socket (`/tmp/vl-daemon.sock`) 或 Windows TCP `localhost:8889`
2. **Pre-connect to ESP32** — 启动时立即建立 TCP 长连接
3. 进入 `while self.running:` 循环, **永驻直到被显式 stop 或进程被 kill**

后台运行建议（避免 shell 退出时被杀）:
```bash
# Linux / Mac: nohup / systemd
nohup python "$DAEMON" start &
# 或: systemd unit (推荐) — 见 ~/code/vibe_light/agent/systemd/

# Windows: pythonw (无控制台窗口)
pythonw "$DAEMON" start
```

### 4. 处理启动失败 / ESP32 不通

如果 ESP32 ping 失败, daemon 仍然会在后台跑, **持续尝试重连**. 不要因为 ping 失败就退出 skill, 而要:

1. 告知用户 ESP32 当前不通
2. 检查 WiFi / ESP32 电源
3. daemon 在后台等 ESP32 出现后自动恢复
4. 用户不需要重新跑连接命令

### 5. 验证连通

```bash
python "$DAEMON" ping      # 走 daemon (会触发重连如果断了)
# 或
python pi5/vibelight.py --host 192.168.0.236 ping   # 直连 (绕过 daemon)
```

### 6. 显示状态

```bash
python "$DAEMON" status      # daemon PID, ESP 长连接 ok/down, uptime
python "agent/vl_win.py" status    # 当前 LED 状态 (client, state, mode, anim, brightness)
```

### 7. 告知用户

向用户报告:
- ✓ / ✗ daemon 状态 (PID, 已运行 X 秒)
- ✓ / ✗ ESP32 连通 (esp_connected: ok/down)
- 当前 LED 显示什么
- **不会自动断开**, 直到用户说断开
- **不要自动推任何 state** — 等用户明确指令 (e.g. "vl_win cc.coding")

---

## 🔌 断开连接（仅用户明确要求时）

用户必须**显式说出**以下任一短语:
- "断开状态灯"
- "停掉灯"
- "stop vibe-light"
- "vld stop"

**❌ 不要**因为以下原因断开:
- "任务做完了" → daemon 设计就是常驻
- "长时间没用" → 闲置不触发断开
- "切换 AI 客户端" → 换 oc/oo/cc 不需要断 daemon, 只是改 STATE 前缀
- "重连一下" → daemon 自己会重连, 不需要 stop/start

断开操作:
```bash
python "$DAEMON" stop    # 优雅退出
# 或: kill $(cat /tmp/vl-daemon.pid)    # 强杀 (Linux)
```

---

## 推状态（用户主动要求时）

```bash
python "agent/vl_win.py" cc.thinking     # Windows
python "agent/vl" cc.thinking            # Linux
```

### 8 灯效语义（2026-07-17 方案）

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

**❌ off 已禁用** — LED 永远保持最后有意义状态 (灯是物理对象, 关掉=失去信息).
**❌ idle 是静态 fallback** — 仅在没事件时亮起 client 色, 不切动画.

---

## 不要做的事

- ❌ 不要自动 `vl_win cc.thinking` / `vl_win cc.coding` 等
- ❌ 不要在用户没要求的情况下推任何状态
- ❌ 不要修改 `agent/vl` / `agent/vld` (Linux 版保持原状)
- ❌ 不要把 daemon 路径写死 (跨平台)
- ❌ 不要主动 stop daemon (除非用户说断开)
- ❌ 不要在任务完成后建议"可以断开灯了"

---

## 失败处理

| 现象 | 处理 |
|---|---|
| daemon 启动超时 | 看 `~/.cache/vibe-light/vl-daemon-win.log` (Win) 或 `/tmp/vl-daemon.log` (Linux) |
| ESP32 ping 超时 | **daemon 仍在后台跑, 持续重连**, 不需要重新启动 skill. 检查 WiFi, ESP32 电源 |
| socket 残留 | `rm -f /tmp/vl-daemon.sock` (Linux) 或重启 daemon (Windows) |
| 端口冲突 | `netstat -an \| grep 8889` (Windows) / `lsof -i :8889` (Linux) |
| daemon 进程消失 | `vld start` 重新拉起. 检查是否有 OOM / crash log |

---

## systemd 持久化（推荐，Linux/Pi 5）

```bash
# 安装 systemd unit
sudo cp ~/code/vibe_light/agent/systemd/vibe-light.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vibe-light

# 查看状态
systemctl status vibe-light
journalctl -u vibe-light -f
```

daemon 会随系统启动, 用户**永远不需要手动连灯**——开箱即用.