"""
vibe_daemon.py
vibe-light 主守护进程入口

启动后会：
1. 确保 ESP32 已 discover 并保存 IP
2. 启动 vibe_watcher.py 监控 /tmp/vibe/ 目录
3. （可选）启动 OpenCode SSE 监听作为 fallback
"""
import sys
import os
import time
import subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from vibelight import discover, load_config


def run_daemon():
    print("=== Vibe Light Daemon ===")

    # 1. 确保有 ESP32 配置
    cfg = load_config()
    if not cfg.get("host"):
        print("No ESP32 config, running discovery...")
        ip = discover()
        if not ip:
            print("ERR: ESP32 not found. Check power / WiFi / reboot ESP32.", file=sys.stderr)
            sys.exit(1)
        print(f"ESP32 found at {ip}")

    # 2. 启动 watcher
    watcher_path = Path(__file__).parent / "vibe_watcher.py"
    print(f"Starting watcher: {watcher_path}")

    # 用 subprocess 跑，方便 Ctrl-C 杀
    try:
        subprocess.run([sys.executable, str(watcher_path)], check=True)
    except KeyboardInterrupt:
        print("\nBye")


if __name__ == "__main__":
    run_daemon()