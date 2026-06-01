# `codecross/workflow.py` 解析

## 文件作用
LangGraph 编排层，定义共享 state、节点函数、条件路由、取消检查和 `run_workflow` 入口。

- 源码行数：629
- 主要依赖：`operator`、`pathlib`、`typing`、`langgraph`、`typing_extensions`、`agents`、`board`、`config`、`executor`、`schemas`

## 函数与类

### `WorkflowCancelled`（类，第 63 行）
- 签名：`class WorkflowCancelled(RuntimeError)`
- 作用：Raised when the API layer requests a cooperative workflow stop.

### `CodeCrossState`（类，第 72 行）
- 签名：`class CodeCrossState(TypedDict)`
- 作用：LangGraph 在所有节点之间传递的共享状态。
- 主要字段/属性：
  - `diff: str`
  - `project_root: str`
  - `config: ProjectConfig`
  - `review_plan: ReviewPlan`
  - `code_context: CodeContext`
  - `reviewer_results: Annotated[list[ReviewerResult], operator.add]`
  - `findings: list[Finding]`
  - `test_intents: list[TestIntent]`
  - `verifications: dict[str, FindingVerification]`
  - `evidence_map: dict[str, EvidenceItem]`
  - `board_summary: BoardSummary`
  - `round_index: int`
  - `verification_targets: list[Finding]`
  - `test_strategy: TestStrategy`
  - `generated_test_cases: list[TestCase]`
  - `all_test_cases: list[TestCase]`
  - `test_results: list[TestResult]`
  - `all_test_results: list[TestResult]`
  - `test_result_analysis: TestResultAnalysis`
  - `test_rounds: list[TestRound]`
  - `targeted_review: TargetedReview | None`
  - `targeted_reviews: list[TargetedReview]`
  - `final_report_en: str`
  - `final_report_zh: str`
  - `final_report: str`
  - 其余 5 个字段见源码。

### `_summary_from_state`（函数，第 109 行）
- 签名：`def _summary_from_state(state: CodeCrossState, stop_reason: str='') -> BoardSummary`
- 作用：从当前 state 重新构建 BoardSummary。

### `planner_node`（函数，第 126 行）
- 签名：`def planner_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：运行 Planner。

### `context_retriever_node`（函数，第 132 行）
- 签名：`def context_retriever_node(state: CodeCrossState) -> dict[str, CodeContext]`
- 作用：Graph 节点：检索本地代码上下文。

### `make_reviewer_node`（函数，第 150 行）
- 签名：`def make_reviewer_node(index: int)`
- 作用：按 reviewer 下标动态创建 LangGraph 节点函数。

### `initial_board_node`（函数，第 169 行）
- 签名：`def initial_board_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：收集 reviewer 输出并初始化 Review Board。

### `select_verification_targets_node`（函数，第 192 行）
- 签名：`def select_verification_targets_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：从 Board 队列里挑选本轮验证目标。

### `route_after_select`（函数，第 210 行）
- 签名：`def route_after_select(state: CodeCrossState) -> str`
- 作用：条件边：有目标就进入测试验证，没有目标就直接评估。

### `test_strategy_node`（函数，第 218 行）
- 签名：`def test_strategy_node(state: CodeCrossState) -> dict[str, TestStrategy]`
- 作用：Graph 节点：生成当前轮测试策略。

### `test_case_node`（函数，第 230 行）
- 签名：`def test_case_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：生成本轮测试草稿，并记录 coverage gap。

### `test_author_node`（函数，第 250 行）
- 签名：`def test_author_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：审校测试草稿并产出 executor 最终要跑的测试包。

### `executor_node`（函数，第 276 行）
- 签名：`def executor_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：调用确定性 executor 真实运行 pytest；当测试类型或执行环境命中交叉验证条件时，把 finding、code_context 和 project_config 一起传给 executor，让内部 ReproAgent 分支接力产出 `llm_cross_verification` 证据。
- ReproAgent 位置：它不是 `build_graph()` 里独立注册的顶层 LangGraph 节点，而是 `executor_node -> execute_test_cases -> _run_cross_verification -> run_repro_agent` 这条内部子路径。

### `test_result_analyzer_node`（函数，第 295 行）
- 签名：`def test_result_analyzer_node(state: CodeCrossState) -> dict[str, TestResultAnalysis]`
- 作用：Graph 节点：分析测试结果并给 Board 状态建议。

### `coverage_gap_node`（函数，第 306 行）
- 签名：`def coverage_gap_node(state: CodeCrossState) -> dict[str, list[str]]`
- 作用：Graph 节点：汇总本轮覆盖缺口。

### `route_after_coverage_gap`（函数，第 319 行）
- 签名：`def route_after_coverage_gap(state: CodeCrossState) -> str`
- 作用：条件边：测试结果不清楚时进入 targeted review。

### `targeted_review_node`（函数，第 327 行）
- 签名：`def targeted_review_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：执行可选的定向复审。

### `update_board_node`（函数，第 342 行）
- 签名：`def update_board_node(state: CodeCrossState) -> dict[str, Any]`
- 作用：Graph 节点：把测试证据和定向复审写回 Board。

### `route_after_update_board`（函数，第 404 行）
- 签名：`def route_after_update_board(state: CodeCrossState) -> str`
- 作用：条件边：Board 决定继续验证还是进入最终评估。

### `evaluator_node`（函数，第 412 行）
- 签名：`def evaluator_node(state: CodeCrossState) -> dict[str, EvaluatorReport]`
- 作用：Graph 节点：运行规则优先 evaluator。

### `route_after_evaluator`（函数，第 425 行）
- 签名：`def route_after_evaluator(state: CodeCrossState) -> str`
- 作用：条件边：高风险时触发二次复核，否则进入最终报告。

### `high_risk_review_node`（函数，第 433 行）
- 签名：`def high_risk_review_node(state: CodeCrossState) -> dict[str, HighRiskReviewReport | None]`
- 作用：Graph 节点：高风险二次分析。

### `final_report_node`（函数，第 446 行）
- 签名：`def final_report_node(state: CodeCrossState) -> dict[str, str]`
- 作用：Graph 节点：生成 canonical English Markdown 最终报告，并先写入 `final_report_en`。

### `report_localizer_node`（函数，第 464 行）
- 签名：`def report_localizer_node(state: CodeCrossState) -> dict[str, str]`
- 作用：Graph 节点：调用 Report Localizer Agent 翻译英文报告，写入 `final_report_zh`，并把中英文合并成对外的 `final_report`。

### `build_graph`（函数，第 481 行）
- 签名：`def build_graph(config: ProjectConfig)`
- 作用：根据当前 config 构建并编译 LangGraph；当前尾部路径是 `final_report -> report_localizer -> END`。

### `_infer_progress_node`（函数，第 556 行）
- 签名：`def _infer_progress_node(state: CodeCrossState) -> str`
- 作用：Best-effort mapping from state keys to a human workflow node name.

### `_check_cancel`（函数，第 587 行）
- 签名：`def _check_cancel(should_cancel: Callable[[], bool] | None) -> None`
- 作用：Centralize the cancellation check so run_workflow stays readable.

### `run_workflow`（函数，第 594 行）
- 签名：`def run_workflow(diff: str, *, project_root: str | Path | None=None, config: ProjectConfig | None=None, on_event: Callable[[str, dict[str, Any]], None] | None=None, should_cancel: Callable[[], bool] | None=None) -> CodeCrossState`
- 作用：外部调用入口：传入 diff，返回完整的最终 state。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
