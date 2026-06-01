"""Issue #3：自指审查降级行为测试。

当 PR 仅修改 CodeCross 自身的集成 / 运行载体代码时，evaluator 必须避免
反复给出 high/critical 评分形成‘修-审-修-审’循环。这里覆盖三种关键路径：

1. 纯自指 diff + 已被证据支持的 high finding -> 强制降级到 medium，
   needs_high_risk_review=False，reasoning/recommended_action 出现英文降级说明。
2. 混合 diff（同时改了业务代码）-> 不降级，按原规则裁决。
3. 空 changed_files（planner 失败兜底）-> 不降级，行为与历史一致。

最终报告渲染必须在自指场景下追加英文 self-reference note。
"""

from __future__ import annotations

import codecross.agents as agents
from codecross.config import load_project_config
from codecross.schemas import (
    BoardSummary,
    EvaluatorReport,
    Finding,
    FindingVerification,
    LineRange,
    ReviewPlan,
    ReviewerResult,
)


def _make_high_supported_board(finding_id: str) -> BoardSummary:
    return BoardSummary(
        finding_count=1,
        finding_verifications={
            finding_id: FindingVerification(finding_id=finding_id, status="supported"),
        },
    )


def _make_high_finding(file_path: str = ".github/workflows/codecross-pr-review.yml") -> Finding:
    return Finding(
        finding_id="finding_self_ref",
        source_reviewer="logic_reviewer",
        model="deepseek/mock",
        category="ci",
        severity="high",
        file_path=file_path,
        line_range=LineRange(start=10, end=20),
        problem="Fallback shell branch may swallow real errors.",
        evidence="set -e is missing on the fallback path.",
        impact="Failed reviews can be reported as success.",
        suggestion="Tighten shell error handling.",
    )


def test_is_self_referential_review_detects_workflow_only_diff():
    plan = ReviewPlan(changed_files=[".github/workflows/codecross-pr-review.yml"])
    assert agents.is_self_referential_review(plan) is True


def test_is_self_referential_review_detects_runner_directory():
    plan = ReviewPlan(changed_files=[".codecross-runner/codecross/agents.py"])
    assert agents.is_self_referential_review(plan) is True


def test_is_self_referential_review_rejects_mixed_diff():
    plan = ReviewPlan(
        changed_files=[
            ".github/workflows/codecross-pr-review.yml",
            "src/server/api/routes.py",
        ]
    )
    assert agents.is_self_referential_review(plan) is False


def test_is_self_referential_review_rejects_empty_changed_files():
    # planner 异常时 changed_files 可能为空，绝不能误判成自指。
    assert agents.is_self_referential_review(ReviewPlan()) is False


def test_evaluator_downgrades_high_to_medium_on_self_reference():
    finding = _make_high_finding()
    report = agents.evaluator_agent(
        reviewer_results=[
            ReviewerResult(reviewer_name="logic_reviewer", model="deepseek/mock", findings=[finding])
        ],
        findings=[finding],
        board_summary=_make_high_supported_board(finding.finding_id),
        config=load_project_config(mock_llm=True),
        review_plan=ReviewPlan(changed_files=[finding.file_path]),
    )

    assert report.risk_level == "medium"
    assert report.needs_high_risk_review is False
    assert "self-referential" in report.reasoning
    assert "Original reasoning" in report.reasoning
    assert "self-reference rule" in report.recommended_action


def test_evaluator_keeps_high_when_diff_is_not_self_reference():
    finding = _make_high_finding(file_path="src/server/api/routes.py")
    report = agents.evaluator_agent(
        reviewer_results=[
            ReviewerResult(reviewer_name="logic_reviewer", model="deepseek/mock", findings=[finding])
        ],
        findings=[finding],
        board_summary=_make_high_supported_board(finding.finding_id),
        config=load_project_config(mock_llm=True),
        review_plan=ReviewPlan(changed_files=[finding.file_path]),
    )

    assert report.risk_level == "high"
    assert report.needs_high_risk_review is True
    assert "self-referential" not in report.reasoning


def test_evaluator_without_review_plan_behaves_like_before():
    finding = _make_high_finding(file_path="src/server/api/routes.py")
    report = agents.evaluator_agent(
        reviewer_results=[
            ReviewerResult(reviewer_name="logic_reviewer", model="deepseek/mock", findings=[finding])
        ],
        findings=[finding],
        board_summary=_make_high_supported_board(finding.finding_id),
        config=load_project_config(mock_llm=True),
    )

    assert report.risk_level == "high"
    assert report.needs_high_risk_review is True


def test_final_report_appends_self_reference_notice():
    finding = _make_high_finding()
    board = _make_high_supported_board(finding.finding_id)
    plan = ReviewPlan(
        changed_files=[finding.file_path],
        changed_symbols=[],
        risk_areas=["ci robustness"],
        testing_focus=[],
    )
    evaluator = agents.evaluator_agent(
        reviewer_results=[
            ReviewerResult(reviewer_name="logic_reviewer", model="deepseek/mock", findings=[finding])
        ],
        findings=[finding],
        board_summary=board,
        config=load_project_config(mock_llm=True),
        review_plan=plan,
    )

    report = agents.render_final_report(
        review_plan=plan,
        code_context=None,
        reviewer_results=[
            ReviewerResult(reviewer_name="logic_reviewer", model="deepseek/mock", findings=[finding])
        ],
        board_summary=board,
        evaluator_report=evaluator,
        high_risk_review=None,
    )

    assert "Self-reference note" in report
    assert "CodeCross integration entrypoints" in report
    assert "## Executive Summary" in report


def test_final_report_omits_self_reference_notice_for_normal_pr():
    finding = _make_high_finding(file_path="src/server/api/routes.py")
    board = _make_high_supported_board(finding.finding_id)
    plan = ReviewPlan(changed_files=[finding.file_path])
    evaluator = EvaluatorReport(
        risk_level="high",
        confidence="high",
        reasoning="A high-severity finding is supported by test evidence.",
    )

    report = agents.render_final_report(
        review_plan=plan,
        code_context=None,
        reviewer_results=[
            ReviewerResult(reviewer_name="logic_reviewer", model="deepseek/mock", findings=[finding])
        ],
        board_summary=board,
        evaluator_report=evaluator,
        high_risk_review=None,
    )

    assert "Self-reference note" not in report
