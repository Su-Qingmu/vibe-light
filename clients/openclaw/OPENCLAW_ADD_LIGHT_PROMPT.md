# 为 OpenClaw handler.ts 补齐 8 灯效 — 提示词

> 这个文件是**给执行方** (你 / Claude Code / OpenClaw / 任何 LLM) 的提示词, 按这里说的步骤改 `clients/openclaw/handler.ts` 并把灯效补齐 8 个.
>
> 上下文完整版见 `clients/EVENTS_TO_LIGHT_PLAN.md`.

---

## 给执行方的提示词 (可直接复制粘贴)

```
你的工作环境里有一个 vibe-light 项目 — 通过 WiFi (192.168.0.236:8888 TCP) 控制 ESP32 + 24 颗 WS2812 LED 状态灯, 已经接入了 3 个 AI 客户端 (OpenClaw / OpenCode / Claude Code).

任务: 给 OpenClaw 的 hook handler (`clients/openclaw/handler.ts`) 补齐 8 个灯效, 当前缺 6 个 (`coding` / `busy` / `waiting` / `success` / `error` / `alarm`).

# LED 灯效语义 (权威, 8 个动画 + 1 静态 idle)

| 灯效 | 语义 | 触发 |
|---|---|---|
| `loading` | 收到用户/系统/内部消息 | 已有 |
| `thinking` | agent 推理中 | 已有 |
| `coding` | 写代码/文件 | **缺** 需补 |
| `busy` | 调用长工具 (Bash / WebSearch) | **缺** 需补 |
| `waiting` | 等用户决策/输入 | **缺** 需补 |
| `success` | 完成 (返回 idle / agent:done) | **缺** 需补 |
| `error` | 失败 (工具 / API) | **缺** 需补 |
| `alarm` | 安全告警 (权限请求 / 危险命令) | **缺** 需补 |
| ~~`off`~~ | LED 保持最后状态, 不发 | |

# 当前 handler.ts (`clients/openclaw/handler.ts`) 现状

STATE_MAP 只有 10 条, 只覆盖了 `thinking` (2 处) + `idle` (5 处).
```
STATE_MAP:
  message:received            -> thinking   ✅
  message:sent                -> idle       ✅
  command:stop/new/reset      -> idle       ✅
  agent:bootstrap             -> thinking   ✅
  session:compact:before      -> thinking   ⚠️ 应升级 busy
  session:compact:after       -> idle       ⚠️ 应升级 success
  gateway:startup             -> idle       ✅
  gateway:shutdown            -> (跳过)     ✅
```

# 你需要做的事

## Step 1: 改 handler.ts 的 STATE_MAP

按下面建议扩展 (含推断事件名, 如果 OpenClaw 上游没有该事件, handler 应该 silent 跳过而不是报错):

```typescript
const STATE_MAP: Record<string, string> = {
  // === 已确认存在 (OpenClaw HOOK.md frontmatter 声明) ===
  "message:received": "thinking",          // 用户/系统消息
  "message:sent": "success",               // // 改: 之前 idle, 应是 success
  "command:stop": "idle",
  "command:new": "idle",
  "command:reset": "idle",
  "agent:bootstrap": "thinking",
  "session:compact:before": "busy",        // // 改: 之前 thinking, 应是 busy (压缩耗时)
  "session:compact:after": "success",      // // 改: 之前 idle, 应是 success (压缩完成)
  "gateway:startup": "idle",
  "gateway:shutdown": "__SKIP__",          // 复用 off 弃用逻辑

  // === 推断事件 (待 OpenClaw 上游文档确认 — handler 收到时正常映射, 没收到无副作用) ===
  // 工具调用
  "tool:called": "coding",                 // Write/Edit/Read 等
  // 如有 tool_name 区分, 大工具 (Bash/WebSearch) → busy, 小工具 (Read/Edit/Write) → coding
  "tool:result": "thinking",               // 工具返回, agent 还在解析
  "tool:error": "error",                   // 工具失败
  "tool:timeout": "error",                 // 工具超时

  // 权限
  "permission:asked": "alarm",             // 权限对话框
  "permission:granted": "thinking",        // 放行, 继续
  "permission:denied": "idle",             // 拒了, 回退

  // 错误
  "error:api": "error",                    // API 失败
  "error:rate_limit": "alarm",             // 限流
  "error:crash": "error",                  // 崩溃
  "error:network": "error",                // 网络断

  // 子 agent / 任务
  "agent:spawn": "thinking",               // 子任务启动
  "agent:done": "success",                 // 子任务/本轮完成
  "agent:error": "error",                  // 子任务失败

  // plan mode
  "plan:enter": "waiting",                 // 进入 plan mode
  "plan:exit": "thinking",                 // 退出 plan mode
  "plan:step": "thinking",                 // plan 单步推进

  // 文件
  "file:edited": "coding",                  // 改文件
  "file:created": "coding",
  "file:deleted": "idle",

  // thinking (如果上游独立暴露)
  "thinking:start": "thinking",
  "thinking:delta": "thinking",
  "thinking:end": "thinking",

  // 模型流式
  "model:stream:start": "thinking",
  "model:stream:end": "thinking",
};

// 兜底: 老的 mismatch 查找
function mapState(event: any): string {
  const key = `${event.type}:${event.action}`;
  if (key in STATE_MAP) return STATE_MAP[key];
  if (event.type in STATE_MAP) return STATE_MAP[event.type];
  return "idle";
}
```

## Step 2: 路径处理 — `off` 已经不用了, 别再加

handler 当前特殊处理:
```typescript
if (cmd.startsWith("STATE ") && cmd.endsWith(".off")) {
    conn.sendall(b"SKIP off disabled\n")
    return
}
```
但 OpenClaw 事件流不发 `off`, 所以**这段逻辑在 handler 里可以删除** (因为 OpenClaw 自己映射 STATE_MAP 时已经跳过 off). 除非 daemon 端有其他客户端共用.

## Step 3: 兼容性 — 新事件无副作用

**关键**: 你新增的 `tool:called` / `permission:asked` / `error:api` 等都是**推断名**. 如果 OpenClaw 框架没发这些事件, handler 不会触发, 不会报错. 已有的 10 个事件继续按当前行为工作.

所以扩展 STATE_MAP 是**纯增量的**, 不破坏现状.

## Step 4: 把"重复发去抖"加上 (重要)

LED 动画有最短渲染时间 (ESP32 收到 STATE 命令后要 ~2s 才完整播一个动画), 太快的切灯会让灯一直在变. 建议加 1s 去抖:

```typescript
const RECENT: Record<string, number> = {};  // state -> last send ts (ms)
const DEBOUNCE_MS = 1000;

function shouldEmit(state: string): boolean {
  const now = Date.now();
  if (state === "__SKIP__" || state === "off") return false;
  const last = RECENT[state] ?? 0;
  if (now - last < DEBOUNCE_MS) return false;
  RECENT[state] = now;
  return true;
}
```

在 handler 主流程里:
```typescript
const state = mapState(event);
if (state === "__SKIP__") return;
if (!shouldEmit(state)) return;
// ... 写文件 / sendCommand
```

## Step 5: 测一遍

不依赖 OpenClaw 上游, 直接用 daemon 模拟各种事件:

```bash
# 启动 daemon
vld_win start

# 逐个触发 (假设你已经知道 daemon 接受 raw STATE 命令, 类似客户端)
# 实际上 OpenClaw handler.ts 走文件 /tmp/vibe/openclaw-main.json 由 Pi 5 watcher 拉,
# 所以这一步改成: 直接改 handler.ts 的事件名映射是否生效,
# 方法是用 node 写一个 events 数组 mock 出来喂给 handler:

node -e '
const handler = require("./clients/openclaw/handler.ts").default;
const events = [
  {type:"tool",action:"called",sessionKey:"test"},
  {type:"permission",action:"asked",sessionKey:"test"},
  {type:"error",action:"api",sessionKey:"test"},
  {type:"agent",action:"done",sessionKey:"test"},
  // ...
];
(async () => {
  for (const e of events) {
    await handler(e);
    await new Promise(r => setTimeout(r, 1500));  // 每事件后等下, 看灯
  }
})();
'

# 或直接测试 daemon 收到的 raw STATE 命令:
python agent/vl_win.py oc.coding    # Windows
python agent/vl oc.coding           # Linux

# 验证 8 个灯效都能触发到 ESP32:
python agent/vl_win.py oc.thinking
python agent/vl_win.py oc.coding
python agent/vl_win.py oc.busy
python agent/vl_win.py oc.waiting
python agent/vl_win.py oc.success
python agent/vl_win.py oc.error
python agent/vl_win.py oc.alarm
python agent/vl_win.py oc.loading
# 每个跑 2-3s, 看 LED 8 种动画都触发
```

## Step 6: 更新 HOOK.md

`clients/openclaw/HOOK.md` 里的状态映射表也要同步. 同时在 `metadata.openclaw.events` 数组里添加新声明的事件 (即使上游没有, 也声明以便于发现):

```yaml
metadata:
  openclaw:
    events:
      # 已有
      - message:received
      - message:sent
      - command:stop
      - command:new
      - command:reset
      - agent:bootstrap
      - session:compact:before
      - session:compact:after
      - gateway:startup
      - gateway:shutdown
      # 新增声明 (待上游确认是否生效)
      - tool:called
      - tool:result
      - tool:error
      - permission:asked
      - permission:granted
      - permission:denied
      - error:api
      - error:rate_limit
      - agent:spawn
      - agent:done
```

# 验证清单

完成所有步骤后, 跑这 6 个验证:

- [ ] `vibe-claude-hook.sh` / `vibe-light.ts` / `handler.ts` 三端的 8 个灯效都能用手工命令触发 (`vl_win oc.coding` 等)
- [ ] OpenClaw handler.ts 在收到 `message:received` 后, 灯是 `oc.thinking` (彩虹滚动)
- [ ] OpenClaw handler.ts 在收到 `message:sent` 后, 灯是 **`oc.success`** (不再是 idle)
- [ ] OpenClaw handler.ts 在收到 `session:compact:before` 后, 灯是 **`oc.busy`** (不再是 thinking)
- [ ] OpenClaw handler.ts 在收到 `session:compact:after` 后, 灯是 **`oc.success`** (不再是 idle)
- [ ] 推断事件 (tool:called / permission:asked / error:api / agent:done) 即使框架不发也不会让 handler 报错 — 静默跳过

# 报告

完成后写一个 `clients/openclaw/CHANGELOG.md` 段, 记:
- 改了哪些文件
- STATE_MAP 哪些 entry 改了 state 值 (从 X → Y)
- 哪些推断事件加了映射
- 是否加了 1s 去抖
- 验证清单 6 项是否全过

---

## 给 OpenClaw 上游 (可选, 单开 issue/PR)

如果你发现 OpenClaw 框架确实发了上面推断事件名 (例如 console 看到 `tool:called` 实际打到 handler), 请在 OpenClaw 仓库提 issue / PR 索取正式文档, 把推断事件名升格为正式 contract. 在 HOOK.md 顶部加一行:

```
> NOTE: 以下 events 是 OpenClaw 框架当前发出的事件全集 (HOOK.md + 上游 issue #XXX 文档化).
> 标 [推断] 的事件名是 vibe-light 启发式命名, 待上游确认.
```
```

---

## 我来直接改 handler.ts 的版本 (P0 部分)

为了让这次任务**动起来**, 我先帮你做最关键的 P0 改动:
- `message:sent` 从 `idle` 改成 `success` (一轮对话结束 = 完成)
- `session:compact:before` 从 `thinking` 改成 `busy` (压缩 = 长任务)
- `session:compact:after` 从 `idle` 改成 `success` (压缩完成)
- 加新推断事件 (tool:called/result/error, permission:asked/granted/denied, error:*, agent:spawn/done) 的 STATE_MAP 条目
- 加 1s 去抖

详见 **任务 #34** → 改 `handler.ts` 然后 `git push`.
