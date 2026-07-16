"""
Vibe Light v2 - TCP 客户端
通过 WiFi 控制 ESP32-C3 上的 WS2812 状态灯

用法：
  vibelight discover                          扫描子网找 ESP32
  vibelight state <client>.<state>            设置状态 (e.g. state cc.thinking)
  vibelight client <oc|oo|cc>                 切换激活 client
  vibelight color <r> <g> <b>                 临时颜色覆盖
  vibelight bright <0-100>                    设置亮度
  vibelight status                            查询 ESP32 状态
  vibelight ping                              健康检查
  vibelight daemon                            启动状态监控守护进程
"""

import socket
import json
import time
import sys
import os
import argparse
import threading
from pathlib import Path

# ============== Config ==============
DEFAULT_TCP_PORT = 8888
DEFAULT_TIMEOUT = 2.0

CONFIG_DIR = Path.home() / ".config" / "vibe-light"
CONFIG_FILE = CONFIG_DIR / "esp32.json"

# 候选子网
SCAN_SUBNETS = ["192.168.0", "192.168.1", "10.0.0"]
SCAN_PORT = 8888
SCAN_TIMEOUT = 0.3  # 每 IP 超时


# ============== TCP Client ==============
class VibeLightClient:
    def __init__(self, host, port=DEFAULT_TCP_PORT, timeout=DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout

    def _send(self, cmd, expect_response=True):
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
                s.sendall((cmd + "\n").encode())
                if not expect_response:
                    return ""
                s.settimeout(self.timeout)
                buf = b""
                deadline = time.time() + self.timeout
                while time.time() < deadline:
                    try:
                        chunk = s.recv(256)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in buf:
                        break
                return buf.decode("utf-8", "replace").strip()
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            return f"ERR: {e}"

    def state(self, client_state):
        return self._send(f"STATE {client_state}")

    def client(self, name):
        return self._send(f"CLIENT {name}")

    def color(self, r, g, b):
        return self._send(f"COLOR {r} {g} {b}")

    def brightness(self, b):
        return self._send(f"BRIGHT {b}")

    def status(self):
        return self._send("STATUS")

    def ping(self):
        return self._send("PING")

    def help(self):
        return self._send("HELP")


# ============== Discovery ==============
def _scan_one(subnet):
    """扫一个 /24 子网，返回 (ip, port) 列表"""
    found = []
    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        try:
            with socket.create_connection((ip, SCAN_PORT), timeout=SCAN_TIMEOUT) as s:
                s.sendall(b"PING\n")
                s.settimeout(SCAN_TIMEOUT)
                data = s.recv(64).decode("utf-8", "replace").strip()
                if "PONG" in data:
                    found.append(ip)
                    break  # 找到就停
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    return found


def discover(subnets=None, save=True):
    """扫描找 ESP32，返回 IP"""
    subnets = subnets or SCAN_SUBNETS
    print(f"Scanning for ESP32 vibe-light on: {subnets}...", flush=True)
    for subnet in subnets:
        print(f"  {subnet}.0/24 ...", end="", flush=True)
        found = _scan_one(subnet)
        if found:
            ip = found[0]
            print(f" FOUND {ip}")
            if save:
                save_config({"host": ip, "port": DEFAULT_TCP_PORT})
            return ip
        print(" -")
    return None


# ============== Config ==============
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def get_host():
    """从 config 或 fallback"""
    cfg = load_config()
    return cfg.get("host")


# ============== CLI ==============
def main():
    parser = argparse.ArgumentParser(description="Vibe Light WiFi controller")
    parser.add_argument("-H", "--host", help="ESP32 IP (default: from config)")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_TCP_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--discover", action="store_true", help="scan subnet for ESP32")

    sub = parser.add_subparsers(dest="cmd", required=False)

    sub.add_parser("discover", help="scan subnet for ESP32")
    sub.add_parser("status", help="query device state")
    sub.add_parser("ping", help="health check")
    sub.add_parser("help", help="device help")
    sub.add_parser("daemon", help="run state daemon")

    p_state = sub.add_parser("state", help="set state (e.g. cc.thinking)")
    p_state.add_argument("name")

    p_client = sub.add_parser("client", help="set active client")
    p_client.add_argument("name", choices=["oc", "oo", "cc"])

    p_color = sub.add_parser("color", help="override color")
    p_color.add_argument("r", type=int)
    p_color.add_argument("g", type=int)
    p_color.add_argument("b", type=int)

    p_bright = sub.add_parser("bright", help="set brightness 0-100")
    p_bright.add_argument("value", type=int)

    args = parser.parse_args()

    # discover 单独处理
    if args.discover or args.cmd == "discover":
        ip = discover()
        if ip:
            print(f"✓ ESP32 at {ip}")
            sys.exit(0)
        else:
            print("✗ ESP32 not found")
            sys.exit(1)

    # daemon 单独处理
    if args.cmd == "daemon":
        from vibe_daemon import run_daemon
        run_daemon()
        return

    # 找 host
    host = args.host or get_host()
    if not host:
        print("No ESP32 host configured. Run 'vibelight --discover' first.", file=sys.stderr)
        sys.exit(2)

    v = VibeLightClient(host, args.port, args.timeout)

    if args.cmd == "state":
        print(v.state(args.name))
    elif args.cmd == "client":
        print(v.client(args.name))
    elif args.cmd == "color":
        print(v.color(args.r, args.g, args.b))
    elif args.cmd == "bright":
        print(v.brightness(args.value))
    elif args.cmd == "status":
        print(v.status())
    elif args.cmd == "ping":
        print(v.ping())
    elif args.cmd == "help":
        print(v.help())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()