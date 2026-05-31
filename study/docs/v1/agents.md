# `codecross/agents.py` 解析

## 文件设计说明

`agents.py` 是 CodeCross 的智能角色层。

它负责：

```text
组装 prompt
调用 llm.py
解析结构化输出
提供 mock_llm 确定性路径
渲染最终报告
```

它不负责：

```text
执行 pytest
修改 Board 状态
决定 LangGraph 路由
```

这样设计是为了保持边界清楚：

```text
agents.py   负责 Agent 怎么想
executor.py 负责测试怎么跑
board.py    负责证据怎么记
workflow.py 负责谁先谁后
```

## 工具函数

### `_json(data)`

把对象转成缩进 JSON。

主要用于 prompt 拼装，让模型看到结构化上下文。

### `parse_changed_files(diff)`

从 unified diff 中提取变更文件。

它识别：

```text
diff --git a/... b/...
+++ b/...
```

返回去重后的文件路径列表。

### `parse_changed_symbols(diff)`

从新增行中提取 `def` 和 `class` 名称。

例如：

```diff
+def safe_divide(a, b):
```

会提取：

```text
safe_divide
```

### `_default_target_file(diff)`

取 diff 里的第一个变更文件。

用于 mock reviewer 或信息不足时的默认目标。

### `_module_path_from_file(file_path)`

把文件路径转成 Python import 路径。

例如：

```text
examples/sample_project/math_utils.py
```

转成：

```text
examples.sample_project.math_utils
```

Test Case 生成时会用。

### `_infer_risk_areas(diff)`

mock planner 的轻量风险识别。

例如：

- 看到 `shell=True` -> command injection
- 看到 token/secret -> secret handling
- 看到 divide -> boundary conditions
- 看到 except/raise -> exception flow

真实链路由模型生成风险区域。

## Planner

### `planner_agent(diff, config)`

Planner 的入口。

如果 `mock_llm=True`：

```python
return _mock_planner(diff)
```

否则调用：

```python
call_model_json(..., ReviewPlan)
```

输出 `ReviewPlan`。

### `_mock_planner(diff)`

确定性 planner。

用于：

- 单测
- 本地 mock 调试
- 不消耗模型额度

它根据 diff 提取：

- changed_files
- changed_symbols
- risk_areas

并生成固定 review_focus/testing_focus。

## Reviewer

### `reviewer_agent(reviewer, diff, review_plan, config)`

单个 reviewer 的执行入口。

它会读取：

- reviewer 配置
- diff
- review_plan

然后输出：

```python
ReviewerResult(
    findings=[...],
    test_intents=[...],
)
```

真实链路中，它调用：

```python
call_model_json(..., ReviewerResult)
```

如果模型失败，会返回带 `errors` 的 `ReviewerResult`，不中断整个 workflow。

### `_normalize_reviewer_result(result, reviewer)`

修正模型返回的 reviewer 元信息。

原因：

模型可能把 `source_reviewer` 写错或漏写。

这里统一覆盖：

- reviewer_name
- model
- finding.source_reviewer
- finding.model
- intent.source_reviewer

### `_mock_reviewer(reviewer, diff)`

确定性 reviewer。

逻辑：

- DeepSeek mock 偏逻辑/runtime。
- Anthropic mock 偏安全/维护性。

例如 diff 里有 `safe_divide`，DeepSeek mock 会产生：

```text
Division helper does not handle a zero denominator.
```

并给出一个 `TestIntent`。

Anthropic mock 如果看到 `shell=True`，会产生 command injection finding。

## Test Strategy

### `test_strategy_agent(round_index, targets, board_summary, config)`

根据当前 verification targets 生成测试策略。

无 targets 时返回空策略。

真实链路调用模型输出 `TestStrategy`。

mock 链路调用 `_mock_test_strategy`。

### `_mock_test_strategy(round_index, targets)`

确定性测试策略。

核心是：

```text
为每个 target 生成最小可复现测试。
```

## Test Case

### `test_case_agent(round_index, strategy, targets, test_intents, config)`

把 finding/test_intent 转成具体 `TestCase`。

真实链路调用模型输出 `TestCaseBatch`。

mock 链路调用 `_mock_test_cases`。

### `_normalize_test_batch(batch, round_index)`

修正模型返回的 test case：

- 设置 round_index
- 修正非法 timeout

### `_mock_test_cases(round_index, targets, test_intents)`

确定性测试生成器。

当前只内置：

```text
safe_divide / divide 的零分母测试
```

会生成类似：

```python
from examples.sample_project.math_utils import safe_divide

def test_safe_divide_zero_denominator_returns_none():
    assert safe_divide(10, 0) is None
```

如果无法安全生成测试，会创建 manual/review_required 类型，并记录 skipped target。

## Test Result Analyzer

### `test_result_analyzer_agent(round_index, results, config)`

解释 executor 结果并给 Board 状态建议。

V1 这里是规则化的：

```text
failed -> supported
passed -> refuted
error/timeout/unsafe/skipped -> inconclusive
```

如果结果不清楚，会设置：

```python
need_targeted_review = True
```

## Coverage Gap

### `coverage_gap_agent(board_summary, config)`

汇总仍未覆盖或未验证的问题。

它会把：

- board_summary.coverage_gaps
- verification_queue 里仍未解决的 finding

合并成列表。

## Targeted Reviewer

### `targeted_reviewer_agent(round_index, targets, results, config)`

只处理证据不足的目标。

触发场景：

- executor error
- timeout
- unsafe
- skipped
- 测试结果不清楚

如果所有目标已经有 failed 结果支持，则不触发 targeted review。

真实链路调用模型输出 `TargetedReview`。

mock 链路返回 inconclusive 建议。

## Evaluator

### `evaluator_agent(reviewer_results, findings, board_summary, config)`

规则优先的最终风险判断。

它不是简单问 LLM：

```text
风险是什么？
```

而是先看 Board 状态。

核心规则：

```text
超过一半 reviewer 失败 -> unknown
critical finding supported -> critical
high finding supported -> high
critical finding unresolved -> high
high finding unresolved -> medium
medium finding supported -> medium
executor error -> medium
否则 -> low
```

输出 `EvaluatorReport`。

### `high_risk_review_agent(evaluator_report, findings, board_summary, config)`

如果 evaluator 判断需要高风险复核，则执行。

触发条件：

```python
evaluator_report.needs_high_risk_review
```

真实链路调用模型输出 `HighRiskReviewReport`。

mock 链路返回固定阻断建议。

## 报告渲染

### `render_final_report(...)`

把 ReviewPlan、ReviewerResult、BoardSummary、EvaluatorReport、HighRiskReviewReport 汇总成 Markdown。

包括：

- Risk
- Review Plan
- Reviewers
- Review Board
- Evidence Summary
- High Risk Review

## 这个文件的关键思想

`agents.py` 的职责是“产生结构化智能输出”，不是做最终裁判。

Reviewer 提出 finding，Board 决定状态。

Test Case Agent 生成测试，Executor 真实执行。

Evaluator 总结风险，但风险等级优先由规则和 Board 证据决定。

