# `codecross/board.py` 解析

## 文件设计说明

`board.py` 是 CodeCross 的证据黑板和规则状态机。

它不调用 LLM，不执行测试，只做确定性的证据管理：

```text
收集 reviewer 输出
初始化 finding verification
登记 evidence
选择下一轮验证目标
根据 TestResult 更新 finding 状态
决定 verification loop 是否继续
生成 BoardSummary
```

这样设计的原因是：

- Reviewer 只是提出风险假设，不能直接决定结论。
- LLM 输出不稳定，Board 必须尽量规则化。
- 测试证据应该优先于模型话术。
- loop 的继续/停止应该由 Board 状态驱动，而不是 Agent 想继续就继续。

## 常量

### `SEVERITY_ORDER`

把严重等级映射成数字：

```text
low      -> 0
medium   -> 1
high     -> 2
critical -> 3
```

用于排序和找最高严重等级。

## 函数详解

### `severity_rank(finding)`

返回 finding 的严重等级数字。

用于：

- cluster 内找最严重 finding
- verification queue 排序

### `collect_review_outputs(reviewer_results)`

把多个 reviewer 的输出合并成：

```python
findings
test_intents
```

每个 reviewer 返回一个 `ReviewerResult`，里面有：

- `findings`
- `test_intents`

Board 先统一收集，后续再初始化验证状态。

### `init_finding_verifications(findings)`

为每个 finding 初始化验证状态。

初始状态：

```text
unverified
```

这体现一个关键原则：

```text
Reviewer 提出 finding，不代表 finding 已经成立。
```

### `init_static_evidence(findings)`

把 reviewer 自带的 evidence 文本登记为 `static_evidence`。

强度是：

```text
weak
```

因为这是模型静态观察，不是真实测试结果。

### `_cluster_key(finding)`

给 finding 生成轻量聚类 key。

组成：

- file_path
- category
- 起始行号
- problem 前几个关键词

这是 V1 简单去重方案。

### `cluster_findings(findings)`

把相似 finding 聚成 `FindingCluster`。

输出包含：

- `finding_ids`
- `representative_problem`
- `reviewers`
- `severity`

用途：

```text
多个 reviewer 独立命中同类问题时，提高验证优先级。
```

### `_finding_by_id(findings)`

内部辅助函数，把 finding list 转成 dict。

方便根据 ID 查找 finding。

### `build_verification_queue(findings, verifications, clusters)`

构建下一轮验证队列。

会进入队列的 finding：

```text
状态是 unverified / inconclusive
并且满足：
  severity 是 high / critical
  或 medium 被多个 reviewer 命中
  或有关联 test_intent
```

不会进入队列的状态：

```text
supported
refuted
not_testable
```

这个函数决定：

```text
哪些 finding 需要进入 Evidence Verification Loop。
```

### `build_board_summary(...)`

构建 Board 当前状态快照。

统计内容：

- reviewer_count
- finding_count
- test_intent_count
- test_case_count
- test_round_count
- passed_count
- failed_count
- error_count
- skipped_count
- finding_clusters
- finding_verifications
- verification_queue
- failed_tests
- coverage_gaps
- evidence_map
- stop_reason

Evaluator 和最终报告主要读取这个对象。

### `select_next_verification_targets(findings, board_summary, runner_config)`

从 `verification_queue` 里选出本轮要验证的 finding。

最多选：

```python
runner_config.max_tests_per_round
```

workflow 中的 `select_verification_targets_node` 调用它。

### `_result_to_evidence(result)`

把 `TestResult` 转成 `EvidenceItem`。

映射规则：

```text
failed -> test_failure, strong
passed -> test_pass, medium
unsafe -> coverage_gap, medium
其他   -> executor_error, weak
```

这一步把 executor 的事实结果写入 Board 证据系统。

### `update_finding_verifications_from_results(verifications, evidence_map, test_results)`

Board 最核心函数之一。

根据测试结果更新 finding verification。

规则：

```text
result.status == failed
  -> supported

result.status == passed
  -> refuted

result.status == unsafe
  -> not_testable

其他 error/timeout/skipped
  -> inconclusive
```

它同时会：

- 创建 EvidenceItem
- 写入 evidence_map
- 把 evidence_id 绑定到 verification
- 把 test_result_id 绑定到 verification
- 记录状态变化 status_changes

这个函数体现：

```text
测试证据驱动 finding 状态。
```

### `apply_analysis_updates(verifications, analysis)`

应用 Test Result Analyzer 的建议状态。

保护规则：

```python
if verification.status in {"supported", "refuted"}:
    continue
```

也就是说：

```text
已经被测试明确支持/反驳的 finding，不被 analyzer 的软建议覆盖。
```

这是为了保证：

```text
真实测试证据 > 模型解释。
```

### `apply_targeted_review(verifications, evidence_map, targeted_review)`

把 Targeted Reviewer 的复审结论登记成 evidence。

它会生成：

```text
evidence_type = targeted_review_note
```

然后根据：

```python
targeted_review.suggested_status_updates
```

更新 verification。

Targeted Reviewer 主要用于：

- 测试证据不清楚
- executor error
- timeout
- unsafe
- reviewer 结论冲突

### `should_continue_verification(board_summary, round_index, runner_config)`

决定 Evidence Verification Loop 是否继续。

规则：

```text
verification_queue 为空
  -> 停止，verification_queue_empty

round_index >= max_verification_rounds
  -> 停止，max_verification_rounds_reached

否则
  -> 继续，verification_queue_has_unresolved_targets
```

workflow 里会根据它的结果路由：

```text
继续 -> select_verification_targets
停止 -> evaluator
```

### `render_board_markdown(summary)`

把 BoardSummary 渲染成最终报告里的 Markdown。

它只负责展示，不参与决策。

## 最核心的四个函数

如果只抓主线，优先理解：

```text
init_finding_verifications
build_verification_queue
update_finding_verifications_from_results
should_continue_verification
```

它们分别对应：

```text
初始化状态
选择验证目标
根据证据更新状态
决定 loop 是否继续
```

## 这个文件的关键思想

Board 是规则层，不是语义判断层。

它不问 LLM：

```text
你觉得这个 finding 成立吗？
```

它只看：

```text
状态是什么
测试结果是什么
证据关联到哪个 finding
是否还有未收敛目标
```

