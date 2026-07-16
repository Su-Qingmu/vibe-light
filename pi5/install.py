#!/usr/bin/env python3
"""
install.py - 一键安装 vibe-light 所有客户端 hooks
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CLAUDE_HOOK = ROOT / "clients" / "claude" / "vibe-claude-hook.sh"
OPENCLAW_HOOK_DIR = ROOT / "clients" / "openclaw"
OPENCODE_PLUGIN = ROOT / "clients" / "opencode" / "vibe-light.ts"


def check_dependencies():
    """检查依赖"""
    missing = []
    for cmd in ["jq", "node"]:
        if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
            missing.append(cmd)
    # watchdog 是 Python 包（检查当前解释器的环境）
    try:
        import watchdog  # noqa
    except ImportError:
        missing.append(f"python:watchdog (run: {sys.executable} -m pip install watchdog)")
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        print()
        print("Install with:")
        print("  sudo apt install jq node")
        print(f"  {sys.executable} -m pip install watchdog")
        sys.exit(2)


def install_claude_hooks():
    """装 Claude Code hooks"""
    print("[1/3] Claude Code hooks...")
    subprocess.run([sys.executable, str(ROOT / "pi5" / "install_claude_hooks.py")], check=True)


def install_openclaw_hooks():
    """装 OpenClaw hook"""
    print("[2/3] OpenClaw hook...")
    target = Path.home() / ".openclaw" / "workspace" / "hooks" / "vibe-light"
    if target.exists():
        print(f"  {target} already exists, updating...")
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for src in OPENCLAW_HOOK_DIR.iterdir():
        shutil.copy2(src, target / src.name)
    print(f"  copied to {target}")
    print("  running: openclaw hooks enable vibe-light")
    try:
        subprocess.run(["openclaw", "hooks", "enable", "vibe-light"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"  WARN: openclaw hooks enable failed: {e}")
        print("  run manually: openclaw hooks enable vibe-light")


def install_opencode_plugin():
    """装 OpenCode plugin"""
    print("[3/3] OpenCode plugin...")
    if not shutil.which("opencode"):
        print("  opencode not installed, skipping")
        print("  (when you install opencode, plugin will go to ~/.config/opencode/plugins/)")
        return
    target_dir = Path.home() / ".config" / "opencode" / "plugins"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "vibe-light.ts"
    shutil.copy2(OPENCODE_PLUGIN, target)
    print(f"  copied to {target}")


def main():
    check_dependencies()
    install_claude_hooks()
    install_openclaw_hooks()
    install_opencode_plugin()
    print()
    print("✓ All hooks installed!")
    print()
    print("Next steps:")
    print("  1. ESP32 WiFi config is in esp32/config.py (already filled with SQM / S20050201h)")
    print("  2. After ESP32 connects to WiFi, run: vibelight --discover")
    print("  3. Start daemon: vibelight daemon")


if __name__ == "__main__":
    main()