"""
Vibe Light v2 - ESP32-C3 端主程序 (动画版)
功能：
  - WiFi Station 模式连接路由器
  - TCP server (port 8888) 接受状态控制命令
  - Boot 按钮 (GPIO9) 切换 client / 清除状态
  - 24 颗 WS2812 环形布局：
      D2 (idx 0) = mode 色 / 起点
      D3-D25 (idx 1-23) = 状态动画区

协议（每行一条命令）：
  STATE <client>.<state>      切到指定 client 的指定状态
  CLIENT <oc|oo|cc>           切换激活的 client（保持当前 state）
  COLOR <r> <g> <b>           临时覆盖所有 LED 颜色（不修改 client/state）
  BRIGHT <0-100>              设置亮度
  STATUS                      返回当前状态（JSON）
  HELP                        帮助
  PING                        健康检查

动画状态（8 种，全部环形 23 颗）：
  thinking - 高速彩虹旋转
  coding   - 青→蓝液态渐变呼吸（环形）
  busy     - 黄色双向扫描（D3↔D25）汇聚到 D14
  waiting  - 红色呼吸
  success  - 绿色呼吸
  error    - 红↔橙快闪 + 抖动
  alarm    - 红蓝全闪
  off      - 全灭
"""

import network
import socket
import time
import machine
import neopixel
import json
import sys
import math

# 加载配置
try:
    from config import (
        WIFI_SSID, WIFI_PASS, WIFI_TIMEOUT_MS,
        LED_PIN, LED_COUNT, DEFAULT_BRIGHTNESS,
        TCP_PORT, TCP_BACKLOG, TCP_TIMEOUT_MS,
        BOOT_PIN, BTN_DEBOUNCE_MS, BTN_LONGPRESS_MS, BTN_DOUBLECLICK_MS,
        CLIENT_BASE, COLORS, CLIENT_STATES,
        ANIMATED_STATES, ANIM_PALETTE, PEAK, TRAIL_TAIL,
    )
except ImportError:
    print("ERR: config.py not found", file=sys.stderr)
    raise


# ============== State ==============
MODE_DISCONNECTED = "disconnected"
MODE_CONNECTED = "connected"
MODE_ACTIVE = "active"


class State:
    def __init__(self):
        self.client = "oc"
        self.state = "off"
        self.brightness = DEFAULT_BRIGHTNESS
        self.temp_override = None
        self.mode = MODE_DISCONNECTED
        self.flash_on = False
        self.last_flash_ms = 0
        self.clients = []
        self.client_bufs = {}


# ============== LED + Animations ==============
class LED:
    """
    24 颗 WS2812，环形布局（idx 23 wraparound 到 idx 0）
    - idx 0 = D2   (mode 色 / busy 起点)
    - idx 1-23 = D3-D25  (状态动画区)
    """
    NUM_ANIM = LED_COUNT - 1   # 23 颗动画灯
    BUSY_PERIOD_MS = 1500

    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(machine.Pin(pin, machine.Pin.OUT), count)
        self.count = count

    # ---------- 工具 ----------
    def _scale(self, c, b):
        """b: 0-100 外部亮度，乘 PEAK (50%) 全局峰值"""
        s = (b / 100.0) * PEAK
        return (int(c[0] * s), int(c[1] * s), int(c[2] * s))

    def _set_all_active(self, color):
        """D3-D25 全部设为 color（已 scale 过的）"""
        for i in range(1, self.count):
            self.np[i] = color

    def _zero_all_active(self):
        for i in range(1, self.count):
            self.np[i] = (0, 0, 0)

    def _hsv(self, h, s=1.0, v=1.0):
        """h: 0-360, s/v: 0-1  → RGB 0-255"""
        h = h % 360
        c = v * s
        x = c * (1 - abs((h / 60) % 2 - 1))
        m = v - c
        seg = int(h // 60) % 6
        rgb = [(c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x)][seg]
        return tuple(int((rgb[i] + m) * 255) for i in range(3))

    # ---------- 顶层 ----------
    def show(self, state):
        base = CLIENT_BASE[state.client]
        scaled_base = self._scale(base, state.brightness)

        if state.mode == MODE_DISCONNECTED:
            now = time.ticks_ms()
            if time.ticks_diff(now, state.last_flash_ms) >= 1000:
                state.last_flash_ms = now
                state.flash_on = not state.flash_on
            self.np[0] = scaled_base if state.flash_on else (0, 0, 0)
            for i in range(1, self.count):
                self.np[i] = (0, 0, 0)

        elif state.mode == MODE_CONNECTED:
            self.np[0] = scaled_base
            for i in range(1, self.count):
                self.np[i] = (0, 0, 0)

        else:  # MODE_ACTIVE
            if state.state in ANIMATED_STATES:
                self._animate(state)
            else:
                # 静态色（off/idle/permission/done/question）
                if state.temp_override:
                    color = self._scale(state.temp_override, state.brightness)
                else:
                    fb = COLORS.get((state.client, state.state), base)
                    color = self._scale(fb, state.brightness)
                self._set_all_active(color)

        self.np.write()

    # ---------- 动画 ----------
    def _animate(self, state):
        now = time.ticks_ms()
        b = state.brightness
        s = state.state
        if   s == "thinking": self._anim_thinking(now, b)
        elif s == "coding":   self._anim_coding(now, b)
        elif s == "busy":     self._anim_busy(now, b)
        elif s == "waiting":  self._anim_breath(now, b, ANIM_PALETTE["waiting"])
        elif s == "success":  self._anim_breath(now, b, ANIM_PALETTE["success"])
        elif s == "error":    self._anim_error(now, b)
        elif s == "alarm":    self._anim_alarm(now, b)

    def _anim_thinking(self, now, b):
        """高速彩虹旋转（环形，顺时针 = idx 增加方向）"""
        period = 800
        phase = (now % period) / period
        for i in range(0, self.count):
            hue = (i / self.count * 360 - phase * 360) % 360
            rgb = self._hsv(hue, 1.0, 1.0)
            self.np[i] = self._scale(rgb, b)

    def _anim_coding(self, now, b):
        """青→蓝渐变 + 全灯呼吸（环形对称：D2/D3/D25 青，D14 最蓝）"""
        period = 4000
        p = (now % period) / period
        breath = 0.5 + 0.5 * math.sin(p * 2 * math.pi)
        v = 0.35 + 0.65 * breath
        # 环形对称：D2(i=0)/D3(i=1)=青  D14(i=12)=蓝  D25(i=23)=青
        for i in range(0, self.count):
            theta = i / self.count * 1.0   # 0..~1（环形，i=23 后回到 0）
            g = int(255 - math.sin(theta * math.pi) * 155)
            r = 0
            bl = 255
            scaled = (int(r * v), int(g * v), int(bl * v))
            self.np[i] = self._scale(scaled, b)

    def _anim_busy(self, now, b):
        """环形双 comet 动画（基于用户时序样例）
        - Comet A: D2 (idx 0) → D14 (idx 12), 顺时针 +1, 4 颗拖尾
        - Comet B: D25 (idx 23) → D14 (idx 12), 逆时针 -1, 4 颗拖尾
        时序节奏:
          3 帧展开 (trail 1, 2, 3 颗)
          N 帧行进 (trail 4 颗)
          3 帧收敛 (trail 3, 2, 1 颗)
        busy 时 D2 也参与，覆盖 ACTIVE 默认 D2=0 逻辑
        """
        # 全 24 颗清空（busy 时 D2 也参与，覆盖 ACTIVE 默认 D2=0）
        for i in range(LED_COUNT):
            self.np[i] = (0, 0, 0)

        period = self.BUSY_PERIOD_MS
        p = (now % period) / period

        POS_D2 = 0
        POS_D14 = 12
        POS_D25 = 23
        TRAIL = 4
        NUM_A = POS_D14 - POS_D2 + 1   # 13 (D2→D14 含两端)
        NUM_B = POS_D25 - POS_D14 + 1  # 12 (D25→D14 含两端)
        TOTAL = NUM_A + TRAIL - 1      # 16 帧同步两条 comet

        frame = min(int(p * TOTAL), TOTAL - 1)
        color_full = ANIM_PALETTE["busy"]

        def trail_len(frame, num):
            """返回帧的 trail 颗数（不含 head 自身）"""
            if frame < TRAIL:
                return frame                       # 0,1,2,3 展开
            elif frame >= num:
                recv = frame - num                 # 0,1,2,3 收敛
                return max(0, TRAIL - 1 - recv)    # 3,2,1,0
            else:
                return TRAIL - 1                   # 行进段 3

        def render(head_idx, trail_n, trail_dir):
            """head_dir: +1 (comet A trail 在 -j) / -1 (comet B trail 在 +j)"""
            for j in range(trail_n + 1):
                s = max(TRAIL_TAIL / PEAK, 1.0 - j / TRAIL)
                rgb = (int(color_full[0] * s),
                       int(color_full[1] * s),
                       int(color_full[2] * s))
                idx = head_idx - j * trail_dir
                if 0 <= idx < LED_COUNT:
                    self.np[idx] = self._scale(rgb, b)

        # Comet A: D2 → D14 (顺时针 +1)
        if frame < NUM_A:
            head_a = POS_D2 + frame
        else:
            head_a = POS_D14
        render(head_a, trail_len(frame, NUM_A), +1)

        # Comet B: D25 → D14 (逆时针 -1)
        if frame < NUM_B:
            head_b = POS_D25 - frame
        else:
            head_b = POS_D14
        render(head_b, trail_len(frame, NUM_B), -1)

    def _anim_breath(self, now, b, color):
        """通用 sin 呼吸（含 D2）"""
        period = 2000
        p = (now % period) / period
        breath = 0.5 + 0.5 * math.sin(p * 2 * math.pi)
        v = 0.3 + 0.7 * breath
        scaled = (int(color[0] * v), int(color[1] * v), int(color[2] * v))
        out = self._scale(scaled, b)
        for i in range(0, self.count):
            self.np[i] = out

    def _anim_error(self, now, b):
        """红↔橙快闪（0.5s/闪）+ 整串偶位/奇位错位抖动（含 D2）"""
        period = 500
        p = (now % period) / period
        on = p < 0.5
        # 每 100ms 切红/橙
        is_orange = (now // 100) % 2 == 1
        if on:
            base = ANIM_PALETTE["error"] if not is_orange else (255, 80, 0)
            full = self._scale(base, b)
            dim = self._scale((base[0] // 4, base[1] // 4, base[2] // 4), b)
        else:
            full = (0, 0, 0)
            dim = (0, 0, 0)
        for i in range(0, self.count):
            self.np[i] = full if i % 2 == 0 else dim

    def _anim_alarm(self, now, b):
        """红蓝全灯带快闪（含 D2）"""
        period = 200
        p = (now % period) / period
        on = p < 0.5
        if on:
            is_blue = (now // period) % 2 == 1
            color = (0, 0, 255) if is_blue else (255, 0, 0)
            out = self._scale(color, b)
        else:
            out = (0, 0, 0)
        for i in range(0, self.count):
            self.np[i] = out


# ============== Button ==============
class Button:
    def __init__(self, pin):
        self.btn = machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP)
        self.pressed = False
        self.press_start = 0
        self.long_fired = False
        self.last_click = 0

    def check(self):
        v = self.btn.value()
        now = time.ticks_ms()
        event = None
        if v == 0:
            if not self.pressed:
                self.pressed = True
                self.press_start = now
                self.long_fired = False
                if self.last_click and time.ticks_diff(now, self.last_click) < BTN_DOUBLECLICK_MS:
                    event = "double"
                    self.last_click = 0
            else:
                if not self.long_fired and time.ticks_diff(now, self.press_start) >= BTN_LONGPRESS_MS:
                    self.long_fired = True
                    event = "long"
        else:
            if self.pressed:
                self.pressed = False
                duration = time.ticks_diff(now, self.press_start)
                if duration >= BTN_DEBOUNCE_MS and not self.long_fired:
                    event = "click"
                    self.last_click = now
        return event


# ============== WiFi ==============
class WiFi:
    def __init__(self, ssid, pwd, timeout_ms):
        self.ssid = ssid
        self.pwd = pwd
        self.timeout_ms = timeout_ms
        self.wlan = network.WLAN(network.STA_IF)

    def connect(self):
        self.wlan.active(True)
        if self.wlan.isconnected():
            return self.wlan.ifconfig()[0]
        print(f"WIFI: connecting to {self.ssid}...")
        try:
            self.wlan.connect(self.ssid, self.pwd)
        except Exception as e:
            print(f"WIFI: connect error: {e}")
            return None
        deadline = time.ticks_add(time.ticks_ms(), self.timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if self.wlan.isconnected():
                break
            time.sleep_ms(200)
        if self.wlan.isconnected():
            ip = self.wlan.ifconfig()[0]
            print(f"WIFI: OK ip={ip}")
            return ip
        print("WIFI: TIMEOUT")
        return None

    def signal(self):
        try:
            return self.wlan.status('rssi')
        except Exception:
            return None

    def is_connected(self):
        return self.wlan.isconnected()


# ============== Commands ==============
def cmd_state(state, args):
    if len(args) != 2:
        return "ERR usage: STATE <client>.<state>"
    full = args[1]
    if "." not in full:
        return "ERR usage: STATE <client>.<state>"
    client, st = full.split(".", 1)
    if client not in CLIENT_STATES:
        return f"ERR unknown client '{client}' (use oc|oo|cc)"
    if st not in CLIENT_STATES[client]:
        return f"ERR unknown state '{st}' for {client}"
    state.client = client
    state.state = st
    state.temp_override = None
    is_anim = " ANIM" if st in ANIMATED_STATES else ""
    return f"OK state={client}.{st}{is_anim}"


def cmd_client(state, args):
    if len(args) != 2:
        return "ERR usage: CLIENT <oc|oo|cc>"
    client = args[1]
    if client not in CLIENT_STATES:
        return f"ERR unknown client '{client}'"
    state.client = client
    state.temp_override = None
    return f"OK client={client}"


def cmd_color(state, args):
    if len(args) != 4:
        return "ERR usage: COLOR <r> <g> <b>"
    try:
        r, g, b = int(args[1]), int(args[2]), int(args[3])
    except ValueError:
        return "ERR color must be integers"
    if not all(0 <= x <= 255 for x in (r, g, b)):
        return "ERR color must be 0-255"
    state.temp_override = (r, g, b)
    return f"OK rgb=({r},{g},{b})"


def cmd_bright(state, args):
    if len(args) != 2:
        return "ERR usage: BRIGHT <0-100>"
    try:
        b = int(args[1])
    except ValueError:
        return "ERR brightness must be integer"
    if not 0 <= b <= 100:
        return "ERR brightness must be 0-100"
    state.brightness = b
    return f"OK bright={b}%"


def cmd_status(state):
    return json.dumps({
        "mode": state.mode,
        "client": state.client,
        "state": state.state,
        "brightness": state.brightness,
        "override": state.temp_override,
        "anim": state.state in ANIMATED_STATES,
    })


def cmd_help(state):
    return ("STATE <c>.<s> | CLIENT <oc|oo|cc> | COLOR r g b | BRIGHT 0-100 | "
            "STATUS | HELP | PING\n"
            "States: off idle thinking coding permission error done "
            "busy waiting success alarm\n"
            "Animated: thinking coding busy waiting success error alarm")


COMMANDS = {
    "STATE": cmd_state,
    "CLIENT": cmd_client,
    "COLOR": cmd_color,
    "BRIGHT": cmd_bright,
    "STATUS": lambda s, a: cmd_status(s),
    "HELP": lambda s, a: cmd_help(s),
    "PING": lambda s, a: "PONG",
}


def handle_line(state, line):
    line = line.strip()
    if not line:
        return ""
    parts = line.split()
    cmd = parts[0].upper()
    handler = COMMANDS.get(cmd)
    if not handler:
        return f"ERR unknown command: {cmd}"
    return handler(state, parts)


# ============== TCP Server ==============
class TCPServer:
    def __init__(self, port, backlog, timeout_ms):
        self.port = port
        self.timeout_ms = timeout_ms
        addr = socket.getaddrinfo("0.0.0.0", port)[0][-1]
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(addr)
        self.srv.listen(backlog)
        self.srv.settimeout(timeout_ms / 1000)
        print(f"TCP: listening on 0.0.0.0:{port}")

    def accept(self):
        try:
            cl, addr = self.srv.accept()
            cl.settimeout(2)
            print(f"TCP: client {addr}")
            return cl, addr
        except OSError:
            return None, None


# ============== Main Loop ==============
def _set_all(np, count, color):
    for i in range(count):
        np[i] = color
    np.write()


def wifi_failure_blink(led):
    for _ in range(5):
        _set_all(led.np, LED_COUNT, (255, 0, 0))
        time.sleep_ms(200)
        _set_all(led.np, LED_COUNT, (0, 0, 0))
        time.sleep_ms(200)


CLIENT_CYCLE = ["oc", "oo", "cc"]


def _longpress_flash(led):
    for _ in range(3):
        _set_all(led.np, LED_COUNT, (255, 255, 255))
        time.sleep_ms(120)
        _set_all(led.np, LED_COUNT, (0, 0, 0))
        time.sleep_ms(120)


def _reset_to_disconnected(state, led):
    for cl in list(state.clients):
        try: cl.close()
        except Exception: pass
    state.clients = []
    state.mode = MODE_DISCONNECTED
    state.temp_override = None
    led.show(state)


def main():
    print("=== Vibe Light v2 (animated) booting ===")

    state = State()
    led = LED(LED_PIN, LED_COUNT)
    btn = Button(BOOT_PIN)
    wifi = WiFi(WIFI_SSID, WIFI_PASS, WIFI_TIMEOUT_MS)

    # 启动测试：全白闪一下（确认灯环 OK）
    _set_all(led.np, LED_COUNT, (10, 10, 10))
    time.sleep_ms(300)
    _set_all(led.np, LED_COUNT, (0, 0, 0))

    # WiFi
    ip = wifi.connect()
    if not ip:
        wifi_failure_blink(led)
        print("BOOT FAILED: no WiFi")
        return

    # Server
    srv = TCPServer(TCP_PORT, TCP_BACKLOG, TCP_TIMEOUT_MS)

    # 初始显示
    led.show(state)
    print(f"=== Ready (ip={ip} client={state.client} state={state.state}) ===")

    render_dirty = True

    while True:
        # ---- Button ----
        event = btn.check()
        if event == "click":
            if state.mode == MODE_DISCONNECTED:
                idx = CLIENT_CYCLE.index(state.client)
                state.client = CLIENT_CYCLE[(idx + 1) % len(CLIENT_CYCLE)]
                state.temp_override = None
                print(f"BTN: click -> client={state.client}")
                render_dirty = True
        elif event == "long":
            if state.mode != MODE_DISCONNECTED:
                _longpress_flash(led)
                print(f"BTN: long press, reset (was {state.mode})")
                _reset_to_disconnected(state, led)
                render_dirty = False

        # ---- TCP accept ----
        srv.srv.settimeout(0)
        try:
            cl, addr = srv.srv.accept()
            cl.settimeout(0.05)
            state.clients.append(cl)
            state.client_bufs[id(cl)] = b""
            if state.mode == MODE_DISCONNECTED:
                state.mode = MODE_CONNECTED
                print(f"TCP: connected from {addr} (mode=connected)")
                render_dirty = True
            print(f"TCP: client {addr} (total={len(state.clients)})")
        except OSError as e:
            if str(e) != 'EAGAIN':
                print(f"TCP accept err: {e}")
        except Exception as e:
            print(f"TCP accept fatal: {e}")

        # ---- TCP recv ----
        for cl in list(state.clients):
            try:
                data = cl.recv(256)
                if data is None or (isinstance(data, bytes) and not data):
                    state.clients.remove(cl)
                    try: cl.close()
                    except Exception: pass
                    state.client_bufs.pop(id(cl), None)
                    if not state.clients and state.mode in (MODE_CONNECTED, MODE_ACTIVE):
                        state.mode = MODE_DISCONNECTED
                        state.temp_override = None
                        print(f"STATE: -> disconnected (client closed)")
                        render_dirty = True
                    continue
                key = id(cl)
                buf = state.client_bufs.get(key, b"") + data
                state.client_bufs[key] = buf
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    resp = handle_line(state, text)
                    if state.mode == MODE_CONNECTED:
                        state.mode = MODE_ACTIVE
                        print(f"STATE: -> active (first cmd)")
                    try:
                        cl.send(resp.encode() + b"\n")
                    except Exception as e:
                        print(f"TCP send err: {e}")
                        break
                    print(f"<- {text} -> {resp}")
                    render_dirty = True
                state.client_bufs[key] = buf
            except OSError as e:
                err_str = str(e)
                e_lower = err_str.lower()
                # MicroPython 1.28 的 timeout 字符串可能是 "ETIMEDOUT" / "[Errno 110] ETIMEDOUT" /
                # "timed out" / "[Errno 11] EAGAIN" 等，统一兼容
                if ('timed out' in e_lower or 'etimedout' in e_lower
                    or 'eagain' in e_lower or 'ewouldblock' in e_lower):
                    pass  # 正常：暂时没数据
                else:
                    print(f"TCP recv err ({len(state.clients)} clients): {e!r}")
                    try: state.clients.remove(cl)
                    except ValueError: pass
                    try: cl.close()
                    except Exception: pass
                    if not state.clients and state.mode in (MODE_CONNECTED, MODE_ACTIVE):
                        state.mode = MODE_DISCONNECTED
                        state.temp_override = None
                        render_dirty = True

        # ---- 渲染 ----
        # DISCONNECTED 模式每帧渲染以处理闪烁
        # ACTIVE + 动画状态每帧渲染
        # 其他只在 render_dirty 时渲染
        is_anim_state = state.mode == MODE_ACTIVE and state.state in ANIMATED_STATES
        if state.mode == MODE_DISCONNECTED or is_anim_state:
            led.show(state)
            render_dirty = False
        elif render_dirty:
            led.show(state)
            render_dirty = False

        time.sleep_ms(20)


main()
