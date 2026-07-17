# Windows vld/vl 支持 + CLAUDE.md 初始化

**日期**: 2026-07-17
**作者**: Claude (MiniMax-M3)
**分支**: main
**相关 commit**: 待推送

## 背景

vibe-light 项目的 daemon (`agent/vld`) 设计目标是 Pi 5 / Linux，使用：
- `os.fork()` 做进程守护化
- `AF_UNIX` 本地 socket (`/tmp/vl-daemon.sock`)
- `/tmp/vibe/` 作为状态文件目录

这些在 Windows 上全部不可用：
- `os.fork()` 不存在 → `AttributeError: module 'os' has no attribute 'fork'`
- 标准 Windows Python build 不带 `AF_UNIX` 支持 → `AttributeError: module 'socket' has no attribute 'AF_UNIX'`
- `/tmp/vibe/` 在 Windows 上不是真实路径

**目标**: 让 vibe-light 在 Windows 上也能完整跑起来（开发调试用），同时**不改任何 Linux 文件**。

## 变更

### 新增文件

#### `agent/vld_win.py`
Windows 版 daemon，逻辑与 Linux 版 `vld` 一一对应：
- `os.fork()` → `subprocess.Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)`
- `AF_UNIX` (`/tmp/vl-daemon.sock`) → TCP localhost (`127.0.0.1:8889`)
- PID 文件路径 → `~/.cache/vibe-light/vl-daemon-win.pid` (跨平台)
- 日志文件 → `~/.cache/vibe-light/vl-daemon-win.log`

内部子命令 `_daemon` 让 detached 进程直接进入 `serve_local()` 主循环。

#### `agent/vl_win.py`
Windows 版 CLI：
- 调用 `vld_win` daemon 通过 TCP localhost:8889
- daemon 不可达时 fallback 直连 ESP32
- 用法与 `agent/vl` 完全相同

#### `CLAUDE.md`
仓库根目录的 AI 辅助文档，包含：
- 烧固件 / 安装 / 测试 / Agent CLI 的常用命令
- 系统架构图 (AI client → hooks → /tmp/vibe → watcher → TCP → ESP32)
- 8 状态机 (DISCONNECTED / CONNECTED / ACTIVE) + 3 模式 LED 规则
- 7 动画状态表 (算法 + 周期)
- TCP 协议 / 状态文件约定 / Boot 按钮
- 已知坑点 (MicroPython 1.28 socket timeout / webrepl 启动异常 / 长连接要求)

### 未修改

`agent/vld` (Linux daemon) / `agent/vl` (Linux CLI) / `pi5/*` / `clients/*` 全部保持原状 — Linux 部署路径不受影响。

## 验证

### 1. ESP32 TCP 连通性

```bash
$ python pi5/vibelight.py --host 192.168.0.236 ping
PONG
```

### 2. Windows daemon 启动

```bash
$ python agent/vld_win.py start
vld_win started (pid=25956)

$ python agent/vld_win.py status
vld_win 运行中
   pid:      25956
   esp 长连接: ok
```

### 3. 7 个动画状态全过 + ACTIVE 模式验证

```
[1/7] cc.thinking    → OK,  mode=active, anim=True   (HSV 彩虹 0.8s)
[2/7] cc.coding      → OK,  mode=active, anim=True   (青蓝呼吸 4s)
[3/7] cc.busy        → OK,  mode=active, anim=True   (双 comet 2.4s)
[4/7] cc.waiting     → OK,  mode=active, anim=True   (红色呼吸 2s)
[5/7] cc.success     → OK,  mode=active, anim=True   (绿色呼吸 2s)
[6/7] cc.error       → OK,  mode=active, anim=True   (红橙闪 500ms)
[7/7] cc.alarm       → OK,  mode=active, anim=True   (红蓝闪 200ms)
```

每次 `STATUS` 查询都返回 `mode=active` — 证明 daemon 长连接工作正常（之前用 `vl` 直连时永远是 `mode=connected`，因为响应在 mode 转换前生成）。

### 4. 实时多步骤工作流

```
cc.thinking  (analyzing)     → OK
cc.coding    (writing code)  → OK
cc.busy      (multi-tool)    → OK
cc.coding    (continued)     → OK
cc.success   (done)          → OK
```

每条命令毫秒级返回，灯效即时切换。

## 使用

### Windows (开发机)

```bash
# 启动 daemon (一次)
python agent/vld_win.py start

# 实时调用
python agent/vl_win.py cc.thinking
python agent/vl_win.py cc.coding
python agent/vl_win.py cc.success
python agent/vl_win.py status

# 停止
python agent/vld_win.py stop
```

### Linux / Pi 5 (生产)

```bash
python agent/vld start
python agent/vl cc.thinking
python agent/vl status
python agent/vld stop
```

两个 daemon 完全独立，互不影响 — Windows 上跑 `vld_win` 不会破坏 Linux 上跑 `vld`，反之亦然。

## 文件清单

| 类型 | 路径 |
|---|---|
| 新增 | `agent/vld_win.py` |
| 新增 | `agent/vl_win.py` |
| 新增 | `CLAUDE.md` |
| 新增 | `2026-07-17-windows-vld-support.md` (本文件) |

## 已知遗留

- `vibe_daemon.py` / `vibe_watcher.py` 仍写 `/tmp/vibe/` — 计划跑在 Pi 5 (Linux) 上，Windows 上未测试文件监控路径
- AI 客户端 hooks (`clients/claude/vibe-claude-hook.sh` 等) 也写 `/tmp/vibe/` — 同样在 Windows 上不可用，但只在 Windows 跑 daemon + 手动 `vl_win` 调用时不影响
- 后续如果需要让 hooks 在 Windows 也能跑，可加 `VIBE_STATE_DIR` 环境变量兼容 (参考 .cache 路径)
