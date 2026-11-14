# Jev Ultrafast × Ego Browser — Persistent Runtime Backend 交付报告

> 任务文档：`JEV_ULTRAFAST_EGO_PERSISTENT_BACKEND_TASK.md`（V1）
> 开发会话：Codex（ChatGPT 桌面版，对话名「执行 JEV 后端任务」）——中断后由 DSH 会话接手收尾
> 收尾验证时间：2026-09-22 23:05（`uv build` 等全部检查在本会话复跑通过）
> 原则：以下只报原始数据与事实，不替用户判断 Ego 是否更快/更慢。

---

## 1. 改了什么

遵循任务文档 §22 的推荐变更范围，采用「小切片、低耦合」：

```text
jev_ultrafast/
    agent.py                    # Agent 支持 backend= / record_dir；接入 metrics、preflight
    browser.py                  # 原 Browser 不变；新增 create_backend() factory（Ego lazy-load）
    backends/
        __init__.py
        base.py                 # BrowserBackend Protocol（observe/fresh/act/close）
        ego.py                  # EgoBrowserBackend：persistent runtime + Task Space + ref 映射 + stale 转译
        transport.py            # ego-browser nodejs 子进程管理：request/response framing、三级退出
    metrics.py                  # RunMetrics：五项核心指标 + 补充诊断
    preflight.py                # 轻量启动检查（secret-free 输出）
    model.py / questions.py     # 仅增加计数/上限（MAX_STALE_RETRIES），不动决策语义

examples/run.py                 # 新增 --backend / --record-dir
tests/                          # 新增 test_ego_backend.py / test_ego_transport.py / test_metrics.py
.env.example                    # 注明 ULTRAFAST_BROWSER_BACKEND 环境变量
README.md                       # 补充 Ego backend 使用说明
```

接口语义：

- `Agent(url, goal)` ≡ `Agent(url, goal, backend="browser_harness")`，默认行为与 upstream 一致；
- `Agent(url, goal, backend="ego")` 选择 Ego；`--backend ego` / `ULTRAFAST_BROWSER_BACKEND=ego` 等效，显式参数 > 环境变量 > 默认；
- `create_backend()` 对未知 backend 抛明确错误；Ego 依赖在 `backend="ego"` 时才 import，未装 Ego 的用户不受影响；
- Jev 决策层不做任何改动：仍输出 operation + target，消费自身 observation 的 element table；Jev index ↔ Ego ref 的映射属于当前 observation generation，新 snapshot 后旧映射失效。

## 2. Persistent Runtime

**如何启动**：`EgoBrowserBackend.__init__` 中 spawn 一次 `ego-browser nodejs`（transport 建立 CLI Bridge + Node Runtime），并 `useOrCreateTaskSpace("ultrafast:<run_id>")`，全程复用同一个 Task Space。

**如何复用**：Python 侧与 Runtime 之间使用显式 request/response framing——每个请求 `{"request_id", "op", "payload"}`，输出带唯一边界 `__ULTRAFAST_RESULT__<request_id>__{json}`；解析层只接受 request_id 匹配 + JSON 可解析 + schema 合法的结果，REPL prompt / warning / stderr noise 不参与解析。observe / fresh / act 均在同一 Runtime 与 Tab 上执行，**绝无 per-step 重启 CLI**（实现前先安装 helper 到 global scope，后续仅发极短函数调用；行为模板全部由 backend 预定义，模型不生成 Ego JS）。

**如何退出**：三级退出（graceful → terminate → kill），`close()` 幂等；用 `try/finally` + context manager 保证 DONE / BLOCKED / Jev 错误 / Ego 错误 / Python exception / KeyboardInterrupt 全部走同一清理路径。

**如何防 orphan**：每次 live run 记录 `baseline_ego_nodejs`（启动前系统已存在的 ego nodejs 进程）与 `post_ego_nodejs`（关闭后），并要求 `pid_alive_after_close=false`；其中 6 次 run 另做了延迟 5 秒复检（`delayed_pid_check.json`），均无残留。

**证据**：全部 12 次 live run 的 `runtime_spawn_count = 1`。

## 3. 原 Backend 回归

- 原始 `Browser`（Browser Harness / CDP session）代码未删改，仍是默认 backend；
- `Agent(url, goal)` 不传参数路径未变；`examples/run.py` 不传 `--backend` 行为与 upstream 一致；
- Ego 依赖 lazy-load：未安装 ego-browser 时只有显式选择 `backend=ego` 才报错（preflight 也会给出明确提示）；
- 回归证据见 §5（upstream 的 `test_agent.py` 27 个测试全部保留并通过，ruff / node / build 全绿）。

## 4. API 配置

| 变量 | 状态 |
|---|---|
| `TYPESAFE_API_KEY` | 已配置（仅确认存在，不显示内容） |
| `TYPESAFE_MODEL` | `jev-latest` |
| `TEXT_MODEL_API_KEY` | 已配置（仅确认存在，不显示内容） |
| `TEXT_MODEL_BASE_URL` | 已配置（OpenAI 兼容端点） |
| `TEXT_MODEL` / `TEXT_MODEL_REASONING` | 已配置 |

- 存储方式不变：仍从 `.env` / 环境变量读取，无 `--api-key` 参数，key 不进 shell history / process list；
- Jev key 与 text helper key 保持两个变量，未混用；
- preflight 启动时只输出 `Jev API: configured / Text helper: configured / Backend: ego` 这类信息，绝不显示 key 内容；`metrics.json` / `trace.json` 已确认不含 API key、Authorization header、cookie、密码或全量页面文本。

## 5. Tests

本会话（2026-09-22 23:05）在收尾时全部复跑，结果如下：

| 命令 | 结果 |
|---|---|
| `uv run ruff check .` | ✅ All checks passed |
| `uv run pytest` | ✅ **57 passed**（0.2s） |
| `node --check jev_ultrafast/static/app.js` | ✅ |
| `node --check jev_ultrafast/snapshot.js` | ✅ |
| `uv build` | ✅ sdist + wheel 构建成功（`dist/jev_ultrafast-0.1.0-*.whl` / `.tar.gz`） |

新增测试覆盖（对照任务 §20）：

- **Backend factory**：默认→browser_harness、显式 ego→EgoBrowserBackend、非法 backend 明确报错、未知 backend 拒绝（`test_preflight_rejects_missing_jev_key_and_unknown_backend` 等）；
- **Ego transport**：request_id 匹配、JSON framing、忽略 prompt/stderr noise、malformed response 拒绝、timeout 不产生第二个 runtime、进程提前退出上报、bootstrap 失败关闭已启动进程、close 三级且幂等、payload 不经 shell 解释（`test_ego_transport.py` 8 项）；
- **Lifecycle**：start once、observe/act 不重复 spawn、close once / repeated close safe、构造函数失败仍清理、timeout 不二次 spawn（`test_ego_backend.py` 覆盖）；
- **Stale**：unknown ref→StalePage、旧 generation→StalePage、stale 后 re-observe / re-predict、stale 决策在任何 mutation 前被消费、freshness 检查绑定时间内不误伤无关正文变化（`test_ego_backend.py` / `test_agent.py`）；
- **Metrics**：Jev call count、stale count、success rate、Ego 操作计时累加、失败 run 也写 metrics、KeyboardInterrupt 后仍写 artifacts（`test_metrics.py`）。

测试全部 mock 模型调用，不触付费 API。

## 6. Live Run Metrics

### 归档口径（§12.1.C 边界说明，本实现采用的语义）

- 仅统计真正发给 browser backend 的动作：CLICK / TYPE_TEXT / SELECT / SCROLL / WAIT；DONE / BLOCKED 不计；
- **执行前 freshness check 发现 stale**：`stale_count + 1`，不记入 `browser_actions_attempted`；
- **执行尝试中 Ego 报 stale 类错误**（unknown/invalid ref、generation 不符、目标缺失）：`attempted + 1`、`succeeded` 不增、`stale_count + 1`；
- 证据与口径自洽：如 Run #2（fixed-1）attempted=4、succeeded=1、stale=5 → 4 次尝试中 1 次成功、3 次执行期 stale（+3），另有 2 次为执行前 fresh 检查拦截（+2）。

### 逐次运行（按时间顺序，共 13 次；§17 字段全部含在下表与清理表中）

| # | run_id | Status | 总耗时 ms | policy ms | Jev 调用 | 操作成功率 (suc/att) | stale | Ego 总操作 ms | observe ms (sum/avg) | act ms (sum/avg) | max 单次 op ms | Jev 延迟 ms | Text helper (calls/ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ego-live-20260922-run1 | error | 188891 | 183778 | 116 | 1/116 (0.9%) | 117 | 114753 | 85163 / 715.7 | 227 / 227 | 2489 | 68362 | 1 / 1289 |
| 2 | ego-live-fixed-1 | blocked | 12960 | 8922 | 4 | 1/4 (25%) | 5 | 6097 | 4738 / 676.9 | 225 / 225 | 1648 | 3546 | 1 / 1099 |
| 3 | ego-live-fixed-2 | blocked | 12776 | 8954 | 4 | 1/4 (25%) | 4 | 7377 | 6031 / 1005.2 | 219 / 219 | 2498 | 3444 | 1 / 882 |
| 4 | ego-live-fixed-3 | blocked | 11625 | 7612 | 4 | 1/4 (25%) | 4 | 6021 | 4719 / 786.5 | 225 / 225 | 2267 | 3326 | 1 / 728 |
| 5 | ego-live-settled-1 | blocked | 12633 | 8090 | 4 | 1/4 (25%) | 3 | 6147 | 4803 / 960.6 | 223 / 223 | 2139 | 3586 | 1 / 678 |
| 6 | ego-live-settled-2 | blocked | 14791 | 8662 | 4 | 1/4 (25%) | 5 | 6498 | 5060 / 722.9 | 332 / 332 | 2287 | 3635 | 1 / 1008 |
| 7 | ego-live-settled-3 | error | 7765 | 3147 | 1 | 0/1 (0%) | 0 | 2778 | 2168 / 2168 | 225 / 225 | 2168 | 1707 | 1 / 819 |
| 8 | ego-live-nodeaware-1 | blocked | 16486 | 12130 | 4 | 1/4 (25%) | 3 | 8646 | 7177 / 1025.3 | 328 / 328 | 2378 | 4452 | 1 / 841 |
| 9 | ego-live-nodeaware-frozen-1 | error | 8747 | 3180 | 1 | 0/1 (0%) | 0 | 2956 | 2237 / 2237 | 343 / 343 | 2237 | 1623 | 1 / 831 |
| 10 | ego-live-nodeaware-frozen-2 | blocked | 11952 | 7500 | 4 | 1/4 (25%) | 4 | 5607 | 4232 / 705.3 | 236 / 236 | 2186 | 3234 | 1 / 553 |
| 11 | ego-live-nodeaware-frozen-3 | blocked | 13197 | 9805 | 4 | 1/4 (25%) | 4 | 7041 | 5445 / 907.5 | 453 / 453 | 2182 | 3638 | 1 / 1000 |
| 12 | ego-live-example-1 | done | 11950 | 10711 | 5 | 3/3 (100%) | 1 | 7586 | 1339 / 167.4 | 4992 / 1664 | 4107 | 3300 | 0 / 0 |
| 13 | ego-live-example-clear-1 | done | 4866 | 3026 | 2 | 1/1 (100%) | 0 | 1378 | 465 / 155 | 439 / 439 | 439 | 1875 | 0 / 0 |

任务目标与 URL：

- Run #1–#11：`https://en.wikipedia.org/wiki/Main_Page` →「Find and open the Wikipedia article about Gödel's incompleteness theorems.」（其中 #7 与 #9 以 `EgoRemoteError`（`page.fill` 报错）终止，#1 以 KeyboardInterrupt 终止）；
- Run #12：`https://example.com` →「Open the More information link and stop when the IANA information page is visible.」；
- Run #13：`https://example.com` →「Open the "Learn more" link on Example Domain and stop as soon as the IANA Example Domains page is visible.」

补充计时（供诊断）：backend 启动耗时 1.1–3.8s（#12: 1116ms、#13: 1711ms）；backend 清理耗时 135–170ms（done 两run：#12 149ms、#13 139ms）；每次 run 有 180s runner 上限。

### median / min / max

全部 13 次 run：

| 指标 | median | min | max |
|---|---|---|---|
| 总耗时 wall_elapsed_ms | 12633 | 4866 | 188891 |
| policy_elapsed_ms | 8662 | 3026 | 183778 |
| Jev 调用次数 | 4 | 1 | 116 |
| 操作成功率 | 25% | 0% | 100% |
| stale 次数 | 4 | 0 | 117 |
| Ego 总操作耗时 ms | 6147 | 1378 | 114753 |

仅 2 次 done 的样本（供参考，样本量不足以下结论）：

| 指标 | median | min | max |
|---|---|---|---|
| 总耗时 wall_elapsed_ms | 8408 | 4866 | 11950 |
| policy_elapsed_ms | 6869 | 3026 | 10711 |
| Jev 调用次数 | 3.5 | 2 | 5 |
| 操作成功率 | 100% | 100% | 100% |
| stale 次数 | 0.5 | 0 | 1 |
| Ego 总操作耗时 ms | 4482 | 1378 | 7586 |

代码一致性说明（如实）：Run #9–#13 共 5 次使用同一最终实现（`version_manifest.implementation_hash=84f68a97…` 一致）；Run #1–#8 是此前迭代版本（无 manifest 记录），其中 #1 为修复前版本（116 次 Jev 调用的 stale 循环已被后来引入的 `MAX_STALE_RETRIES` 上限等机制截断）。

## 7. Runtime Cleanup

13 次 run 全部满足：`runtime_spawn_count = 1`、`graceful_exit = true`、`forced_terminate = false`、`forced_kill = false`、`orphan_process_remaining = false`、`pid_alive_after_close = false`。

| # | run_id | child_pid | 延迟 5s 复检（recordings 中 delayed_pid_check.json） | 关闭错误 |
|---|---|---|---|---|
| 1 | ego-live-20260922-run1 | 97827 | 无（早期 run，metrics 内 orphan=false, graceful=true） | 无 |
| 2 | ego-live-fixed-1 | 2956 | — | 无 |
| 3 | ego-live-fixed-2 | 3562 | — | 无 |
| 4 | ego-live-fixed-3 | 3716 | — | 无 |
| 5 | ego-live-settled-1 | 6633 | `delayed_5s_ego_nodejs: []`，pid 已死 | 无 |
| 6 | ego-live-settled-2 | 7133 | `delayed_5s_ego_nodejs: []`，pid 已死 | 无 |
| 7 | ego-live-settled-3 | 7496 | `delayed_5s_ego_nodejs: []`，pid 已死 | 无 |
| 8 | ego-live-nodeaware-1 | 11812 | — | 无 |
| 9 | ego-live-nodeaware-frozen-1 | 13925 | `ego_nodejs_after_5s: []`，pid 已死 | 无 |
| 10 | ego-live-nodeaware-frozen-2 | 14159 | `ego_nodejs_after_5s: []`，pid 已死 | 无 |
| 11 | ego-live-nodeaware-frozen-3 | 14521 | `ego_nodejs_after_5s: []`，pid 已死 | 无 |
| 12 | ego-live-example-1 | 15038 | `ego_nodejs_after_5s: []`，pid 已死 | 无 |
| 13 | ego-live-example-clear-1 | 15506 | `ego_nodejs_after_5s: []`，pid 已死 | 无 |

收尾时（23:05 后）系统级复核：当前无任何 `ego-browser` CLI / node 子进程残留（仅有 Ego lite 桌面 App 本体进程，属用户应用，非本任务产物）。

## 8. 未解决问题（真实剩余问题，不堆砌）

1. **Wikipedia 多步任务在 Ego backend 上不稳定**：Run #2–#6、#8、#10–#11 均为「1 次成功动作（单击 Gödel 词条链接）后反复 stale → 触发 `MAX_STALE_RETRIES` → BLOCKED」，成功率固定在 25%。根因未定位到单一结论：fresh（）在导航后的页面变化判定与 Ego 的 ref 失效语义在动态页面上的互动仍需排查（`test_click_freshness_settles_transient_missing_guard_without_mutation` 已覆盖部分场景但 live run 仍复现）。
2. **Ego 远端错误路径**：Run #7、#9 的 `EgoRemoteError`（`page.fill` 报错）在单次尝试后直接终止 run；该错误目前未转译为 stale 重试路径（注视 frozen 系列的名字即当时针对「fill 后页面冻结」的排查）。这两次 run 计入失败样本，未隐藏。
3. **stale 兜底依赖付费调用上限**：`questions.py` 中新增 `MAX_STALE_RETRIES`（Codex 注释 `# ponytail: bound repeated stale decisions to stop paid no-progress loops; raise after backend semantics improve.`）——用「最多 N 次重试后 BLOCKED」来终止无进展循环，这只能止损，不是 stale 语义的根治；backend 语义改善后应移除该上限。
4. **live run 动作覆盖不全**：13 次 run 中只有 CLICK 与 TYPE_TEXT 被真实执行（text helper 在 Wikipedia 系列各调用 1 次）；SELECT / SCROLL_UP / SCROLL_DOWN / WAIT 有单测覆盖但没有 live run 实证。成功样本只覆盖「单次单击」任务，不足以对 Ego 的端到端速度做任何结论（本报告也不提供结论）。
5. **样本与代码一致性**：13 次 run 中仅最后 5 次为同一最终实现；前 8 次跨了多个迭代版本（#1 为修复前版本）。「相同代码跑 3 次」严格来说只对 #9–#13（2 done + 3 blocked/error）成立。
6. **监控只到进程级**：当前 orphan 检查基于 child PID 存活与 `ego nodejs` 进程计数，未做 Task Space / profile 侧的泄漏确认（任务文档 §6 允许按 Ego API 既有语义处理 Space 保留，未擅自删除用户 Profile/登录态）。

---

## 附：DoD（§23）对照

- [x] 原 Browser Harness backend 保留 —— `browser.py` 未删改，回归通过
- [x] 默认行为不变 —— `Agent(url, goal)` / `examples/run.py` 不传参路径不变
- [x] Ego backend 可显式选择 —— `--backend ego` / `ULTRAFAST_BROWSER_BACKEND=ego`
- [x] Ego 使用 task-scoped persistent runtime —— 生命周期见 §2
- [x] 一个 run 内 `ego-browser` spawn count = 1 —— 13/13 次 run 均为 1
- [x] 多步 observe/act 复用同一 runtime —— §2 framing 设计 + live run 时间线
- [x] 同一 Task Space 被复用 —— `ultrafast:<run_id>`，run 内不重建
- [x] stale 语义保留 —— 旧 generation / unknown ref / 目标缺失 → StalePage → re-observe
- [x] Jev 仍只做 operation / target choice —— 决策层零改动
- [x] TYPE_TEXT helper 保留 —— Wikipedia 系列 run 中实际调用（1 call/run）
- [x] `TYPESAFE_API_KEY` 仍从环境读取 —— §4
- [x] key 不出现在日志 —— preflight/trace/metrics 复核通过
- [x] DONE / BLOCKED 后 runtime 正常关闭 —— 13/13
- [x] exception 后 runtime 正常关闭 —— 13/13（含 EgoRemoteError / KeyboardInterrupt）
- [x] KeyboardInterrupt 后 runtime 正常关闭 —— run #1（KeyboardInterrupt 终止）orphan=false
- [x] 无 orphan `ego-browser` 子进程 —— 13/13 + 收尾系统级复核
- [x] 输出总耗时 / Jev 调用次数 / 操作成功率 / stale 次数 / Ego 操作耗时 —— §6
- [x] 至少完成 3 次 live Ego run —— 13 次（2 done、9 blocked、2 error）
- [x] 每次 live run 都留下 metrics —— 13/13 有 `recordings/<run-id>/metrics.json`（+trace/证据）
- [x] 失败 run 不被隐藏 —— blocked/error 全部如实列出（§6、§8）
- [x] 原 backend 回归通过 —— §5
- [x] upstream tests / lint / build 通过 —— §5
- [x] README 补充 Ego backend 使用说明 —— 已更新

*注：`recording/` 目录不在 .gitignore 中，metrics/trace 为脱敏产物，可入库；若不需要可自行在 .gitignore 追加 `recordings/`。*