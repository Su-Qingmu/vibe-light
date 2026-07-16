"""
vibe_watcher.py
统一的状态监控守护进程

监控 /tmp/vibe/ 下各客户端写入的状态文件，把最新状态推送给 ESP32。

文件约定：
  /tmp/vibe/claude-{session_id}.json     -> client=cc
  /tmp/vibe/opencode-current.json         -> client=oo
  /tmp/vibe/openclaw-{session_key}.json   -> client=oc

每个文件内容：
  {"client": "cc", "state": "thinking", "event": "UserPromptSubmit", "ts": 1700000000, "session": "..."}

聚合策略（按 client 分组）：
  - 多个 cc session：取最近更新的那个 state
  - 若最近 30s 内没有任何 session 更新 client 的状态，回退到 idle
"""
import json
import time
import os
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 复用 vibelight.py 的客户端
sys.path.insert(0, str(Path(__file__).parent))
from vibelight import VibeLightClient, load_config, get_host

STATE_DIR = Path("/tmp/vibe")
IDLE_TIMEOUT_S = 30  # 30s 没更新 → idle


class StateAggregator:
    """聚合多个 session 的状态"""

    def __init__(self):
        # client -> { session -> (state, ts) }
        self.states = {"oc": {}, "oo": {}, "cc": {}}

    def update(self, client, session, state, ts):
        if client not in self.states:
            return
        if session:
            self.states[client][session] = (state, ts)
        else:
            # opencode 单 session
            self.states["oo"]["current"] = (state, ts)

    def current(self, client):
        """返回 client 当前应该显示的状态"""
        now = time.time()
        sessions = self.states.get(client, {})
        if not sessions:
            return "off" if client == "oc" else "idle"

        # 取最近更新的 session，过滤超时的
        latest = None
        for sess, (state, ts) in sessions.items():
            if now - ts > IDLE_TIMEOUT_S:
                continue  # 过期
            if latest is None or ts > latest[1]:
                latest = (sess, state, ts)

        if latest:
            return latest[1]
        return "off" if client == "oc" else "idle"

    def load_file(self, path: Path):
        try:
            data = json.loads(path.read_text())
            client = data.get("client")
            state = data.get("state", "idle")
            ts = data.get("ts", time.time())
            session = data.get("session", "")
            self.update(client, session, state, ts)
        except Exception as e:
            print(f"WARN: cannot parse {path}: {e}", file=sys.stderr)

    def load_all(self):
        if not STATE_DIR.exists():
            return
        for p in STATE_DIR.iterdir():
            if p.suffix == ".json" and p.name.startswith(("claude-", "openclaw-", "opencode-")):
                self.load_file(p)


class FileWatcher(FileSystemEventHandler):
    def __init__(self, agg, callback):
        self.agg = agg
        self.callback = callback

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_modified(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def _handle(self, path: Path):
        if not path.name.startswith(("claude-", "openclaw-", "opencode-")):
            return
        if path.suffix != ".json":
            return
        self.agg.load_file(path)
        self.callback()


def main():
    host = get_host()
    if not host:
        print("No ESP32 host configured. Run 'vibelight --discover' first.", file=sys.stderr)
        sys.exit(2)

    print(f"Vibe watcher starting, target ESP32: {host}")
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    agg = StateAggregator()
    agg.load_all()

    client = VibeLightClient(host, timeout=1.0)

    last_sent = {"oc": None, "oo": None, "cc": None}

    def push_updates():
        # 三个 client 都查一遍
        for c in ["oc", "oo", "cc"]:
            cur = agg.current(c)
            if cur != last_sent[c]:
                cmd = f"STATE {c}.{cur}"
                resp = client._send(cmd)
                print(f"PUSH {cmd} -> {resp}")
                last_sent[c] = cur

    # 启动时先 push 一次
    push_updates()

    # 文件监控
    handler = FileWatcher(agg, push_updates)
    observer = Observer()
    observer.schedule(handler, str(STATE_DIR), recursive=False)
    observer.start()

    print(f"Watching {STATE_DIR} ...")

    # 主循环：每 5s 检查 idle timeout
    try:
        while True:
            time.sleep(5)
            push_updates()
    except KeyboardInterrupt:
        observer.stop()
        print("\nBye")


if __name__ == "__main__":
    main()