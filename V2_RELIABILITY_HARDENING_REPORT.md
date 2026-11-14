# Jev Ultrafast × Ego Browser — V2 Reliability Hardening 交付报告

本报告只覆盖本轮硬化（Ego backend 稳定性 + 真实动作覆盖），不改架构。
所有 live 证据都来自 `recordings/v2/`，历史 run 不作为本轮性能样本。

**代码与证据标识**

```text
硬化代码 commit:        e535248  fix(ego): bind freshness to the observed target instead of a page fingerprint
证据用最终 commit:      b31fd35  (README + MCP 服务端，未触碰本轮硬化的 Ego/agent/transport/timeline 代码)
implementation hash:    4b1046166a4c77f63af6386cdf1db0e3daf6d3c39555cc75abb61575d020dee3
```

`implementation_hash`（`jev_ultrafast/**/*.py` + `snapshot.js` 的内容哈希）在 `recordings/v2/` 的**全部 33 次 run**
（32 `done` + 1 次模型网关失败）中完全一致，因此回归批次与 A/B 批次测的是同一份实现。
本报告与 README 的提交是 docs-only：在交付提交上重算 `implementation_hash` 仍然是 `4b1046166a4c…dee3`，
即 A/B 跑的代码与交付点的代码逐字节相同（报告随 `docs: record the V2 reliability hardening report and refresh the README`
提交在 `main` 上，本轮只做本地提交、不 push）。
**注意该哈希的覆盖面**：它不含 `probe.js`（本轮 freshness 探针的实现文件），也不含 `tests/`——
`probe.js` 最后一次改动就在 `e535248`，此后没有提交再动过它，但"工作区当时是否临时改过它"无法由哈希回证。

```text
recordings/v2/ 33 次 run 的版本清单：
  implementation_hash  4b1046166a4c…dee3   × 33
  git_commit           b31fd35 × 25（A/B 全部 + 后补的 example 批次 + scroll-2） / e535248 × 8（Wikipedia/select/scroll/wait）
  git_dirty            False × 24 / True × 9
```

`git_dirty=true` 的 9 次分两类：8 次是回归批次（当时工作区里还有后来才单独提交的范围外改动
`README.md`、`jev_ultrafast/mcp_server.py`、`tests/test_mcp_server.py`，以及未跟踪的证据目录）；
1 次是最后补的 `scroll-2`（当时工作区里只有本报告与 `README.md` 的未提交改动，`git status --porcelain` 可复现）。
两类都不在本轮硬化的改动面内，代码内容（`implementation_hash`）与 A/B 批次完全相同；A/B 的 18 次全部为 `git_dirty=false`。
`recordings/v2-supplementary/` 里 28 个过程性 run 中有 16 个的哈希是 `04f4a6fc…`（= `e535248` 时的实现），
差别只在范围外的 `mcp_server.py`（`git diff e535248 HEAD -- jev_ultrafast/` 只有这一个文件），硬化代码本身一致。

---

## 1. Baseline

```text
baseline commit: 6da3c29  feat: add persistent Ego browser backend
baseline tag:    ego-backend-v1-baseline
working tree before development: clean —— 按 §0.1 先把上一轮的 persistent Ego backend 全部提交后再动手
```

- 基线可恢复：`git worktree add /tmp/jev-baseline 6da3c29` 可得到一份基线检出；§2 的 before/after 复现实验就是用这个 worktree 跑的。
- 本轮提交序列：

```text
8648b5e  chore: carry over the opt-in MCP stdio server from the previous session   (范围外，单独提交)
e535248  fix(ego): bind freshness to the observed target instead of a page fingerprint   ← 本轮硬化
4044dfa  chore: ignore local live-run evidence directories                        (.gitignore)
b31fd35  docs: document the opt-in MCP server and tag traces with the server pid
         (README.md + jev_ultrafast/mcp_server.py + tests/test_mcp_server.py；均为范围外文件)
```

- 冻结项未动：persistent runtime、backend factory、Browser Harness backend、metrics 口径、runtime cleanup、三级退出、
  Ego Task Space 生命周期、API key 配置、Jev 决策模型、text helper 模型。

---

## 2. Stale 根因

### 到底哪里让 ref 失效

Ego 的 `ref` 只在**最近一次 snapshot** 内有效。真实页面会在加载后替换节点：Wikipedia 头部搜索框在 load/fill 之后
1–5 秒被重新渲染（节点 3 → 节点 120），旧 `@N` 当场失效，动作报 `element is not connected`。

基线 `fresh()`（`jev_ultrafast/backends/ego.py:410`）为了回答"现在还新鲜吗"，**又发了一次完整 fresh 请求**
（`ego.py:414` `response = self._request_timed("fresh", {})`）：重新 snapshot、重建 refMap、重建整个 action space。
然后拿**整页指纹**比较 —— `_state_fingerprint`（`ego.py:187`）= `url` + 6000 字符视口文本 + **全部 actions** + `scroll`，
由 `_same_page`（`ego.py:405`）判定。任何无关文本变化、任何控件替换、任何滚动都会让它返回 False。
CLICK/SELECT 还额外比较**完整 guard**（`ego.py:434` `expected != actual`），而 guard 的最后一项是
`scope.innerText`（最近容器的文本）。

于是循环无法区分两件完全不同的事：

```text
A. 我的目标真的被替换了（页面变更，需要重新观察）
B. 页面别处的文本/滚动/其它控件在变（与我的目标无关）
```

`predict` 路径上的 `_backend_fresh(page)`（`agent.py:173`）用的是同一套整页指纹，所以**每次决策前都可能重新 observe**。
每次重新 observe 又会产生新一代 ref，旧编号继续作废，循环直到 `MAX_STALE_RETRIES` 用尽 → `blocked`。

### 为什么

1. **职责混淆**：`fresh` 同时做了"检查"和"重新观察"，而重新观察这一动作本身就会让被检查的编号作废。
2. **分类混淆**：`ego.py:480` 是 `if _looks_stale(str(error)) or isinstance(error, EgoProcessExited): raise StalePage`；
   子串表 `ego.py:529` 里有 `selector`、`not found`、`does not exist`、`page changed` 等，
   action error 与 transport error 都会落进 stale；runtime 退出也被伪装成 stale。
3. **词表缺口**：Ego 打印 `comboboxgrouping "Search Wikipedia" [ref=92]`，而 READ_STATE 认的 role 是 `combobox`；
   搜索建议 Ego 打印 `anchor [ref=94]`，READ_STATE 认的是 `option`。
   真实可点目标因此拿不到 ref（`ref: null`），`fresh` 报 `missing_ref` → stale。这是 TYPE_TEXT/CLICK 直接卡死的另一条独立原因。
   上一轮诊断在建议列表打开的状态下记录到 13 个这样的目标；本轮免费探针（`/tmp/unmapped_probe2.py`）在建议列表
   未渲染的状态下量到 `actions=74 / unmapped=2`（只有 `scroll_down` 与 `wait` 本来就不需要 ref），
   即该缺口只在建议列表出现时暴露；修复由 role 别名 + 唯一 label 兜底的单元测试固定。

### 怎么修

| 改动 | 位置 | 作用 |
| --- | --- | --- |
| 只读探针 `probe.js` | `jev_ultrafast/probe.js` | 一次 `page.evaluate` 返回 page identity（`epoch`/`url`/`ready`）与每个目标节点的 trimmed guard、`actionable`、`writable` |
| 目标级 freshness | `ego.py:566` `fresh()`、`ego.py:553` `_target_reason()` | generation 匹配 + 同一 document（epoch/url）+ 目标节点仍在 + guard（去掉末尾 `scope.innerText`）+ 可操作 + fill 需可写；**不再 snapshot、不再重建 refMap** |
| 有限只读复读 | `ego.py:35` `_PROBE_SETTLE_DELAYS_MS = (100, 200, 300)` | 目标被短暂挪动时不立刻判 stale；仍是只读操作 |
| 真实替换 → 重新决策 | `ego.py` `fresh` → `StalePage` → 循环 re-observe → Jev 重新决策 | 绝不重放浏览器 mutation |
| role 别名 + label 兜底 | `ego.py:136` `comboboxgrouping → combobox`；唯一 label 兜底映射 | 消除 `ref: null` 的 13 个目标 |
| fill 稳定观察 | `ego.py:492` `_settled_fill_observe()`（3 次一致、上限 2s） | 输入后等待页面稳定，避免把 fill 的中间态当成新决策依据 |
| 错误按来源分类 | `ego.py:301` `classify_ego_error()` | 见 §3 |
| 保险丝保留 | `agent.py` `MAX_STALE_RETRIES = 3` | 未放宽、未用重试掩盖问题 |

### 可复现实验（免费、确定性、无需付费调用）

本地页面 `/tmp/churn_server.py` 同时复刻两种真实机制：无关容器每 250 ms 追加文本（指纹/scope churn），
加载后 N ms 把搜索输入**替换成 clone**（重新挂载，断开旧 ref），提交后真实导航。
策略为脚本化决策（`time.sleep(1.2)` 模拟 Jev 调用时长），对基线与修复后代码跑同一页面：

| swap 时序 | 基线 `6da3c29` | 修复后 `e535248` |
| --- | --- | --- |
| 300 ms | **blocked**，stale=6，执行 0 个动作，Jev 3 次 | **done**，stale=1，2/3 成功，终页 `/result?q=hello` |
| 1200 ms | **blocked**，stale=5，执行 0 个动作 | **done**，stale=1，2/3 成功 |
| 2200 ms | **blocked**，stale=5，执行 0 个动作 | **done**，stale=0，2/2 成功 |

基线在该页面上的原始判定输出（诊断子类打印）：

```text
FRESH(no action) -> False
  url_same=True fingerprint_same=False
  actions differ: 4 -> 4
    action e1 left : {"node": 1, "role": "searchbox", "label": "Query", "kind": "fill", "value": "", "id": "e1"}
    action e1 right: {"node": 3, "role": "searchbox", "label": "Query", "kind": "fill", "value": "", "id": "e1"}
...（连续 6 次）→ stale_count=6 → blocked，attempted=0
```

也就是说：**同一个"输入节点被替换（1 → 3）"的事实，基线把它读成"整页变了 → 决策作废"，连试 3 轮重试、
一个动作都没执行就 blocked**；修复后的代码把它读成"目标被替换了 → 重新观察 → Jev 重新决策"。

修复后同一页面的 timeline（`record_dir` 时间线，格式见 §8）：

```text
observe gen=1 actions=4 mapped_refs=3 unmapped_targets=0
fresh   gen=1 result=True  scope=document
decision index=1 ref=@1 operation=TYPE_TEXT
fresh   gen=1 result=True  scope=document
fresh   gen=1 index=e1 ref=@1 operation=fill result=False reason=target_detached     ← 目标被替换
stale   gen=1 index=e1 ref=@1 operation=fill reason=fresh_check_failed source=act
stale_retry gen=1
observe gen=2 actions=4 mapped_refs=3 unmapped_targets=0
decision index=3 ref=@3 operation=TYPE_TEXT                                          ← 重新决策到新节点
fresh   gen=2 index=e1 ref=@3 operation=fill result=True  scope=target
act     gen=2 index=e1 ref=@3 operation=fill result=success                          ← 只执行一次
observe gen=3 → decision index=2 ref=@2 operation=CLICK → fresh True → act success
observe gen=4（已导航）→ decision DONE
```

历史 live 证据（基线代码、真实 Wikipedia）与之一致：
`recordings/ego-live-nodeaware-frozen-2/` = `blocked`，`jev_calls=4`，`stale_count=4`，
`browser_actions_attempted=4`、`succeeded=1`（只有那次 FILL 成功，其余 CLICK 全部因 stale 未执行）。

同一失败的**当轮免费复现**（`/tmp/unmapped_probe2.py`，真实页面 `https://en.wikipedia.org/wiki/Main_Page`，
直接调用 backend，无模型调用）：同一次 observe 拿到的搜索框 `role=searchbox ref=@3`，

```text
基线 6da3c29 :  act(searchbox, fill "Godel")
                → StalePage: Page changed since this decision. Observe again.
                （决策到执行之间，整页 fresh 比较失败，FILL 根本没有执行）
修复后 e535248: act(searchbox, fill "Godel") → {'executed': 'e2'}
                after fill: actions=74 unmapped=2（只有不需要 ref 的 scroll_down / wait）
                cleanup graceful_exit=true, orphan_process_remaining=false
```

**这条是时序相关的，不是每次都复现**：基线在 3 次复测里有 1 次 fill 也执行成功了（Wikipedia 头部重挂载发生在
决策与执行之间时才会否掉决策）；修复后的代码 3/3 都执行成功。确定性、每次都复现的证据是 §2 上面的本地页面实验
（基线 300/1200/2200 ms 三种时序全部 blocked 且执行 0 个动作）。

### Ego ref 生命周期（本轮固定的契约）

```text
产生   每次 observe() 产生新的一代（generation + 1），ref（@N）只在这一代内有效
绑定   每个决策记录它当时的 generation / 动作 index / ref，执行时原样带上
校验   执行前的 fresh 是只读探针：generation 未变 + 同一 document(epoch/url) + 目标节点仍在
       + trimmed guard 未变 + 可操作（fill 还需可写）；不 snapshot、不重建 action 列表
失效   目标被替换 / 页面已导航 / 输入过程中节点被摘除 → 该 ref 作废
恢复   re-observe 产生新一代 → Jev 重新决策 → 用新 ref 执行；旧代 ref 永不跨代使用
不变量 浏览器 mutation 永不重放；stale 不是错误而是"重新决策"的信号
上限   连续 stale 仍受 MAX_STALE_RETRIES = 3 约束，超过即 BLOCKED（保险丝未被放宽）
```

---

## 3. Error Classification

分类入口 `classify_ego_error(error, *, phase)`（`jev_ultrafast/backends/ego.py:301`），
标记表分别在 `ego.py:254`（resolution）、`ego.py:266`（action）、`ego.py:289`（detached）。

| 类别 | 触发条件 | 处理 | 本轮 live 证据 |
| --- | --- | --- | --- |
| `StalePage` | ① `fresh` 探测失败：generation 不匹配 / 同一 document 判定失败（导航）/ 目标缺失 / guard 变化 / 不可操作 / fill 不可写；② 动作解析失败：`unknown ref`、`invalid ref`、`take a new snapshot`、`matched 0 elements`、`no matching element`，或 Ego 报出的类名 `ElementResolutionError`（`ego.py:317`，名字比文本更可靠）；③ 动作执行后目标被摘除：`element is not connected`、`no longer attached`、`not attached to the dom`、`element is detached`；④ probe 阶段无法归类的其它 `EgoRemoteError` 也按 stale 处理（`ego.py:322`，只读、无 mutation 风险） | re-observe → **Jev 重新决策**；不重放 mutation | `metrics.stale_count` 合计 24 次（Wikipedia 4、A/B Ego 2、A/B Browser Harness 18）；时间线 29 行 —— `fresh_check` 16 行 + `act` 13 行（act 路径会同时留 backend 行与 agent 行）：3 次是 fill 输入后目标被摘除，5 次是"决策到执行之间页面已导航"，其余为决策边界的 fresh 检查（导航 / 目标被替换）；`wikipedia-3` 一次 run 内 3 次 stale 仍以 `done` 收尾 |
| `EgoActionError` | 动作**已执行**、页面拒绝或校验失败：`page.fill failed`、`page.click failed`、`intercepts pointer events`、`timed out after`、`could not verify`（非 detached 原因）、`unsupported action kind`、`not editable`；另外 fresh 探针本身报出这些"动作类"错误时也 fail closed 归到此类（`ego.py:319-323`，探针阶段无法区分时宁可报错也不当成 stale） | 直接失败，**不重试**，不伪装成 stale | 单测覆盖（`tests/test_ego_backend.py`：`test_act_action_failure_is_reported_and_never_recast_as_stale`、`test_act_detached_target_is_stale_but_other_post_input_failures_are_not`）；本轮 live 未触发 |
| `EgoTransportError` / `EgoRemoteError` | runtime 退出（`EgoProcessExited`）、请求超时（`EgoTimeout`）、frame 畸形（`EgoMalformedResponse`）、CLI bridge 断开；这些 `EgoTransportError` 子类在探针与执行两个阶段都原样抛出（`ego.py:324-325`），**非 `EgoRemoteError` 的未知异常也一律包装成 transport error**（`ego.py:326`） | 原样抛出，绝不转成 stale | live 1 次 `EgoTimeout: Timed out waiting for Ego frame b'__ULTRAFAST_RESULT__r1__'`（construct 阶段；当时有 1 秒间隔的 `ps` 采样负载），记入 `runner_failure.json` 而不是 stale |

分类的**残余边界**（验收时用对抗输入确认）：一个既不带上述标记、也不是已知类名的 `EgoRemoteError`，
在 **probe 阶段**会落到 `StalePage`（`ego.py:322`），只有 **act 阶段**才归 `EgoActionError`（`ego.py:323`）。
这是刻意的 fail-closed 选择：probe 是只读的，判成 stale 只会重新观察，不会重放任何输入。
"未知错误 → transport"只对非 `EgoRemoteError` 成立。

`EgoRemoteError` 同时携带 Ego 自己的错误类名（`transport.py:47-54`，`EgoRemoteError.__init__` 里的 `ego_name`），
因此 `ElementResolutionError` 这类有名字的错误按名字分类，而不是只靠字符串猜。

---

## 4. TYPE_TEXT 修复

```text
原问题
  Wikipedia 搜索框上的 TYPE_TEXT 会先 fill 成功，然后在后续决策/执行中被判 stale；
  严重时连续 stale 直到 MAX_STALE_RETRIES 用尽 → blocked（历史 run recordings/ego-live-nodeaware-frozen-2）。
  另一部分真实目标（搜索建议）根本拿不到 ref，TYPE_TEXT/CLICK 无法执行。

根因
  1) fresh 用整页指纹 + 完整 guard（含 scope.innerText），无关 churn 也会作废决策；
  2) fresh 自己重新 snapshot，重建 refMap，导致刚观察到的编号当场作废；
  3) Wikipedia 头部在 load/fill 后 1–5s 重新挂载输入节点，旧 ref 必然失效，
     而 fill 后置校验错误 "element is not connected" 在基线里既不算 stale（子串表没有它）也不是 action error，直接失败；
  4) role 词表缺口（comboboxgrouping / anchor-as-option）让真实目标 ref=null → missing_ref。

修复
  · fresh 改为目标级只读探针（probe.js）：generation + epoch/url + 目标节点 + trimmed guard + actionable + writable；
  · guard 去掉末尾 scope.innerText（`_target_guard`），无关容器文本不再影响；
  · fill 后置校验里 "element is not connected" 类错误按"目标被摘除"归为 StalePage → re-observe → 重新决策（不重放输入）；
    其它后置失败仍为 EgoActionError（fail closed）；
  · role 别名 + 唯一 label 兜底，消除 ref=null 目标；
  · fill 后有界稳定观察（≤2s，3 次一致）。

live 结果
  最终样本 Wikipedia ×5 全部 done，终页均为 Gödel's_incompleteness_theorems；
  fill 直接成功 3/5（wikipedia-1/4/5），另 2/5（wikipedia-2/3）在 fill 过程中被页面替换，
  由 stale→重新决策→点击搜索建议完成，终页仍然正确；
  TYPE_TEXT 没有再出现 blocked，MAX_STALE_RETRIES 未触发。
```

单次 2/5 的失败形态（`wikipedia-2` 时间线原文）清楚地显示了这种"不重放"的恢复：

```text
decision index=2 ref=@3 operation=TYPE_TEXT
fresh   gen=1 index=e2 ref=@3 operation=fill result=True scope=target
act     gen=1 index=e2 ref=@3 operation=fill result=error error_class=StalePage
        detail=Error: page.fill could not verify the result: element is not connected
stale_retry gen=1
observe gen=2 actions=88 mapped_refs=86 unmapped_targets=0
decision index=3 ref=@94 operation=CLICK          ← 改为点击搜索建议
act     gen=2 index=e4 ref=@94 operation=click result=success
observe gen=3（文章页）→ decision DONE
```

免费复现（§2 的本地页面）也给出同样的行为：fill 目标被替换 → `reason=target_detached` → 重新决策到新节点 `@3` → fill 成功一次。

---

## 5. Tests

完整命令与结果（全部离线，不调用付费 API）：

```text
$ UV_CACHE_DIR=/tmp/uvcache uv run --offline ruff check .
All checks passed!

$ UV_CACHE_DIR=/tmp/uvcache uv run --offline pytest -q
126 passed

$ node --check jev_ultrafast/static/app.js && node --check jev_ultrafast/snapshot.js && node --check jev_ultrafast/probe.js
(no output = 通过)

$ UV_CACHE_DIR=/tmp/uvcache uv run --offline uv build
Successfully built dist/jev_ultrafast-0.1.0.tar.gz and dist/jev_ultrafast-0.1.0-py3-none-any.whl
```

测试分布（`pytest --collect-only`）：

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_ego_backend.py` | 24 | observe/generation 绑定（`test_old_observation_generation_is_stale`）、导航瞬时态、fill 稳定观察（`sleeps == [0.2,0.2,0.2,0.2]`）、**指纹/scope/工具条 churn 被忽略**（`test_fresh_ignores_unrelated_text_scroll_and_toolbar_churn`）、同文档、目标被替换（不执行 act）、瞬时摘除恢复、被遮挡目标、fill 需可写、探针失败 → stale、runtime 退出 → transport（不是 stale）、unknown ref → 单次 stale、动作失败永不被当成 stale（`test_act_action_failure_is_reported_and_never_recast_as_stale`）、输入后被摘除 → stale 而其它后置失败不是（`test_act_detached_target_is_stale_but_other_post_input_failures_are_not`）、`classify_ego_error` 真实报文、无 ref 时不发探针、`comboboxgrouping` + label 兜底映射、timing/cleanup、timeline 生命周期、构造失败关闭 transport |
| `tests/test_timeline.py` | 5 | JSONL 内容、截断/None 丢弃、凭证脱敏、400 事件上限、`Timeline(None)`、重复 close/坏路径 |
| `tests/test_agent.py` | 34 | 含本轮新增：动作错误 fail closed 且不消耗 stale 重试、stale 保险丝到上限仍然 BLOCKED（`test_stale_retry_fuse_still_blocks_after_the_limit`）、timeline 记录 stale 生命周期与保险丝 |
| `tests/test_live_runs.py` | 27 | live-run 量测工具（离线）：pid 存活/zombie、证据采集、watchdog、hash 稳定性、交错顺序、聚合数学 |
| 其它（`test_mcp_server.py` 等） | 36 | 范围外沿用 |

---

## 6. 新 Live Runs

只列本轮最终代码（`implementation_hash=4b1046…dee3`）的 run；`recordings/v2/` 内这 33 次 run 合计 146 次 Jev 调用
（Wikipedia 17 + example 11 + SELECT 2 + SCROLL 3 + SCROLL-2 33 + WAIT 5 + A/B 75）；`recordings/v2-supplementary/` 里的过程性尝试不计入。

### Wikipedia ×5（goal：找到并打开 Gödel's incompleteness theorems 条目）

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale | observe ms | fresh ms | act ms | spawns | orphan | pid alive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `wikipedia-1` | done | 13019 | 3 | 2/2 | 1.0 | 0 | 3881 | 813 | 1691 | 1 | False | False |
| `wikipedia-2` | done | 11920 | 3 | 1/2 | 0.5 | 1 | 3074 | 818 | 1594 | 1 | False | False |
| `wikipedia-3` | done | 15480 | 5 | 2/4 | 0.5 | 3 | 7843 | 1287 | 1799 | 1 | False | False |
| `wikipedia-4` | done | 13520 | 3 | 2/2 | 1.0 | 0 | 4909 | 820 | 2011 | 1 | False | False |
| `wikipedia-5` | done | 12607 | 3 | 2/2 | 1.0 | 0 | 4981 | 802 | 1908 | 1 | False | False |

median / min / max：wall **13019** / 11920 / 15480；Jev calls **3** / 3 / 5；action success rate **1.0** / 0.5 / 1.0；
stale **0** / 0 / 3；observe **4909** / 3074 / 7843；fresh **818** / 802 / 1287；act **1799** / 1594 / 2011。

**独立结果核对**（DONE 不等于成功，逐个读终页）：

```text
wikipedia-1..5 最终 URL = https://en.wikipedia.org/wiki/G%C3%B6del%27s_incompleteness_theorems
（wikipedia-3 带 ?wprov=srpw1_0 跟踪参数，仍是同一篇文章）
```

### Example.com（goal：打开 Learn more 链接后停止）

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale | spawns | orphan | pid alive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `example-2` | done | 4355 | 2 | 1/1 | 1.0 | 0 | 1 | False | False |
| `example-3` | done | 3174 | 2 | 1/1 | 1.0 | 0 | 1 | False | False |
| `example-r2-1` | done | 4734 | 2 | 1/1 | 1.0 | 0 | 1 | False | False |
| `example-r2-2` | done | 3377 | 2 | 1/1 | 1.0 | 0 | 1 | False | False |
| `example-r2-3` | done | 3326 | 2 | 1/1 | 1.0 | 0 | 1 | False | False |

成功的 5 次 run 终页均为 `https://www.iana.org/help/example-domains`。
`example-1`（批次的首个 run）失败于模型网关首调用：`RuntimeError: Model connection failed; no action executed.`
（两次尝试都在同一位置失败，见 §9.2），不是 stale、不是 loop 问题。

### SELECT（Selenium web-form，选择第三个下拉项）

| run | status | wall ms | Jev calls | actions ok/attempted | stale | act ms |
| --- | --- | --- | --- | --- | --- | --- |
| `select-1` | done | 5111 | 2 | 1/1 | 0 | 113 |

免费 DOM 核对（`/tmp/select_probe.py`，直接执行同一个选择动作，无模型调用）：

```text
选择前 [Dropdown (select) → One/Two/Three]，同一节点 ref=@7，current_value 均为 "Open this select menu"
执行 Dropdown (select) → Three（value="3"）→ {'executed': 'e9'}
选择后 同一节点 current_value 全部为 "Three"，可选项变为 Open this select menu / One / Two
cleanup graceful_exit=True, orphan_process_remaining=False
```

### SCROLL（Wikipedia 文章，向下滚动 / 滚动后继续动作）

| run | status | wall ms | Jev calls | actions ok/attempted | stale | act ms |
| --- | --- | --- | --- | --- | --- | --- |
| `scroll-1` | done | 16910 | 3 | 2/2 | 0 | 759 |
| `scroll-2-1` | done | 146467 | 33 | 32/32 | 0 | 26973 |

`scroll-1` 证明"滚动动作本身"能真实执行（2 次 `SCROLL_DOWN`，`actions_by_kind.scroll=2/2`）。
`scroll-2-1` 证明 §5.1 要求的链路（滚动 → 目标进入 viewport → **后续 action 成功**）：
goal 是"滚到页脚可见，然后打开 Privacy policy 链接"，run 内 31 次 `SCROLL_DOWN`（全部成功、0 stale）
→ 随后 1 次 `CLICK` 执行成功并真的导航，终页 `https://en.wikipedia.org/wiki/Privacy_policy`
（`actions_by_kind = {scroll: 31/31, click: 1/1}`）。
**但这次 run 的结果要精确读**（DONE 不等于成功）：被点的元素是**正文里的**一个小写 `privacy policy` 超链接，
不是页脚链接 —— 31 次滚动并没有滚到页脚，因此"页脚可见"这一半 goal 没有被验证，
`DONE` 只对"打开了一个 Privacy policy 链接"成立。这条结论有两个可复算的证据：
run 的 `trace.json` 里那次 CLICK 的 label 正是小写 `privacy policy`，终页是 `https://en.wikipedia.org/wiki/Privacy_policy`；
而对该页 HTML 做锚文本大小写匹配会看到两个不同的目标 ——
`<a …>privacy policy</a>` → `https://en.wikipedia.org/wiki/Privacy_policy`（正文，小写）与
`<a …>Privacy policy</a>` → `https://foundation.wikimedia.org/wiki/Special:MyLanguage/Policy:Privacy_policy`（页脚，大写；
该 href 的规范目标是 `https://foundation.wikimedia.org/wiki/Policy:Privacy_policy`）。
run 落在前者，所以点的是正文链接。CLICK 决策所用的那张截图（`137285.jpg`）里该链接也在视口内、像素为链接蓝。
DoD 要的那条链路（滚动之后还能成功执行下一个动作）是成立的。
代价是这条 goal 花了 33 次 Jev 调用（146s，其中 observe 81.9s + fresh 8.5s + act 27.0s），是本样本里最贵的一次。

免费滚动核对（`/tmp/scroll_probe.py`，跑的是 Gödel 那篇文章 `…/wiki/G%C3%B6del%27s_incompleteness_theorems`，
与 `scroll-1` 的 `/wiki/Wikipedia` 不是同一页；数字描述的是该文章页）：

```text
observe scroll={"y": 0, "height": 23830} → act scroll_down(delta=560) → {"y": 560, "height": 23830}
第二次 scroll_down → y 落在 1089（本机当次测量）；独立复测同一脚本得到 1120
   —— 第二次的落点随页面动态内容变化，第一次的 0 → 560 是确定的
cleanup graceful_exit=True, orphan_process_remaining=False
```

### WAIT（the-internet dynamic loading，点击 Start 并等待）

| run | status | wall ms | Jev calls | actions ok/attempted | stale | act ms |
| --- | --- | --- | --- | --- | --- | --- |
| `wait-1` | done | 12925 | 5 | 4/4 | 0 | 1858 |

免费等待核对（`/tmp/diag_actions2.py`）：点击 Start 后页面文本在 3.0s 内一直是 `Loading...`，
执行 WAIT（`wait_ms=3000`）后观察文本变为 `Hello World!`；`cleanup graceful_exit=True, orphan_process_remaining=False`。

### 时间线样例（`wikipedia-3`，一次 run 内 3 次 stale 仍 done）

```text
observe gen=1 actions=84 mapped_refs=82 unmapped_targets=0
fresh   gen=1 result=True scope=document
decision index=2 ref=@3 operation=TYPE_TEXT
fresh   gen=1 index=e2 ref=@3 operation=fill result=True  scope=target
act     gen=1 index=e2 ref=@3 operation=fill result=error error_class=StalePage   ← 中途被替换
stale_retry gen=1
observe gen=2 actions=88 mapped_refs=86 unmapped_targets=0
decision index=4 ref=@95 operation=CLICK → act success（导航）
observe gen=3
decision index=19 ref=@115 operation=CLICK
fresh   gen=3 index=e20 ref=@115 operation=click result=False reason=navigation     ← 决策到执行之间页面已导航
stale_retry gen=3
observe gen=4 → decision index=18 ref=@149 operation=CLICK → act success（导航）
observe gen=5 → fresh result=False reason=navigation → stale（fresh_check）
observe gen=6 → decision DONE
```

三个 stale 都发生在**决策边界**（目标被替换 / 已导航），没有一次是"无关页面变化"造成的；
每一次都由 re-observe + Jev 重新决策恢复，没有任何 mutation 被重放。

---

## 7. A/B（Browser Harness vs Ego）

条件：同一页面、同一 goal、同一 commit（`b31fd35`）、同一 `implementation_hash`，交错执行（A,B,A,B,A,B），
每格 3 次；不给出最终性能结论，只列数据。

### 简单任务：`https://example.com/` — "Open the Learn more link on this page, then stop."

**Browser Harness**

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale |
| --- | --- | --- | --- | --- | --- | --- |
| `ab-simple-browser_harness-1` | done | 3039 | 2 | 1/1 | 1.0 | 0 |
| `ab-simple-browser_harness-2` | done | 3065 | 3 | 1/1 | 1.0 | 1 |
| `ab-simple-browser_harness-3` | done | 1969 | 2 | 1/1 | 1.0 | 0 |

median / min / max：wall **3039** / 1969 / 3065；Jev calls **2** / 2 / 3；success rate **1.0** / 1.0 / 1.0；stale **0** / 0 / 1。

**Ego**

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale | observe ms | fresh ms | act ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ab-simple-ego-1` | done | 3701 | 2 | 1/1 | 1.0 | 0 | 255 | 530 | 445 |
| `ab-simple-ego-2` | done | 3031 | 2 | 1/1 | 1.0 | 0 | 366 | 510 | 345 |
| `ab-simple-ego-3` | done | 2846 | 2 | 1/1 | 1.0 | 0 | 365 | 525 | 338 |

median / min / max：wall **3031** / 2846 / 3701；Jev calls **2** / 2 / 2；success rate **1.0** / 1.0 / 1.0；stale **0** / 0 / 0；
observe **365** / 255 / 366；fresh **525** / 510 / 530；act **345** / 338 / 445。

### 中等任务：`dynamic_loading/2` — "Click Start and wait until the loaded message appears."

**Browser Harness**

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale |
| --- | --- | --- | --- | --- | --- | --- |
| `ab-medium-browser_harness-1` | done | 7839 | 3 | 2/2 | 1.0 | 0 |
| `ab-medium-browser_harness-2` | done | 7429 | 9 | 7/8 | 0.875 | 1 |
| `ab-medium-browser_harness-3` | done | 7123 | 9 | 8/8 | 1.0 | 1 |

median / min / max：wall **7429** / 7123 / 7839；Jev calls **9** / 3 / 9；success rate **1.0** / 0.875 / 1.0；stale **1** / 0 / 1。

**Ego**

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale | observe ms | fresh ms | act ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ab-medium-ego-1` | done | 11383 | 5 | 4/4 | 1.0 | 0 | 3335 | 1286 | 1927 |
| `ab-medium-ego-2` | done | 10311 | 5 | 4/4 | 1.0 | 0 | 3339 | 1276 | 1901 |
| `ab-medium-ego-3` | done | 10306 | 5 | 4/4 | 1.0 | 0 | 3303 | 1293 | 1912 |

median / min / max：wall **10311** / 10306 / 11383；Jev calls **5** / 5 / 5；success rate **1.0** / 1.0 / 1.0；stale **0** / 0 / 0；
observe **3335** / 3303 / 3339；fresh **1286** / 1276 / 1293；act **1912** / 1901 / 1927。

### 复杂任务：Wikipedia 主页 — "Find and open the Wikipedia article about Godel's incompleteness theorems."

**Browser Harness**

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale |
| --- | --- | --- | --- | --- | --- | --- |
| `ab-complex-browser_harness-1` | done | 13255 | 7 | 2/4 | 0.5 | 7 |
| `ab-complex-browser_harness-2` | done | 8050 | 4 | 2/2 | 1.0 | 4 |
| `ab-complex-browser_harness-3` | done | 7475 | 4 | 2/2 | 1.0 | 4 |

median / min / max：wall **8050** / 7475 / 13255；Jev calls **4** / 4 / 7；success rate **1.0** / 0.5 / 1.0；stale **4** / 4 / 7。

**Ego**

| run | status | wall ms | Jev calls | actions ok/attempted | success rate | stale | observe ms | fresh ms | act ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ab-complex-ego-1` | done | 13507 | 3 | 2/2 | 1.0 | 0 | 4001 | 876 | 2732 |
| `ab-complex-ego-2` | done | 15918 | 5 | 2/4 | 0.5 | 2 | 7485 | 1421 | 1893 |
| `ab-complex-ego-3` | done | 13271 | 3 | 2/2 | 1.0 | 0 | 5765 | 894 | 1949 |

median / min / max：wall **13507** / 13271 / 15918；Jev calls **3** / 3 / 5；success rate **1.0** / 0.5 / 1.0；stale **0** / 0 / 2；
observe **5765** / 4001 / 7485；fresh **894** / 876 / 1421；act **1949** / 1893 / 2732。

**读表须知（不给性能结论）**

- `ego_*` 计时只对 Ego backend 有值；Browser Harness 仍是本轮冻结的旧口径，其列为 0 表示"未采集"，不是 0 ms。
- `wall_elapsed_ms` 含进程启动、浏览器会话建立与关闭，两个 backend 的启动路径不同（Ego 走 PTY REPL，Browser Harness 走 CDP）；
  Ego 的 `backend_startup_ms` median 1118 ms（572–2557）、`backend_cleanup_ms` median 157.5 ms，小样本下不可直接比。
- Browser Harness 的 `stale_count`（复杂任务 4–7）是它自己既有的 stale 语义，本轮未修改、未优化。
- 每格 n=3，方差明显（例如复杂任务 wall：Browser Harness 7.5s–13.3s、Ego 13.3s–15.9s），不构成速度结论。

---

## 8. Runtime Cleanup

最终样本 33 次 run 逐次确认（`metrics.json` 与 `summary.json`）：

```text
runtime_spawn_count = 1            24/24 次 Ego run
metrics.orphan_process_remaining   false × 24（全部 Ego run）；Browser Harness 无 Ego runtime，记为 null
pid_alive_after_close = false      23/24 次 Ego run；1 次为 null（模型网关失败的 run 未观测到 runtime pid）
```

| 批次 | run 数 | spawns=1 | metrics.orphan=false | pid_alive=false | pid_alive=null |
| --- | --- | --- | --- | --- | --- |
| Wikipedia ×5 | 5 | 5/5 | 5/5 | 5/5 | 0 |
| example（5 次成功） | 5 | 5/5 | 5/5 | 5/5 | 0 |
| example-1（网关失败） | 1 | 1/1 | 1/1 | — | 1 |
| SELECT / SCROLL / SCROLL-2 / WAIT | 4 | 4/4 | 4/4 | 4/4 | 0 |
| A/B Ego 9 次 | 9 | 9/9 | 9/9 | 9/9 | 0 |
| A/B Browser Harness 9 次 | 9 | n/a | n/a（该 backend 无 Ego runtime） | n/a | n/a |
| **合计** | **33** | **24/24** | **24/24** | **23/24** | **1（+9 次 bh 不适用）** |

**量测口径说明（重要，且已给出证据）**：live-run 工具在 close 之后额外做一次
`pgrep -f "ego-browser nodejs"` 扫描，并用它写 `summary.json.orphan_process_remaining`。
该扫描会被**命令行文本里含有这一串字符的进程自匹配**：批次运行期间我自己的监控 shell
（`bash -c ... pgrep -f 'ego-browser nodejs' ...`）就被算成了"存活进程"。
证据（`/tmp/proc_monitor.py` 以 1 秒间隔记录 pid/ppid/lstart）：

```text
1790105868.1  68900 43632 68900  bash -c sleep 150; ... $(pgrep -f 'ego-browser nodejs' | wc -l)   ← 被误判的"存活进程"，父进程是 DSH shell
1790105866.0  68880 68877 68874  ego-browser nodejs        ← 真正的 runtime，父进程是 harness python(68877)
1790105879.8  68963 68877 68874  ego-browser nodejs
1790105896.1  69075 68877 68874  ego-browser nodejs
1790105910.1  69130 68877 68874  ego-browser nodejs
1790105943.1  69318 68877 68874  ego-browser nodejs        ← 5 次 run 各自一个 runtime，全部是 harness 子进程
close 之后：pgrep 匹配数 = 0
```

即：每次 run 的 runtime 都是 harness 的子进程，`close()` 后全部退出 —— 24 次 Ego run 的 `graceful_exit` 全为 `true`，
`backend_cleanup_ms` median **157.5 ms**（min 118 / max 180），无一次需要 `terminate` 或 `kill`；
被扫描标记的 pid 与 harness 无父子关系，是监控 shell 自己。
在安静环境（无监控 shell）重跑的 5 次 Wikipedia 全部记录为 `orphan_process_remaining=false`。
其余批次里出现的同一现象（`pid 64850`、`63893`）同样是长驻监控 shell，run 结束后自行退出。

**过程性样本（已从最终样本中排除，保存在 `recordings/v2-supplementary/`）**：

```text
attempt1-contaminated/       第一批 Wikipedia ×5，被监控 shell 自匹配污染
attempt2-model-error/        Wikipedia ×5，1 次模型网关失败
attempt3-construct-timeout/  Wikipedia ×5，1 次模型网关失败 + 1 次 Ego construct 超时（EgoTimeout，jev_calls=0）
attempt1-regression/         第一批 example/select/wait/scroll，scroll 1 次模型网关失败
attempt2-regression/         example 目标措辞导致模型选择 BLOCKED 的 3 次 + scroll 网关失败
attempt3-example-gateway-error/  example 首 run 网关失败的 3 次
```

这些 run 的硬化代码与最终样本相同（`recordings/v2-supplementary/` 共 28 个：前 3 组 16 个的哈希是 `e535248`
时代的 `04f4a6fc…`，后 3 组 12 个已经是 `4b104616…`；差别只在范围外的 `mcp_server.py`），
失败原因都是环境/措辞造成的，故不作为 DoD 样本，但保留在磁盘上可复查。

---

## 9. Remaining Issues

1. **Wikipedia 头部的重新挂载是时序性的**：同一份代码，一批 5 次里 2 次 fill 中途被摘除，另一批 5 次里 0 次。
   现在它会退化为"一次 stale + 一次额外 Jev 调用后重新决策"，不再 blocked；但这一次额外调用无法消除
   （不重放 mutation 是硬约束）。
2. **模型网关的首调用失败未被重试**：`jev_ultrafast/model.py:11-20` 使用
   `httpx.Client(http2=True, timeout=25)`，只对 HTTP 429/529/503 重试；
   连接类错误（`httpx.HTTPError`）立即抛 `RuntimeError: Model connection failed; no action executed.`。
   本轮付费 run 中共 6 次（`v2/example-1` 1 次 + `recordings/v2-supplementary/` 里 5 次：
   `attempt1-regression/scroll-1`、`attempt2-model-error/wikipedia-1`、`attempt2-regression/scroll-1`、
   `attempt3-construct-timeout/wikipedia-4`、`attempt3-example-gateway-error/example-1`），多发生在批次的首个 run，
   失败时间约 5s（连接层错误，不是 25s 超时）。A/B 18 次为 0 次。
   这属于冻结范围（"Jev 决策模型""API key 配置"），本轮未改；它不会伪装成 stale（fail closed），但会让 run 直接失败。
   另有一批 3 次 `blocked`（`attempt2-regression/example-1..3`）是目标措辞让模型自己选择 BLOCKED，
   与 stale 无关；换成最终措辞后同一任务 5/5 成功。
3. **`EgoTimeout` 于 construct 阶段**：观察到 1 次（`attempt3-construct-timeout/wikipedia-5`，`jev_calls=0`）
   `Timed out waiting for Ego frame b'__ULTRAFAST_RESULT__r1__'`（transport 默认 bootstrap 超时 15s），
   当时本机有 1 秒间隔的 `ps` 采样负载。它被正确归类为 transport error（`runner_failure.phase=construct`），
   不是 stale；如需更强韧性，可考虑提高 construct 超时或在 bootstrap 阶段做一次重试，但这超出本轮范围。
4. **`pid_alive_after_close=null` 的语义**：33 次 run 中有 10 次为 `null` —— 9 次 Browser Harness（该 backend 不启动 Ego
   runtime）与 1 次在模型调用阶段就失败的 run（从未观测到 runtime pid）。工具如实写 `null`
   而不是 `false`。不是"未清理"，是"没有可检查的对象"。
5. **live-run 工具的 pgrep 扫描不够稳健**（§8）：建议改为"运行前 pid 基线 + 差集"，并把命令行含该模式的
   进程排除；本轮为了让 A/B 全部落在同一 commit 上，未在跑动中修改工具代码。
6. **timeline 中 act 路径的 stale 记录两行**（backend 一行带 `error_class`，agent 一行不带），
   `metrics.stale_count` 仍是 1。纯日志冗余，为保持"同一 commit"未在跑动中修改。
7. **`stale_count` 不等于保险丝用量**：`predict` 路径上的 fresh 失败会重新观察并计入 `metrics.stale_count`，
   但**不**增加 `stale_retries` 计数（`wikipedia-3` 就是 3 次 stale、只有 2 次 `stale_retry`）；
   保险丝本身统计的是**连续** stale，成功执行一个动作后清零（`agent.py:266`）。
   两者都是基线既有语义，本轮没有改动，但读 metrics 时不能把 `stale_count` 当作"重试了几次"。
8. **`implementation_hash` 不覆盖 `probe.js` 与 `tests/`**：本轮 freshness 的探针实现不在哈希里（见开头说明），
   因此"同一实现"的论证由三层组成：哈希（.py + snapshot.js）+ `probe.js` 只在 `e535248` 被改动过的 git 历史 + A/B 全部 `git_dirty=false`。
   如果后续要更强的可复现性，应把 `probe.js` 纳入哈希。
9. **没有单测直接断言"fresh 不发 snapshot 请求"**：现有覆盖是行为等价的（`test_fresh_ignores_unrelated_text_scroll_and_toolbar_churn`
   等），加上 live 时间线里 `fresh` 从不产生 `observe` 事件；代码层面 `fresh` 只调 `page.evaluate(__jevProbeSource)`。
10. **Browser Harness backend 本轮冻结未优化**：复杂任务上它自己的 stale_count 为 4–7，
    `ego_*` 计时为 0（未采集）。A/B 因此只能作为"两边都还能跑完"的对照，不能作为性能结论。
    另外仓库里没有 BH 专属的单测文件，BH 的回归靠 9 次 live run + `browser.py` 零改动。
11. **单动作覆盖的样本量**（DoD 要求 ≥1）：SELECT 1 次、SCROLL 2 次（其中 `scroll-2-1` 覆盖了"滚动后继续动作"的完整链路）、
    WAIT 1 次。动作级正确性另有免费 DOM 核对（§6），但样本量不足以谈稳定性。
12. **A/B 每格 n=3**，方差大（复杂任务 wall：Browser Harness 7.5s–13.3s、Ego 13.3s–15.9s；Jev calls 两边合计 3–7），报告不给最终速度结论 —— 这是刻意的。
    另外同一任务的 A/B 里两边都是 3/3 `done`，所以 A/B **本身**并不体现完成率差异；
    可靠性提升的证据在回归矩阵（Wikipedia 5/5 含一次 fail→recover）与 §2 的对照实验里。

---

## 附录：证据索引与复现命令

```text
recordings/v2/                     本轮样本（33 run，32 done + 1 网关失败）：metrics.json / trace.json / summary.json /
                                   process_evidence.json / version_manifest.json / events.jsonl / *.jpg
recordings/v2-supplementary/       过程性样本（6 组 28 run，见 §8）
recordings/ego-live-nodeaware-frozen-2/   基线 live 反例（blocked、4 stale、只成功 1 个动作）

复现检查（离线）：
  UV_CACHE_DIR=/tmp/uvcache uv run --offline ruff check .
  UV_CACHE_DIR=/tmp/uvcache uv run --offline pytest -q
  node --check jev_ultrafast/static/app.js && node --check jev_ultrafast/snapshot.js && node --check jev_ultrafast/probe.js
  UV_CACHE_DIR=/tmp/uvcache uv run --offline uv build

复现 live run（付费）：
  UV_CACHE_DIR=/tmp/uvcache uv run --env-file .env python scripts/live_runs.py \
      --backend ego --record-root recordings/v2 --timeout-seconds 180 \
      --url https://en.wikipedia.org/wiki/Main_Page \
      --goal "Find and open the Wikipedia article about Godel's incompleteness theorems." \
      --runs 5 --label wikipedia
  UV_CACHE_DIR=/tmp/uvcache uv run --env-file .env python scripts/live_runs.py \
      --ab --backends browser_harness,ego --record-root recordings/v2 --timeout-seconds 180 \
      --runs 3 --url https://example.com/ --goal "Open the Learn more link on this page, then stop." --label ab-simple
  UV_CACHE_DIR=/tmp/uvcache uv run --offline python scripts/live_summary.py recordings/v2

复现 §2 的 before/after（免费，无付费调用）：
  python3 /tmp/churn_server.py 8791 &
  .venv/bin/python /tmp/churn_experiment.py /tmp/jev-baseline 1200 250     # 基线：blocked，stale=5，0 动作
  .venv/bin/python /tmp/churn_experiment.py "$PWD" 1200 250                # 修复后：done，stale=1，2/3 成功
  .venv/bin/python /tmp/churn_diag.py /tmp/jev-baseline 1200 250          # 基线的 fresh 判定原因（节点 1 → 3）
  .venv/bin/python /tmp/unmapped_probe2.py /tmp/jev-baseline              # 真实 Wikipedia：基线多数情况下 act 前 StalePage（3 次里 1 次能执行）
  .venv/bin/python /tmp/unmapped_probe2.py "$PWD"                         # 修复后：同一 FILL 3/3 执行成功

复现 §6 的动作覆盖核对（免费，无付费调用）：
  .venv/bin/python /tmp/select_probe.py    # SELECT：current_value "Open this select menu" → "Three"
  .venv/bin/python /tmp/scroll_probe.py    # SCROLL：scroll.y 0 → 560 →（第二次 1089~1120）
  .venv/bin/python /tmp/diag_actions2.py   # WAIT：Loading... → Hello World!
```