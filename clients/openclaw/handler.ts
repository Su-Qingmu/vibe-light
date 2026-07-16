/**
 * Vibe Light hook handler for OpenClaw
 *
 * Writes the current agent state to /tmp/vibe/openclaw-{sessionKey}.json
 * for the openclaw_watcher.py daemon to pick up.
 *
 * State mapping:
 *   message:received, agent:bootstrap, session:compact:before -> thinking
 *   message:sent, command:stop/new/reset, session:compact:after, gateway:startup -> idle
 *   gateway:shutdown -> off
 */

import * as fs from "node:fs";
import * as path from "node:path";

const STATE_DIR = "/tmp/vibe";

const STATE_MAP: Record<string, string> = {
  "message:received": "thinking",
  "message:sent": "idle",
  "command:stop": "idle",
  "command:new": "idle",
  "command:reset": "idle",
  "agent:bootstrap": "thinking",
  "session:compact:before": "thinking",
  "session:compact:after": "idle",
  "gateway:startup": "idle",
  "gateway:shutdown": "off",
};

const handler = async (event: any) => {
  const key = `${event.type}:${event.action}`;
  const state = STATE_MAP[key] ?? STATE_MAP[event.type] ?? "idle";
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