# `codecross/workflow.py` 解析

## 文件设计说明

`workflow.py` 是 LangGraph 编排层。

它只负责：

```text
定义 State
定义节点函数
注册节点
注册边和条件边
编译图
启动 app.invoke
```

它不负责：

- prompt 细节
- 模型解析
- pytest 执行细节
- Board 状态规则

这样设计的原因是：

```text
workflow.py 只回答“谁先谁后”
agents.py   回答“Agent 怎么想”
board.py    回答“证据怎么更新”
executor.py 回答“测试怎么执行”
```

## State 设计

### `CodeCrossState`

LangGraph 在节点之间传递的共享状态。

重要字段：

- `diff`
- `project_root`
- `config`
- `review_plan`
- `reviewer_results`
- `findings`
- `test_intents`
- `verifications`
- `evidence_map`
- `board_summary`
- `round_index`
- `verification_targets`
- `generated_test_cases`
- `all_test_cases`
- `test_results`
- `all_test_results`
- `test_rounds`
- `targeted_reviews`
- `coverage_gaps`
- `continue_verification`
- `evaluator_report`
- `high_risk_review`
- `final_report`

`reviewer_results` 使用：

```python
Annotated[list[ReviewerResult], operator.add]
```

原因：多个 reviewer fan-out 后，可以把结果 append 回同一个列表。

## 节点函数详解

### `_summary_from_state(state, stop_reason="")`

从当前 state 构造 `BoardSummary`。

它只是调用 `build_board_summary`，把 state 中的各类对象交给 Board。

### `planner_node(state)`

Graph 节点：运行 Planner。

调用：

```python
planner_agent(state["diff"], state["config"])
```

返回：

```python
{"review_plan": ReviewPlan}
```

### `make_reviewer_node(index)`

动态创建 reviewer 节点函数。

为什么要动态：

```text
reviewer 数量来自 config.enabled_reviewers()
```

V1 默认两个 reviewer，后续可以变成三个、四个。

内部 `_node(state)` 会：

1. 根据 index 取 reviewer 配置。
2. 调用 `reviewer_agent`。
3. 返回：

```python
{"reviewer_results": [result]}
```

### `initial_board_node(state)`

Graph 节点：初始化 Board。

流程：

1. 收集 reviewer 输出：

```python
findings, intents = collect_review_outputs(...)
```

2. 初始化 verification：

```python
verifications = init_finding_verifications(findings)
```

3. 初始化 static evidence：

```python
evidence_map = init_static_evidence(findings)
```

4. 初始化 loop 相关列表：

- all_test_cases
- all_test_results
- test_rounds
- coverage_gaps
- targeted_reviews

5. 构建 `board_summary`。

这是 reviewer fan-in 的地方。

### `select_verification_targets_node(state)`

Graph 节点：选择本轮验证目标。

流程：

1. `round_index + 1`
2. 调用：

```python
select_next_verification_targets(...)
```

3. 返回本轮 targets。

这是 Evidence Verification Loop 的入口。

### `route_after_select(state)`

条件路由函数。

规则：

```python
if state.get("verification_targets"):
    return "test_strategy"
return "evaluator"
```

含义：

```text
有待验证目标 -> 进入测试验证
没有目标 -> 直接最终评估
```

### `test_strategy_node(state)`

运行 Test Strategy Agent。

输入：

- round_index
- verification_targets
- board_summary

输出：

```python
{"test_strategy": TestStrategy}
```

### `test_case_node(state)`

运行 Test Case Agent。

输入：

- test_strategy
- verification_targets
- test_intents

输出：

- generated_test_cases
- all_test_cases
- coverage_gaps

如果某些 finding 无法生成安全测试，会记录 coverage gap。

### `executor_node(state)`

运行确定性 executor。

调用：

```python
execute_test_cases(...)
```

输出：

- test_results：本轮结果
- all_test_results：累计结果

### `test_result_analyzer_node(state)`

分析本轮测试结果。

输出：

```python
{"test_result_analysis": TestResultAnalysis}
```

### `coverage_gap_node(state)`

汇总覆盖缺口。

它会结合：

- 已有 coverage_gaps
- test_result_analysis.coverage_gaps
- board_summary.verification_queue

### `route_after_coverage_gap(state)`

条件路由。

规则：

```python
if state["test_result_analysis"].need_targeted_review:
    return "targeted_review"
return "update_board"
```

注意：

无论是否走 targeted_review，最后都会进入 update_board。

含义：

```text
证据不清楚 -> 先 targeted_review -> update_board
证据清楚 -> 直接 update_board
```

### `targeted_review_node(state)`

运行可选定向复审。

如果有 targeted review，会追加到 `targeted_reviews`。

### `update_board_node(state)`

Graph 中非常核心的节点。

流程：

1. 用测试结果更新 verification：

```python
update_finding_verifications_from_results
```

2. 应用 Test Result Analyzer 建议：

```python
apply_analysis_updates
```

3. 应用 Targeted Review：

```python
apply_targeted_review
```

4. 生成 `TestRound`。
5. 构建新 BoardSummary。
6. 调用：

```python
should_continue_verification(...)
```

7. 写入：

- continue_verification
- stop_reason

### `route_after_update_board(state)`

真正形成 loop 的条件路由。

规则：

```python
if state.get("continue_verification"):
    return "select_verification_targets"
return "evaluator"
```

这就是 Board-driven Evidence Verification Loop 的回边。

### `evaluator_node(state)`

运行最终 evaluator。

输入：

- reviewer_results
- findings
- board_summary

输出：

```python
{"evaluator_report": EvaluatorReport}
```

### `route_after_evaluator(state)`

如果 evaluator 认为需要高风险复核：

```text
evaluator -> high_risk_review
```

否则：

```text
evaluator -> final_report
```

### `high_risk_review_node(state)`

运行 high risk reviewer。

只有 high/critical 时才有实际内容。

### `final_report_node(state)`

生成 Markdown 最终报告。

调用：

```python
render_final_report(...)
```

## 图构建

### `build_graph(config)`

构建并编译 LangGraph。

过程：

1. 创建 StateGraph。
2. 注册 planner。
3. 根据 reviewer 配置动态注册 reviewer 节点。
4. 注册 Board/Test/Evaluator 节点。
5. 注册普通边。
6. 注册条件边。
7. `graph.compile()`。

重点理解：

```text
add_node / add_edge 只是注册图，不执行节点。
app.invoke 才执行节点。
```

### 普通边

例如：

```python
graph.add_edge("test_strategy", "test_case")
```

意思是 `test_strategy` 执行完固定进入 `test_case`。

### 条件边

例如：

```python
graph.add_conditional_edges(
    "select_verification_targets",
    route_after_select,
    {"test_strategy": "test_strategy", "evaluator": "evaluator"},
)
```

意思是：

```text
select_verification_targets 执行完
调用 route_after_select(state)
根据返回值选择下一个节点
```

## 工作流总图

```text
START
  -> planner
  -> reviewer_0 + reviewer_1 并行
  -> initial_board
  -> select_verification_targets
      -> 如果无目标 -> evaluator
      -> 如果有目标 -> test_strategy
          -> test_case
          -> executor
          -> test_result_analyzer
          -> coverage_gap
              -> targeted_review? 可选
              -> update_board
                  -> 如果继续 -> select_verification_targets
                  -> 如果停止 -> evaluator
  -> high_risk_review? 可选
  -> final_report
  -> END
```

## `run_workflow(diff, project_root=None, config=None)`

外部调用入口。

流程：

```python
root = Path(project_root or Path.cwd()).resolve()
active_config = config or load_project_config(root)
app = build_graph(active_config)
return app.invoke({...})
```

注意：

`build_graph` 只是搭图。

真正执行是：

```python
app.invoke(initial_state)
```

## 调试建议

不要把断点打在：

```python
graph.add_node(...)
graph.add_conditional_edges(...)
```

这些只是注册阶段。

应该打在：

- `planner_node`
- `make_reviewer_node` 里的 `_node`
- `initial_board_node`
- `select_verification_targets_node`
- `executor_node`
- `update_board_node`
- `evaluator_node`

然后用 Resume 跳转。

## 这个文件的关键思想

`workflow.py` 是流程骨架。它不做具体智能判断，而是把所有模块按设计文档串成一个可执行状态机。

