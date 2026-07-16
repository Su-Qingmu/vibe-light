#!/usr/bin/env python3
"""
测试所有 8 个动画状态,每个保持 10 秒
整个测试用单条 TCP 长连接(避免 ESP32 因 client 断开切回 DISCONNECTED)
"""
import socket
import time
import sys

ESP_IP = "192.168.0.236"
ESP_PORT = 8888
HOLD_SEC = 10

STATES = [
    ("thinking", "高速彩虹旋转"),
    ("coding",   "青→蓝渐变呼吸"),
    ("busy",     "黄色双向扫描"),
    ("waiting",  "红色呼吸"),
    ("success",  "绿色呼吸"),
    ("error",    "红橙快闪+抖动"),
    ("alarm",    "红蓝全闪"),
    ("off",      "全灭"),
]

class ConnLost(Exception):
    pass

def send_cmd(s, cmd):
    """在长连接上发一条命令,等服务器回复"""
    try:
        s.sendall((cmd + "\n").encode())
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        raise ConnLost(str(e))
    s.settimeout(2)
    try:
        resp = b""
        while b"\n" not in resp:
            chunk = s.recv(64)
            if not chunk:
                raise ConnLost("server closed")
            resp += chunk
        s.settimeout(None)
        return resp.decode().strip()
    except socket.timeout:
        s.settimeout(None)
        return "(timeout)"

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect((ESP_IP, ESP_PORT))
    except Exception as e:
        print(f"❌ 连不上 ESP32: {e}")
        sys.exit(1)
    print(f"✅ 长连接 {ESP_IP}:{ESP_PORT} 已建立\n")
    print("提示:整个测试过程中 socket 保持不断开,避免 ESP32 切 DISCONNECTED 模式")

    # 第一条命令会触发 CONNECTED → ACTIVE
    try:
        for i, (st, desc) in enumerate(STATES, 1):
            cmd = f"STATE oc.{st}"
            print(f"\n[{i}/8] {st:10} | {desc}")
            try:
                resp = send_cmd(s, cmd)
                print(f"        → {resp}")
            except ConnLost as e:
                print(f"        ⚠️ 连接断了({e}),用单连接重发…")
                try: s.close()
                except: pass
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((ESP_IP, ESP_PORT))
                resp = send_cmd(s, cmd)
                print(f"        → {resp} (重连)")

            print(f"        保持 {HOLD_SEC}s...")
            t0 = time.time()
            while time.time() - t0 < HOLD_SEC:
                time.sleep(0.1)
    finally:
        # 切到 off 收尾
        try:
            send_cmd(s, "STATE oc.off")
        except Exception:
            pass
        try: s.close()
        except: pass

    print("\n✅ 测试完成")

if __name__ == "__main__":
    main()
