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

# ============== UI palette (dark theme, inspired by iambest1-hue/light) ==============
# Color tokens (inspired by reference repo, adapted for tkinter)
BG_DEEP   = "#0e1015"   # window background (deeper than reference's #13151c)
BG_PANEL  = "#181b23"   # panel background
BG_INPUT  = "#222632"   # input control background
TXT_MAIN  = "#f4f5f8"   # primary text (~ rgba(255,255,255,0.96))
TXT_DIM   = "#9099a8"   # secondary text (~ rgba(232,236,244,0.62))
HAIRLINE  = "#222631"   # subtle separator (~ rgba(255,255,255,0.09))

# Reference state palette (4 buckets; mapped from my 9 states)
STATE_COLORS = {
    "off":      "#3a3f4b",
    "idle":     "#8b94a3",
    "thinking": "#4ea2ff",
    "coding":   "#4ea2ff",
    "busy":     "#4ea2ff",
    "loading":  "#4ea2ff",
    "waiting":  "#25d6a0",
    "success":  "#25d6a0",
    "error":    "#ff6058",
    "alarm":    "#ff6058",
}

# Client colors (from existing pi5/CLIENT_BASE: oc=red, oo=blue, cc=orange)
CLIENT_COLORS = {
    "oc": "#ff5a4e",
    "oo": "#5a8eff",
    "cc": "#ffa84a",
}

# Fonts
FONT_FAMILY = ("Segoe UI", "Microsoft YaHei", "PingFang SC", "Helvetica Neue", "sans-serif")
FONT_MONO   = ("Cascadia Code", "Consolas", "Courier New", "monospace")
FONT_REG    = (FONT_FAMILY[0], 10)
FONT_BOLD   = (FONT_FAMILY[0], 10, "bold")
FONT_DIM    = (FONT_FAMILY[0], 9)
FONT_HDR    = (FONT_FAMILY[0], 11, "bold")
FONT_TINY   = (FONT_FAMILY[0], 8)

# Layout
WINDOW_W, WINDOW_H = 480, 640
TITLE = "Vibe-Win"
PILL_H = 38               # state button height
PILL_RADIUS = 999         # pill border radius (effectively full curve)
PILL_PAD = 8
CHIP_H = 32

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
    """Dark-themed tkinter control panel. Canvas-drawn for rounded corners,
    glossy indicator, halo animation, custom pill buttons / chips / slider."""

    def __init__(self):
        self.cfg = load_config()
        self.root = tk.Tk()
        self.root.title(TITLE)
        self.root.geometry(f"{WINDOW_W}x{WINDOW_H}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG_DEEP)

        # Configure ttk styles for dark theme
        self._setup_styles()

        # State vars
        self.host = tk.StringVar(value=self.cfg.get("host", ""))
        self.port = tk.IntVar(value=self.cfg.get("port", DEFAULT_PORT))
        self.client = tk.StringVar(value=self.cfg.get("client", "oc"))
        self.brightness = tk.IntVar(value=self.cfg.get("brightness", DEFAULT_BRIGHTNESS))
        self.r_var = tk.IntVar(value=self.cfg.get("color", [128, 128, 128])[0])
        self.g_var = tk.IntVar(value=self.cfg.get("color", [128, 128, 128])[1])
        self.b_var = tk.IntVar(value=self.cfg.get("color", [128, 128, 128])[2])
        self.status_text = tk.StringVar(value="Ready.")
        self.indicator_text = tk.StringVar(value="offline")
        self.last_cmd_text = tk.StringVar(value="—")

        # Animation state for indicator
        self._anim_phase = 0.0
        self._anim_color = STATE_COLORS["off"]
        self._anim_pulse = False   # True when working/blue
        self._anim_shake = False   # True when error/red
        self._anim_after_id = None

        # Cached button canvas refs (for hover redraw)
        self._state_btns = {}     # name -> {'canvas', 'rect', 'text', 'dot'}
        self._client_chips = {}   # name -> {'canvas', 'rect', 'text', 'dot'}

        self.monitor = None
        self._build()
        self._start_monitor()
        self._animate_indicator()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- ttk style (dark theme) -----
    def _setup_styles(self):
        s = ttk.Style()
        # Use clam as base (most styleable)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".",
                    background=BG_DEEP, foreground=TXT_MAIN,
                    fieldbackground=BG_INPUT, bordercolor=HAIRLINE,
                    font=FONT_REG)
        s.configure("TFrame", background=BG_DEEP)
        s.configure("Panel.TFrame", background=BG_PANEL)
        s.configure("TLabel", background=BG_DEEP, foreground=TXT_MAIN, font=FONT_REG)
        s.configure("Dim.TLabel", background=BG_DEEP, foreground=TXT_DIM, font=FONT_DIM)
        s.configure("Hdr.TLabel", background=BG_DEEP, foreground=TXT_MAIN, font=FONT_HDR)
        s.configure("Mono.TLabel", background=BG_DEEP, foreground=TXT_MAIN, font=(FONT_MONO[0], 10))
        s.configure("TEntry", fieldbackground=BG_INPUT, foreground=TXT_MAIN,
                    insertcolor=TXT_MAIN, borderwidth=1, relief="flat")
        s.map("TEntry",
              foreground=[("focus", TXT_MAIN)],
              fieldbackground=[("focus", BG_INPUT)])
        s.configure("TSpinbox", fieldbackground=BG_INPUT, foreground=TXT_MAIN,
                    arrowcolor=TXT_DIM, borderwidth=1, relief="flat")
        s.configure("Discover.TButton",
                    background=BG_INPUT, foreground=TXT_MAIN,
                    borderwidth=0, relief="flat", font=FONT_BOLD)
        s.map("Discover.TButton",
              background=[("active", "#2c3340"), ("disabled", BG_INPUT)],
              foreground=[("disabled", TXT_DIM)])
        s.configure("Set.TButton",
                    background="#4ea2ff", foreground="#0e1015",
                    borderwidth=0, relief="flat", font=FONT_BOLD)
        s.map("Set.TButton",
              background=[("active", "#6ab2ff"), ("disabled", "#2a3a55")])

    # ----- UI construction -----
    def _build(self):
        pad = {"padx": 16, "pady": 0}

        # Endpoint section
        sec = self._make_section(self.root, "ENDPOINT")
        row = ttk.Frame(sec, style="Panel.TFrame")
        row.pack(fill="x", padx=12, pady=8)
        ttk.Label(row, text="Host", style="Dim.TLabel").pack(side="left")
        host_e = ttk.Entry(row, textvariable=self.host, width=18, font=(FONT_MONO[0], 10))
        host_e.pack(side="left", padx=(6, 12))
        ttk.Label(row, text="Port", style="Dim.TLabel").pack(side="left")
        port_e = ttk.Entry(row, textvariable=self.port, width=6, font=(FONT_MONO[0], 10))
        port_e.pack(side="left", padx=(6, 12))
        self._discover_btn = ttk.Button(row, text="Discover", style="Discover.TButton",
                                        command=self.on_discover)
        self._discover_btn.pack(side="right", ipadx=10, ipady=4)
        self.host.trace_add("write", lambda *_: self._apply_endpoint())
        self.port.trace_add("write", lambda *_: self._apply_endpoint())

        # Indicator row (glossy dot + label + last command)
        ind = ttk.Frame(sec, style="Panel.TFrame")
        ind.pack(fill="x", padx=12, pady=(0, 10))
        self.indicator_canvas = tk.Canvas(ind, width=24, height=24,
                                           bg=BG_PANEL, highlightthickness=0)
        self.indicator_canvas.pack(side="left")
        self.indicator_canvas.bind("<Button-1>", lambda e: None)  # decorative
        ttk.Label(ind, textvariable=self.indicator_text,
                  style="Dim.TLabel").pack(side="left", padx=(8, 16))
        ttk.Label(ind, textvariable=self.last_cmd_text,
                  style="Mono.TLabel").pack(side="right")

        # Client chips section
        csec = self._make_section(self.root, "CLIENT")
        crows = ttk.Frame(csec, style="Panel.TFrame")
        crows.pack(fill="x", padx=12, pady=8)
        for c in VALID_CLIENTS:
            chip = self._make_chip(crows, c.upper(), CLIENT_COLORS[c],
                                   lambda c=c: self.on_client_change(c))
            chip.pack(side="left", padx=(0, 8))
        self._refresh_client_chips()

        # State grid section
        ssec = self._make_section(self.root, "STATE")
        sgrid = ttk.Frame(ssec, style="Panel.TFrame")
        sgrid.pack(fill="x", padx=12, pady=8)
        # 5 columns x 2 rows for 9 states
        cols = 5
        for i, name in enumerate(VALID_STATES):
            r, c = divmod(i, cols)
            btn = self._make_pill(sgrid, name, STATE_COLORS[name],
                                  lambda n=name: self.on_state(n))
            btn.grid(row=r, column=c, padx=4, pady=4, sticky="nsew")
        for c in range(cols):
            sgrid.columnconfigure(c, weight=1, uniform="state")

        # Brightness custom slider
        bsec = self._make_section(self.root, "BRIGHTNESS")
        brow = ttk.Frame(bsec, style="Panel.TFrame")
        brow.pack(fill="x", padx=12, pady=8)
        self.brightness_canvas = tk.Canvas(brow, height=24, bg=BG_PANEL,
                                            highlightthickness=0)
        self.brightness_canvas.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.brightness_canvas.bind("<Configure>", lambda e: self._draw_brightness())
        self.brightness_canvas.bind("<Button-1>", self._brightness_click)
        self.brightness_canvas.bind("<B1-Motion>", self._brightness_drag)
        self.brightness_canvas.bind("<ButtonRelease-1>", self.on_brightness_release)
        self.brightness_value_label = ttk.Label(brow, text=f"{self.brightness.get()}",
                                                  style="Mono.TLabel")
        self.brightness_value_label.pack(side="right")
        # Sync label + redraw when brightness changes programmatically
        self.brightness.trace_add("write", lambda *_: self._on_brightness_var_change())

    def _on_brightness_var_change(self):
        self.brightness_value_label.config(text=f"{self.brightness.get()}")
        self._draw_brightness()

        # Color override row
        ksec = self._make_section(self.root, "COLOR")
        krow = ttk.Frame(ksec, style="Panel.TFrame")
        krow.pack(fill="x", padx=12, pady=8)
        self.color_preview = tk.Canvas(krow, width=24, height=24, bg=BG_PANEL,
                                        highlightthickness=0)
        self.color_preview.pack(side="right", padx=(8, 0))
        for label, var in (("R", self.r_var), ("G", self.g_var), ("B", self.b_var)):
            grp = ttk.Frame(krow, style="Panel.TFrame")
            grp.pack(side="left", padx=(0, 12))
            ttk.Label(grp, text=label, style="Dim.TLabel").pack(side="left")
            sp = ttk.Spinbox(grp, from_=0, to=255, width=4, textvariable=var,
                             font=(FONT_MONO[0], 10))
            sp.pack(side="left", padx=(4, 0))
        for var in (self.r_var, self.g_var, self.b_var):
            var.trace_add("write", lambda *_: self._draw_color_preview())
        self._draw_color_preview()
        ttk.Button(krow, text="Set", style="Set.TButton",
                   command=self.on_color_set).pack(side="right", padx=(8, 0),
                                                    ipadx=12, ipady=4)

        # Status bar (bottom, hairline top, dim text)
        bar = tk.Frame(self.root, bg=BG_PANEL, height=26)
        bar.pack(fill="x", side="bottom")
        tk.Frame(bar, bg=HAIRLINE, height=1).pack(fill="x", side="top")
        ttk.Label(bar, textvariable=self.status_text,
                  style="Dim.TLabel").pack(side="left", padx=12, pady=4)

    def _make_section(self, parent, title):
        sec = ttk.Frame(parent, style="Panel.TFrame")
        sec.pack(fill="x", padx=16, pady=(12, 0))
        ttk.Label(sec, text=title, style="Dim.TLabel").pack(anchor="w", padx=12, pady=(8, 0))
        return sec

    # ----- Pill button (state) -----
    def _make_pill(self, parent, label, accent, on_click):
        """Canvas-drawn pill button (rounded full-radius)."""
        c = tk.Canvas(parent, height=PILL_H, bg=BG_PANEL,
                       highlightthickness=0, cursor="hand2")
        state = {"hover": False, "pressed": False}
        rect = c.create_rectangle(0, 0, 100, PILL_H, fill=BG_INPUT, outline=HAIRLINE, width=1)
        dot = c.create_oval(8, PILL_H//2 - 3, 14, PILL_H//2 + 3, fill=accent, outline="")
        text = c.create_text(22, PILL_H//2, text=label, fill=TXT_MAIN, anchor="w",
                              font=(FONT_FAMILY[0], 10, "bold"))

        def redraw():
            cw = c.winfo_width() or 100
            # Resize rect
            c.coords(rect, 1, 1, cw - 2, PILL_H - 1)
            # Background depends on hover/pressed
            if state["pressed"]:
                c.itemconfig(rect, fill=accent)
                c.itemconfig(text, fill="#0e1015")
            elif state["hover"]:
                c.itemconfig(rect, fill="#2a2f3a")
                c.itemconfig(text, fill=TXT_MAIN)
            else:
                c.itemconfig(rect, fill=BG_INPUT)
                c.itemconfig(text, fill=TXT_MAIN)
            # Center the text in remaining space (after dot)
            text_bbox = c.bbox(text)
            if text_bbox:
                text_w = text_bbox[2] - text_bbox[0]
                c.coords(text, cw // 2 + 4, PILL_H // 2)

        def on_enter(_):
            state["hover"] = True
            redraw()

        def on_leave(_):
            state["hover"] = False
            state["pressed"] = False
            redraw()

        def on_press(_):
            state["pressed"] = True
            redraw()

        def on_release(_):
            if state["pressed"]:
                state["pressed"] = False
                redraw()
                on_click()

        c.bind("<Configure>", lambda e: redraw())
        c.bind("<Enter>", on_enter)
        c.bind("<Leave>", on_leave)
        c.bind("<ButtonPress-1>", on_press)
        c.bind("<ButtonRelease-1>", on_release)
        # Forward events on dot/text too
        for item in (dot, text):
            c.tag_bind(item, "<Enter>", on_enter)
            c.tag_bind(item, "<Leave>", on_leave)
            c.tag_bind(item, "<ButtonPress-1>", on_press)
            c.tag_bind(item, "<ButtonRelease-1>", on_release)

        self._state_btns[label] = {"canvas": c, "rect": rect, "text": text, "dot": dot,
                                    "state": state, "redraw": redraw, "accent": accent}
        return c

    # ----- Chip button (client) -----
    def _make_chip(self, parent, label, accent, on_click):
        c = tk.Canvas(parent, height=CHIP_H, width=110, bg=BG_PANEL,
                       highlightthickness=0, cursor="hand2")
        state = {"hover": False}
        rect = c.create_rectangle(0, 0, 110, CHIP_H, fill=BG_INPUT, outline=HAIRLINE, width=1)
        dot = c.create_oval(10, CHIP_H//2 - 4, 18, CHIP_H//2 + 4, fill=accent, outline="")
        text = c.create_text(56, CHIP_H//2, text=label, fill=TXT_MAIN, font=FONT_BOLD)

        def redraw():
            cw = c.winfo_width() or 110
            c.coords(rect, 1, 1, cw - 2, CHIP_H - 1)
            # Background based on hover + selected (set by _refresh_client_chips)
            if state.get("selected", False):
                c.itemconfig(rect, fill=accent, outline=accent)
                c.itemconfig(text, fill="#0e1015")
            elif state["hover"]:
                c.itemconfig(rect, fill="#2a2f3a")
                c.itemconfig(text, fill=TXT_MAIN)
            else:
                c.itemconfig(rect, fill=BG_INPUT, outline=HAIRLINE)
                c.itemconfig(text, fill=TXT_MAIN)

        def on_enter(_):
            state["hover"] = True
            redraw()

        def on_leave(_):
            state["hover"] = False
            redraw()

        def on_click_event(_):
            on_click()

        c.bind("<Configure>", lambda e: redraw())
        c.bind("<Enter>", on_enter)
        c.bind("<Leave>", on_leave)
        c.bind("<ButtonRelease-1>", on_click_event)
        for item in (dot, text):
            c.tag_bind(item, "<Enter>", on_enter)
            c.tag_bind(item, "<Leave>", on_leave)
            c.tag_bind(item, "<ButtonRelease-1>", on_click_event)

        self._client_chips[label.lower()] = {"canvas": c, "rect": rect, "text": text,
                                              "dot": dot, "state": state,
                                              "redraw": redraw, "accent": accent}
        return c

    def _refresh_client_chips(self):
        cur = self.client.get()
        for name, info in self._client_chips.items():
            info["state"]["selected"] = (name == cur)
            info["redraw"]()

    # ----- Glossy indicator (online/offline) -----
    def _update_indicator(self, online, ts):
        if online:
            self._anim_color = "#25d6a0"   # green = online
            self._anim_pulse = True
            self._anim_shake = False
            local = time.strftime("%H:%M:%S", time.localtime(ts))
            self.indicator_text.set(f"online · since {local}")
        else:
            self._anim_color = "#ff6058"   # red = offline
            self._anim_pulse = False
            self._anim_shake = True
            self.indicator_text.set("offline")

    def _animate_indicator(self):
        """Pulse / shake loop for the online indicator.
        Per reference: working = 1.6s pulse, error = 0.4s shake."""
        c = self.indicator_canvas
        c.delete("all")
        w = 24
        # Outer halo (3 concentric circles, fading)
        cx, cy = w // 2, w // 2
        halo_r = 11 + int(2 * abs(0.5 - (self._anim_phase % 1.0))) if self._anim_pulse else 11
        for r, color in [(halo_r + 2, "#222631"), (halo_r, "#3a3f4b")]:
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
        # Main dot 9px
        dot_r = 4
        # Shake offset for offline
        dx = 0
        if self._anim_shake:
            shake_phase = (self._anim_phase * 10) % 1
            dx = int(2 * (0.5 - shake_phase) * 2)  # ±2px
        c.create_oval(cx - dot_r + dx, cy - dot_r, cx + dot_r + dx, cy + dot_r,
                       fill=self._anim_color, outline="")
        # Glossy highlight (top-left radial gradient approximation)
        c.create_oval(cx - 2 + dx, cy - 2, cx + 1 + dx, cy + 1,
                       fill="#ffffff", outline="")

        self._anim_phase = (self._anim_phase + 0.06) % 1.6
        self._anim_after_id = self.root.after(50, self._animate_indicator)

    # ----- Brightness custom slider -----
    def _draw_brightness(self):
        c = self.brightness_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10:
            return
        # Track
        track_y = h // 2 - 3
        # Left half: filled with accent
        val = self.brightness.get()
        fill_w = int((val / 100) * (w - 8))
        if fill_w > 0:
            c.create_rectangle(4, track_y, 4 + fill_w, track_y + 6,
                                fill="#4ea2ff", outline="")
        # Right half: BG_INPUT
        if 4 + fill_w < w - 4:
            c.create_rectangle(4 + fill_w, track_y, w - 4, track_y + 6,
                                fill=BG_INPUT, outline="")
        # Thumb
        thumb_x = 4 + fill_w
        thumb_y = h // 2
        c.create_oval(thumb_x - 7, thumb_y - 7, thumb_x + 7, thumb_y + 7,
                       fill=TXT_MAIN, outline=HAIRLINE)

    def _brightness_click(self, event):
        self._brightness_set_from_x(event.x)

    def _brightness_drag(self, event):
        self._brightness_set_from_x(event.x)

    def _brightness_set_from_x(self, x):
        w = self.brightness_canvas.winfo_width()
        if w < 10:
            return
        pct = max(0, min(100, int((x / (w - 8)) * 100)))
        self.brightness.set(pct)
        self.brightness_value_label.config(text=f"{pct}")
        self._draw_brightness()

    def on_brightness_release(self, _event=None):
        self._send_and_show(f"BRIGHT {int(self.brightness.get())}")

    # ----- Color preview -----
    def _draw_color_preview(self):
        c = self.color_preview
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        try:
            color = f"#{self.r_var.get():02x}{self.g_var.get():02x}{self.b_var.get():02x}"
        except Exception:
            color = "#888888"
        c.create_rectangle(2, 2, w - 2, h - 2, fill=color, outline=HAIRLINE)

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
        self.cfg["host"] = host
        self.cfg["port"] = port
        save_config(self.cfg)

    def _on_monitor_change(self, online, ts):
        self.root.after(0, self._update_indicator, online, ts)

    def _on_close(self):
        if self.monitor:
            self.monitor.stop()
        if self._anim_after_id:
            try:
                self.root.after_cancel(self._anim_after_id)
            except Exception:
                pass
        save_config(self.cfg)
        self.root.destroy()

    # ----- Discover -----
    def on_discover(self):
        self._set_status("Discovering (UDP 3s + TCP scan)...")

        def worker():
            hit = discover()
            self.root.after(0, self._discover_done, hit)

        threading.Thread(target=worker, daemon=True, name="Discover").start()

    def _discover_done(self, hit):
        if hit:
            host, port = hit
            self.host.set(host)
            self.port.set(port)
            self._apply_endpoint()
            self._set_status(f"Discovered {host}:{port}")
        else:
            self._set_status("Discovery failed (no ESP32 found)")

    # ----- Client / state / color -----
    def on_client_change(self, name):
        self.client.set(name)
        self._refresh_client_chips()
        self.cfg["client"] = name
        save_config(self.cfg)
        self._send_and_show(f"CLIENT {name}")

    def on_state(self, name):
        if name not in VALID_STATES:
            return
        # Brief color feedback on the indicator (matches the state color)
        prev_color = self._anim_color
        self._anim_color = STATE_COLORS[name]
        self.root.after(1500, lambda: setattr(self, "_anim_color", prev_color)
                        if self._anim_color == STATE_COLORS[name] else None)
        self._send_and_show(f"STATE {self.client.get()}.{name}")

    def on_color_set(self):
        r, g, b = self.r_var.get(), self.g_var.get(), self.b_var.get()
        self.cfg["color"] = [r, g, b]
        save_config(self.cfg)
        self._send_and_show(f"COLOR {r} {g} {b}")

    # ----- Command dispatch (worker thread per call) -----
    def _send_and_show(self, cmd):
        host = self.host.get().strip()
        if not host:
            self._set_status("No host set. Click Discover or type IP.")
            return
        port = int(self.port.get() or DEFAULT_PORT)
        self._set_status(f"Sending: {cmd}")
        # Brief indicator feedback with command's "mood" color
        self.last_cmd_text.set(cmd)

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