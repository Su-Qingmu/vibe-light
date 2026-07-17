#!/bin/bash
# vibe-claude-hook.sh
# Claude Code hook 入口脚本
# 接收 hook_event_name + session_id，写状态到 /tmp/vibe/claude-{session_id}.json

set -e

INPUT=$(cat)

# 需要 jq 解析 JSON
if ! command -v jq >/dev/null 2>&1; then
    # 没有 jq，fallback：直接 idle
    echo "WARN: jq not installed, falling back to idle" >&2
    echo '{"client":"cc","state":"idle","ts":'$(date +%s)'}' > /tmp/vibe/claude-unknown.json
    exit 0
fi

EVENT=$(echo "$INPUT" | jq -r '.hook_event_name // "unknown"')
SESSION=$(echo "$INPUT" | jq -r '.session_id // "unknown"')

# 状态映射
case "$EVENT" in
    UserPromptSubmit)    STATE="thinking" ;;
    PreToolUse)          STATE="coding" ;;
    PostToolUse)         STATE="coding" ;;
    PermissionRequest)   STATE="permission" ;;
    Notification)        STATE="question" ;;     # AskUserQuestion 类型的通知
    Stop)                STATE="done" ;;
    StopFailure)         STATE="error" ;;
    SessionStart)        STATE="idle" ;;
    SessionEnd)          exit 0 ;;               # off 已弃用: 跳过 SessionEnd, LED 保持上次
    SubagentStart)       STATE="thinking" ;;
    SubagentStop)        STATE="coding" ;;
    *)                   STATE="idle" ;;
esac

mkdir -p /tmp/vibe
# 原子写入（避免 watcher 读到半行 JSON）
TMPFILE="/tmp/vibe/.claude-${SESSION}.json.tmp"
OUTFILE="/tmp/vibe/claude-${SESSION}.json"

printf '{"client":"cc","state":"%s","event":"%s","ts":%s,"session":"%s"}' \
    "$STATE" "$EVENT" "$(date +%s)" "$SESSION" > "$TMPFILE"
mv "$TMPFILE" "$OUTFILE"

exit 0