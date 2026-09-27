"""
vibewin - Vibe Light Windows GUI (tkinter + stdlib only)

Single-file tkinter GUI for controlling the ESP32 vibe-light via WiFi TCP.
Designed to be wrapped as vibewin.exe via PyInstaller (onefile, windowed).

Features:
  - Online detection via persistent TCP heartbeat (5s PING, 2s reconnect)
  - Discovery: UDP broadcast listen -> TCP subnet scan fallback
  - Client selector (oc / oo / cc)
  - State buttons (thinking / coding / busy / waiting / success / error / alarm / loading / off)
  - Brightness slider + RGB color override
  - Persistent config in ~/.config/vibe-light/esp32.json

Reuses (inlined for PyInstaller compatibility, behavior-equivalent):
  - pi5/vibelight.py:45-66   _send()  blocking TCP send/recv
  - pi5/vibelight.py:91-106  _scan_one()  TCP subnet scan
  - pi5/vibelight.py:127-138 load/save config
  - agent/vl-discover:23-61  UDP listen + parse_msg "vibe-light:v2 ip=... tcp_port=..."
  - agent/vl_win.py:36-42    VALID_STATES, VALID_CLIENTS
"""

import os
import sys
import json
import time
import socket
import threading
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# ============== Constants ==============
VALID_CLIENTS = ("oc", "oo", "cc")
VALID_STATES = (
    "thinking", "coding", "busy", "waiting",
    "success", "error", "alarm", "loading", "off",
)
DEFAULT_HOST = "192.168.0.236"
DEFAULT_PORT = 8888
DEFAULT_BRIGHTNESS = 50

# Discovery
UDP_BROADCAST_PORT = 5000
UDP_DISCOVER_TIMEOUT = 3.0
SCAN_SUBNETS = ("192.168.0", "192.168.1", "10.0.0")
SCAN_PORT = 8888
SCAN_TIMEOUT = 0.3   # per-IP
SCAN_MAX_PARALLEL = 32

# Config persistence
CONFIG_DIR = Path.home() / ".config" / "vibe-light"
CONFIG_FILE = CONFIG_DIR / "esp32.json"

# UI
WINDOW_W, WINDOW_H = 540, 560
TITLE = "Vibe-Win"

# Heartbeat
HEARTBEAT_INTERVAL = 5.0   # seconds between PINGs
RECONNECT_DELAY = 2.0      # wait after failure before retry
PING_TIMEOUT = 2.0         # recv timeout per PING

# ============== TCP client (one-shot, blocking) ==============
def _send_one_shot(host, port, cmd, timeout=2.0):
    """Open socket, send cmd + newline, read until \\n or timeout. Return decoded str."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall((cmd + "\n").encode())
        s.settimeout(timeout)
        buf = b""
        deadline = time.time() + timeout
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


# ============== Discovery ==============
def _parse_msg(data: bytes):
    """Parse 'vibe-light:v2 ip=... tcp_port=...' -> (ip, port) or None."""
    try:
        s = data.decode("utf-8", "replace")
        if "vibe-light:v2" not in s:
            return None
        ip = None
        port = None
        for tok in s.replace("\n", " ").split():
            if tok.startswith("ip="):
                ip = tok[3:].strip()
            elif tok.startswith("tcp_port="):
                port = int(tok.split("=", 1)[1].strip())
        if ip and port:
            return (ip, port)
    except Exception:
        return None
    return None


def _discover_udp(timeout=UDP_DISCOVER_TIMEOUT):
    """Listen on UDP 5000 for vibe-light broadcasts. Return (ip, port) or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(timeout)
        s.bind(("0.0.0.0", UDP_BROADCAST_PORT))
    except OSError as e:
        print(f"UDP bind failed: {e}", file=sys.stderr)
        return None
    try:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, _ = s.recvfrom(1024)
            except socket.timeout:
                break
            hit = _parse_msg(data)
            if hit:
                return hit
    finally:
        s.close()
    return None


def _scan_one(subnet):
    """TCP-scan subnet /24 for ESP32 vibe-light. Return (ip, port) or None."""
    for i in range(1, 255):
        ip = f"{subnet}.{i}"
        try:
            with socket.create_connection((ip, SCAN_PORT), timeout=SCAN_TIMEOUT) as s:
                s.sendall(b"PING\n")
                s.settimeout(SCAN_TIMEOUT)
                data = s.recv(64).decode("utf-8", "replace").strip()
                if "PONG" in data:
                    return (ip, SCAN_PORT)
        except (socket.timeout, ConnectionRefusedError, OSError):
            continue
    return None


def discover(timeout_udp=UDP_DISCOVER_TIMEOUT):
    """UDP listen first; fall back to TCP scan if no UDP broadcast seen. Return (ip, port) or None."""
    hit = _discover_udp(timeout_udp)
    if hit:
        return hit
    for subnet in SCAN_SUBNETS:
        hit = _scan_one(subnet)
        if hit:
            return hit
    return None


# ============== Config persistence ==============
def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"Failed to save config: {e}", file=sys.stderr)


# ============== Online monitor (long TCP + heartbeat) ==============
class OnlineMonitor(threading.Thread):
    """Background thread: persistent TCP socket, 5s PING heartbeat, 2s reconnect on failure.

    Calls on_change(online: bool, ts: float) on every transition.
    """

    def __init__(self, host, port, on_change):
        super().__init__(daemon=True, name="OnlineMonitor")
        self.host = host
        self.port = port
        self.on_change = on_change
        self._sock = None
        self._stop = threading.Event()
        self.online = False
        self.last_transition_ts = 0.0

    def stop(self):
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    def update_endpoint(self, host, port):
        """Hot-swap target. Closes socket; worker reconnects on next loop."""
        self.host = host
        self.port = port
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def run(self):
        while not self._stop.is_set():
            sock = None
            try:
                sock = socket.create_connection((self.host, self.port), timeout=5)
                sock.settimeout(PING_TIMEOUT)
                # Initial probe to confirm protocol works
                sock.sendall(b"PING\n")
                resp = sock.recv(64).decode("utf-8", "replace").strip()
                if "PONG" not in resp:
                    raise ConnectionError(f"unexpected probe reply: {resp!r}")
                self._sock = sock
                self._set_online(True)
                last_hb = time.time()
                while not self._stop.is_set():
                    if time.time() - last_hb >= HEARTBEAT_INTERVAL:
                        sock.sendall(b"PING\n")
                        data = sock.recv(64).decode("utf-8", "replace").strip()
                        if "PONG" not in data:
                            raise ConnectionError(f"heartbeat reply: {data!r}")
                        last_hb = time.time()
                    # Wake every 0.5s to check stop / heartbeat
                    self._stop.wait(0.5)
            except (ConnectionRefusedError, ConnectionError, OSError, socket.timeout) as e:
                # Print to stderr only on transition (avoid log spam)
                if self.online:
                    print(f"[monitor] offline: {e}", file=sys.stderr)
                self._sock = None
                self._set_online(False)
                if self._stop.wait(RECONNECT_DELAY):
                    break
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
        self._set_online(False)

    def _set_online(self, val):
        if self.online != val:
            self.online = val
            ts = time.time()
            self.last_transition_ts = ts
            try:
                self.on_change(val, ts)
            except Exception as e:
                print(f"[monitor] on_change error: {e}", file=sys.stderr)


# ============== GUI ==============
class VibeGUI:
    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.root.resizable(False, False)

        # State vars
        self.host = tk.StringVar(value=self.cfg.get("host", ""))
        self.port = tk.IntVar(value=self.cfg.get("port", DEFAULT_PORT))
        self.client = tk.StringVar(value=self.cfg.get("client", "oc"))
        self.brightness = tk.IntVar(value=self.cfg.get("brightness", DEFAULT_BRIGHTNESS))
        self.status_text = tk.StringVar(value="Ready.")
        self.indicator_text = tk.StringVar(value="● offline")
        self.indicator_color = tk.StringVar(value="#d04040")

        self.monitor = None
        self._build()
        self._start_monitor()

        # Persist config on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- UI construction -----
    def _build(self):
        pad = {"padx": 8, "pady": 4}

        # Endpoint frame
        f_endpoint = ttk.LabelFrame(self.root, text="Endpoint")
        f_endpoint.pack(fill="x", **pad)
        ttk.Label(f_endpoint, text="Host:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(f_endpoint, textvariable=self.host, width=20).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(f_endpoint, text="Port:").grid(row=0, column=2, sticky="w", padx=4)
        ttk.Entry(f_endpoint, textvariable=self.port, width=8).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Button(f_endpoint, text="Discover", command=self.on_discover).grid(row=0, column=4, padx=4)

        # Indicator (online/offline)
        f_ind = ttk.Frame(f_endpoint)
        f_ind.grid(row=1, column=0, columnspan=5, sticky="w", padx=4, pady=2)
        self.indicator_canvas = tk.Canvas(f_ind, width=14, height=14, highlightthickness=0)
        self.indicator_canvas.pack(side="left")
        self.indicator_dot = self.indicator_canvas.create_oval(2, 2, 12, 12, fill="#d04040", outline="")
        ttk.Label(f_ind, textvariable=self.indicator_text).pack(side="left", padx=6)

        # Apply endpoint on Enter
        self.host.trace_add("write", lambda *_: self._apply_endpoint())
        self.port.trace_add("write", lambda *_: self._apply_endpoint())

        # Client frame
        f_client = ttk.LabelFrame(self.root, text="Client")
        f_client.pack(fill="x", **pad)
        for i, c in enumerate(VALID_CLIENTS):
            ttk.Radiobutton(f_client, text=c.upper(), value=c, variable=self.client,
                            command=self.on_client_change).pack(side="left", padx=10, pady=4)

        # State frame (9 buttons in 2 rows)
        f_state = ttk.LabelFrame(self.root, text="State")
        f_state.pack(fill="x", **pad)
        for i, s in enumerate(VALID_STATES):
            r, c = divmod(i, 5)
            ttk.Button(f_state, text=s, width=10,
                       command=lambda s=s: self.on_state(s)).grid(row=r, column=c, padx=4, pady=4)

        # Brightness
        f_bright = ttk.LabelFrame(self.root, text="Brightness")
        f_bright.pack(fill="x", **pad)
        self.brightness_label = ttk.Label(f_bright, text=f"{self.brightness.get()}")
        self.brightness_label.pack(side="right", padx=8)
        scale = ttk.Scale(f_bright, from_=0, to=100, orient="horizontal",
                          variable=self.brightness, command=self._on_brightness_scale)
        scale.pack(side="left", fill="x", expand=True, padx=8, pady=4)
        scale.bind("<ButtonRelease-1>", self.on_brightness_release)

        # Color override
        f_color = ttk.LabelFrame(self.root, text="Color override")
        f_color.pack(fill="x", **pad)
        self.r_var = tk.IntVar(value=128)
        self.g_var = tk.IntVar(value=128)
        self.b_var = tk.IntVar(value=128)
        for label, var in (("R", self.r_var), ("G", self.g_var), ("B", self.b_var)):
            ttk.Label(f_color, text=label).pack(side="left", padx=(8, 2))
            ttk.Spinbox(f_color, from_=0, to=255, width=5, textvariable=var).pack(side="left", padx=2)
        ttk.Button(f_color, text="Set", command=self.on_color_set).pack(side="left", padx=8)

        # Status bar
        f_status = ttk.Frame(self.root, relief="sunken", borderwidth=1)
        f_status.pack(fill="x", side="bottom", ipady=4)
        ttk.Label(f_status, textvariable=self.status_text, foreground="#444").pack(side="left", padx=8)

    # ----- Online indicator -----
    def _update_indicator(self, online, ts):
        if online:
            self.indicator_canvas.itemconfig(self.indicator_dot, fill="#40c040")
            local = time.strftime("%H:%M:%S", time.localtime(ts))
            self.indicator_text.set(f"● online since {local}")
        else:
            self.indicator_canvas.itemconfig(self.indicator_dot, fill="#d04040")
            self.indicator_text.set("● offline")

    # ----- Monitor lifecycle -----
    def _start_monitor(self):
        host = self.host.get().strip()
        if not host:
            return
        if self.monitor:
            self.monitor.stop()
            self.monitor = None
        self.monitor = OnlineMonitor(host, int(self.port.get()), self._on_monitor_change)
        self.monitor.start()

    def _apply_endpoint(self):
        host = self.host.get().strip()
        port = int(self.port.get() or DEFAULT_PORT)
        if not host:
            return
        if self.monitor:
            self.monitor.update_endpoint(host, port)
        else:
            self._start_monitor()
        # Persist
        self.cfg["host"] = host
        self.cfg["port"] = port
        save_config(self.cfg)

    def _on_monitor_change(self, online, ts):
        # Cross-thread -> schedule on main thread
        self.root.after(0, self._update_indicator, online, ts)

    def _on_close(self):
        if self.monitor:
            self.monitor.stop()
        save_config(self.cfg)
        self.root.destroy()

    # ----- Discover -----
    def on_discover(self):
        if not self._discover_btn_state(True):
            return
        self._set_status("Discovering (UDP 3s + TCP scan)...")

        def worker():
            hit = discover()
            self.root.after(0, self._discover_done, hit)

        threading.Thread(target=worker, daemon=True, name="Discover").start()

    def _discover_btn_state(self, busy):
        # Could disable button; placeholder for future enhancement
        return True

    def _discover_done(self, hit):
        self._discover_btn_state(False)
        if hit:
            host, port = hit
            self.host.set(host)
            self.port.set(port)
            self._apply_endpoint()
            self._set_status(f"Discovered {host}:{port}")
        else:
            self._set_status("Discovery failed (no ESP32 found)")

    # ----- Client / state / brightness / color -----
    def on_client_change(self):
        # Persist preference
        self.cfg["client"] = self.client.get()
        save_config(self.cfg)
        self._send_and_show(f"CLIENT {self.client.get()}")

    def on_state(self, name):
        if name not in VALID_STATES:
            return
        self._send_and_show(f"STATE {self.client.get()}.{name}")

    def _on_brightness_scale(self, v):
        # Update label live, send only on release (debounced)
        self.brightness_label.config(text=f"{int(float(v))}")

    def on_brightness_release(self, _event=None):
        self._send_and_show(f"BRIGHT {int(self.brightness.get())}")

    def on_color_set(self):
        r, g, b = self.r_var.get(), self.g_var.get(), self.b_var.get()
        self._send_and_show(f"COLOR {r} {g} {b}")

    # ----- Command dispatch (worker thread per call) -----
    def _send_and_show(self, cmd):
        host = self.host.get().strip()
        if not host:
            self._set_status("No host set. Click Discover or type IP.")
            return
        port = int(self.port.get() or DEFAULT_PORT)
        self._set_status(f"Sending: {cmd}")

        def worker():
            try:
                resp = _send_one_shot(host, port, cmd)
                self.root.after(0, self._set_status, f"{cmd} -> {resp}")
            except Exception as e:
                self.root.after(0, self._set_status, f"{cmd} ERR: {e}")

        threading.Thread(target=worker, daemon=True, name=f"cmd-{cmd}").start()

    def _set_status(self, text):
        self.status_text.set(text)


# ============== Main ==============
def main():
    VibeGUI().root.mainloop()


if __name__ == "__main__":
    main()