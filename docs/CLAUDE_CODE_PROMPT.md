# Claude Code 接入 Prompt

把这个文件内容作为 prompt 喂给 Claude Code，让他自己想办法接入 vibe-light 灯效。

---

## 提示词（喂给 cc）

```
你的工作环境里有一个 ESP32 + 24 颗 WS2812 LED 的状态灯项目，叫 vibe-light。
项目仓库：https://github.com/Su-Qingmu/vibe-light

这是一个"AI 编程客户端状态指示灯"——通过 TCP 控制 LED 颜色和动画，
反映 AI 客户端（OpenClaw / OpenCode / Claude Code）的工作状态。

# 任务

让 Claude Code（你）的工作状态自动驱动这个 LED 灯效。

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
- `oo` = OpenCode
- `cc` = Claude Code（你）

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

# 关键限制

1. **必须用 client 前缀 `cc`**，不要用 `oc`（那是给 OpenClaw agent 用的）。
2. **每次断连 ESP32 都会切回 disconnected 模式**（D2 1Hz 闪，
   D3-D25 全灭）——所以一定要保持 TCP 长连接，否则灯不会完整显示动画。
3. **避免在每个 tool call 都发 STATE 命令**，会爆 token。
   推荐按"事件"触发，比如 UserPromptSubmit / PostToolUse / Stop 等。

# 你要选择的实现方式（任选其一或组合）

## 方案 A：用 Claude Code 的 hooks（推荐）

Claude Code 支持 hooks，在 `~/.claude/settings.json` 配置，事件触发时跑 shell 命令。
在以下事件触发时发 STATE 命令：
- `UserPromptSubmit` → `STATE cc.thinking`（用户刚发了消息）
- `PreToolUse` (Bash/Write/Edit) → `STATE cc.coding`（开始写代码）
- `SubagentStart` → `STATE cc.busy`（spawn 了子 agent）
- `Stop` / `UserPromptSubmit`（新一轮）→ `STATE cc.success`（上一轮完成）
- `PostToolUseFailure` / `ErrorEvent` → `STATE cc.error`（出错）

参考：
- 仓库已有一个 hooks 示例：`clients/claude/vibe-claude-hook.sh`
- Claude Code hooks 文档：~/.claude/ 目录

**注意**：cc 的 hooks 是基于 shell command 的，不能保持 TCP 长连接。
解决方案：
- 每次 hook 短连接发 STATE 后立即断开（接受 1 秒动画损失）
- 或者写一个 `cc-hook-daemon` 守护进程（用 unix socket 让 hooks 命令快速转 TCP 长连接）

## 方案 B：用 Claude Code 的 MCP / plugin 系统

更深入集成，能拿到更多事件。但复杂度高。

## 方案 C：自己写 daemon

写个 Python daemon，监听 Claude Code session 文件或事件，
自己维护 TCP 长连接发 STATE。

# 验证方法

实现后做这几步验证：

1. **连通性测试**：
   ```bash
   echo "PING" | nc 192.168.0.236 8888
   # 应该返回: PONG
   ```

2. **状态切换测试**：
   ```bash
   echo "STATE cc.thinking" | nc 192.168.0.236 8888
   # 应该返回: OK state=cc.thinking ANIM
   ```
   然后看 ESP32 上的灯，应该在 rainbow 旋转。

3. **模式验证**：
   ```bash
   echo "STATUS" | nc 192.168.0.236 8888
   # 应该看到 mode=active（不是 connected）
   ```
   如果显示 connected，说明你只是连上了，没发有效 STATE 切换到 active。

4. **hook 触发测试**：
   - 重启 Claude Code
   - 给 cc 发个简单消息（如"echo hello"）
   - 看灯是否变到 thinking，然后 coding，再 success

# 期望效果

实现成功后的用户体验：

| cc 状态 | 灯效 |
|---|---|
| 你刚开始思考用户消息 | thinking 彩虹 |
| 你刚写完一段代码 | coding 青蓝呼吸 |
| 跑长任务 / Spawn 子 agent | busy 双向扫描 |
| 任务成功完成 | success 绿色呼吸 |
| 工具调用失败 | error 红橙闪 |
| 长时间没活动 | off 全灭 |

# 报告

完成接入后，告诉我：
1. 你选了哪个实现方案（A / B / C）
2. 改了哪些文件
3. 怎么验证（命令清单）
4. 任何踩坑记录（特别是保持 TCP 长连接的 trick）

# 额外提示

- 仓库已经有个 `clients/claude/vibe-claude-hook.sh` shell 脚本案例，你可以参考
- 有个 `pi5/vibelight.py` Python TCP 客户端库可以复用
- ESP32 端代码在 `esp32/main.py`，协议定义在 `esp32/config.py`
- 你可以自己 fork、修改、提交 PR 到这个仓库
```

---

## 喂给 cc 的方式

### 方式 1：粘贴到 cc 的对话

直接把第一段（提示词）粘贴到 cc 的输入框，回车。

### 方式 2：放到 settings 里

```bash
# 把整个文件（含提示词）内容存到本地
# 然后在 ~/.claude/CLAUDE.md 里引用
echo "请阅读 /path/to/this/CLAUDE_CODE_PROMPT.md，按照里面的任务接入 vibe-light" >> ~/.claude/CLAUDE.md
```

### 方式 3：cc 自己启动时读

cc 启动时会自动加载 `~/.claude/CLAUDE.md`，
你把这段提示词的关键部分放到那里就行。

## 实现后会发生什么

cc 会：
1. 先 PING 测试连通
2. 选择一种实现方案
3. 写代码 + 改配置
4. 报告改了哪些文件

如果他搞不定，让他告诉我具体卡在哪。