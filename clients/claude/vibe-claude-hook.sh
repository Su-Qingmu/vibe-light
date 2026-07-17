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

# 状态映射 (8 个灯效)
#   loading  - 收到用户/系统/内部信息
#   busy     - 调用工具 (WebSearch, Bash, ...)
#   waiting  - 等用户决策/输入 (plan mode, AskUserQuestion)
#   success  - 完成
#   error    - 失败
#   alarm    - 安全告警/权限请求
#   thinking - agent 推理中 (Claude hooks 无直接事件, 不会自动触发)
#   coding   - agent 写代码 (Claude hooks 无直接事件, 不会自动触发)
case "$EVENT" in
    UserPromptSubmit)    STATE="loading" ;;      # 用户发来消息
    PreToolUse)          STATE="busy" ;;          # 即将调用工具
    PostToolUse)         STATE="busy" ;;          # 工具刚返回
    PermissionRequest)   STATE="alarm" ;;         # 权限请求 (Bash/Dangerous)
    Notification)        STATE="waiting" ;;       # AskUserQuestion 等待用户选
    Stop)                STATE="success" ;;       # 本轮完成
    StopFailure)         STATE="error" ;;         # 本轮失败
    SessionStart)        STATE="loading" ;;       # 会话开始 (收到系统消息)
    SessionEnd)          exit 0 ;;               # off 已弃用: 跳过 SessionEnd
    SubagentStart)       STATE="loading" ;;       # 子 agent 启动 (内部消息)
    SubagentStop)        STATE="busy" ;;          # 子 agent 完事, 回到主任务
    *)                   STATE="idle" ;;          # 兜底
esac

mkdir -p /tmp/vibe
# 原子写入（避免 watcher 读到半行 JSON）
TMPFILE="/tmp/vibe/.claude-${SESSION}.json.tmp"
OUTFILE="/tmp/vibe/claude-${SESSION}.json"

printf '{"client":"cc","state":"%s","event":"%s","ts":%s,"session":"%s"}' \
    "$STATE" "$EVENT" "$(date +%s)" "$SESSION" > "$TMPFILE"
mv "$TMPFILE" "$OUTFILE"

exit 0