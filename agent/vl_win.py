#!/usr/bin/env python3
"""
vl_win - Vibe Light CLI (Windows 版)

跟 agent/vl 用法相同, 但走 vld_win daemon (TCP localhost:8889) 而不是 AF_UNIX.
两个 CLI 完全独立:
  - agent/vl       : Linux/Mac, 走 vld (AF_UNIX)
  - agent/vl_win.py: Windows, 走 vld_win (TCP localhost)

用法:
  vl_win thinking                  # 切到 thinking (默认 oc client)
  vl_win oc.thinking               # 指定 client
  vl_win busy                      # 切到 busy
  vl_win off                       # 全灭
  vl_win bright 80                 # 设亮度
  vl_win status                    # 查询 ESP32 状态
  vl_win ping                      # 健康检查
"""
import sys
import os
import socket
import time
import json
import argparse
import atexit
from pathlib import Path

# ============== Config ==============
DEFAULT_HOST = "192.168.0.236"
DEFAULT_PORT = 8888
DEFAULT_CLIENT = "oc"
DEFAULT_TIMEOUT = 2.0

VALID_STATES = {
    "thinking", "coding", "busy", "waiting", "success", "error", "alarm",
    "idle", "permission", "done", "question",
}
# off 已弃用: 跳过不发, LED 保持上次状态
SKIP_STATES = {"off"}
VALID_CLIENTS = {"oc", "oo", "cc"}

# Windows daemon: TCP localhost:8889
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 8889


def send_via_daemon(cmd, timeout=3):
    """通过 TCP localhost 发送给 vld_win 守护进程"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((DAEMON_HOST, DAEMON_PORT))
        s.sendall((cmd + "\n").encode())
        resp = b""
        while b"\n" not in resp:
            chunk = s.recv(1024)
            if not chunk:
                raise ConnectionResetError("daemon closed")
            resp += chunk
        return resp.decode().strip()
    finally:
        try:
            s.close()
        except Exception:
            pass


def _daemon_alive():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((DAEMON_HOST, DAEMON_PORT))
        s.sendall(b"__PING__\n")
        resp = s.recv(64).decode().strip()
        s.close()
        return resp == "PONG"
    except Exception:
        return False


# ============== Global Socket (fallback direct) ==============
_SOCK = None
_SOCK_HOST = None
_SOCK_PORT = None


def get_sock(host, port):
    global _SOCK, _SOCK_HOST, _SOCK_PORT
    if _SOCK is not None and _SOCK_HOST == host and _SOCK_PORT == port:
        try:
            _SOCK.getpeername()
            return _SOCK
        except (OSError, BrokenPipeError):
            try:
                _SOCK.close()
            except Exception:
                pass
            _SOCK = None

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(DEFAULT_TIMEOUT)
    s.connect((host, port))
    s.settimeout(None)
    _SOCK = s
    _SOCK_HOST = host
    _SOCK_PORT = port
    return s


def send_cmd(host, port, cmd, expect_response=True):
    """优先走 daemon, daemon 不在时直连 ESP32"""
    if _daemon_alive():
        try:
            return send_via_daemon(cmd)
        except Exception:
            pass

    s = get_sock(host, port)
    try:
        s.sendall((cmd + "\n").encode())
    except (BrokenPipeError, ConnectionResetError, OSError):
        try:
            s.close()
        except Exception:
            pass
        global _SOCK
        _SOCK = None
        s = get_sock(host, port)
        s.sendall((cmd + "\n").encode())

    if not expect_response:
        return ""

    s.settimeout(2)
    try:
        resp = b""
        while b"\n" not in resp:
            chunk = s.recv(64)
            if not chunk:
                raise ConnectionResetError("server closed")
            resp += chunk
        s.settimeout(None)
        return resp.decode().strip()
    except socket.timeout:
        s.settimeout(None)
        return "(timeout)"


@atexit.register
def cleanup():
    global _SOCK
    if _SOCK is not None:
        try:
            _SOCK.close()
        except Exception:
            pass
        _SOCK = None


# ============== Subcommands ==============
def cmd_state(host, port, args):
    if len(args) != 1:
        print("用法: vl_win <client>.<state>  或  vl_win <state>")
        sys.exit(1)
    target = args[0]
    if "." in target:
        client, state = target.split(".", 1)
    else:
        client, state = DEFAULT_CLIENT, target

    if client not in VALID_CLIENTS:
        print(f"❌ client 必须是 {VALID_CLIENTS} 之一")
        sys.exit(1)
    if state in SKIP_STATES:
        print(f"(skip: '{state}' 状态已禁用, LED 保持上次状态)")
        return
    if state not in VALID_STATES:
        print(f"❌ state 必须是 {VALID_STATES} 之一")
        sys.exit(1)

    resp = send_cmd(host, port, f"STATE {client}.{state}")
    print(resp)


def cmd_client(host, port, args):
    if len(args) != 1 or args[0] not in VALID_CLIENTS:
        print("用法: vl_win --client <oc|oo|cc>")
        sys.exit(1)
    resp = send_cmd(host, port, f"CLIENT {args[0]}")
    print(resp)


def cmd_bright(host, port, args):
    if len(args) != 1:
        print("用法: vl_win bright <0-100>")
        sys.exit(1)
    try:
        n = int(args[0])
        if not 0 <= n <= 100:
            raise ValueError
    except ValueError:
        print("❌ brightness 必须是 0-100 的整数")
        sys.exit(1)
    resp = send_cmd(host, port, f"BRIGHT {n}")
    print(resp)


def cmd_status(host, port, args):
    resp = send_cmd(host, port, "STATUS")
    try:
        d = json.loads(resp)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print(resp)


def cmd_ping(host, port, args):
    resp = send_cmd(host, port, "PING")
    print(resp)


def cmd_off(host, port, args):
    # off 已弃用: 不发命令, LED 保持上次状态
    print("(skip: 'off' 已禁用, LED 保持上次状态)")
    return


def cmd_help(host, port, args):
    print("""
vl_win - Vibe Light CLI (Windows 版)

用法:
  vl_win <client>.<state>     # 切状态 (e.g. vl_win oc.thinking)
  vl_win <state>              # 切状态（默认 oc）
  vl_win off                  # 全灭
  vl_win bright <0-100>       # 设亮度
  vl_win status               # 查询当前状态
  vl_win ping                 # 健康检查
  vl_win --client <oc|oo|cc>  # 切激活 client
  vl_win --host <ip>          # 指定 ESP32 IP

允许的状态:
  动画: thinking coding busy waiting success error alarm
  静态: off idle permission done question

依赖:
  vld_win start  (后台 daemon, 维护 ESP32 TCP 长连接)
""")


# ============== Main ==============
def main():
    parser = argparse.ArgumentParser(description="Vibe Light CLI (Windows)", add_help=False)
    parser.add_argument("--host", default=os.environ.get("VIBE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", default=int(os.environ.get("VIBE_PORT", DEFAULT_PORT)), type=int)
    parser.add_argument("--client", choices=list(VALID_CLIENTS), default=DEFAULT_CLIENT)
    parser.add_argument("-h", "--help", action="store_true")
    args, remaining = parser.parse_known_args()

    if args.help or (not remaining):
        cmd_help(args.host, args.port, [])
        return

    subcmd = remaining[0]
    subargs = remaining[1:]

    if subcmd == "help":
        cmd_help(args.host, args.port, [])
    elif subcmd == "status":
        cmd_status(args.host, args.port, subargs)
    elif subcmd == "ping":
        cmd_ping(args.host, args.port, subargs)
    elif subcmd == "off":
        cmd_off(args.host, args.port, subargs)
    elif subcmd == "bright":
        cmd_bright(args.host, args.port, subargs)
    elif subcmd == "--client":
        cmd_client(args.host, args.port, subargs)
    elif "." in subcmd or subcmd in VALID_STATES:
        cmd_state(args.host, args.port, [subcmd])
    else:
        print(f"❌ 未知命令: {subcmd}")
        print("运行 'vl_win help' 看用法")
        sys.exit(1)


if __name__ == "__main__":
    main()
