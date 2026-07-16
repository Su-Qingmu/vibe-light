# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)

# webrepl 启动（如果未配置会抛异常，包好）
try:
    import webrepl
    webrepl.start()
    print("BOOT: webrepl started")
except Exception as e:
    print("BOOT: webrepl skipped:", e)

# Auto-start Vibe Light main loop (added 2026-07-16)
# 必须放到最后：import main 会执行 main() 进入死循环，boot.py 后续代码不会执行
print("BOOT: importing main...")
try:
    import main
    print("BOOT: main imported OK")
except Exception as e:
    print("BOOT: main.py auto-start FAILED:", e)
