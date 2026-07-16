"""
Vibe Light v2 - ESP32-C3 端主程序
功能：
  - WiFi Station 模式连接路由器
  - TCP server (port 8888) 接受状态控制命令
  - Boot 按钮 (GPIO9) 切换 client / 清除状态
  - 24 颗 WS2812：D2 显示 client 颜色，其余 23 颗显示状态颜色

协议（每行一条命令）：
  STATE <client>.<state>      切到指定 client 的指定状态
  CLIENT <oc|oo|cc>           切换激活的 client（保持当前 state）
  COLOR <r> <g> <b>           临时覆盖所有 LED 颜色（不修改 client/state）
  BRIGHT <0-100>              设置亮度
  STATUS                      返回当前状态（JSON）
  HELP                        帮助
  PING                        健康检查
"""

import network
import socket
import time
import machine
import neopixel
import json
import sys

# 加载配置
try:
    from config import (
        WIFI_SSID, WIFI_PASS, WIFI_TIMEOUT_MS,
        LED_PIN, LED_COUNT, DEFAULT_BRIGHTNESS,
        TCP_PORT, TCP_BACKLOG, TCP_TIMEOUT_MS,
        BOOT_PIN, BTN_DEBOUNCE_MS, BTN_LONGPRESS_MS, BTN_DOUBLECLICK_MS,
        CLIENT_BASE, COLORS, CLIENT_STATES,
    )
except ImportError:
    print("ERR: config.py not found", file=sys.stderr)
    raise


# ============== State ==============
# 状态机模式
MODE_DISCONNECTED = "disconnected"  # 等待连接：D2 闪烁 1Hz（client 色）
MODE_CONNECTED = "connected"        # 已连接：D2 常亮（client 色）
MODE_ACTIVE = "active"              # 已激活（收到任何 TCP 命令）：D2 灭，23 颗状态色


class State:
    def __init__(self):
        self.client = "oc"           # 当前激活 client
        self.state = "off"           # 当前状态
        self.brightness = DEFAULT_BRIGHTNESS  # 0-100
        self.temp_override = None    # COLOR 命令的临时覆盖
        # 连接状态机
        self.mode = MODE_DISCONNECTED  # 初始等待连接
        # 闪烁控制（DISCONNECTED 状态用）
        self.flash_on = False
        self.last_flash_ms = 0
        # 当前所有 TCP client socket（用于长按时断开）
        self.clients = []
        # client 接收缓冲（id(cl) → bytes，MicroPython socket 不能设属性）
        self.client_bufs = {}


# ============== LED ==============
class LED:
    def __init__(self, pin, count):
        self.np = neopixel.NeoPixel(machine.Pin(pin, machine.Pin.OUT), count)
        self.count = count

    def _scale(self, c, b):
        s = b / 100.0
        return (int(c[0] * s), int(c[1] * s), int(c[2] * s))

    def show(self, state):
        """按 mode 决定 D2 / D3-D25 的行为"""
        base = CLIENT_BASE[state.client]
        scaled_base = self._scale(base, state.brightness)

        if state.mode == MODE_DISCONNECTED:
            # D2 慢闪 1Hz（1s 亮 / 1s 灭），其余 23 颗全黑
            now = time.ticks_ms()
            if time.ticks_diff(now, state.last_flash_ms) >= 1000:
                state.last_flash_ms = now
                state.flash_on = not state.flash_on
            if state.flash_on:
                self.np[0] = scaled_base
            else:
                self.np[0] = (0, 0, 0)
            for i in range(1, self.count):
                self.np[i] = (0, 0, 0)

        elif state.mode == MODE_CONNECTED:
            # D2 常亮（client 色），其余 23 颗全黑
            self.np[0] = scaled_base
            for i in range(1, self.count):
                self.np[i] = (0, 0, 0)

        else:  # MODE_ACTIVE
            # D2 熄灭，其余 23 颗显示状态色（temp_override 优先）
            self.np[0] = (0, 0, 0)
            if state.temp_override:
                r, g, b = state.temp_override
                scaled = self._scale((r, g, b), state.brightness)
                for i in range(1, self.count):
                    self.np[i] = scaled
            else:
                color = COLORS.get((state.client, state.state), base)
                scaled_color = self._scale(color, state.brightness)
                for i in range(1, self.count):
                    self.np[i] = scaled_color

        self.np.write()


# ============== Button ==============
class Button:
    def __init__(self, pin):
        self.btn = machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP)
        self.pressed = False
        self.press_start = 0
        self.long_fired = False
        self.last_click = 0  # 上次 click 释放时间

    def check(self):
        """返回事件: 'click' / 'long' / 'double' / None"""
        v = self.btn.value()
        now = time.ticks_ms()
        event = None

        if v == 0:
            # 当前是按下状态
            if not self.pressed:
                # 边沿：新按下
                self.pressed = True
                self.press_start = now
                self.long_fired = False
                # 双击检测：上次 click 到现在 < BTN_DOUBLECLICK_MS
                if self.last_click and time.ticks_diff(now, self.last_click) < BTN_DOUBLECLICK_MS:
                    event = "double"
                    self.last_click = 0
            else:
                # 持续按下
                if not self.long_fired and time.ticks_diff(now, self.press_start) >= BTN_LONGPRESS_MS:
                    self.long_fired = True
                    event = "long"
        else:
            # 当前是释放状态
            if self.pressed:
                # 边沿：释放
                self.pressed = False
                duration = time.ticks_diff(now, self.press_start)
                if duration >= BTN_DEBOUNCE_MS and not self.long_fired:
                    # 单击（不算 long）
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
        else:
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
        return "ERR usage: STATE <client>.<state>  (e.g. STATE cc.thinking)"
    full = args[1]
    if "." not in full:
        return "ERR usage: STATE <client>.<state>  (e.g. STATE cc.thinking)"
    client, st = full.split(".", 1)
    if client not in CLIENT_STATES:
        return f"ERR unknown client '{client}' (use oc|oo|cc)"
    if st not in CLIENT_STATES[client]:
        return f"ERR unknown state '{st}' for {client}"
    state.client = client
    state.state = st
    state.temp_override = None
    return f"OK state={client}.{st}"


def cmd_client(state, args):
    if len(args) != 2:
        return "ERR usage: CLIENT <oc|oo|cc>"
    client = args[1]
    if client not in CLIENT_STATES:
        return f"ERR unknown client '{client}' (use oc|oo|cc)"
    state.client = client
    state.temp_override = None
    return f"OK client={client}"


def cmd_color(state, args):
    if len(args) != 4:
        return "ERR usage: COLOR <r> <g> <b>"
    try:
        r, g, b = int(args[1]), int(args[2]), int(args[3])
    except ValueError:
        return "ERR color values must be integers"
    if not all(0 <= x <= 255 for x in (r, g, b)):
        return "ERR color values must be 0-255"
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
    })


COMMANDS = {
    "STATE": cmd_state,
    "CLIENT": cmd_client,
    "COLOR": cmd_color,
    "BRIGHT": cmd_bright,
    "STATUS": lambda s, a: cmd_status(s),
    "HELP": lambda s, a: "STATE <c>.<s> | CLIENT <oc|oo|cc> | COLOR r g b | BRIGHT 0-100 | STATUS | PING",
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
    """WiFi 连不上：全灯快闪红 5 次"""
    for _ in range(5):
        _set_all(led.np, LED_COUNT, (255, 0, 0))
        time.sleep_ms(200)
        _set_all(led.np, LED_COUNT, (0, 0, 0))
        time.sleep_ms(200)


CLIENT_CYCLE = ["oc", "oo", "cc"]


def _longpress_flash(led):
    """长按提示：全灯闪白 3 次"""
    for _ in range(3):
        _set_all(led.np, LED_COUNT, (255, 255, 255))
        time.sleep_ms(120)
        _set_all(led.np, LED_COUNT, (0, 0, 0))
        time.sleep_ms(120)


def _reset_to_disconnected(state, led):
    """退出激活状态：关闭所有 client，重置 mode（保留 client/state）"""
    for cl in list(state.clients):
        try:
            cl.close()
        except Exception:
            pass
    state.clients = []
    state.mode = MODE_DISCONNECTED
    # 保留 state.client 和 state.state（用户偏好：长按只断开 TCP）
    state.temp_override = None
    led.show(state)


def main():
    print("=== Vibe Light v2 booting ===")

    state = State()
    led = LED(LED_PIN, LED_COUNT)
    btn = Button(BOOT_PIN)
    wifi = WiFi(WIFI_SSID, WIFI_PASS, WIFI_TIMEOUT_MS)

    # 启动测试：先全白闪一下（确认灯环 OK）
    _set_all(led.np, LED_COUNT, (10, 10, 10))
    time.sleep_ms(300)
    _set_all(led.np, LED_COUNT, (0, 0, 0))

    # WiFi 连接
    ip = wifi.connect()
    if not ip:
        wifi_failure_blink(led)
        print("BOOT FAILED: no WiFi")
        return

    # 启动 server
    srv = TCPServer(TCP_PORT, TCP_BACKLOG, TCP_TIMEOUT_MS)

    # 初始显示（DISCONNECTED 状态）
    led.show(state)

    print(f"=== Ready (ip={ip} mode={state.mode} client={state.client}) ===")

    render_dirty = True  # 标记是否需要重新渲染

    while True:
        # ---- Boot 按钮 ----
        event = btn.check()
        if event == "click":
            if state.mode == MODE_DISCONNECTED:
                # 单击只在 DISCONNECTED 有效：循环切换 client
                idx = CLIENT_CYCLE.index(state.client)
                state.client = CLIENT_CYCLE[(idx + 1) % len(CLIENT_CYCLE)]
                state.temp_override = None
                print(f"BTN: click -> client={state.client}")
                render_dirty = True
            # CONNECTED / ACTIVE 模式下单击忽略
        elif event == "long":
            if state.mode != MODE_DISCONNECTED:
                # 长按：闪烁提示 + 断开 TCP + 回 DISCONNECTED
                _longpress_flash(led)
                print(f"BTN: long press, reset (was {state.mode})")
                _reset_to_disconnected(state, led)
                render_dirty = False  # reset_to_disconnected 已经渲染
            # DISCONNECTED 下长按忽略（已在等待状态）
        # 双击事件不再处理（已禁用）

        # ---- TCP 事件轮询（不阻塞，并行处理多 client）----
        # 1) 检查 server 有没有新连接（非阻塞 0ms timeout）
        srv.srv.settimeout(0)
        try:
            cl, addr = srv.srv.accept()
            cl.settimeout(0.05)  # 50ms recv timeout（够快响应）
            state.clients.append(cl)
            state.client_bufs[id(cl)] = b""  # 用 id(cl) 作 key（MicroPython socket 不支持属性）
            if state.mode == MODE_DISCONNECTED:
                state.mode = MODE_CONNECTED
                print(f"TCP: connected from {addr} (mode=connected)")
                render_dirty = True
            print(f"TCP: client {addr} (total={len(state.clients)})")
        except OSError as e:
            # EAGAIN 是正常的“暂时没新连接”，静默处理
            if str(e) != 'EAGAIN':
                print(f"TCP accept err: {e}")
        except Exception as e:
            print(f"TCP accept fatal: {e}")

        # 2) 检查每个已连接 client 有没有数据
        for cl in list(state.clients):
            try:
                data = cl.recv(256)
                if data is None or (isinstance(data, bytes) and not data):
                    # 客户端关闭
                    state.clients.remove(cl)
                    try:
                        cl.close()
                    except Exception:
                        pass
                    state.client_bufs.pop(id(cl), None)
                    print(f"TCP: client closed (total={len(state.clients)})")
                    if not state.clients and state.mode in (MODE_CONNECTED, MODE_ACTIVE):
                        state.mode = MODE_DISCONNECTED
                        # 保留 state.client 和 state.state（用户偏好：长按只断开 TCP）
                        state.temp_override = None
                        print(f"STATE: -> disconnected (all clients left)")
                        render_dirty = True
                    continue
                # 收到数据：处理
                key = id(cl)
                buf = state.client_bufs.get(key, b"")
                buf += data
                state.client_bufs[key] = buf
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    resp = handle_line(state, text)
                    if state.mode == MODE_CONNECTED:
                        state.mode = MODE_ACTIVE
                        print(f"STATE: -> active (first cmd: {text!r})")
                    try:
                        cl.send(resp.encode() + b"\n")
                    except Exception as e:
                        print(f"TCP send err: {e}")
                        break
                    print(f"<- {text} -> {resp}")
                    render_dirty = True
                state.client_bufs[key] = buf
            except OSError as e:
                # 可能是 timeout（没数据）或其他 IO 错误
                err_str = str(e)
                if 'ETIMEDOUT' in err_str or 'timed out' in err_str.lower():
                    pass  # 正常：暂时没数据
                else:
                    print(f"TCP recv err: {e}")
                    # 可能是连接错误，移除 client
                    try:
                        state.clients.remove(cl)
                    except ValueError:
                        pass
                    try:
                        cl.close()
                    except Exception:
                        pass
                    if not state.clients and state.mode in (MODE_CONNECTED, MODE_ACTIVE):
                        state.mode = MODE_DISCONNECTED
                        state.temp_override = None
                        print(f"STATE: -> disconnected (after err)")
                        render_dirty = True

        # ---- 渲染 ----
        # DISCONNECTED 需要每帧重渲染以处理闪烁
        if state.mode == MODE_DISCONNECTED or render_dirty:
            led.show(state)
            render_dirty = False

        # 防止忙循环，20ms 让出 CPU（闪烁在 led.show 里靠 ticks_ms 判断）
        time.sleep_ms(20)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        import sys
        sys.print_exception(e)