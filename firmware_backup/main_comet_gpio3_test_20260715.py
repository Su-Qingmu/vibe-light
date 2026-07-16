"""
Comet effect for WS2812 (24 LEDs, GPIO 3):
- D2 (index 0) dim, D13 (index 11) bright, D14-D25 (indices 12-23) off
- Clockwise rotation, 2 seconds per revolution
"""

import machine
import neopixel
import time

LED_PIN = 3
LED_COUNT = 24
COMET_LEN = 12
MIN_BRIGHT = 5      # D2 暗端（几乎黑但还能看见）
MAX_BRIGHT = 255    # D13 亮端
DURATION_MS = 2000
CURVE_POWER = 2.0   # 渐变曲线（>1 让前半段更暗，后半段更亮）

np = neopixel.NeoPixel(machine.Pin(LED_PIN), LED_COUNT)


def render(offset):
    for i in range(LED_COUNT):
        pos = (i - offset) % LED_COUNT
        if pos < COMET_LEN:
            # 二次曲线渐变：暗端长，亮端短，渐变更明显
            t = pos / (COMET_LEN - 1)  # 0..1
            t = t ** CURVE_POWER
            b = MIN_BRIGHT + (MAX_BRIGHT - MIN_BRIGHT) * t
        else:
            b = 0
        np[i] = (0, int(b), 0)
    np.write()


print("Comet started. Pin=", LED_PIN, "LEDs=", LED_COUNT)
start = time.ticks_ms()

while True:
    now = time.ticks_ms()
    elapsed = time.ticks_diff(now, start)
    offset = (elapsed / DURATION_MS) * LED_COUNT
    render(offset)
    time.sleep_ms(20)