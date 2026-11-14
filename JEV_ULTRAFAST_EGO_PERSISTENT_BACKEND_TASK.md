# Jev Ultrafast × Ego Browser — Persistent Runtime Backend 开发任务文档

> 版本：V1  
> 日期：2026-09-22  
> 基线仓库：https://github.com/browser-use/jev-ultrafast  
> Ego Browser：https://github.com/citrolabs/ego-lite  
> 目标：在 **不破坏原 Browser Harness backend** 的前提下，为 `jev-ultrafast` 增加 Ego Browser backend，并直接采用 **单任务生命周期内 persistent runtime**，同时补齐性能指标采集与真实运行报告。

---

## 0. 先纠正一个关键认识

原版 `jev-ultrafast` **并不会每次 Jev 决策都重新打开浏览器**。

当前 upstream 的 `browser.py` 明确采用：

- 一个 Browser Harness / CDP session；
- Agent 生命周期内复用；
- 不为每一步创建 subprocess。

因此，所谓“性能陷阱”并不是原 repo 自带的问题，而是 **如果把 Ego Browser 错误接成“每次 observe/click/fill 都重新启动一次 `ego-browser` CLI / Node Runtime”**，才会人为产生：

```text
Jev 很快
  ↓
但每一步都重新：
启动 CLI
→ 建 Bridge
→ 建 Runtime
→ 执行动作
→ 退出
  ↓
固定启动成本反而吞掉 Jev 的速度优势
```

本任务直接避免这条错误路径。

---

# 1. 本次开发目标

一次性完成以下内容，不拆阶段：

1. 保留 upstream 原始 Browser Harness backend；
2. 新增 Ego Browser backend；
3. Ego backend 使用 **task-scoped persistent runtime**：
   - Agent / 验收任务开始时启动；
   - 整个任务期间复用同一个 `ego-browser nodejs` CLI Bridge / Node Runtime；
   - 任务结束后立即退出；
   - 不实现系统级永久 daemon；
4. 保持 Jev-ultrafast 原有：
   - indexed action space；
   - operation + target 决策；
   - stale protection；
   - text helper；
   - DONE / BLOCKED；
   - 原有 Browser Harness 使用方式；
5. 增加完整运行指标：
   - 总耗时；
   - Jev 调用次数；
   - 操作成功率；
   - stale 次数；
   - Ego 操作耗时；
6. 完成后实际使用 Ego backend 连续运行若干次浏览器任务，输出每次指标，**只提供原始数据，不替用户做最终性能结论**。

---

# 2. 非目标 / 禁止事项

本任务不要做以下事情：

- 不删除或覆盖 upstream `Browser` / Browser Harness 能力；
- 不把 Ego Browser 设成唯一 backend；
- 不把 `ego-browser` 做成开机自启；
- 不做全局常驻服务；
- 不允许 Agent 退出后遗留 `ego-browser nodejs` 子进程；
- 不允许 Jev 直接输出 CSS selector、坐标、JavaScript 或 shell command 后执行；
- 不绕过 indexed action space；
- 不为了 Ego 重写 Jev 决策逻辑；
- 不改变 `TYPESAFE_API_KEY` 的安全存储方式；
- 不把 API key 写入日志、trace、测试产物或 Git；
- 不为了 benchmark 删除 freshness / stale / validation 保护。

---

# 3. 目标架构

```text
                         Agent
                           │
                           │ browser backend interface
                           ▼
               ┌─────────────────────────┐
               │      BrowserBackend     │
               └───────────┬─────────────┘
                           │
             ┌─────────────┴─────────────┐
             ▼                           ▼
┌────────────────────────┐   ┌─────────────────────────┐
│ BrowserHarnessBackend  │   │    EgoBrowserBackend    │
│                        │   │                         │
│ upstream behavior      │   │ Persistent Runtime      │
│ one CDP session        │   │ one CLI Bridge/runtime  │
└────────────┬───────────┘   └────────────┬────────────┘
             │                            │
             ▼                            ▼
          Chrome                    Ego Browser
                                          │
                                          ▼
                                     Task Space
```

Jev 层不应该知道当前实际使用哪个浏览器 backend。

对 Jev 来说，两种 backend 都只提供统一的：

```text
observe()
fresh()
act()
close()
```

---

# 4. Backend 抽象要求

建议新增：

```text
jev_ultrafast/backends/
    __init__.py
    base.py
    browser_harness.py
    ego.py
```

如果为了最小改动，也可以保留原 `browser.py` 并向外包装，但必须满足：

```python
class BrowserBackend(Protocol):
    def observe(self, screenshot: bool = False) -> dict: ...
    def fresh(self, page: dict) -> bool: ...
    def act(self, action: dict, page: dict, text: str | None = None) -> None: ...
    def close(self) -> None: ...
```

要求：

- `Agent` 不再硬编码 `Browser(url)`；
- 通过 backend factory / 参数创建 backend；
- 原 backend 仍然是默认行为，保证 upstream 使用者不改代码也能运行。

建议兼容：

```python
Agent(url, goal)
```

仍等价于：

```python
Agent(url, goal, backend="browser_harness")
```

新增：

```python
Agent(url, goal, backend="ego")
```

也允许环境变量：

```bash
ULTRAFAST_BROWSER_BACKEND=ego
```

优先级建议：

```text
显式 Agent 参数
>
环境变量
>
browser_harness 默认值
```

---

# 5. Ego Persistent Runtime 设计

## 5.1 生命周期

persistent 的含义只允许是：

```text
一次 Agent 任务
```

不是：

```text
整个操作系统生命周期
```

生命周期必须严格如下：

```text
Agent start
  ↓
spawn `ego-browser nodejs`
  ↓
建立 CLI Bridge + Node Runtime
  ↓
创建/复用本任务 Task Space
  ↓
observe / act / observe / act ...
  ↓
DONE / BLOCKED / exception / user interrupt
  ↓
close()
  ↓
正常请求 runtime 退出
  ↓
等待
  ↓
必要时 terminate
  ↓
必要时 kill
  ↓
确认无子进程残留
```

必须使用 `try/finally` 或上下文管理器保证清理。

---

## 5.2 不允许的实现

禁止：

```text
observe:
    subprocess.run("ego-browser nodejs ...")

act:
    subprocess.run("ego-browser nodejs ...")

observe:
    subprocess.run("ego-browser nodejs ...")
```

因为这会形成 per-step runtime startup。

---

## 5.3 推荐实现

在 Ego backend 初始化时启动一次：

```bash
ego-browser nodejs
```

保持该进程直到 `close()`。

Python backend 与 REPL/Runtime 间使用 **明确的 request/response framing**，不要依赖模糊的人类终端输出。

建议每个请求包含：

```json
{
  "request_id": "uuid-or-monotonic-id",
  "op": "observe|click|fill|select|scroll|wait|fresh",
  "payload": {}
}
```

输出必须带唯一边界，例如：

```text
__ULTRAFAST_RESULT__<request_id>__{json}
```

解析层只接受：

- request_id 匹配；
- JSON 可解析；
- schema 合法；

其他 REPL 提示、warning、console noise 不作为结果。

如果当前 Ego REPL 对多行代码不稳定：

- 将每次请求压成单行 JS；
- 或在 Runtime 初始化时先安装一组 helper 到 global scope；
- 后续仅发送极短函数调用。

不要因此退回“每步重启 CLI”。

---

# 6. Ego Task Space

整个 Agent 任务必须复用同一个 Ego Task Space。

建议：

```text
ultrafast:<run_id>
```

初始化时：

```javascript
useOrCreateTaskSpace(...)
```

同一个任务内：

- 不重复创建 Space；
- 不重复登录；
- 不重复初始化 Browser；
- observe / act 都在同一 Task Space 与 Tab 上执行。

任务结束后：

- Runtime 必须退出；
- Task Space 是否保留由 Ego API 的既有语义决定；
- 不要为了释放 Runtime 擅自删除用户 Profile / 登录状态。

---

# 7. Observation / Indexed Action Space

目标不是让 Ego 自己变成 Agent。

Ego 只负责提供浏览器状态与执行动作。

流程：

```text
Ego snapshot
   ↓
Adapter normalize
   ↓
Jev-ultrafast element table
   ↓
[1] button ...
[2] textbox ...
[3] combobox ...
   ↓
Jev
```

Ego 原生 ref：

```text
@21
@38
@51
```

不要直接暴露给 Jev。

建立本轮 observation mapping：

```text
Jev index 1 -> Ego @21
Jev index 2 -> Ego @38
Jev index 3 -> Ego @51
```

mapping 必须属于：

```text
当前 observation generation
```

新 snapshot 后旧 mapping 失效。

---

# 8. Action 映射

至少一次性实现原 Jev-ultrafast 当前动作集：

```text
CLICK
TYPE_TEXT
SELECT
SCROLL_UP
SCROLL_DOWN
WAIT
DONE
BLOCKED
```

建议映射：

```text
CLICK       -> Ego click(ref)
TYPE_TEXT   -> Ego fillInput(ref, text)
SELECT      -> Ego 对应 select helper / safe native select 路径
SCROLL_UP   -> Ego scrollBy(...)
SCROLL_DOWN -> Ego scrollBy(...)
WAIT        -> runtime 内短等待
DONE        -> 不执行浏览器动作
BLOCKED     -> 不执行浏览器动作
```

不要让模型生成 Ego JS。

JS 只能由 backend 中预定义模板产生。

---

# 9. Stale / Freshness 语义

必须保留 stale protection。

最低要求：

1. action 必须来自最近一次 observation；
2. 每个 observation 有 generation/fingerprint；
3. action 携带 observation generation；
4. 新 observation 后旧 action 不可执行；
5. Ego 出现：
   - unknown ref；
   - ref invalid；
   - tab/page 已改变；
   - target 已不存在；
   - 其他等价的 stale 状态；

   统一转换为：

```python
StalePage
```

然后沿用原 Agent loop：

```text
StalePage
  ↓
丢弃本轮 decision
  ↓
重新 observe
  ↓
重新调用 Jev
```

不要把 stale 当普通失败后继续点击。

---

# 10. Runtime 清理要求

Ego backend 至少实现三级退出：

```text
1. graceful exit
2. terminate
3. kill
```

示意：

```python
def close():
    if already_closed:
        return

    try:
        request_graceful_exit()
        wait(timeout=...)
    except:
        pass

    if process_alive:
        terminate()
        wait(timeout=...)

    if process_alive:
        kill()
        wait()

    clear_handles()
```

必须处理：

- 正常 DONE；
- BLOCKED；
- Jev API 报错；
- text helper 报错；
- Ego 报错；
- Python exception；
- Ctrl-C / KeyboardInterrupt；
- context manager 退出。

要求 `close()` 幂等。

---

# 11. API Key 配置

## 11.1 Jev / TypeSafe

当前 upstream 使用：

```env
TYPESAFE_API_KEY=
TYPESAFE_MODEL=jev-latest
```

Jev API endpoint 当前为：

```text
https://api.typesafe.ai/v1/systemone
```

API Key 从 TypeSafe dashboard 获取：

```text
https://console.typesafe.ai
```

不要新增：

```bash
--api-key xxx
```

这种默认方式，避免 key 留在 shell history / process list。

继续使用 `.env` / 环境变量。

---

## 11.2 TYPE_TEXT helper

Jev 自己只负责结构化决策，不负责自由文本生成。

当动作是：

```text
TYPE_TEXT
```

仍需要：

```env
TEXT_MODEL_API_KEY=
TEXT_MODEL_BASE_URL=
TEXT_MODEL=
TEXT_MODEL_REASONING=
```

当前 upstream `.env.example` 为：

```env
TYPESAFE_API_KEY=
TYPESAFE_MODEL=jev-latest

TEXT_MODEL_API_KEY=
TEXT_MODEL_BASE_URL=https://openrouter.ai/api/v1
TEXT_MODEL=inception/mercury-2.5
TEXT_MODEL_REASONING=none
```

本任务不要把 Jev key 与 text helper key 混成一个变量。

---

## 11.3 启动前检查

新增轻量 preflight：

必须检查：

```text
TYPESAFE_API_KEY 是否存在
backend 是否可用
如果 backend=ego：
    ego-browser 是否可执行
    Ego Browser 是否可连接
```

`TEXT_MODEL_API_KEY`：

- 不要把 key 输出到日志；
- 如果配置存在，正常使用；
- 如果任务运行到 `TYPE_TEXT` 而 key 缺失，要给出明确错误 / BLOCKED 信息；
- 不允许静默猜测文本。

可选：启动时只显示：

```text
Jev API: configured
Text helper: configured / missing
Backend: ego
```

绝不显示 key 内容。

---

# 12. 性能指标

本次必须新增统一 metrics，不只给 Ego 用。

建议结构：

```python
metrics = {
    "backend": "...",
    "wall_elapsed_ms": 0,
    "policy_elapsed_ms": 0,
    "jev_calls": 0,
    "browser_actions_attempted": 0,
    "browser_actions_succeeded": 0,
    "action_success_rate": 0.0,
    "stale_count": 0,
    "backend_operation_ms": 0,
    "ego_operation_ms": 0,
}
```

## 12.1 用户要求的五项必须输出

### A. 总耗时

至少输出：

```text
wall_elapsed_ms
```

从：

```text
backend / Agent 开始启动
```

到：

```text
DONE / BLOCKED + 最后一次必要浏览器操作完成
```

另外保留 upstream 可比口径：

```text
policy_elapsed_ms
```

从 initial observation 后开始计时。

这样用户既能看真实端到端速度，也能与 upstream published timing 对比。

---

### B. Jev 调用次数

定义：

```text
每次 choose() / TypeSafe System One request = 1
```

输出：

```text
jev_calls
```

stale 后重新预测产生的新 request 继续计数。

---

### C. 操作成功率

定义：

```text
browser_actions_succeeded / browser_actions_attempted
```

只统计真正发给 browser backend 的动作：

- CLICK
- TYPE_TEXT
- SELECT
- SCROLL
- WAIT（如确实发给 backend）

DONE / BLOCKED 不算浏览器动作。

stale 导致动作被拒绝时：

- attempted +1（仅当已经进入执行尝试）；
- succeeded 不增加；
- stale_count +1。

如果 stale 在执行前 freshness check 就被发现，则：

- stale_count +1；
- 不计为 browser action attempted。

报告中必须说明采用哪一种边界。

---

### D. stale 次数

所有：

```python
StalePage
```

以及从 Ego 转译过来的：

```text
unknown ref
invalid ref
stale generation
page changed before execution
```

统一累计。

输出：

```text
stale_count
```

---

### E. Ego 操作耗时

Ego backend 每次 request 单独计时。

至少区分：

```text
ego_observe_ms
ego_act_ms
ego_fresh_ms
ego_total_operation_ms
```

最终用户要求的：

```text
Ego 操作耗时
```

以：

```text
ego_total_operation_ms
```

为主。

同时报告：

```text
平均 observe
平均 act
最大单次 Ego operation
```

方便发现异常。

---

# 13. 建议补充但不要取代五项核心指标

可以额外记录：

```text
backend_startup_ms
backend_cleanup_ms
text_helper_calls
text_helper_total_ms
jev_total_latency_ms
actions_by_kind
run_status
error_type
```

这些只作为诊断，不要让最终报告失焦。

---

# 14. Metrics 输出

每次 run 结束写：

```text
recordings/
  <run-id>/
    metrics.json
    trace.json
```

`metrics.json` 示例：

```json
{
  "backend": "ego",
  "status": "done",
  "wall_elapsed_ms": 4231,
  "policy_elapsed_ms": 3810,
  "jev_calls": 8,
  "browser_actions_attempted": 7,
  "browser_actions_succeeded": 7,
  "action_success_rate": 1.0,
  "stale_count": 1,
  "ego_total_operation_ms": 1023
}
```

不要写：

- API key；
- Authorization header；
- Cookie；
- 用户密码；
- 全量敏感页面文本。

---

# 15. CLI / 使用方式

保留 upstream：

```bash
uv run jev
```

原 backend 默认正常工作。

新增 backend 选择，例如：

```bash
ULTRAFAST_BROWSER_BACKEND=ego \
uv run --env-file .env python examples/run.py \
  --url https://example.com \
  --goal "..."
```

如果项目已有 argparse，允许：

```bash
--backend ego
```

但必须保证：

```text
不传 --backend
```

时行为与 upstream 一致。

---

# 16. 真实运行与验收

开发完成后，不要只跑 unit test。

必须实际通过 Ego Browser backend 完成至少 **3 次独立 live run**。

要求：

- 使用相同代码；
- 每次都真正调用 Jev；
- 每次都真正操作 Ego Browser；
- 每次任务结束确认 persistent runtime 已关闭；
- 不人工修改结果；
- 失败也要记录，不只保留成功样本。

优先选择：

1. upstream Wikipedia 示例；
2. upstream 可稳定复现的简单公开网页任务；
3. Google Flights 示例如果当前网页状态允许稳定运行。

不要为了追求漂亮数字把失败 run 删除。

---

# 17. 每次 live run 必须报告

格式固定：

```text
Run #1

Backend:
Status:

总耗时:
policy elapsed:
Jev 调用次数:
操作成功率:
stale 次数:
Ego 操作耗时:

Ego observe:
Ego act:
Jev latency:
Text helper latency:

Runtime cleanup:
- graceful exit: yes/no
- forced terminate: yes/no
- orphan process remaining: yes/no
```

至少连续给出：

```text
Run #1
Run #2
Run #3
```

最后给：

```text
median
min
max
```

但 **不要替用户判断 Ego 一定更快或更慢**。

用户自己与之前浏览器运行数据比较。

---

# 18. 原 backend 回归测试

必须验证：

```text
backend=browser_harness
```

仍然可运行。

至少：

- unit tests 通过；
- upstream `examples/run.py` 路径仍兼容；
- 未要求 Ego 安装的用户不应该因为 import Ego backend 而启动失败；
- Ego 依赖必须 lazy-load；
- `ego-browser` 不存在时，只在选择 `backend=ego` 时提示错误。

---

# 19. Persistent Runtime 专项验收

这是本任务最重要的新增验收之一。

Agent 必须证明：

### 任务中

```text
同一 run 内没有 per-step 启动新的 ego-browser CLI
```

记录：

```text
runtime_spawn_count
```

期望：

```text
1
```

### 任务结束后

检查：

```text
该 run 对应 child PID 已不存在
```

期望：

```text
orphan_process_remaining = false
```

即使：

```text
任务报错
Jev报错
Ego报错
KeyboardInterrupt
```

也必须成立。

---

# 20. 测试

至少新增以下测试：

## Backend factory

- 默认 -> browser_harness
- 显式 ego -> EgoBrowserBackend
- 非法 backend -> 明确错误

## Ego transport

- request id 匹配
- JSON framing 正常
- timeout
- malformed response
- Ego process 提前退出
- stderr warning 不污染 result parser

## Lifecycle

- start once
- multiple observe/act 不重复 spawn
- close once
- repeated close safe
- exception -> cleanup
- KeyboardInterrupt -> cleanup

## Stale

- invalid ref -> StalePage
- old observation generation -> StalePage
- stale 后重新 observe / repredict

## Metrics

- Jev call count 正确
- stale count 正确
- success rate 正确
- Ego operation timing 累加正确
- failed run 也写 metrics

---

# 21. upstream 检查仍必须通过

运行：

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

如果新增 JS bridge/helper，再增加对应：

```bash
node --check <new-file>
```

---

# 22. 推荐文件变更范围

建议：

```text
jev_ultrafast/
    agent.py
    browser.py                 # 兼容层 / factory，避免破坏旧 import
    backends/
        __init__.py
        base.py
        browser_harness.py
        ego.py
    metrics.py                 # 如果指标逻辑较多
    model.py                   # 只做必要计数/配置检查，不改决策语义

examples/
    run.py

tests/
    test_backends.py
    test_ego_backend.py
    test_metrics.py
    test_lifecycle.py

.env.example
README.md
```

原则：

```text
小切片
低耦合
原 backend 行为不变
Ego 独立适配
```

---

# 23. DoD — Definition of Done

只有以下全部满足才能宣布完成：

- [ ] 原 Browser Harness backend 保留；
- [ ] 默认行为不变；
- [ ] Ego backend 可显式选择；
- [ ] Ego 使用 task-scoped persistent runtime；
- [ ] 一个 run 内 `ego-browser` spawn count = 1；
- [ ] 多步 observe/act 复用同一 runtime；
- [ ] 同一 Task Space 被复用；
- [ ] stale 语义被保留；
- [ ] Jev 仍只做 operation / target choice；
- [ ] TYPE_TEXT helper 保留；
- [ ] `TYPESAFE_API_KEY` 仍从环境读取；
- [ ] key 不出现在日志；
- [ ] DONE / BLOCKED 后 runtime 正常关闭；
- [ ] exception 后 runtime 正常关闭；
- [ ] KeyboardInterrupt 后 runtime 正常关闭；
- [ ] 无 orphan `ego-browser` 子进程；
- [ ] 输出总耗时；
- [ ] 输出 Jev 调用次数；
- [ ] 输出操作成功率；
- [ ] 输出 stale 次数；
- [ ] 输出 Ego 操作耗时；
- [ ] 至少完成 3 次 live Ego run；
- [ ] 每次 live run 都留下 metrics；
- [ ] 失败 run 不被隐藏；
- [ ] 原 backend 回归通过；
- [ ] upstream tests / lint / build 通过；
- [ ] README 补充 Ego backend 使用说明。

---

# 24. Agent 最终交付报告格式

最终只需要按以下结构汇报：

## 1. 改了什么

简述架构与关键文件。

## 2. Persistent Runtime

说明：

```text
如何启动
如何复用
如何退出
如何防 orphan
```

并报告：

```text
runtime_spawn_count
```

## 3. 原 Backend 回归

说明 Browser Harness 是否保持兼容。

## 4. API 配置

说明：

```text
TYPESAFE_API_KEY
TYPESAFE_MODEL
TEXT_MODEL_API_KEY
TEXT_MODEL_BASE_URL
TEXT_MODEL
```

不得显示真实 key。

## 5. Tests

列出执行命令和结果。

## 6. Live Run Metrics

逐次给：

```text
总耗时
Jev 调用次数
操作成功率
stale 次数
Ego 操作耗时
```

至少 3 次。

## 7. Runtime Cleanup

每次 run 报告：

```text
orphan_process_remaining = false
```

如不是 false，任务不得标记完成。

## 8. 未解决问题

只写真实剩余问题，不要用“未来可优化”堆砌报告。

---

# 25. 本任务最核心的工程原则

一句话：

> **Jev 继续负责“快速选择下一步”，Browser backend 负责“安全且高效地执行”；Ego 的 Runtime 只在单次任务期间持续存在，用一次启动换取多步复用，任务结束必须彻底回收。**

不要把：

```text
persistent runtime
```

实现成：

```text
permanent daemon
```

也不要为了接 Ego 破坏 upstream 已经很快的 Browser Harness 路径。
