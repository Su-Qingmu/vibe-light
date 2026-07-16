#!/usr/bin/env python3
"""
install_claude_hooks.py
智能合并 vibe-light hooks 到 ~/.claude/settings.json，保留已有 env 等配置

用法：
  python3 install_claude_hooks.py          # 安装
  python3 install_claude_hooks.py --remove # 卸载
"""
import json
import sys
import os
from pathlib import Path

HOOK_SCRIPT = Path(__file__).parent.parent / "clients" / "claude" / "vibe-claude-hook.sh"

HOOK_EVENTS = [
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "Notification",
    "Stop",
    "StopFailure",
    "SubagentStart",
    "SubagentStop",
]


def build_hooks():
    """构造 hooks 配置"""
    hooks = {}
    for event in HOOK_EVENTS:
        hooks[event] = [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": str(HOOK_SCRIPT),
                    }
                ]
            }
        ]
    return hooks


def install():
    settings_path = Path.home() / ".claude" / "settings.json"

    # 读取现有
    cfg = {}
    if settings_path.exists():
        try:
            cfg = json.loads(settings_path.read_text())
        except Exception as e:
            print(f"WARN: cannot parse existing settings.json: {e}", file=sys.stderr)
            backup = settings_path.with_suffix(".json.bak")
            settings_path.rename(backup)
            print(f"  backed up to {backup}", file=sys.stderr)

    # 合并 hooks（替换 vibe-light 相关事件）
    cfg.setdefault("hooks", {})
    vibe_hooks = build_hooks()
    for event, conf in vibe_hooks.items():
        # 移除已有的 vibe-light hook（保留用户的其他 hook）
        existing = cfg["hooks"].get(event, [])
        existing = [h for h in existing if "vibe-claude-hook" not in json.dumps(h)]
        existing.append(conf[0])
        cfg["hooks"][event] = existing

    settings_path.write_text(json.dumps(cfg, indent=2))
    print(f"✓ Installed hooks in {settings_path}")
    print(f"  hook script: {HOOK_SCRIPT}")


def remove():
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return

    cfg = json.loads(settings_path.read_text())
    hooks = cfg.get("hooks", {})
    for event in HOOK_EVENTS:
        if event in hooks:
            hooks[event] = [
                h for h in hooks[event]
                if "vibe-claude-hook" not in json.dumps(h)
            ]
            if not hooks[event]:
                del hooks[event]

    cfg["hooks"] = hooks
    settings_path.write_text(json.dumps(cfg, indent=2))
    print(f"✓ Removed hooks from {settings_path}")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        install()