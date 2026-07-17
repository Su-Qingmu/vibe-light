#!/usr/bin/env python3
"""
vld_win - Vibe Light Daemon (Windows 版)

跟 agent/vld 功能相同, 但用 TCP localhost 代替 AF_UNIX (Windows Python 不支持),
用 subprocess.DETACHED_PROCESS 代替 os.fork().

两个 daemon 完全独立:
  - agent/vld         : Linux/Mac 原版 (AF_UNIX + os.fork)
  - agent/vld_win.py  : Windows 版 (TCP localhost + DETACHED_PROCESS)

用法:
  vld_win start                    # 启动 daemon (后台)
  vld_win stop                     # 停止 daemon
  vld_win status                   # 查看 daemon 状态
"""
import sys
import os
import socket
import time
import json
import signal
import subprocess
from pathlib import Path

# ============== Config ==============
DAEMON_HOST = "192.168.0.236"
DAEMON_PORT = 8888

# TCP localhost: 跨 Windows 通用
DAEMON_SOCK_HOST = "127.0.0.1"
DAEMON_SOCK_PORT = 8889

_RUNTIME_DIR = Path.home() / ".cache" / "vibe-light"
_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
DAEMON_PID_FILE = str(_RUNTIME_DIR / "vl-daemon-win.pid")
DAEMON_LOG_FILE = str(_RUNTIME_DIR / "vl-daemon-win.log")


class VibeDaemon:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.esp_sock = None
        self.local_sock = None
        self.running = True

    def connect_esp(self):
        """连接到 ESP32, 长连接"""
        if self.esp_sock is not None:
            try:
                self.esp_sock.getpeername()
                return
            except OSError:
                try:
                    self.esp_sock.close()
                except Exception:
                    pass
                self.esp_sock = None

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((self.host, self.port))
        s.settimeout(None)
        self.esp_sock = s

    def send_to_esp(self, cmd, timeout=2):
        """发送一条命令到 ESP32, 返回响应字符串"""
        self.connect_esp()
        try:
            self.esp_sock.sendall((cmd + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.esp_sock = None
            self.connect_esp()
            self.esp_sock.sendall((cmd + "\n").encode())

        self.esp_sock.settimeout(timeout)
        try:
            resp = b""
            while b"\n" not in resp:
                chunk = self.esp_sock.recv(256)
                if not chunk:
                    raise ConnectionResetError("server closed")
                resp += chunk
            return resp.decode().strip()
        except socket.timeout:
            return "(timeout)"
        finally:
            self.esp_sock.settimeout(None)

    def shutdown(self):
        self.running = False
        if self.local_sock:
            try:
                self.local_sock.close()
            except Exception:
                pass
        if self.esp_sock:
            try:
                self.esp_sock.close()
            except Exception:
                pass
        if os.path.exists(DAEMON_PID_FILE):
            try:
                os.unlink(DAEMON_PID_FILE)
            except Exception:
                pass

    def handle_client(self, conn):
        try:
            conn.settimeout(5)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
            if not data:
                return
            cmd = data.decode().strip()
            # 跳过 'off' 状态: LED 保持上次, 不发 ESP32
            if cmd.startswith("STATE ") and cmd.endswith(".off"):
                conn.sendall(b"SKIP off disabled\n")
                return
            if cmd == "__PING__":
                conn.sendall(b"PONG\n")
                return
            if cmd == "__STATUS__":
                d = {
                    "esp_connected": self.esp_sock is not None,
                    "pid": os.getpid(),
                    "uptime": time.time(),
                }
                conn.sendall((json.dumps(d) + "\n").encode())
                return
            if cmd == "__STOP__":
                conn.sendall(b"OK stopping\n")
                self.shutdown()
                os._exit(0)

            resp = self.send_to_esp(cmd)
            conn.sendall((resp + "\n").encode())
        except Exception as e:
            try:
                conn.sendall(("ERR " + str(e) + "\n").encode())
            except Exception:
                pass

    def serve_local(self):
        """监听 TCP localhost, 接受本地命令"""
        self.local_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.local_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.local_sock.bind((DAEMON_SOCK_HOST, DAEMON_SOCK_PORT))
        self.local_sock.listen(8)
        self.local_sock.settimeout(0.5)

        with open(DAEMON_PID_FILE, "w") as f:
            f.write(str(os.getpid()))

        signal.signal(signal.SIGTERM, lambda *a: self.shutdown())
        signal.signal(signal.SIGINT, lambda *a: self.shutdown())

        print("[vld_win] listening on " + DAEMON_SOCK_HOST + ":" + str(DAEMON_SOCK_PORT)
              + " (pid=" + str(os.getpid()) + ")", flush=True)
        print("[vld_win] ESP32 = " + self.host + ":" + str(self.port), flush=True)

        try:
            while self.running:
                try:
                    conn, _ = self.local_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with conn:
                    self.handle_client(conn)
        finally:
            self.shutdown()


# ============== Probe ==============
def _probe():
    """通过 TCP localhost 探测 daemon 是否响应"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect((DAEMON_SOCK_HOST, DAEMON_SOCK_PORT))
        s.sendall(b"__PING__\n")
        resp = s.recv(64).decode().strip()
        s.close()
        return resp == "PONG"
    except Exception:
        return False


# ============== Commands ==============
def cmd_daemon_start():
    """以 daemon 模式运行 (Windows: 用 DETACHED_PROCESS 启动后台进程)"""
    if _probe():
        print("vld_win 已在运行")
        return

    # 用 subprocess 启动自己 + _daemon 子命令
    log_fp = open(DAEMON_LOG_FILE, "ab", buffering=0)
    creationflags = (
        subprocess.DETACHED_PROCESS
        | subprocess.CREATE_NEW_PROCESS_GROUP
        | subprocess.CREATE_NO_WINDOW
    )
    p = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "_daemon"],
        stdin=subprocess.DEVNULL,
        stdout=log_fp,
        stderr=log_fp,
        creationflags=creationflags,
        close_fds=True,
    )

    for _ in range(30):
        time.sleep(0.1)
        if _probe():
            print("vld_win started (pid=" + str(p.pid) + ")")
            return
    print("vld_win 启动超时, 查看日志: " + DAEMON_LOG_FILE)


def cmd_daemon_stop():
    """通过 TCP localhost 发送停止命令, 失败则 taskkill"""
    if not _probe():
        print("vld_win 未运行")
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((DAEMON_SOCK_HOST, DAEMON_SOCK_PORT))
        s.sendall(b"__STOP__\n")
        print(s.recv(64).decode().strip())
        s.close()
        return
    except Exception as e:
        print("停止失败: " + str(e))
    # 兜底: 读 PID 文件, taskkill
    if os.path.exists(DAEMON_PID_FILE):
        try:
            pid = int(open(DAEMON_PID_FILE).read().strip())
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                check=False,
                capture_output=True,
            )
            print("已 taskkill pid=" + str(pid))
        except Exception as e2:
            print("kill 失败: " + str(e2))


def cmd_daemon_status():
    """查 daemon 状态"""
    if not _probe():
        print("vld_win 未运行")
        return
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect((DAEMON_SOCK_HOST, DAEMON_SOCK_PORT))
        s.sendall(b"__STATUS__\n")
        resp = s.recv(1024).decode().strip()
        d = json.loads(resp)
        print("vld_win 运行中")
        print("   pid:      " + str(d["pid"]))
        print("   uptime:   " + str(round(time.time() - d["uptime"], 1)) + "s")
        print("   esp 长连接: " + ("ok" if d["esp_connected"] else "down"))
        s.close()
    except Exception as e:
        print("查询失败: " + str(e))


# ============== Main ==============
def main():
    if len(sys.argv) < 2:
        print("用法: vld_win {start|stop|status}")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "_daemon":
        # 内部入口: 直接跑 daemon 主循环
        d = VibeDaemon(DAEMON_HOST, DAEMON_PORT)
        d.serve_local()
    elif cmd == "start":
        cmd_daemon_start()
    elif cmd == "stop":
        cmd_daemon_stop()
    elif cmd == "status":
        cmd_daemon_status()
    else:
        print("未知命令: " + cmd)


if __name__ == "__main__":
    main()
