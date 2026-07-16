/**
 * Vibe Light plugin for OpenCode
 *
 * Subscribes to OpenCode SDK events and pushes agent state
 * to the vibe-light status LED via TCP.
 *
 * Event types per https://opencode.ai/docs/plugins#events
 *
 * Install (after opencode is installed):
 *   mkdir -p ~/.config/opencode/plugins
 *   cp clients/opencode/vibe-light.ts ~/.config/opencode/plugins/vibe-light.ts
 */

import type { Plugin } from "@opencode-ai/plugin";
import * as net from "node:net";
import * as fs from "node:fs";
import * as path from "node:path";

const TCP_PORT = 8888;
const CONFIG_PATH = path.join(
  process.env.HOME || "~",
  ".config",
  "vibe-light",
  "esp32.json",
);

const STATE_FILE = "/tmp/vibe/opencode-current.json";

// OpenCode SDK event types → vibe-light state
// Reference: https://opencode.ai/docs/plugins#events
const STATE_MAP: Record<string, string> = {
  // ---- Session ----
  "session.idle":        "idle",
  "session.created":     "idle",
  "session.compacted":   "idle",
  "session.deleted":     "off",
  "session.error":       "error",
  "session.diff":        "coding",
  "session.updated":     "thinking",
  // session.status: handle in code (data has status field)

  // ---- Tool ----
  "tool.execute.before": "coding",
  "tool.execute.after":  "coding",

  // ---- Message ----
  "message.updated":       "thinking",
  "message.part.updated":  "thinking",
  "message.part.removed":  "thinking",
  "message.removed":       "thinking",

  // ---- Permission ----
  "permission.asked":   "permission",
  "permission.replied": "idle",

  // ---- Command ----
  "command.executed": "thinking",

  // ---- TUI ----
  "tui.prompt.append":   "thinking",
  "tui.command.execute": "thinking",
  "tui.toast.show":      "idle",

  // ---- File ----
  "file.edited":           "coding",
  "file.watcher.updated":  "idle",

  // ---- Misc ----
  "server.connected":      "idle",
  "installation.updated":  "idle",
  "lsp.client.diagnostics":"idle",
  "lsp.updated":           "idle",
  "todo.updated":          "thinking",
  "shell.env":             "idle",
};

function getHost(): string {
  try {
    const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
    return cfg.host || "127.0.0.1";
  } catch {
    return "127.0.0.1";
  }
}

function sendCommand(host: string, cmd: string): void {
  try {
    const sock = net.createConnection({ host, port: TCP_PORT, timeout: 1000 });
    sock.on("connect", () => {
      sock.write(cmd + "\n");
      sock.end();
    });
    sock.on("error", () => {});
    sock.on("timeout", () => sock.destroy());
  } catch {
    // silent — vibe-light offline shouldn't break opencode
  }
}

function writeState(state: string, event: string): void {
  try {
    fs.mkdirSync("/tmp/vibe", { recursive: true });
    const tmp = STATE_FILE + ".tmp";
    fs.writeFileSync(
      tmp,
      JSON.stringify({
        client: "oo",
        state,
        event,
        ts: Math.floor(Date.now() / 1000),
      }),
    );
    fs.renameSync(tmp, STATE_FILE);
  } catch {
    // silent
  }
}

function mapEvent(event: any): string {
  const type: string = event?.type ?? "unknown";

  // session.status 事件：data.sessionStatus 可能是 idle / busy
  if (type === "session.status") {
    const status = event?.data?.sessionStatus ?? event?.data?.status;
    if (status === "busy" || status === "running") return "busy";
    if (status === "idle") return "idle";
    return "idle";
  }

  return STATE_MAP[type] ?? "idle";
}

export const VibeLight: Plugin = async ({ project, client, $, directory, worktree }) => {
  const host = getHost();

  return {
    // 监听所有事件 - OpenCode 会根据 key 分发
    event: async ({ event }: { event: any }) => {
      const state = mapEvent(event);
      const cmd = `STATE oo.${state}`;
      writeState(state, event?.type ?? "unknown");
      sendCommand(host, cmd);
    },
  };
};