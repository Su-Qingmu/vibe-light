/**
 * Vibe Light hook handler for OpenClaw
 *
 * Writes the current agent state to /tmp/vibe/openclaw-{sessionKey}.json
 * for the openclaw_watcher.py daemon to pick up.
 *
 * State mapping (8 灯效语义):
 *   loading  - 收到用户/系统/内部信息
 *   thinking - agent 推理中
 *   coding   - 写代码/文件
 *   busy     - 调用工具 (Bash / WebSearch / 长任务)
 *   waiting  - 等用户决策/输入 (plan mode / 权限对话框)
 *   success  - 完成 (message:sent / agent:done / session:compact:after)
 *   error    - 失败 (tool:error / error:api)
 *   alarm    - 安全告警 (permission:asked / error:rate_limit)
 *
 * 已确认事件 (OpenClaw HOOK.md frontmatter 声明):
 *   message:received            -> thinking
 *   message:sent                -> success       (改: 原 idle)
 *   command:stop/new/reset      -> idle
 *   agent:bootstrap             -> thinking
 *   session:compact:before      -> busy          (改: 原 thinking — 压缩是耗时任务)
 *   session:compact:after       -> success       (改: 原 idle)
 *   gateway:startup             -> idle
 *   gateway:shutdown            -> (跳过, off 已弃用)
 *
 * 推断事件 (待 OpenClaw 上游文档确认 — 即使不发也不会导致 handler 报错, 仅 STATE_MAP 占位):
 *   tool:called  - 工具调用, coding 或 busy (按 tool_name 区分)
 *   tool:result  - 工具返回, thinking
 *   tool:error   - 工具失败, error
 *   tool:timeout - 工具超时, error
 *   permission:asked    -> alarm
 *   permission:granted  -> thinking
 *   permission:denied   -> idle
 *   error:api       -> error
 *   error:rate_limit -> alarm
 *   error:crash     -> error
 *   error:network   -> error
 *   agent:spawn -> thinking (子任务启动)
 *   agent:done  -> success (子任务/本轮完成)
 *   plan:enter  -> waiting
 *   plan:exit   -> thinking
 *   file:edited -> coding
 */

import * as fs from "node:fs";
import * as path from "node:path";

const STATE_DIR = "/tmp/vibe";

const STATE_MAP: Record<string, string> = {
  // === 已确认事件 (OpenClaw HOOK.md frontmatter) ===
  "message:received": "thinking",
  "message:sent": "success",                 // 改: idle -> success (本轮输出完成)
  "command:stop": "idle",
  "command:new": "idle",
  "command:reset": "idle",
  "agent:bootstrap": "thinking",
  "session:compact:before": "busy",         // 改: thinking -> busy (压缩耗时)
  "session:compact:after": "success",       // 改: idle -> success (压缩完成)
  "gateway:startup": "idle",
  "gateway:shutdown": "__SKIP__",           // off 已弃用, 跳过让 LED 保持上次

  // === 推断事件 (待上游确认 — handler 收到映射, 不收到无副作用) ===
  // ---- 工具调用 ----
  "tool:called": "coding",                  // 工具调用, 若有 tool_name 字段可细化 (Bash/WebSearch -> busy)
  "tool:result": "thinking",                // 工具返回
  "tool:error": "error",                    // 工具失败
  "tool:timeout": "error",                  // 工具超时

  // ---- 权限 ----
  "permission:asked": "alarm",              // 权限对话框 = 告警
  "permission:granted": "thinking",         // 放行, 继续
  "permission:denied": "idle",              // 拒了, 回退

  // ---- 错误 ----
  "error:api": "error",                     // API 调用失败
  "error:rate_limit": "alarm",              // 限流
  "error:crash": "error",                   // 崩溃
  "error:network": "error",                 // 网络断开

  // ---- 子 agent / 任务 ----
  "agent:spawn": "thinking",                // 子 agent 启动
  "agent:done": "success",                  // 子任务完成 (新 — 解决 LED success 未触发)
  "agent:error": "error",                   // 子 agent 失败

  // ---- plan mode ----
  "plan:enter": "waiting",                  // 进入 plan mode = 等用户决策
  "plan:exit": "thinking",                  // 退出 plan mode
  "plan:step": "thinking",                  // 单步推进

  // ---- 文件 ----
  "file:edited": "coding",
  "file:created": "coding",
  "file:deleted": "idle",

  // ---- thinking / 流式 (若上游独立暴露) ----
  "thinking:start": "thinking",
  "thinking:delta": "thinking",
  "thinking:end": "thinking",
  "model:stream:start": "thinking",
  "model:stream:end": "thinking",
};

const handler = async (event: any) => {
  const key = `${event.type}:${event.action}`;
  let state = STATE_MAP[key] ?? STATE_MAP[event.type] ?? "idle";

  // off / __SKIP__: LED 保持上次有意义状态, 不写文件
  if (state === "__SKIP__" || state === "off") return;

  const sessionKey = event.sessionKey ?? "main";
  const safeSession = String(sessionKey).replace(/[^a-zA-Z0-9_-]/g, "_");

  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    const tmp = path.join(STATE_DIR, `.openclaw-${safeSession}.json.tmp`);
    const out = path.join(STATE_DIR, `openclaw-${safeSession}.json`);
    const payload = JSON.stringify({
      client: "oc",
      state,
      event: key,
      ts: Math.floor(Date.now() / 1000),
      session: safeSession,
    });
    fs.writeFileSync(tmp, payload);
    fs.renameSync(tmp, out);
  } catch (err) {
    // 不要因为 hook 失败让 OpenClaw 报错，静默写日志即可
    console.error(`[vibe-light] failed to write state: ${err}`);
  }
};

export default handler;