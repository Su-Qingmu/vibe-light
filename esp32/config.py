# config.py - Vibe Light ESP32 配置
# ============== WiFi ==============
WIFI_SSID = "SQM"
WIFI_PASS = "S20050201h"
WIFI_TIMEOUT_MS = 10000  # 连接超时

# ============== LED ==============
LED_PIN = 3             # WS2812 数据线 (U0RXD，启动后作普通 GPIO 用)
LED_COUNT = 24
DEFAULT_BRIGHTNESS = 50  # 0-100
LED_BAUDRATE = 400000   # WS2812 不支持改这个，固定

# 动画峰值亮度（用户外层亮度 0-100 还要乘这个）
PEAK = 0.50             # 全局动画峰值 50%（拖尾该暗就暗）

# 拖尾尾巴最低亮度（相对主色）
TRAIL_TAIL = 0.04

# ============== TCP Server ==============
TCP_PORT = 8888
TCP_BACKLOG = 2

# UDP 广播服务发现（断网也能找到 ESP32）
UDP_BROADCAST_PORT = 5000
UDP_BROADCAST_INTERVAL_MS = 1000
UDP_BROADCAST_ENABLED = True
UDP_BROADCAST_MSG = "vibe-light:v2"
# Subnet used as broadcast target. 192.168.x.255. Fallback to 255.255.255.255.
UDP_BROADCAST_ADDR = "255.255.255.255"
TCP_TIMEOUT_MS = 50     # accept timeout，让 button loop 能跑

# ============== Boot Button ==============
BOOT_PIN = 9
BTN_DEBOUNCE_MS = 50
BTN_LONGPRESS_MS = 1500  # 长按阈值
BTN_DOUBLECLICK_MS = 400 # 双击间隔

# ============== 颜色表 ==============
# 24 颗 LED 显示规则：
#   D2 (index 0) = 当前激活 client 颜色 (mode 控制：闪/亮/灭)
#   D3-D25 (index 1-23) = 当前状态颜色 / 动画
# 所有颜色先乘以 (brightness/100) 后输出

CLIENT_BASE = {
    "oc": (255, 0, 0),       # OpenClaw - 红
    "oo": (0, 0, 255),       # OpenCode - 蓝
    "cc": (255, 165, 0),     # Claude Code - 橙
}

# 动画驱动状态（这些走 LED._animate，其余用静态色）
ANIMATED_STATES = {
    "thinking", "coding",
    "busy", "waiting", "success",
    "error", "alarm",
}

# 各状态用于动画的固定主色（不依赖 client）
# 静态色 fallback 仍用 COLORS 表
ANIM_PALETTE = {
    "thinking": None,            # 用 HSV 彩虹
    "coding":   None,            # 用青→蓝渐变
    "busy":     (255, 180, 0),   # 用户定义：黄
    "waiting":  (255, 0, 0),     # 用户定义：红
    "success":  (0, 255, 0),     # 用户定义：绿
    "error":    (255, 40, 0),    # 用户定义：红→橙主色
    "alarm":    None,            # 红蓝交替
}

# (client, state) -> RGB (静态色 / 颜色覆写时用)
COLORS = {
    # ---- OpenClaw ----
    ("oc", "off"):        (0, 0, 0),
    ("oc", "idle"):       (51, 0, 0),
    ("oc", "thinking"):   (255, 200, 0),
    ("oc", "coding"):     (255, 80, 0),
    ("oc", "permission"): (200, 0, 255),
    ("oc", "error"):      (255, 0, 80),
    ("oc", "done"):       (0, 255, 0),
    ("oc", "busy"):       (255, 180, 0),
    ("oc", "waiting"):    (255, 0, 0),
    ("oc", "success"):    (0, 255, 0),
    ("oc", "alarm"):      (255, 0, 0),

    # ---- OpenCode ----
    ("oo", "off"):        (0, 0, 0),
    ("oo", "idle"):       (0, 0, 51),
    ("oo", "thinking"):   (255, 255, 0),
    ("oo", "busy"):       (0, 200, 255),
    ("oo", "permission"): (80, 0, 255),
    ("oo", "error"):      (255, 0, 255),
    ("oo", "done"):       (0, 255, 0),
    ("oo", "coding"):     (0, 100, 255),
    ("oo", "waiting"):    (255, 0, 0),
    ("oo", "success"):    (0, 255, 0),
    ("oo", "alarm"):      (255, 0, 0),

    # ---- Claude Code ----
    ("cc", "off"):        (0, 0, 0),
    ("cc", "idle"):       (51, 33, 0),
    ("cc", "thinking"):   (255, 255, 0),
    ("cc", "coding"):     (255, 100, 0),
    ("cc", "permission"): (200, 0, 255),
    ("cc", "error"):      (255, 0, 0),
    ("cc", "done"):       (0, 255, 0),
    ("cc", "question"):   (255, 0, 255),
    ("cc", "busy"):       (255, 180, 0),
    ("cc", "waiting"):    (255, 0, 0),
    ("cc", "success"):    (0, 255, 0),
    ("cc", "alarm"):      (255, 0, 0),
}

# 每个 client 允许的状态
CLIENT_STATES = {
    "oc": {"off", "idle", "thinking", "coding", "permission", "error", "done",
           "busy", "waiting", "success", "alarm"},
    "oo": {"off", "idle", "thinking", "busy", "permission", "error", "done",
           "coding", "waiting", "success", "alarm"},
    "cc": {"off", "idle", "thinking", "coding", "permission", "error", "done",
           "question", "busy", "waiting", "success", "alarm"},
}
