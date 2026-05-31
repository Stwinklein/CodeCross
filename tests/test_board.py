"""Review Board 测试。

这里用最小数据验证 Board 的关键规则：失败测试会把相关 finding 标记为 supported。
"""

from codecross.board import (
    build_board_summary,
    init_finding_verifications,
    should_continue_verification,
    update_finding_verifications_from_results,
)
from codecross.config import RunnerConfig
from codecross.schemas import BoardSummary
from codecross.schemas import Finding, ReviewerResult, TestResult


def test_board_marks_failed_related_test_as_supported():
    """相关 pytest 失败时，FindingVerification 应从 unverified 变成 supported。"""

    # 构造一个 reviewer 提出的高风险 finding。
    finding = Finding(
        finding_id="finding_1",
        source_reviewer="logic_reviewer",
        model="deepseek/test",
        category="runtime",
        severity="high",
        confidence="high",
        file_path="pkg/math_utils.py",
        problem="Division by zero is not handled.",
        evidence="a / b is used directly.",
        impact="Runtime exception.",
        suggestion="Handle zero denominator.",
    )
    # Board 初始化时并不相信 reviewer，只先登记 unverified 状态。
    verifications = init_finding_verifications([finding])
    evidence_map = {}
    result = TestResult(
        test_case_id="test_1",
        round_index=1,
        status="failed",
        is_reproducible=True,
        failure_summary="ZeroDivisionError",
        related_finding_ids=["finding_1"],
    )

    # 测试结果进入 Board 后，会变成 evidence，并驱动 verification 状态变化。
    verifications, evidence_map, changes = update_finding_verifications_from_results(
        verifications,
        evidence_map,
        [result],
    )

    assert verifications["finding_1"].status == "supported"
    assert changes == {"finding_1": "unverified -> supported"}
    assert len(evidence_map) == 1

    # BoardSummary 是 evaluator 和报告层使用的汇总视图。
    summary = build_board_summary(
        reviewer_results=[
            ReviewerResult(
                reviewer_name="logic_reviewer",
                model="deepseek/test",
                findings=[finding],
            )
        ],
        findings=[finding],
        test_intents=[],
        test_cases=[],
        test_results=[result],
        test_rounds=[],
        verifications=verifications,
        evidence_map=evidence_map,
        coverage_gaps=[],
        stop_reason="verification_queue_empty",
    )

    assert summary.failed_count == 1
    assert summary.verification_queue == []


def test_board_keeps_non_reproducible_failure_inconclusive():
    """失败测试没有复现标记时，只能算弱信号，不能直接 supported。"""

    finding = Finding(
        finding_id="finding_1",
        source_reviewer="logic_reviewer",
        model="deepseek/test",
        category="runtime",
        severity="high",
        confidence="high",
        file_path="pkg/math_utils.py",
        problem="Division by zero is not handled.",
        evidence="a / b is used directly.",
        impact="Runtime exception.",
        suggestion="Handle zero denominator.",
    )
    verifications = init_finding_verifications([finding])
    result = TestResult(
        test_case_id="test_1",
        round_index=1,
        status="failed",
        is_reproducible=False,
        failure_summary="ZeroDivisionError",
        related_finding_ids=["finding_1"],
    )

    verifications, evidence_map, changes = update_finding_verifications_from_results(
        verifications,
        {},
        [result],
    )

    assert verifications["finding_1"].status == "inconclusive"
    assert changes == {"finding_1": "unverified -> inconclusive"}
    assert next(iter(evidence_map.values())).evidence_type == "executor_error"


def test_board_stops_when_round_has_no_verification_progress():
    """验证队列仍有目标但本轮无状态变化时，应提前停止，避免重复空转。"""

    should_continue, reason = should_continue_verification(
        BoardSummary(verification_queue=["finding_1"]),
        round_index=1,
        runner_config=RunnerConfig(max_verification_rounds=3),
        status_changes={},
    )

    assert should_continue is False
    assert reason == "no_verification_progress"
