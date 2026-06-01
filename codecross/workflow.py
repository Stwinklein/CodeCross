from __future__ import annotations

"""LangGraph 编排层。

workflow.py 只描述“谁先执行、状态怎么流转”。具体思考逻辑在 agents.py，
证据状态规则在 board.py，pytest 执行在 executor.py。
"""

import operator
from pathlib import Path
from typing import Annotated, Any, Callable

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from .agents import (
    context_retriever_agent,
    coverage_gap_agent,
    evaluator_agent,
    high_risk_review_agent,
    planner_agent,
    render_final_report,
    reviewer_agent,
    targeted_reviewer_agent,
    test_author_agent,
    test_case_agent,
    test_result_analyzer_agent,
    test_strategy_agent,
)
from .board import (
    apply_analysis_updates,
    apply_targeted_review,
    build_board_summary,
    collect_review_outputs,
    init_finding_verifications,
    init_static_evidence,
    select_next_verification_targets,
    should_continue_verification,
    update_finding_verifications_from_results,
)
from .config import ProjectConfig, load_project_config
from .executor import execute_test_cases
from .schemas import (
    BoardSummary,
    CodeContext,
    EvaluatorReport,
    EvidenceItem,
    Finding,
    FindingVerification,
    HighRiskReviewReport,
    ReviewPlan,
    ReviewerResult,
    TargetedReview,
    TestCase,
    TestIntent,
    TestResult,
    TestResultAnalysis,
    TestRound,
    TestStrategy,
)


class WorkflowCancelled(RuntimeError):
    """Raised when the API layer requests a cooperative workflow stop.

    Python threads cannot be safely killed from the outside. V1.4 therefore uses
    cooperative cancellation: the API asks for cancellation, and workflow checks
    that flag between graph updates.
    """


class CodeCrossState(TypedDict, total=False):
    """LangGraph 在所有节点之间传递的共享状态。

    total=False 允许节点逐步补字段；reviewer_results 使用 operator.add，
    这样多个 reviewer fan-out 后可以把结果 append 回同一个列表。
    """

    diff: str
    project_root: str
    config: ProjectConfig
    review_plan: ReviewPlan
    code_context: CodeContext
    reviewer_results: Annotated[list[ReviewerResult], operator.add]
    findings: list[Finding]
    test_intents: list[TestIntent]
    verifications: dict[str, FindingVerification]
    evidence_map: dict[str, EvidenceItem]
    board_summary: BoardSummary
    round_index: int
    verification_targets: list[Finding]
    test_strategy: TestStrategy
    generated_test_cases: list[TestCase]
    all_test_cases: list[TestCase]
    test_results: list[TestResult]
    all_test_results: list[TestResult]
    test_result_analysis: TestResultAnalysis
    test_rounds: list[TestRound]
    targeted_review: TargetedReview | None
    targeted_reviews: list[TargetedReview]
    coverage_gaps: list[str]
    continue_verification: bool
    stop_reason: str
    evaluator_report: EvaluatorReport
    high_risk_review: HighRiskReviewReport | None
    final_report: str


def _summary_from_state(state: CodeCrossState, stop_reason: str = "") -> BoardSummary:
    """从当前 state 重新构建 BoardSummary。"""

    return build_board_summary(
        reviewer_results=state.get("reviewer_results", []),
        findings=state.get("findings", []),
        test_intents=state.get("test_intents", []),
        test_cases=state.get("all_test_cases", []),
        test_results=state.get("all_test_results", []),
        test_rounds=state.get("test_rounds", []),
        verifications=state.get("verifications", {}),
        evidence_map=state.get("evidence_map", {}),
        coverage_gaps=state.get("coverage_gaps", []),
        stop_reason=stop_reason,
    )


def planner_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：运行 Planner。"""

    return {"review_plan": planner_agent(state["diff"], state["config"])}


def context_retriever_node(state: CodeCrossState) -> dict[str, CodeContext]:
    """Graph 节点：检索本地代码上下文。

    这是 V1.2 的关键节点。它把 Planner 的 changed_files/changed_symbols
    转成真实代码片段，再交给 reviewer。这样 reviewer 不再只看 diff，
    但 Context Retriever 本身不做任何风险判断，避免引入新的审查偏置。
    """

    return {
        "code_context": context_retriever_agent(
            diff=state["diff"],
            project_root=state["project_root"],
            review_plan=state["review_plan"],
            config=state["config"],
        )
    }


def make_reviewer_node(index: int):
    """按 reviewer 下标动态创建 LangGraph 节点函数。"""

    def _node(state: CodeCrossState) -> dict[str, list[ReviewerResult]]:
        """Graph 节点：运行一个独立 reviewer。"""

        reviewer = state["config"].enabled_reviewers()[index]
        result = reviewer_agent(
            reviewer=reviewer,
            diff=state["diff"],
            review_plan=state["review_plan"],
            config=state["config"],
            code_context=state.get("code_context"),
        )
        return {"reviewer_results": [result]}

    return _node


def initial_board_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：收集 reviewer 输出并初始化 Review Board。"""

    findings, intents = collect_review_outputs(state.get("reviewer_results", []))
    verifications = init_finding_verifications(findings)
    evidence_map = init_static_evidence(findings)
    next_state: CodeCrossState = {
        "findings": findings,
        "test_intents": intents,
        "verifications": verifications,
        "evidence_map": evidence_map,
        "round_index": 0,
        "all_test_cases": [],
        "all_test_results": [],
        "test_rounds": [],
        "coverage_gaps": [],
        "targeted_reviews": [],
    }
    merged = {**state, **next_state}
    summary = _summary_from_state(merged)
    return {**next_state, "board_summary": summary}


def select_verification_targets_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：从 Board 队列里挑选本轮验证目标。"""

    round_index = state.get("round_index", 0) + 1
    targets = select_next_verification_targets(
        state.get("findings", []),
        state["board_summary"],
        state["config"].runner_config,
    )
    return {
        "round_index": round_index,
        "verification_targets": targets,
        "generated_test_cases": [],
        "test_results": [],
        "targeted_review": None,
    }


def route_after_select(state: CodeCrossState) -> str:
    """条件边：有目标就进入测试验证，没有目标就直接评估。"""

    if state.get("verification_targets"):
        return "test_strategy"
    return "evaluator"


def test_strategy_node(state: CodeCrossState) -> dict[str, TestStrategy]:
    """Graph 节点：生成当前轮测试策略。"""

    strategy = test_strategy_agent(
        round_index=state["round_index"],
        targets=state.get("verification_targets", []),
        board_summary=state["board_summary"],
        config=state["config"],
    )
    return {"test_strategy": strategy}


def test_case_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：生成本轮测试草稿，并记录 coverage gap。"""

    batch = test_case_agent(
        round_index=state["round_index"],
        strategy=state["test_strategy"],
        targets=state.get("verification_targets", []),
        test_intents=state.get("test_intents", []),
        config=state["config"],
    )
    coverage_gaps = [
        *state.get("coverage_gaps", []),
        *[f"{key}: {value}" for key, value in batch.skipped_targets.items()],
    ]
    return {
        "generated_test_cases": batch.test_cases,
        "coverage_gaps": coverage_gaps,
    }


def test_author_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：审校测试草稿并产出 executor 最终要跑的测试包。

    V1.6 把 Test Case Agent 和 Test Author Agent 拆开，方便你面试时解释：
    前者负责“把风险转成可执行测试”，后者负责“让测试证据更可信、更像人工写的”。
    """

    batch = test_author_agent(
        round_index=state["round_index"],
        targets=state.get("verification_targets", []),
        test_intents=state.get("test_intents", []),
        draft_test_cases=state.get("generated_test_cases", []),
        config=state["config"],
    )
    all_cases = [*state.get("all_test_cases", []), *batch.test_cases]
    coverage_gaps = [
        *state.get("coverage_gaps", []),
        *[f"{key}: {value}" for key, value in batch.skipped_targets.items()],
    ]
    return {
        "generated_test_cases": batch.test_cases,
        "all_test_cases": all_cases,
        "coverage_gaps": coverage_gaps,
    }


def executor_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：调用确定性 executor 真实运行 pytest。"""

    results = execute_test_cases(
        state.get("generated_test_cases", []),
        project_root=state["project_root"],
        round_index=state["round_index"],
        runner_config=state["config"].runner_config,
        # Issue #2：把 findings / code_context / project_config 一起递给 executor，
        # 让它在 test_type == "llm_cross_verification" 时能找到 finding 上下文并
        # 调用 ReproAgent。普通 pytest 路径不会用到这些字段。
        findings=state.get("findings", []),
        code_context=state.get("code_context"),
        project_config=state["config"],
    )
    all_results = [*state.get("all_test_results", []), *results]
    return {"test_results": results, "all_test_results": all_results}


def test_result_analyzer_node(state: CodeCrossState) -> dict[str, TestResultAnalysis]:
    """Graph 节点：分析测试结果并给 Board 状态建议。"""

    analysis = test_result_analyzer_agent(
        round_index=state["round_index"],
        results=state.get("test_results", []),
        config=state["config"],
    )
    return {"test_result_analysis": analysis}


def coverage_gap_node(state: CodeCrossState) -> dict[str, list[str]]:
    """Graph 节点：汇总本轮覆盖缺口。"""

    gaps = [
        *state.get("coverage_gaps", []),
        *state.get("test_result_analysis").coverage_gaps,
    ]
    interim = {**state, "coverage_gaps": gaps}
    board_summary = _summary_from_state(interim)
    gaps = coverage_gap_agent(board_summary=board_summary, config=state["config"])
    return {"coverage_gaps": gaps}


def route_after_coverage_gap(state: CodeCrossState) -> str:
    """条件边：测试结果不清楚时进入 targeted review。"""

    if state.get("test_result_analysis") and state["test_result_analysis"].need_targeted_review:
        return "targeted_review"
    return "update_board"


def targeted_review_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：执行可选的定向复审。"""

    review = targeted_reviewer_agent(
        round_index=state["round_index"],
        targets=state.get("verification_targets", []),
        results=state.get("test_results", []),
        config=state["config"],
    )
    reviews = state.get("targeted_reviews", [])
    if review:
        reviews = [*reviews, review]
    return {"targeted_review": review, "targeted_reviews": reviews}


def update_board_node(state: CodeCrossState) -> dict[str, Any]:
    """Graph 节点：把测试证据和定向复审写回 Board。"""

    verifications, evidence_map, result_changes = update_finding_verifications_from_results(
        state.get("verifications", {}),
        state.get("evidence_map", {}),
        state.get("test_results", []),
    )
    analysis_changes = apply_analysis_updates(verifications, state["test_result_analysis"])
    targeted_changes: dict[str, str] = {}
    if state.get("targeted_review"):
        verifications, evidence_map, targeted_changes = apply_targeted_review(
            verifications,
            evidence_map,
            state["targeted_review"],  # type: ignore[arg-type]
        )

    status_changes = {**result_changes, **analysis_changes, **targeted_changes}
    round_record = TestRound(
        round_index=state["round_index"],
        input_focus=[target.finding_id for target in state.get("verification_targets", [])],
        generated_test_case_ids=[
            test_case.test_case_id for test_case in state.get("generated_test_cases", [])
        ],
        executed_test_case_ids=[
            result.test_case_id for result in state.get("test_results", [])
        ],
        new_failures=[
            result.test_result_id for result in state.get("test_results", []) if result.status == "failed"
        ],
        new_coverage_notes=state.get("coverage_gaps", []),
        verification_status_changes=status_changes,
    )

    merged = {
        **state,
        "verifications": verifications,
        "evidence_map": evidence_map,
        "test_rounds": [*state.get("test_rounds", []), round_record],
    }
    provisional_summary = _summary_from_state(merged)
    continue_verification, stop_reason = should_continue_verification(
        provisional_summary,
        state["round_index"],
        state["config"].runner_config,
        status_changes,
    )
    round_record.continue_verification = continue_verification
    round_record.stop_reason = stop_reason

    merged["test_rounds"] = [*state.get("test_rounds", []), round_record]
    board_summary = _summary_from_state(merged, stop_reason=stop_reason)
    return {
        "verifications": verifications,
        "evidence_map": evidence_map,
        "test_rounds": merged["test_rounds"],
        "board_summary": board_summary,
        "continue_verification": continue_verification,
        "stop_reason": stop_reason,
    }


def route_after_update_board(state: CodeCrossState) -> str:
    """条件边：Board 决定继续验证还是进入最终评估。"""

    if state.get("continue_verification"):
        return "select_verification_targets"
    return "evaluator"


def evaluator_node(state: CodeCrossState) -> dict[str, EvaluatorReport]:
    """Graph 节点：运行规则优先 evaluator。"""

    report = evaluator_agent(
        reviewer_results=state.get("reviewer_results", []),
        findings=state.get("findings", []),
        board_summary=state["board_summary"],
        config=state["config"],
        review_plan=state.get("review_plan"),
    )
    return {"evaluator_report": report}


def route_after_evaluator(state: CodeCrossState) -> str:
    """条件边：高风险时触发二次复核，否则进入最终报告。"""

    if state["evaluator_report"].needs_high_risk_review:
        return "high_risk_review"
    return "final_report"


def high_risk_review_node(state: CodeCrossState) -> dict[str, HighRiskReviewReport | None]:
    """Graph 节点：高风险二次分析。"""

    return {
        "high_risk_review": high_risk_review_agent(
            evaluator_report=state["evaluator_report"],
            findings=state.get("findings", []),
            board_summary=state["board_summary"],
            config=state["config"],
        )
    }


def final_report_node(state: CodeCrossState) -> dict[str, str]:
    """Graph 节点：生成 Markdown 最终报告。"""

    return {
        "final_report": render_final_report(
            review_plan=state["review_plan"],
            code_context=state.get("code_context"),
            reviewer_results=state.get("reviewer_results", []),
            board_summary=state["board_summary"],
            evaluator_report=state["evaluator_report"],
            high_risk_review=state.get("high_risk_review"),
        )
    }


def build_graph(config: ProjectConfig):
    """根据当前 config 构建并编译 LangGraph。"""

    graph = StateGraph(CodeCrossState)
    graph.add_node("planner", planner_node)
    graph.add_node("context_retriever", context_retriever_node)

    reviewer_nodes: list[str] = []
    # reviewer 数量来自配置，V1 默认两个；未来可以加第三个 reviewer 而不用改主流程。
    for index, reviewer in enumerate(config.enabled_reviewers()):
        name = f"reviewer_{index}_{reviewer.display_name}"
        reviewer_nodes.append(name)
        graph.add_node(name, make_reviewer_node(index))

    graph.add_node("initial_board", initial_board_node)
    graph.add_node("select_verification_targets", select_verification_targets_node)
    graph.add_node("test_strategy", test_strategy_node)
    graph.add_node("test_case", test_case_node)
    graph.add_node("test_author", test_author_node)
    graph.add_node("executor", executor_node)
    graph.add_node("test_result_analyzer", test_result_analyzer_node)
    graph.add_node("coverage_gap", coverage_gap_node)
    graph.add_node("targeted_review", targeted_review_node)
    graph.add_node("update_board", update_board_node)
    graph.add_node("evaluator", evaluator_node)
    graph.add_node("high_risk_review", high_risk_review_node)
    graph.add_node("final_report", final_report_node)

    # 主干流程：Planner -> Context Retriever -> 多 reviewer 并行 -> Board -> Evidence Loop -> Evaluator。
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "context_retriever")
    if reviewer_nodes:
        for node_name in reviewer_nodes:
            graph.add_edge("context_retriever", node_name)
        graph.add_edge(reviewer_nodes, "initial_board")
    else:
        graph.add_edge("context_retriever", "initial_board")

    graph.add_edge("initial_board", "select_verification_targets")
    #Board 选出了待验证目标？
      # 是 -> 进入 Evidence Verification Loop，生成测试策略
      # 否 -> 跳过测试验证，直接最终评估
    graph.add_conditional_edges(
        "select_verification_targets",
        route_after_select,
        {"test_strategy": "test_strategy", "evaluator": "evaluator"},
    )
    graph.add_edge("test_strategy", "test_case")
    graph.add_edge("test_case", "test_author")
    graph.add_edge("test_author", "executor")
    graph.add_edge("executor", "test_result_analyzer")
    graph.add_edge("test_result_analyzer", "coverage_gap")
    graph.add_conditional_edges(
        "coverage_gap",
        route_after_coverage_gap,
        {"targeted_review": "targeted_review", "update_board": "update_board"},
    )
    graph.add_edge("targeted_review", "update_board")
    graph.add_conditional_edges(
        "update_board",
        route_after_update_board,
        {"select_verification_targets": "select_verification_targets", "evaluator": "evaluator"},
    )
    graph.add_conditional_edges(
        "evaluator",
        route_after_evaluator,
        {"high_risk_review": "high_risk_review", "final_report": "final_report"},
    )
    graph.add_edge("high_risk_review", "final_report")
    graph.add_edge("final_report", END)
    return graph.compile()


def _infer_progress_node(state: CodeCrossState) -> str:
    """Best-effort mapping from state keys to a human workflow node name.

    LangGraph's value stream gives us snapshots, not always the exact node name.
    For V1.4 UI progress, the newest high-level state field is precise enough and
    avoids a larger rewrite of every graph node.
    """

    checkpoints = [
        ("final_report", "final_report"),
        ("high_risk_review", "high_risk_review"),
        ("evaluator_report", "evaluator"),
        ("board_summary", "update_board"),
        ("test_result_analysis", "test_result_analyzer"),
        ("all_test_results", "executor"),
        ("all_test_cases", "test_author"),
        ("generated_test_cases", "test_case"),
        ("test_strategy", "test_strategy"),
        ("verification_targets", "select_verification_targets"),
        ("findings", "initial_board"),
        ("reviewer_results", "reviewers"),
        ("code_context", "context_retriever"),
        ("review_plan", "planner"),
    ]
    for key, node_name in checkpoints:
        if key in state:
            return node_name
    return "workflow"


def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
    """Centralize the cancellation check so run_workflow stays readable."""

    if should_cancel and should_cancel():
        raise WorkflowCancelled("Run was cancelled by request.")


def run_workflow(
    diff: str,
    *,
    project_root: str | Path | None = None,
    config: ProjectConfig | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CodeCrossState:
    """外部调用入口：传入 diff，返回完整的最终 state。"""

    root = Path(project_root or Path.cwd()).resolve()
    active_config = config or load_project_config(root)
    app = build_graph(active_config)
    initial_state = {
        "diff": diff,
        "project_root": str(root),
        "config": active_config,
    }

    # The historical CLI/test path stays on invoke for maximum compatibility.
    if on_event is None and should_cancel is None:
        return app.invoke(initial_state)

    _check_cancel(should_cancel)
    if on_event:
        on_event("workflow_started", {"node": "workflow", "message": "Workflow started."})

    latest_state: CodeCrossState = dict(initial_state)
    for step_index, update in enumerate(app.stream(initial_state, stream_mode="updates"), start=1):
        # `updates` gives the real LangGraph node name. That is much better for
        # the Web UI than guessing from accumulated state keys, because the
        # accumulated state quickly contains board/analyzer fields from older
        # checkpoints and makes the frontend look like it jumped ahead.
        for node_name, node_update in update.items():
            if isinstance(node_update, dict):
                if "reviewer_results" in node_update and "reviewer_results" in latest_state:
                    latest_state["reviewer_results"] = [
                        *latest_state.get("reviewer_results", []),
                        *node_update["reviewer_results"],
                    ]
                    node_update = {
                        key: value for key, value in node_update.items() if key != "reviewer_results"
                    }
                latest_state.update(node_update)
            if on_event:
                on_event(
                    "node",
                    {
                        "node": node_name,
                        "step_index": step_index,
                        "message": f"{node_name} checkpoint finished.",
                    },
                )
        _check_cancel(should_cancel)

    _check_cancel(should_cancel)
    if on_event:
        on_event("workflow_finished", {"node": "final_report", "message": "Workflow finished."})
    return latest_state
