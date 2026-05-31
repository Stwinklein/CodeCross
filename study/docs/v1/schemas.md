# `codecross/schemas.py` 解析

## 文件设计说明

`schemas.py` 是 CodeCross 的“数据契约层”。它只定义数据结构，不写业务流程。

这样设计的原因是：

- 多 Agent 系统里，最容易乱的是“每个 Agent 输出到底长什么样”。
- 用 Pydantic schema 固定结构，可以让 Reviewer、Test Runner、Board、Evaluator 之间用统一对象交流。
- LLM 输出必须经过 schema 校验，不能让自然语言 dict 在系统里到处流动。
- Board 状态、测试结果、最终报告都可以被序列化、保存、评测和调试。

简单说：

```text
schemas.py 决定 CodeCross 系统里的所有数据对象长什么样。
```

## 枚举类型

文件顶部定义了一批 `Literal` 类型：

- `RiskLevel`
- `Severity`
- `Confidence`
- `Priority`
- `VerificationStatus`
- `EvidenceType`
- `EvidenceStrength`
- `TestType`
- `TestStatus`

这些类型把字符串值限制在固定集合里。

例如 `VerificationStatus` 只能是：

```text
unverified
supported
refuted
inconclusive
not_testable
```

这样可以避免模型输出：

```text
probably_supported
maybe true
confirmed
```

这类系统无法识别的状态。

## 工具函数

### `new_id(prefix: str) -> str`

生成带业务前缀的短 ID。

例如：

```text
finding_abc123
test_abc123
evidence_abc123
```

这样报告里看到 ID 时，能直观看出对象类型。

### `utc_now() -> str`

生成 UTC ISO 时间字符串。证据时间统一用 UTC，避免本地时区影响后续追踪。

## 基类

### `CodeCrossModel`

所有 schema 的共同基类。

配置：

```python
model_config = ConfigDict(extra="ignore", validate_assignment=True)
```

含义：

- `extra="ignore"`：LLM 多输出字段时忽略，不直接崩溃。
- `validate_assignment=True`：后续给字段赋值时也校验类型。
- `__test__ = False`：避免 pytest 把 `TestCase`、`TestResult` 当测试类收集。

## 核心结构

### `LineRange`

描述 finding 在文件里的行号范围。

字段：

- `start`
- `end`

行号未知时允许为空。

### `ReviewPlan`

Planner 的输出。

字段：

- `plan_id`
- `changed_files`
- `changed_symbols`
- `risk_areas`
- `review_focus`
- `testing_focus`
- `context_notes`

它回答：

```text
这次 diff 改了什么？
应该重点审什么？
应该重点测什么？
```

### `Finding`

Reviewer 提出的一个风险假设。

重要理解：

```text
Finding 不是最终事实，只是 reviewer 的怀疑。
```

字段：

- `finding_id`
- `source_reviewer`
- `model`
- `category`
- `severity`
- `confidence`
- `file_path`
- `line_range`
- `problem`
- `evidence`
- `impact`
- `suggestion`
- `reproduction_hint`
- `related_test_intent_ids`

例如 reviewer 说：

```text
safe_divide 没有处理 b == 0
```

这会成为一个 `Finding`，但 Board 初始仍会把它记为 `unverified`。

### `FindingVerification`

Board 给每条 finding 维护的验证状态。

字段：

- `finding_id`
- `status`
- `evidence_ids`
- `related_test_result_ids`
- `related_targeted_review_ids`
- `last_updated_round`
- `reason`
- `confidence`
- `next_action`

它回答：

```text
这个 finding 目前被证据支持了吗？
被反驳了吗？
还是还没验证？
```

### `EvidenceItem`

Board 中的证据条目。

证据来源可以是：

- reviewer 静态观察
- pytest failed
- pytest passed
- executor error
- targeted review note
- coverage gap
- manual note

字段：

- `evidence_id`
- `evidence_type`
- `source`
- `related_finding_ids`
- `related_test_case_id`
- `related_test_result_id`
- `related_targeted_review_id`
- `summary`
- `raw_reference`
- `strength`
- `created_round`
- `created_at`

### `TestIntent`

Reviewer 给 Test Runner 的测试意图。

它不是可执行测试代码，而是：

```text
我认为应该测试这个场景，因为它能验证某个风险。
```

字段：

- `test_intent_id`
- `source_reviewer`
- `target_file`
- `target_symbol`
- `scenario`
- `reason`
- `expected_behavior`
- `risk_category`
- `priority`
- `related_finding_ids`

### `TestCase`

Test Case Agent 生成的具体测试。

字段：

- `test_case_id`
- `source_agent`
- `source_test_intent_ids`
- `related_finding_ids`
- `round_index`
- `test_type`
- `target_file`
- `target_symbol`
- `test_name`
- `test_code`
- `expected_behavior`
- `assertions`
- `timeout_seconds`
- `requires_external_service`
- `safety_level`

V1 主要支持 `pytest` 类型。

### `TestResult`

Executor 真实执行测试后返回的结果。

字段：

- `test_result_id`
- `test_case_id`
- `round_index`
- `status`
- `exit_code`
- `stdout`
- `stderr`
- `duration_ms`
- `failure_summary`
- `error_type`
- `is_reproducible`
- `related_finding_ids`

Board 主要根据 `status` 更新 finding verification。

### `TestRound`

Evidence Verification Loop 的一轮记录。

字段：

- `round_index`
- `input_focus`
- `generated_test_case_ids`
- `executed_test_case_ids`
- `new_failures`
- `new_coverage_notes`
- `verification_status_changes`
- `continue_verification`
- `stop_reason`

它用于追踪每一轮验证做了什么、为什么继续或停止。

### `TargetedReview`

定向复审结果。

它不是第一轮 reviewer，而是在证据不足、冲突、测试错误时被触发。

字段：

- `targeted_review_id`
- `round_index`
- `target_finding_ids`
- `input_test_result_ids`
- `reviewer_name`
- `model`
- `analysis`
- `suggested_status_updates`
- `new_test_intents`
- `not_testable_reasons`

### `FindingCluster`

轻量 finding 聚类。

字段：

- `cluster_id`
- `finding_ids`
- `representative_problem`
- `reviewers`
- `severity`

用途：判断多个 reviewer 是否独立命中了同类问题。

### `BoardSummary`

Board 当前状态快照。

字段很多，核心是：

- reviewer 数量
- finding 数量
- test intent 数量
- test case 数量
- 测试通过/失败/error 统计
- finding clusters
- finding verifications
- verification queue
- evidence map
- stop reason

Evaluator 和最终报告主要读取这个对象。

### `EvaluatorReport`

Evaluator 的最终风险判断。

字段：

- `risk_level`
- `confidence`
- `top_findings`
- `evidence_summary`
- `failed_tests_summary`
- `coverage_gaps`
- `verification_summary`
- `recommended_action`
- `needs_high_risk_review`
- `reasoning`

### `ReviewerResult`

单个 reviewer 的完整输出。

字段：

- `reviewer_name`
- `model`
- `findings`
- `test_intents`
- `reviewer_notes`
- `errors`

workflow 里多个 reviewer 会 append 成一个列表。

### `TestStrategy`

测试策略 Agent 的输出。

它说明本轮要验证哪些 finding、覆盖目标是什么、什么时候停止。

### `TestCaseBatch`

Test Case Agent 一次返回的一批测试。

字段：

- `test_cases`
- `skipped_targets`

如果某个 finding 无法生成安全测试，会进入 `skipped_targets`。

### `TestResultAnalysis`

Test Result Analyzer 对测试结果的解释。

字段：

- `result_notes`
- `suggested_status_updates`
- `coverage_gaps`
- `need_targeted_review`

### `HighRiskReviewReport`

高风险二次复核输出。

字段：

- `analysis`
- `recommended_action`
- `blocking_findings`

## 这个文件的关键思想

`schemas.py` 是 CodeCross 的边界契约。所有 Agent、Board、Executor、Evaluator 都围绕这些对象协作。

如果后续系统变复杂，优先维护 schema 的稳定性，而不是让每个模块自由传 dict。

