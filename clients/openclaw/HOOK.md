---
name: vibe-light
description: "Push OpenClaw agent state to vibe-light status LED"
metadata:
  { "openclaw": { "emoji": "💡", "events": ["message:received", "message:sent", "command:stop", "command:new", "command:reset", "agent:bootstrap", "session:compact:before", "session:compact:after", "gateway:startup", "gateway:shutdown", "tool:called", "tool:result", "tool:error", "tool:timeout", "permission:asked", "permission:granted", "permission:denied", "error:api", "error:rate_limit", "error:crash", "error:network", "agent:spawn", "agent:done", "agent:error", "plan:enter", "plan:exit", "plan:step", "file:edited", "file:created", "file:deleted", "thinking:start", "thinking:delta", "thinking:end", "model:stream:start", "model:stream:end"], "requires": { "bins": ["jq"] } } }
---

# Vibe Light Hook

Pushes OpenClaw agent state to the vibe-light status LED system.

When OpenClaw fires any subscribed event, this hook writes the current state
to `/tmp/vibe/openclaw-{sessionKey}.json` for the `openclaw_watcher.py` daemon
to pick up and forward to the ESP32 over WiFi.

> 完整调研 + 三端对比见 [`../EVENTS_TO_LIGHT_PLAN.md`](../../EVENTS_TO_LIGHT_PLAN.md).

## States emitted

事件 → 灯效映射 (8 个动画 + 1 静态).

### 已确认事件 (OpenClaw HOOK.md frontmatter 声明)

| Event                      | State      | 备注 |
| -------------------------- | ---------- | --- |
| `message:received`         | `thinking` | 用户/系统消息 |
| `message:sent`             | `success`  | 本轮输出完成 (`改: 原 idle`) |
| `command:stop`             | `idle`     | 中断 |
| `command:new`              | `idle`     | 新会话 |
| `command:reset`            | `idle`     | 重置 |
| `agent:bootstrap`          | `thinking` | session 启动 |
| `session:compact:before`   | `busy`     | 压缩开始 (`改: 原 thinking — 压缩是耗时操作`) |
| `session:compact:after`    | `success`  | 压缩完成 (`改: 原 idle`) |
| `gateway:startup`          | `idle`     | |
| `gateway:shutdown`         | **(跳过)** | off 已弃用, LED 保持上次状态 |

### 推断事件 (待上游确认 — handler 收到映射, 不收到无副作用)

| Event                      | State      | 说明 |
| -------------------------- | ---------- | --- |
| `tool:called`              | `coding`   | 工具调用 (若有 tool_name 字段应区分 Bash/WebSearch -> busy) |
| `tool:result`              | `thinking` | 工具返回 |
| `tool:error`               | `error`    | 工具失败 |
| `tool:timeout`             | `error`    | 工具超时 |
| `permission:asked`         | `alarm`    | 权限对话框 |
| `permission:granted`       | `thinking` | |
| `permission:denied`        | `idle`     | |
| `error:api`                | `error`    | |
| `error:rate_limit`         | `alarm`    | |
| `error:crash`              | `error`    | |
| `error:network`            | `error`    | |
| `agent:spawn`              | `thinking` | 子 agent 启动 |
| `agent:done`               | `success`  | 子任务完成 (解决 success 未触发) |
| `agent:error`              | `error`    | 子 agent 失败 |
| `plan:enter`               | `waiting`  | 进入 plan mode |
| `plan:exit`                | `thinking` | 退出 plan mode |
| `plan:step`                | `thinking` | 单步推进 |
| `file:edited`              | `coding`   | |
| `file:created`             | `coding`   | |
| `file:deleted`             | `idle`     | |
| `thinking:start` / `thinking:delta` / `thinking:end` | `thinking` | 若上游独立暴露 |
| `model:stream:start` / `model:stream:end`           | `thinking` | |

## Install

```bash
# copy hook to workspace
mkdir -p ~/.openclaw/workspace/hooks/vibe-light
cp -r clients/openclaw/* ~/.openclaw/workspace/hooks/vibe-light/

# enable
openclaw hooks enable vibe-light
openclaw hooks check
```