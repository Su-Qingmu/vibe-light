# OpenCode 接入 Prompt

把这个文件内容作为 prompt 喂给 OpenCode（OpenCode AI coding agent），
让他自己改进 `clients/opencode/vibe-light.ts` 接入 vibe-light 灯效。

---

## 提示词（喂给 OpenCode）

```
你的工作环境里有一个 ESP32 + 24 颗 WS2812 LED 的状态灯项目，叫 vibe-light。
项目仓库：https://github.com/Su-Qingmu/vibe-light

这是一个"AI 编程客户端状态指示灯"——通过 TCP 控制 LED 颜色和动画，
反映 AI 客户端（OpenClaw / OpenCode / Claude Code）的工作状态。

# 任务

OpenCode 已经有了一个 vibe-light plugin 雏形在 `clients/opencode/vibe-light.ts`，
但是有几个问题需要改进：

## 现有实现的问题（先读一下文件）

1. **每次发 STATE 命令都新建 TCP 连接** (`net.createConnection` 在 sendCommand 里)
   - ESP32 收到新连接后从 disconnected → connected
   - 命令处理完 → active
   - socket 关闭 → 切回 disconnected
   - 结果：动画只在命令那一帧被触发，**之后马上被拉回 disconnected**（D2 1Hz 闪，D3-D25 全黑）
   - **正确做法：保持长连接**

2. **客户端前缀用 `oc`**，应该是 `oo`（OpenCode 的前缀）

3. **状态映射太粗**：
   - `message.updated` → `thinking`，但应该是 `thinking` 然后切到 `coding`
   - `tool.execute.before` → `coding`，但如果是长工具（如 Bash）应该是 `busy`
   - 缺少工具耗时判断逻辑

4. **没有错误处理**：如果 ESP32 不在，写 JSON 到 /tmp/vibe 没问题，但 sendCommand 应该 silent 失败 + 重试

# 必备信息

## ESP32 端点
- IP: 192.168.0.236（WiFi SQM 网络）
- TCP 端口: 8888
- MicroPython v1.28，服务器监听 TCP，每条命令以 `\n` 结尾，回复也是

## 协议（行式文本协议）

```
PING                       → PONG
STATE <client>.<state>     → OK state=<client>.<state> ANIM
CLIENT <oc|oo|cc>          → OK client=<client>
BRIGHT <0-100>             → OK bright=<n>%
COLOR <r> <g> <b>          → OK override=<r>,<g>,<b>
STATUS                     → {"client":"...","state":"...","mode":"...",...}
HELP                       → 帮助文本
```

`<client>` 前缀：
- `oc` = OpenClaw
- `oo` = OpenCode（你就是这个）
- `cc` = Claude Code

`<state>` 允许的值和含义：
| state | 含义 | LED 动画 |
|---|---|---|
| thinking | 正在思考 | 高速彩虹旋转（顺时针）|
| coding   | 正在写代码 | 青→蓝液态渐变呼吸 |
| busy     | 并发/长任务执行中 | 黄色双向 comet 扫描 |
| waiting  | 等用户输入 | 红色呼吸 |
| success  | 任务完成 | 绿色呼吸 |
| error    | 报错/工具失败 | 红橙快闪 + 抖动 |
| alarm    | 严重错误 | 红蓝快闪 |
| off      | 灯灭 | 全黑 |

## OpenCode Plugin 关键知识

- 官方文档：https://opencode.ai/docs/plugins
- 事件列表：https://opencode.ai/docs/plugins#events
- 文件位置（用户级）：`~/.config/opencode/plugins/vibe-light.ts`
- 文件位置（项目级）：`<project>/.opencode/plugin/vibe-light.ts`
- Plugin SDK import：`import type { Plugin } from "@opencode-ai/plugin"`
- 事件订阅：通过 `event: async ({ event }) => {...}` hook

# 关键限制

1. **必须用 client 前缀 `oo`**，不是 `oc`、不是 `cc`。
2. **必须保持 TCP 长连接**到 ESP32，每次新建 socket 会让灯在 disconnected 模式闪烁。
3. **不要在每个事件都发 STATE**，爆 token + 让灯一直在切换。
   推荐：聚合状态（如 message.updated → thinking；tool.execute.before Bash → coding；tool.execute.after 长时间 → busy）。
4. **出错要 silent**：vibe-light 不能用会让 opencode 崩溃。

# 推荐实现方向

## 维护一个 TCP 长连接管理器

```typescript
// 伪代码
class VibeLightClient {
  private sock: net.Socket | null = null;
  private host: string;
  private port: number;

  constructor(host: string, port: number) {
    this.host = host;
    this.port = port;
  }

  async connect(): Promise<void> {
    if (this.sock && !this.sock.destroyed) return;
    return new Promise((resolve, reject) => {
      this.sock = net.createConnection({ host: this.host, port: this.port });
      this.sock.once("connect", () => resolve());
      this.sock.once("error", (e) => reject(e));
    });
  }

  async send(cmd: string): Promise<string> {
    await this.connect();
    return new Promise((resolve, reject) => {
      this.sock!.write(cmd + "\n");
      // 等待响应
      const buf: Buffer[] = [];
      const onData = (chunk: Buffer) => {
        buf.push(chunk);
        const str = Buffer.concat(buf).toString();
        if (str.includes("\n")) {
          this.sock!.off("data", onData);
          resolve(str.trim());
        }
      };
      this.sock!.on("data", onData);
      setTimeout(() => {
        this.sock!.off("data", onData);
        reject(new Error("timeout"));
      }, 2000);
    });
  }
}

const client = new VibeLightClient("192.168.0.236", 8888);
```

**关键**：
- 连接断开要重连（指数退避）
- 错误要 silent catch，不能让 plugin 崩溃
- 可以加 ping/keepalive（定时 PING 防 ESP32 主动断开）

## 状态机优化

考虑这些规则：
- `tool.execute.before` (Bash + 命令耗时 > 1s) → busy
- `tool.execute.before` (Write/Edit/Read) → coding
- `message.updated` + 用户消息 → thinking
- `message.updated` + assistant 消息 → thinking
- 工具完成后 → 回到上一个非 busy 状态
- 多个事件去重，相同状态 5s 内不重复发
- 出错 → error 持续显示直到新事件覆盖

## 写入 /tmp/vibe/opencode-current.json

仍然需要保留：
```json
{"client": "oo", "state": "<state>", "event": "<event>", "ts": <timestamp>}
```

让 Pi5 端的 `vibe_watcher.py`（如果用户跑了的话）能监控。

# 验证方法

实现后做这几步验证：

1. **连通性测试**：
   ```bash
   echo "PING" | nc 192.168.0.236 8888
   # 应该返回: PONG
   ```

2. **状态切换测试**（保持连接看动画）：
   用一个简单的 Node 脚本：
   ```javascript
   const net = require("net");
   const s = net.createConnection(8888, "192.168.0.236");
   s.on("connect", () => {
     s.write("STATE oo.thinking\n");
   });
   setTimeout(() => s.write("STATUS\n"), 1000);
   setTimeout(() => s.end(), 3000);
   ```
   跑这个脚本，观察 ESP32 上的灯：
   - 应该进入 thinking（彩虹旋转）
   - 1s 后 STATUS 返回 `mode: active`（不是 connected）
   - 3s 后 socket 关闭，灯回到 disconnected

3. **opencode 集成测试**：
   - 把 vibe-light.ts 复制到 `~/.config/opencode/plugins/`
   - 重启 OpenCode
   - 给 OpenCode 发个简单消息（如"echo hello"）
   - 看灯是否从 disconnected（等待） → thinking → coding → success

4. **长连接测试**：
   - 让 OpenCode 处理一个长任务（>5s）
   - 灯应该进 busy（双向扫描）整个过程中保持
   - 不是一闪就回 disconnected

# 期望效果

实现成功后的用户体验：

| OpenCode 状态 | 灯效 |
|---|---|
| 等用户输入（idle） | D2 1Hz 闪（disconnected） |
| 收到用户消息 | thinking 彩虹 |
| 写代码 / 编辑文件 | coding 青蓝呼吸 |
| 跑长 Bash / 长任务 | busy 双向扫描 |
| 工具成功完成 | coding / success |
| 工具失败 | error 红橙闪 |
| 用户停止会话 | off 全灭 |

# 文件改动点

主要改 `clients/opencode/vibe-light.ts`：
1. 引入 VibeLightClient 长连接管理器
2. 修正 client 前缀为 `oo`
3. 优化状态映射规则
4. 加错误处理 + 重连
5. 加状态去抖（5s 内不重发同 state）
6. 写测试脚本 `clients/opencode/test-integration.ts`

# 报告

完成接入后，告诉我：
1. 改了哪些函数 / 文件
2. 长连接管理器怎么实现的（reconnect 策略）
3. 状态去抖的逻辑
4. 怎么验证（命令清单）
5. 任何踩坑记录
```

---

## 喂给 OpenCode 的方式

### 方式 1：粘贴到 OpenCode 对话框

直接把第一段（提示词）粘贴到 OpenCode 输入框，回车。

### 方式 2：写到项目 .opencode/ 系统 prompt

```bash
# 在 vibe-light 项目根目录的 .opencode/ 下加系统 prompt
mkdir -p /path/to/vibe-light/.opencode/
echo "请阅读 ./clients/opencode/PROMPT.md，按照里面的任务改进 vibe-light.ts" \
    > /path/to/vibe-light/.opencode/SYSTEM.md
```

OpenCode 启动会自动加载。

### 方式 3：OpenCode CLI 启动参数

```bash
opencode --system-prompt "$(cat PROMPT.md)"
```

## 实现后会发生什么

OpenCode 会：
1. 先 PING 测试连通
2. 读现有 `vibe-light.ts` 找问题
3. 重写为长连接管理器
4. 加状态去抖 + 错误处理
5. 写测试验证
6. 报告改动

如果他卡了，把他的问题转给我——我可以：
- 帮他看 ESP32 端的状态机确认 mode 切换逻辑
- 排查长连接重连问题
- 帮他写测试脚本