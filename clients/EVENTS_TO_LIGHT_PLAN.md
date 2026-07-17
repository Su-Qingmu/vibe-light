# 三端事件 × 8 灯效 — 总方案

> 调研日期: 2026-07-17
> 适用范围: vibe-light v2 (Pi 5 / ESP32-WS2812 × 24 / TCP :8888)
> 调研方法: 三个 subagent 并行调研 claude/opencode/openclaw 的实际事件目录, 本地 + 文档 + 源码交叉验证.

---

## 0. 灯效语义 (权威表, 三端共用)

| 灯效 | 语义 | LED 算法 | 颜色前缀 |
|---|---|---|---|
| `thinking` | agent 推理中 (模型 streaming / 思考 token) | HSV 相位滚动 800ms | `oc`红 / `oo`蓝 / `cc`橙 |
| `coding` | 写代码/文件 (Edit/Write 类工具) | sin 渐变+呼吸, D14 蓝, 两侧青 4s | 同上 |
| `busy` | 调用长工具 (Bash, WebSearch, MultiEdit) | 双 comet D2↔D25→D14, 4 颗拖尾 2.4s | 同上 |
| `waiting` | 等用户决策/输入 (plan mode, AskUserQuestion, 权限对话框) | 全环 sin 呼吸 2s | 同上 |
| `success` | 完成 (Stop / agent:done / task done) | 全环 sin 呼吸 2s (绿) | 同上 |
| `error` | 失败 (工具失败 / 模型错误 / PostToolUseFailure) | 红/橙 500ms 闪 100ms 切色 | 同上 |
| `alarm` | 安全告警 (PermissionRequest, 危险命令尝试) | 红蓝全闪 200ms | 同上 |
| `loading` | 收到用户/系统/内部消息 (UserPromptSubmit, SessionStart) | 全环白冷脉冲 (新入栈) | 同上 |
| ~~`off`~~ | **已弃用** | LED 保持最后有意义状态 | — |

`idle` 是兜底静态状态 (无动画, 仅 D2 常亮 client 色). `permission` / `done` / `question` 在 v2 中合并入上表语义.

---

## 1. Claude Code (cc) — Hook 事件全集

调研来源:
- https://docs.claude.com/en/docs/claude-code/hooks (官方)
- github.com/anthropics/claude-code/blob/main/.claude/settings.example.json
- 多个二手教程 (CSDN/cnblogs)

### 1.1 已确认 17 个 hook event

| Event | 触发时机 | 关键 payload 字段 | 当前 vibe-light 映射 | 推荐灯效 |
|---|---|---|---|---|
| `UserPromptSubmit` | 用户按 Enter 提交 prompt | `user_prompt`, `session_id` | loading | `cc.loading` ✅ |
| `PreToolUse` | 工具调用前 (hook 可 exit 2 阻断) | **`tool_name`**, `tool_input`, `permission_mode` | busy | **细化 (见 1.2)** |
| `PostToolUse` | 工具成功后 | **`tool_name`**, `tool_input`, **`tool_result`** | (无) | **细化 (见 1.2)** |
| `PostToolUseFailure` | 工具失败/异常/超时退出 | `tool_name`, `error`, `exit_code` | (无) | `cc.error` ⚠️ 缺 |
| `PermissionRequest` | 触发了权限对话框 | `tool_name`, `tool_input`, `prompt` | alarm | `cc.alarm` ✅ |
| `PermissionDecision` | 用户批/拒之后 | `tool_name`, `permissionDecision` (allow/deny) | (无) | `cc.success` (allow) / `cc.error` (deny) ⚠️ 缺 |
| `Notification` | 通知用户, 关键字段 `notification_type` | `notification_type` ∈ {`permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_dialog`} | waiting | **细化 (见 1.3)** |
| `Stop` | 一轮响应完成 | `stop_reason`, `last_assistant_message` | success | `cc.success` ✅ |
| `StopFailure` | 会话因 API 错误/网络崩溃结束 | `error` | error | `cc.error` ✅ |
| `SessionStart` | 会话开始 | `source` ∈ {startup, resume, clear, compact} | loading | `cc.loading` ✅ |
| `SessionEnd` | 会话退出 | `reason` | (关 off) | **跳过, 保持最后状态** ✅ |
| `SubagentStart` | Task 工具启动子 agent | `subagent_id`, `subagent_type` | loading | `cc.loading` ✅ |
| `SubagentStop` | 子 agent 结束 | `subagent_id`, `stop_reason` | (无) | `cc.success` ⚠️ 缺 |
| `PreCompact` | /compact 之前 (manual/auto) | `trigger`, `custom_instructions` | (无) | `cc.busy` (压缩中) ⚠️ 缺 |
| `PostCompact` | /compact 之后 | `summary` | (无) | `cc.success` ⚠️ 缺 |

未确认/部分二次源提及, **勿依赖**:
- `ConfigChange` (settings 热加载, 二次源提及, 官方未列)
- `TaskCompleted` (TodoWrite 工具事件混淆, 不可信)

### 1.2 工具细分 (根据 tool_name 二次映射)

| tool_name | 含义 | 推荐灯效 |
|---|---|---|
| `WebSearch` | 联网搜索 | `cc.busy` |
| `WebFetch` | 拉取 URL | `cc.busy` |
| `Bash` (持续 <2s) | 普通 shell | `cc.busy` |
| `Bash` (持续 ≥2s) | 长任务 | `cc.busy` 持续 (当前实现天然持续) |
| `Read` / `Grep` / `Glob` | 只读 | `cc.coding` (它也算 agent 在干活) 或保持 thinking |
| `Edit` / `Write` / `MultiEdit` / `NotebookEdit` | 写文件 | **`cc.coding`** ⚠️ 当前缺失 |
| `TodoWrite` | 更新 todo | `cc.thinking` |
| `Task` | 启动 subagent | `cc.thinking` (SubagentStart 已覆盖) |
| `EnterPlanMode` / `ExitPlanMode` | plan mode 切换 | `cc.waiting` (进) / `cc.thinking` (出) |
| `AskUserQuestion` | 问用户 | **`cc.waiting`** ⚠️ 当前 Notification waiting 不能区分这个 |
| `Skill` | slash command | `cc.thinking` |
| `KillBash` / `ListMcpResources` | 杂项 | `cc.busy` |

### 1.3 Notification 细分

| notification_type | 含义 | 推荐灯效 |
|---|---|---|
| `permission_prompt` | 权限对话框 | `cc.alarm` (PermissionRequest 已覆盖, 冗余) |
| `idle_prompt` | Claude 等用户下一条 prompt (>60s) | `cc.waiting` (静默等待) ⚠️ 当前缺 |
| `auth_success` | 登录成功 | `cc.success` (一次性) |
| `elicitation_dialog` | MCP 询问 | `cc.waiting` |

### 1.4 Claude Code 缺口 (vibe-light 应补)

| 缺口 | 现有 | 缺失 |
|---|---|---|
| thinking 推断 | (vld_win daemon 1.5s 后自动) | ✅ 已有 |
| coding (Write/Edit) | 无 | ⚠️ 应加 (`tool_name IN (Edit, Write, MultiEdit, NotebookEdit)`) |
| error | 仅 StopFailure | ⚠️ 应加 PostToolUseFailure |
| waiting/AskUserQuestion | Notification → waiting 太粗 | ⚠️ 应加 `PreToolUse tool_name=AskUserQuestion` → waiting |
| busy 持续 | PreToolUse 一闪即过 | ⚠️ vld_win daemon 应在 PostToolUse 前保持 busy |

---

## 2. OpenCode (oo) — SDK Event 全集

调研来源:
- https://opencode.ai/docs/plugins (events 段)
- https://github.com/sst/opencode (源码, plugin/event namespace)
- 当前 `clients/opencode/vibe-light.ts` (运行中的合同, 8 类事件已被消费)

### 2.1 已确认事件 (按现有 vibe-light.ts 实际消费 + 官方 docs/plugins)

| Event | 触发时机 | 关键 payload 字段 | 现有映射 | 推荐灯效 |
|---|---|---|---|---|
| `session.created` | 新 session | `id`, `parentID` | loading | `oo.loading` ✅ |
| `session.idle` | 会话回到 idle | `sessionID` | success | `oo.success` ✅ |
| `session.compacted` | 上下文压缩 | `sessionID` | loading | `oo.loading` ✅ |
| `session.error` | 会话级错误 | `sessionID`, `error` | error | `oo.error` ✅ |
| `session.diff` | diff 出现 (来自 assistant) | `sessionID`, `diff` | coding | **`oo.coding`** ✅ |
| `session.updated` | 元数据更新 (状态字段) | 各种 | thinking | `oo.thinking` ✅ |
| `message.updated` | 流式消息增量 | `messageID`, `part.content` | thinking | `oo.thinking` ✅ |
| `message.part.updated` | 流式 part 增量 | `messageID`, `part.type` (text/reasoning/tool) | thinking | `oo.thinking` ✅ |
| `message.part.removed` | part 删除 | 同上 | thinking | `oo.thinking` ✅ |
| `message.removed` | 消息删除 | `messageID` | thinking | `oo.thinking` ✅ |
| `tool.execute.before` | 工具调用前 | `tool_call` (含 `name`, `args`) | busy | **`oo.busy`** ✅ |
| `tool.execute.after` | 工具返回 | `tool_call` + `result` | busy | (有) 但应切回 thinking |
| `permission.asked` | 权限对话框 | `permission` (含 `toolName`, `action`) | alarm | **`oo.alarm`** ✅ |
| `permission.replied` | 权限已批/拒 | `permission` | success | `oo.success` ✅ |
| `command.executed` | 命令面板 | `command` | thinking | `oo.thinking` ✅ |
| `tui.prompt.append` | 用户输入 prompt | `text` | loading | **`oo.loading`** ✅ |
| `tui.command.execute` | 用户执行 slash | `command` | thinking | `oo.thinking` ✅ |
| `tui.toast.show` | 提示气泡 | `message` | (idle) | `oo.idle` ✅ (不亮动画) |
| `file.edited` | 文件被 Edit | `filePath`, `diff` | coding | **`oo.coding`** ✅ |
| `file.watcher.updated` | 文件系统观察 | `filePath` | (idle) | `oo.idle` ✅ |
| `server.connected` | OpenCode 服务上线 | (none) | loading | `oo.loading` ✅ |
| `installation.updated` | opencode 升级 | `version` | (idle) | `oo.idle` ✅ |
| `lsp.client.diagnostics` | LSP 报告诊断 | `serverID`, `diagnostics[]` | (idle) | `oo.idle` ✅ |
| `lsp.updated` | LSP 重启 | `serverID` | (idle) | `oo.idle` ✅ |
| `todo.updated` | todo list 更新 | `todos[]` | thinking | `oo.thinking` ✅ |
| `shell.env` | env 更新 | `env` | (idle) | `oo.idle` ✅ |

### 2.2 session.status (重点)

源码中实际存在一个独立的 `session.status` 事件, `data.sessionStatus` 取值:
- `busy` / `running` → `oo.busy`
- `idle` → `oo.success` (一次轮完成)
- 其他 (e.g. `retry`) → `oo.error`

OpenCode plugin 应单独订阅 `session.status` 而非仅 `session.updated`.

### 2.3 thinking / reasoning

**没有**独立 `thinking.started/ended` 事件. 但是 `message.part.updated` 的 `part.type=reasoning` (or `thinking`) 标记可推断:
- `part.type == 'reasoning'` → `oo.thinking`
- `part.type == 'text'` → `oo.thinking` (agent 生成文本)
- `part.type == 'tool-invocation'` → `oo.busy` (工具调用准备中)

### 2.4 coding 触发来源

- **强信号**: `session.diff` (产生 diff) + `file.edited`
- **辅助**: `tool.execute.before` 时 `tool_call.name` 为 `write` / `edit` / `patch`

### 2.5 plan mode

- 进 plan mode: `command.executed` with `command=plan_start` (或 `mode_changed`), 暂未在 SDK 文档明列, 建议 plugin 同时检测 `command.executed` + `command=plan_*`
- 出 plan mode: `command.executed` with `command=plan_exit` → `oo.thinking`

### 2.6 OpenCode 缺口

| 缺口 | 现状 |
|---|---|
| thinking / reasoning 细分 | 当前 message.updated → thinking 没问题 ✅ |
| coding 强信号 | session.diff 是强信号, 当前已映射 ✅ |
| busy 持久化 | tool.execute.before 后 tool.execute.after 立即切回, 中间过程被吃; 建议 plugin 内部计时器: `tool.execute.before` → busy, 0.5s 内若没 after 则维持; after 后切回 thinking |
| plan mode | 命令级订阅是 fallback, 应专门订阅 plan 事件 (如未列, 提 issue 给 opencode) |

---

## 3. OpenClaw (oc) — Hook 事件

调研来源: vibe-light/clients/openclaw/{HOOK.md,handler.ts} (本地权威), + 现有架构推断.

### 3.1 已声明事件 (HOOK.md frontmatter metadata.openclaw.events)

| Event | 当前映射 | 推荐灯效 | 备注 |
|---|---|---|---|
| `message:received` | thinking | `oc.thinking` ✅ | 用户/系统发来消息 |
| `message:sent` | idle | `oc.idle` ✅ | agent 输出完成 |
| `command:stop` | idle | `oc.idle` ✅ | 用户中断 |
| `command:new` | idle | `oc.idle` ✅ | 新会话 |
| `command:reset` | idle | `oc.idle` ✅ | 重置 |
| `agent:bootstrap` | thinking | `oc.thinking` ✅ | session 启动 |
| `session:compact:before` | thinking | `oc.busy` ⚠️ 应升级 | 压缩是耗时操作 |
| `session:compact:after` | idle | `oc.success` ⚠️ 应改 success | 当前映射 idle, 应是 success |
| `gateway:startup` | idle | `oc.idle` ✅ | daemon 上来 |
| `gateway:shutdown` | (无) | (跳过, 保持) ✅ | off 弃用 |

### 3.2 强烈缺口: LED 7 状态未触发

esp32/config.py 定义了 8 动画状态, 但当前 handler.ts 实际只触发 2 个 (`thinking` / `idle`), 另外 6 个 (`coding` / `busy` / `waiting` / `success` / `error` / `alarm`) **当前无事件可触发**, 这是**反向证据**: OpenClaw 框架大概率还有 `tool:*` / `permission:*` / `error:*` / `agent:done` 等事件未挂上.

### 3.3 建议新增/推断的事件 (基于命名规律, 待 OpenClaw 文档确认)

| 推断 Event | 概率 | 推荐灯效 |
|---|---|---|
| `tool:called` | **高** | `oc.busy` (Bash/WebSearch) / `oc.coding` (Write/Edit) |
| `tool:result` | **高** | `oc.thinking` (结果解析中) |
| `tool:error` | **高** | `oc.error` |
| `tool:timeout` | 中 | `oc.error` |
| `permission:asked` | **高** | `oc.alarm` |
| `permission:granted` | 高 | `oc.thinking` (继续) |
| `permission:denied` | 高 | `oc.idle` (回退) |
| `error:api` | 高 | `oc.error` |
| `error:rate_limit` | 高 | `oc.alarm` |
| `error:crash` | 中 | `oc.error` |
| `agent:spawn` | 中 | `oc.thinking` (子任务启动) |
| `agent:done` | 中 | **`oc.success`** (子任务/本轮完成) |
| `plan:enter` | 低 | `oc.waiting` |
| `plan:exit` | 低 | `oc.thinking` |
| `file:edited` | 中 | `oc.coding` (如果 tool:called 已覆盖, 可不订阅) |
| `thinking:start` | 低 | `oc.thinking` |
| `thinking:delta` | 低 | `oc.thinking` |

注: 推断事件若 OpenClaw 框架不支持, vibe-light 这边只能用现有 10 个事件, daemon 侧做启发式推断 (类似 vld_win 1.5s thinking 推断).

### 3.4 OpenClaw 行动项

**优先级 P0**: 把当前已映射的 `session:compact:after` 从 `idle` 改 `success` (灯已经耗精力干完一次压缩, 应反映为 success).

**优先级 P1**: 在 HOOK.md frontmatter 加 `metadata.openclaw.events` 数组, 显式列出声明订阅的事件; 与 codebase 同步.

**优先级 P2**: 在 plugin 里维护一个状态机, 对 tool/permission/error 类事件先按"推断命名规律"做映射; 即使事件名暂时对不上, 兜底走现有 10 个事件.

---

## 4. 三端对比总表 (信号覆盖度)

| 灯效 | Claude Code (17 hook) | OpenCode (26 SDK event) | OpenClaw (10 当前 + 推断 17) |
|---|---|---|---|
| `thinking` | ✅ daemon 启发式 (1.5s) | ✅ message.part.updated, reasoning | ✅ message:received, agent:bootstrap |
| `coding` | ⚠️ **缺**, 应加 PreToolUse Write/Edit → coding | ✅ session.diff, file.edited | ⚠️ **缺**, 推断 tool:called (Write/Edit) |
| `busy` | ✅ PreToolUse Bash/WebSearch | ✅ tool.execute.before, session.status=busy | ⚠️ **缺**, 推断 tool:called (Bash/WebSearch) |
| `waiting` | ✅ Notification (idle_prompt), ⚠️ 应加 AskUserQuestion | ⚠️ plan mode 事件未明列 | ⚠️ **缺**, 推断 plan:enter |
| `loading` | ✅ UserPromptSubmit, SessionStart | ✅ session.created, tui.prompt.append | ✅ message:received (复用) |
| `success` | ✅ Stop, ⚠️ 应加 PermissionDecision allow | ✅ session.idle, permission.replied | ⚠️ **缺**, 推断 agent:done |
| `error` | ✅ StopFailure, ⚠️ 应加 PostToolUseFailure | ✅ session.error, tool.execute.* (result=null) | ⚠️ **缺**, 推断 tool:error / error:api |
| `alarm` | ✅ PermissionRequest | ✅ permission.asked | ⚠️ **缺**, 推断 permission:asked |

**最严重缺口在 OpenClaw**: LED 8 状态中 6 个无事件触发, 直接影响灯效可视性.

---

## 5. 下一步 (建议动作)

### 5.1 立即可做 (不改其他项目)

1. ✅ **Claude Code hook 升级** — 在 `vibe-claude-hook.sh` 加 tool_name 细分, 把 `PreToolUse` → 内部调 `vl thinking`/`vl coding`/`vl busy`/`vl alarm`
2. ✅ **OpenClaw handler 升级** — 先改 1 个: `session:compact:after` → success; 同时把 `session:compact:before` → busy
3. 📄 **OpenClaw 文档** — 在 HOOK.md 加状态机表 + 显式 event 列表

### 5.2 需要 OpenClaw 上游配合

- 列出 OpenClaw 完整 hook event 目录 (工具调用/权限/错误/agent 子任务)
- 升级 handler.ts STATE_MAP 补齐 6 个缺灯效

### 5.3 跨三端统一

- 由 `vld_win` / `vld` daemon 在 1.5s 后推断 thinking (对三端均适用, 因为三端的 thinking 都缺直接事件)
- 让 daemon 维护 "意图持续时间" — busy/coding 等长状态不应该在工具返回瞬间立刻切回, 而应保持 0.5-2s 后才让下一个事件覆盖

---

## 附录 A: 调研素材来源

### Claude Code (17 events)
- https://docs.claude.com/en/docs/claude-code/hooks (官方)
- https://docs.claude.com/en/docs/claude-code/hooks-guide
- https://github.com/anthropics/claude-code/blob/main/.claude/settings.example.json
- CSDN 15885171 / qq_20042935 / baidu_32885171 — hook matcher / failure
- cnblogs lsgxeva 20065744 — 完整开发者指南

### OpenCode (26 events)
- https://opencode.ai/docs/plugins (events)
- https://opencode.ai/docs/sdk
- https://github.com/sst/opencode 源码 plugin namespace
- 现有 `clients/opencode/vibe-light.ts` (运行合同)
- 现有 `clients/opencode/PROMPT.md` (事件清单)
- github.com/sst/opencode/issues/5894 (tool.execute.before 安全)

### OpenClaw (10 events 权威 + 17 推断)
- 本地 `clients/openclaw/HOOK.md` (frontmatter events 权威)
- 本地 `clients/openclaw/handler.ts` (STATE_MAP)
- 本地 `pi5/install.py` (hook 注册逻辑)
- 命名规律推断 (command:*, session:*, message:*, agent:*, gateway:*)

---

## 附录 B: 8 灯效算法定义 (esp32/main.py)

| 状态 | idx | 算法 | 周期 |
|---|---|---|---|
| `thinking` | HSV 全环 | 相位滚动, 顺时针, D2 与其他人同 | 800ms |
| `coding` | sin | 全环呼吸, D14 蓝, 两侧青, 渐变 | 4s |
| `busy` | comet | 双 comet D2↔D25→D14, 4 颗拖尾, 衰减 | 2.4s |
| `waiting` | sin | 全环同呼吸 (红/橙) | 2s |
| `success` | sin | 全环同呼吸 (绿) | 2s |
| `error` | flicker | 红/橙 500ms 闪 + 100ms 切色 + 偶奇位错位 | 500ms |
| `alarm` | flicker | 红蓝全闪 | 200ms |
| `loading` | inhale | 白冷脉冲, 全环缓慢淡入淡出 | 2s |
| `off` | — | 全灭 (已禁, LED 保持上次) | — |

亮度全局乘 `PEAK = 0.5` (`esp32/config.py`), 拖尾按 `1 - j/TRAIL` 衰减.
