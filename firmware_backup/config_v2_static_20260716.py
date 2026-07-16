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

# ============== TCP Server ==============
TCP_PORT = 8888
TCP_BACKLOG = 2
TCP_TIMEOUT_MS = 50     # accept timeout，让 button loop 能跑

# ============== Boot Button ==============
BOOT_PIN = 9
BTN_DEBOUNCE_MS = 50
BTN_LONGPRESS_MS = 1500  # 长按阈值
BTN_DOUBLECLICK_MS = 400 # 双击间隔

# ============== 颜色表 ==============
# 24 颗 LED 显示规则：
#   D2 (index 0) = 当前激活 client 颜色
#   D3-D25 (index 1-23) = 当前状态颜色
# 所有颜色先乘以 (brightness/100) 后输出

CLIENT_BASE = {
    "oc": (255, 0, 0),       # OpenClaw - 红
    "oo": (0, 0, 255),       # OpenCode - 蓝
    "cc": (255, 165, 0),     # Claude Code - 橙
}

# (client, state) -> RGB
COLORS = {
    # ---- OpenClaw ----
    ("oc", "off"):        (0, 0, 0),
    ("oc", "idle"):       (51, 0, 0),        # 暗红 (1/5 client 色，让 D2 client 色突出)
    ("oc", "thinking"):   (255, 200, 0),     # 黄
    ("oc", "coding"):     (255, 80, 0),      # 橙红
    ("oc", "permission"): (200, 0, 255),     # 紫
    ("oc", "error"):      (255, 0, 80),      # 紫红
    ("oc", "done"):       (0, 255, 0),       # 绿

    # ---- OpenCode ----
    ("oo", "off"):        (0, 0, 0),
    ("oo", "idle"):       (0, 0, 51),        # 暗蓝 (1/5 client 色)
    ("oo", "thinking"):   (255, 255, 0),     # 黄
    ("oo", "busy"):       (0, 200, 255),     # 青蓝
    ("oo", "permission"): (80, 0, 255),      # 紫蓝
    ("oo", "error"):      (255, 0, 255),     # 品红
    ("oo", "done"):       (0, 255, 0),       # 绿

    # ---- Claude Code ----
    ("cc", "off"):        (0, 0, 0),
    ("cc", "idle"):       (51, 33, 0),       # 暗橙 (1/5 client 色)
    ("cc", "thinking"):   (255, 255, 0),     # 黄
    ("cc", "coding"):     (255, 100, 0),     # 深橙
    ("cc", "permission"): (200, 0, 255),     # 紫
    ("cc", "error"):      (255, 0, 0),       # 红
    ("cc", "done"):       (0, 255, 0),       # 绿
    ("cc", "question"):   (255, 0, 255),     # 品红
}

# 每个 client 允许的状态
CLIENT_STATES = {
    "oc": {"off", "idle", "thinking", "coding", "permission", "error", "done"},
    "oo": {"off", "idle", "thinking", "busy", "permission", "error", "done"},
    "cc": {"off", "idle", "thinking", "coding", "permission", "error", "done", "question"},
}