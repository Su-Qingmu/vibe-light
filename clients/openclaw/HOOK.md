---
name: vibe-light
description: "Push OpenClaw agent state to vibe-light status LED"
metadata:
  { "openclaw": { "emoji": "💡", "events": ["message:received", "message:sent", "command:stop", "command:new", "command:reset", "agent:bootstrap", "session:compact:before", "session:compact:after", "gateway:startup", "gateway:shutdown"], "requires": { "bins": ["jq"] } } }
---

# Vibe Light Hook

Pushes OpenClaw agent state to the vibe-light status LED system.

When OpenClaw fires any subscribed event, this hook writes the current state
to `/tmp/vibe/openclaw-{sessionKey}.json` for the `openclaw_watcher.py` daemon
to pick up and forward to the ESP32 over WiFi.

## States emitted

| Event                | State      |
| -------------------- | ---------- |
| `message:received`   | `thinking` |
| `message:sent`       | `idle`     |
| `command:stop`       | `idle`     |
| `command:new`        | `idle`     |
| `command:reset`      | `idle`     |
| `agent:bootstrap`    | `thinking` |
| `session:compact:before` | `thinking` |
| `session:compact:after`  | `idle`     |
| `gateway:startup`    | `idle`     |
| `gateway:shutdown`   | `off`      |

## Install

```bash
# copy hook to workspace
mkdir -p ~/.openclaw/workspace/hooks/vibe-light
cp -r clients/openclaw/* ~/.openclaw/workspace/hooks/vibe-light/

# enable
openclaw hooks enable vibe-light
openclaw hooks check
```