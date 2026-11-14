# Jev Ultrafast × Ego Browser — V2 Reliability Hardening

> 日期：2026-09-23  
> 基线：上一轮 Ego Persistent Runtime Backend 已完成并通过交付  
> 本轮目标：**不扩展总体架构，只修 Ego backend 的稳定性问题，并补齐真实动作覆盖。**

---

# 0. 开始开发前必须先做

## 0.1 先提交当前版本

在开始任何修改之前，先检查当前 Git 状态，并把上一轮已经完成的 Ego backend 版本提交保存。

要求：

```bash
git status
git add .
git commit -m "feat: add persistent Ego browser backend"
```

如果当前已经存在等价 commit，则不要重复提交，但必须：

```bash
git log -1 --oneline
git status
```

确认：

- 当前基线已经有明确 commit；
- 工作区干净；
- 下一轮修改都发生在这个 commit 之后。

建议额外打一个 tag：

```bash
git tag ego-backend-v1-baseline
```

如果 tag 已存在则不要重复创建。

**目的：这一轮主要排查 stale / fill 稳定性，不能把上一轮已经验证可用的 persistent runtime、cleanup、backend factory 一起改乱。**

---

# 1. 这轮到底要解决什么

上一轮已经完成：

- Ego backend；
- task-scoped persistent runtime；
- 一个 run 只 spawn 1 次；
- 任务结束 runtime 自动关闭；
- 无 orphan process；
- 原 Browser Harness backend 保留；
- metrics / trace；
- unit tests / lint / build；
- 真实 live run。

这些部分本轮默认视为：

```text
已收口
不要主动重构
```

当前真正的问题只有：

```text
Ego 在多步任务里容易 stale
↓
Jev 重新判断
↓
又 stale
↓
最终触发 MAX_STALE_RETRIES
↓
BLOCKED
```

以及：

```text
TYPE_TEXT 时出现 EgoRemoteError / page.fill 错误
```

因此本轮名称：

```text
V2 — Ego Backend Reliability Hardening
```

---

# 2. 第一件事：把 stale 根因查清楚

## 大白话

现在可以把流程想成：

```text
Ego 看网页
↓
给页面上的按钮贴号码
↓
Jev 说：“点 3 号”
↓
程序准备去点
```

问题是：

```text
Jev 刚选完 3 号
↓
程序又重新看了一遍网页
↓
Ego 重新给按钮编号
↓
原来的 3 号可能已经作废
↓
程序再去点旧的 3 号
↓
stale
```

所以本轮第一优先级不是增加重试次数，而是确认：

> **是不是我们自己在 Jev 决策和真正执行动作之间，又重新生成了一次 Ego snapshot/ref。**

## 2.1 要检查的完整链路

逐段跟踪：

```text
observe
↓
生成 Jev index ↔ Ego ref
↓
Jev decision
↓
fresh
↓
act
↓
下一次 observe
```

重点确认在：

```text
Jev decision
↓
fresh
↓
act
```

之间是否调用了：

```text
snapshotText()
```

或任何会导致 Ego refMap 重建的等价操作。

## 2.2 如果存在

优先改成：

```text
observe
↓
snapshot 一次
↓
保存本轮 ref
↓
Jev decision
↓
轻量 fresh 检查
↓
直接使用这一轮 observation 对应 ref 执行动作
↓
动作完成
↓
重新 observe
```

不要：

```text
observe
↓
Jev
↓
又 snapshot
↓
再拿旧 ref act
```

## 2.3 observation 必须带 generation

每次 observation：

```text
generation += 1
```

例如：

```text
generation 17
```

这一轮：

```text
Jev index 1 -> Ego @21
Jev index 2 -> Ego @38
```

所有 action 都必须记录：

```text
action_generation = 17
```

如果当前页面已经进入：

```text
generation 18
```

则 generation 17 的 action 必须直接判 stale，不允许执行。

---

# 3. 第二件事：重新设计 fresh，让它只是“检查”，不要重新看完整页面

## 大白话

现在 `fresh()` 的职责应该只是：

> “Jev 刚才看到的东西现在还靠谱吗？”

而不是：

> “我重新完整扫描一次网页，再生成一整套新的按钮编号。”

因为后一种做法很容易让旧编号直接作废。

## 3.1 fresh 应尽量做到

检查：

```text
当前是不是同一个 tab
页面是不是已经发生明显 navigation
目标是不是还存在
目标是不是还可操作
generation 是否仍匹配
```

但不要为了做 fresh 而无条件：

```text
重新 snapshotText()
重新生成 refMap
重新生成整个 action space
```

## 3.2 如果 Ego 当前 API 无法在不 snapshot 的情况下完成 fresh

不要自己猜。

需要：

1. 查 Ego 当前 helper / API；
2. 找有没有稳定定位信息：
   - loc
   - backendNodeId
   - page identity
   - URL / navigation state
   - 其他不重建 refMap 的状态；
3. 优先使用这些信息做轻量检查。

如果最终确认 Ego 的 ref 天生只能依赖“最近一次 snapshot”，那么 `fresh` 就应该避免产生新的 snapshot。

---

# 4. 第三件事：把 EgoRemoteError / page.fill 错误分清楚

## 大白话

现在遇到错误时不能全部归成：

```text
“按钮过期了，重新看一遍吧”
```

因为有些错误确实是 ref stale；

但有些可能是：

```text
输入框不能 fill
参数不对
Ego 内部报错
页面状态不支持
helper 使用方式不对
```

如果所有错误都当 stale：

```text
报错
↓
重新 Jev
↓
重新报错
↓
重新 Jev
```

会白白烧 API。

## 4.1 错误必须分类

至少分：

### A. stale 类

例如：

```text
unknown ref
invalid ref
target detached
target missing
generation mismatch
```

处理：

```text
→ StalePage
→ re-observe
→ Jev 重新决策
```

### B. action error 类

例如：

```text
fill 调用方式错误
参数错误
Ego runtime 内部异常
不支持的控件
```

处理：

```text
→ EgoActionError
```

不要自动当 stale 无限重试。

### C. runtime / transport 类

例如：

```text
CLI bridge断开
runtime退出
request timeout
invalid response
```

处理：

```text
→ EgoTransportError / EgoRemoteError
```

这类也不要伪装成 stale。

## 4.2 TYPE_TEXT 专项检查

重点检查当前实现到底调用：

```text
page.fill
```

还是：

```text
fillInput
```

以及：

```text
Ego ref 是否被错误当成普通 CSS selector
```

确保 TYPE_TEXT 走 Ego 推荐的安全 helper。

---

# 5. 第四件事：稳定后补 SELECT / SCROLL / WAIT 的 live run

## 大白话

上一轮：

```text
CLICK
TYPE_TEXT
```

已经真的跑过。

但：

```text
SELECT
SCROLL
WAIT
```

目前主要只是测试代码证明“理论上能调用”。

这轮在 stale 和 fill 稳定之后，要真正让浏览器跑一次。

## 5.1 要求

至少分别找一个简单、稳定、公开网页验证：

### SELECT

必须真的：

```text
打开下拉框
→ 选择一个值
→ 页面状态变化正确
```

### SCROLL

必须真的：

```text
页面向下滚动
→ 目标进入 viewport
→ 后续 action 成功
```

### WAIT

必须真的：

```text
等待异步页面变化
→ 等待后 observation 正常
```

不要只跑 mocked unit tests。

---

# 6. MAX_STALE_RETRIES 怎么处理

本轮：

```text
保留
```

不要删。

它目前的作用是：

```text
如果 stale 出现异常死循环
↓
及时停下来
↓
防止连续付费调用 Jev
```

但它只能作为：

```text
保险丝
```

不能继续把它当：

```text
stale 问题的解决办法
```

本轮目标是：

```text
正常任务尽量永远不要撞到 MAX_STALE_RETRIES
```

但即使修好 stale，也建议保留一个 no-progress 上限，防止陌生网站再次出现无限循环。

---

# 7. 本轮冻结内容

以下部分除非发现明确 bug，否则不要改架构：

```text
persistent runtime
backend factory
Browser Harness backend
metrics 基本口径
runtime cleanup
三级退出
Ego Task Space 生命周期
API key 配置
Jev 决策模型
text helper 模型
```

原则：

```text
不要为了修 stale 顺手重构整个项目
```

---

# 8. 调试日志要求

为了真正找到 stale 根因，增加一份仅用于开发诊断的事件时间线。

每一步至少记录：

```text
observation_generation
ego_ref
jev_index
operation
fresh_result
act_result
stale_source
error_class
page/url identity
```

例如：

```text
gen=12 observe
index=3 ref=@27

gen=12 jev CLICK index=3

gen=12 fresh=true

gen=12 act CLICK @27 success

gen=13 observe
```

失败时：

```text
gen=18 jev TYPE_TEXT index=4
gen=18 fresh=true
gen=18 act @42
error=UnknownRef
classified=StalePage
```

或：

```text
error=page.fill failed
classified=EgoActionError
```

这样才能知道 stale 是在：

```text
fresh 前
fresh 内
act 前
act 内
navigation 后
```

哪一步发生。

注意：

- 不记录 API key；
- 不记录 password；
- 不记录 cookie；
- 不记录敏感完整网页正文。

---

# 9. 修复后的真实验证

本轮不要再把历史 13 次混在新性能结论里。

修复完成后使用：

```text
同一最终代码
同一 commit
```

重新跑一组全新的 live runs。

## 9.1 Wikipedia 回归

至少连续运行：

```text
5 次
```

目标仍使用上一轮失败最多的任务：

```text
Wikipedia Main Page
→ Find and open the Wikipedia article about Gödel's incompleteness theorems
```

这一项是 stale 修复的核心回归。

每次记录：

```text
status
wall_elapsed_ms
Jev calls
action success rate
stale count
Ego operation ms
```

## 9.2 简单任务回归

Example.com 再跑：

```text
3 次
```

确认修 stale 没有把原来已经能成功的简单任务改坏。

## 9.3 新动作覆盖

SELECT / SCROLL / WAIT：

每种至少：

```text
1 个真实 live task
```

并保留 metrics + trace。

---

# 10. Browser Harness vs Ego A/B

只有满足：

```text
Wikipedia 多步任务明显不再稳定撞 MAX_STALE_RETRIES
```

之后才开始 A/B。

不要在 stale 仍然高发时做性能结论。

## 10.1 A/B 条件

必须：

```text
同一最终 commit
同一 Jev
同一 text model
同一机器
同一任务
```

交替跑：

```text
Browser Harness
Ego
Browser Harness
Ego
...
```

避免一口气先跑完全部 A 再跑全部 B。

## 10.2 至少三个任务

### 简单

```text
Example.com 单击
```

### 中等

```text
Wikipedia 搜索 / 导航
```

### 复杂

优先：

```text
Google Flights
```

如果当前页面不稳定，可替换成其他稳定公开多步任务，但必须记录具体目标。

## 10.3 每 backend 每任务

建议：

```text
3 次
```

如果成本允许：

```text
5 次
```

## 10.4 仍记录五项核心指标

```text
总耗时
Jev 调用次数
操作成功率
stale 次数
backend / Ego 操作耗时
```

只提供：

```text
逐次数据
median
min
max
```

不要替用户给最终“谁更快”的结论。

---

# 11. Runtime 清理要求继续保留

每次 live run 继续确认：

```text
runtime_spawn_count = 1
orphan_process_remaining = false
pid_alive_after_close = false
```

修 stale 不能破坏上一轮已经稳定的 runtime lifecycle。

如果出现任何 orphan：

```text
本轮不得标记完成
```

---

# 12. Tests

至少新增 / 更新：

## stale lifecycle

- fresh 不会无意中重建 refMap；
- observation generation 正确；
- 旧 generation action 被拒绝；
- action 前后 ref 生命周期正确；
- navigation 后重新 observe；
- no-progress 不无限调用 Jev。

## error classification

- unknown ref -> StalePage；
- detached -> StalePage；
- fill 参数错误 -> EgoActionError；
- runtime timeout -> EgoTransportError；
- 不同错误不会全部错误转 stale。

## TYPE_TEXT

- 正确使用 Ego helper；
- ref 不被当成 CSS selector；
- successful fill；
- failed fill 分类正确。

## regression

上一轮全部测试仍通过。

---

# 13. DoD

只有全部满足才算完成：

- [ ] 开始开发前已提交上一轮基线 commit；
- [ ] 工作区基线可恢复；
- [ ] persistent runtime 未被重构破坏；
- [ ] 原 Browser Harness backend 回归通过；
- [ ] 已定位并记录 stale 的真实触发位置；
- [ ] fresh 不会无意义重建 action ref；
- [ ] observation generation 与 action generation 正确绑定；
- [ ] Ego ref 生命周期明确；
- [ ] stale / action error / transport error 已分类；
- [ ] `page.fill` / TYPE_TEXT 错误路径已修；
- [ ] `MAX_STALE_RETRIES` 保留为安全保险丝；
- [ ] Wikipedia 最少 5 次全新 live run；
- [ ] Example.com 最少 3 次全新 live run；
- [ ] SELECT 有 live 实证；
- [ ] SCROLL 有 live 实证；
- [ ] WAIT 有 live 实证；
- [ ] 每次 live run 有完整 metrics；
- [ ] 每次 runtime_spawn_count = 1；
- [ ] 每次 orphan_process_remaining = false；
- [ ] 每次 pid_alive_after_close = false；
- [ ] unit tests / lint / build 全绿；
- [ ] stale 稳定后完成 Browser Harness vs Ego A/B；
- [ ] A/B 使用同一最终 commit；
- [ ] 最终报告不混入上一轮历史 run 作为本轮性能样本。

---

# 14. 最终交付报告

按以下格式：

## 1. Baseline

```text
baseline commit:
baseline tag:
working tree before development:
```

## 2. Stale 根因

用大白话说明：

```text
到底哪里让 ref 失效
为什么
怎么修
```

不要只说“已优化 stale”。

## 3. Error Classification

列：

```text
StalePage
EgoActionError
EgoTransportError / EgoRemoteError
```

分别什么情况下触发。

## 4. TYPE_TEXT 修复

说明：

```text
原问题
根因
修复
live 结果
```

## 5. Tests

完整命令 + 结果。

## 6. 新 Live Runs

只列本轮最终代码 run。

分别：

```text
Wikipedia × >=5
Example.com × >=3
SELECT
SCROLL
WAIT
```

## 7. A/B

分别列：

```text
Browser Harness
Ego
```

逐次数据 + median / min / max。

不要给最终性能结论。

## 8. Runtime Cleanup

确认每次：

```text
runtime_spawn_count=1
orphan_process_remaining=false
pid_alive_after_close=false
```

## 9. Remaining Issues

只写真正还没解决的问题。

---

# 15. 本轮一句话原则

> **先把 Ego backend 从“能跑”修到“多步任务稳定跑”，再谈速度；不要用更多重试掩盖 ref/stale 生命周期问题，也不要为修这一点重构已经稳定的 persistent runtime。**
