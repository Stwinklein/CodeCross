"""LLM 交叉验证 Fallback 单测（Issue #2）。

覆盖两个层面：

1. **Step 1**：触发器、executor 分派、Board 状态机
   - trigger 表驱动判定（unsupported_language / environment_error /
     executor_blocked / 默认 None）
   - executor 在 cross_verification_enabled=False 时回退到 skipped
   - 单轮预算用尽后再多触发都落到 skipped
   - Board 在同一 finding 上看到 confirmed + refuted 时升级到 needs_human_review

2. **Step 2**：ReproAgent 的纯函数与 mock 行为
   - mock_llm 模式下 trigger=executor_blocked / unsupported_language 走 supported
   - _spec_to_payload 把 abstain 翻译成 inconclusive 并兜底 abstain_reason
   - 模型调用失败包成 inconclusive，不会让整条 workflow 抛异常
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codecross.board import update_finding_verifications_from_results
from codecross.config import (
    ModelSettings,
    ProjectConfig,
    RunnerConfig,
    load_project_config,
)
from codecross.cross_verification.repro_agent import (
    ReproSpec,
    _spec_to_payload,
    run_repro_agent,
)
from codecross.cross_verification.triggers import detect_cross_verification_trigger
from codecross.executor import execute_test_cases
from codecross.llm import ModelCallError
from codecross.schemas import (
    CrossVerificationPayload,
    Finding,
    FindingVerification,
    LineRange,
    TestCase,
    TestResult,
)


# ---------------------------------------------------------------------------
# 通用 fixture：构造一个最小的 Finding + TestCase 组合。
# 多个测试都需要同样的对象形状，集中在这里方便后续维护。
# ---------------------------------------------------------------------------


def _make_finding(file_path: str = "src/server/routes.js") -> Finding:
    return Finding(
        finding_id="finding_test",
        source_reviewer="logic_reviewer",
        model="deepseek/mock",
        category="logic",
        severity="high",
        file_path=file_path,
        line_range=LineRange(start=1, end=10),
        problem="Suspect logic in route handler.",
        evidence="Static review hint.",
        impact="Potential 500 error path.",
        suggestion="Add explicit branching.",
    )


def _make_cross_test_case(
    finding_id: str = "finding_test",
    *,
    target_language: str | None = "javascript",
    trigger: str | None = "unsupported_language",
) -> TestCase:
    return TestCase(
        source_agent="test_author",
        related_finding_ids=[finding_id],
        test_type="llm_cross_verification",
        target_file="src/server/routes.js",
        target_symbol="handleRequest",
        test_name="cross_verify_route_handler",
        test_code="// LLM cross verification placeholder",
        expected_behavior="ReproAgent supplies reproduction steps.",
        target_language=target_language,
        cross_verification_trigger=trigger,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 触发器
# ---------------------------------------------------------------------------


def test_trigger_unsupported_language_detected():
    test_case = _make_cross_test_case(target_language="javascript", trigger=None)
    runner = RunnerConfig()

    assert (
        detect_cross_verification_trigger(test_case, runner)
        == "unsupported_language"
    )


def test_trigger_environment_error_from_stderr():
    # target_language=python，stderr 表明 pytest 装不上 -> environment_error。
    test_case = TestCase(
        source_agent="test_author",
        target_file="pkg/m.py",
        test_name="env_test",
        test_code="def test_x(): assert True",
        expected_behavior="run pytest",
        target_language="python",
    )
    runner = RunnerConfig()

    trigger = detect_cross_verification_trigger(
        test_case,
        runner,
        pytest_stderr="ModuleNotFoundError: No module named 'pytest'",
    )
    assert trigger == "environment_error"


def test_trigger_default_pytest_path_returns_none():
    test_case = TestCase(
        source_agent="test_author",
        target_file="pkg/m.py",
        test_name="ok",
        test_code="def test_x(): assert True",
        expected_behavior="run pytest",
        target_language="python",
    )
    assert detect_cross_verification_trigger(test_case, RunnerConfig()) is None


def test_trigger_disabled_by_runner_config():
    test_case = _make_cross_test_case(target_language="javascript", trigger=None)
    runner = RunnerConfig(cross_verification_enabled=False)

    # 即使语言不是 python，关闭开关后也必须返回 None，让 executor 走原 not_testable 路径。
    assert detect_cross_verification_trigger(test_case, runner) is None


def test_trigger_executor_blocked_lowest_priority():
    # 同时给 executor_blocked + unsupported_language 时，先命中 unsupported_language。
    test_case = _make_cross_test_case(target_language="javascript", trigger=None)
    trigger = detect_cross_verification_trigger(
        test_case,
        RunnerConfig(),
        sanitizer_blocked=True,
    )
    assert trigger == "unsupported_language"


# ---------------------------------------------------------------------------
# Executor 分派
# ---------------------------------------------------------------------------


def test_executor_disabled_cross_verification_skips(tmp_path: Path):
    finding = _make_finding()
    case = _make_cross_test_case(finding.finding_id)
    runner = RunnerConfig(cross_verification_enabled=False)
    config = load_project_config(mock_llm=True)

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=runner,
        findings=[finding],
        project_config=config,
    )

    assert len(results) == 1
    assert results[0].status == "skipped"
    assert results[0].error_type == "cross_verification_disabled"


def test_executor_cross_verification_budget_exhausted(tmp_path: Path):
    finding = _make_finding()
    cases = [_make_cross_test_case(finding.finding_id) for _ in range(3)]
    runner = RunnerConfig(cross_verification_max_per_round=2)
    config = load_project_config(mock_llm=True)

    results = execute_test_cases(
        cases,
        project_root=tmp_path,
        round_index=1,
        runner_config=runner,
        findings=[finding],
        project_config=config,
    )

    assert len(results) == 3
    # 前两个真正走 cross_verification 路径（status 以 cross_ 开头）。
    assert results[0].status.startswith("cross_")
    assert results[1].status.startswith("cross_")
    # 第三个超预算，落到 skipped。
    assert results[2].status == "skipped"
    assert results[2].error_type == "cross_verification_budget_exceeded"


def test_executor_cross_verification_missing_project_config(tmp_path: Path):
    finding = _make_finding()
    case = _make_cross_test_case(finding.finding_id)

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        findings=[finding],
        project_config=None,
    )

    assert len(results) == 1
    assert results[0].status == "cross_inconclusive"
    assert results[0].evidence_kind == "llm_cross_verification"


def test_executor_cross_verification_missing_finding(tmp_path: Path):
    case = _make_cross_test_case("missing_finding_id")
    config = load_project_config(mock_llm=True)

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        findings=[],
        project_config=config,
    )

    assert len(results) == 1
    assert results[0].status == "cross_inconclusive"
    assert results[0].cross_payload is not None
    assert "finding" in results[0].cross_payload.abstain_reason.lower()


def test_executor_cross_verification_mock_supported(tmp_path: Path):
    finding = _make_finding()
    case = _make_cross_test_case(finding.finding_id)
    config = load_project_config(mock_llm=True)

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        findings=[finding],
        project_config=config,
    )

    assert len(results) == 1
    result = results[0]
    assert result.status == "cross_supported"
    assert result.evidence_kind == "llm_cross_verification"
    assert result.cross_payload is not None
    assert result.cross_payload.layer == "repro_agent"


# ---------------------------------------------------------------------------
# Board 状态机
# ---------------------------------------------------------------------------


def _result_with_status(
    finding_id: str,
    status: str,
    *,
    round_index: int = 1,
) -> TestResult:
    return TestResult(
        test_case_id=f"case_{status}",
        round_index=round_index,
        status=status,  # type: ignore[arg-type]
        related_finding_ids=[finding_id],
        evidence_kind="llm_cross_verification",
        cross_payload=CrossVerificationPayload(
            layer="repro_agent",
            model="anthropic/test",
            status=(
                "confirmed"
                if status == "cross_confirmed"
                else "refuted"
                if status == "cross_refuted"
                else "supported"
                if status == "cross_supported"
                else "inconclusive"
            ),
            reasoning=f"mock {status}",
            suggested_strength="medium",
        ),
    )


def test_board_cross_supported_promotes_unverified():
    finding = _make_finding()
    verifications = {
        finding.finding_id: FindingVerification(finding_id=finding.finding_id),
    }
    results = [_result_with_status(finding.finding_id, "cross_confirmed")]

    new_verifications, _, changes = update_finding_verifications_from_results(
        verifications, {}, results
    )

    assert new_verifications[finding.finding_id].status == "cross_supported"
    assert finding.finding_id in changes


def test_board_cross_refuted_marks_finding_refuted():
    finding = _make_finding()
    verifications = {
        finding.finding_id: FindingVerification(finding_id=finding.finding_id),
    }
    results = [_result_with_status(finding.finding_id, "cross_refuted")]

    new_verifications, _, _ = update_finding_verifications_from_results(
        verifications, {}, results
    )

    assert new_verifications[finding.finding_id].status == "cross_refuted"


def test_board_cross_conflict_falls_to_human_review():
    finding = _make_finding()
    verifications = {
        finding.finding_id: FindingVerification(finding_id=finding.finding_id),
    }
    results = [
        _result_with_status(finding.finding_id, "cross_confirmed"),
        _result_with_status(finding.finding_id, "cross_refuted"),
    ]

    new_verifications, _, _ = update_finding_verifications_from_results(
        verifications, {}, results
    )

    assert new_verifications[finding.finding_id].status == "needs_human_review"


def test_board_does_not_overwrite_pytest_supported():
    finding = _make_finding()
    verifications = {
        finding.finding_id: FindingVerification(
            finding_id=finding.finding_id,
            status="supported",
        ),
    }
    results = [_result_with_status(finding.finding_id, "cross_refuted")]

    new_verifications, _, _ = update_finding_verifications_from_results(
        verifications, {}, results
    )

    # pytest 给的 supported 比 cross_refuted 强，不应该被覆盖。
    assert new_verifications[finding.finding_id].status == "supported"


# ---------------------------------------------------------------------------
# ReproAgent 纯函数 / 错误路径
# ---------------------------------------------------------------------------


def test_spec_to_payload_abstain_falls_back_to_inconclusive():
    settings = ModelSettings(role="repro_agent", provider="anthropic", model="claude-test")
    spec = ReproSpec(status="abstain")  # abstain_reason 故意留空

    payload = _spec_to_payload(spec, settings=settings)

    assert payload.status == "inconclusive"
    # abstain 时即使模型没填理由也要兜底，避免 board 看到空文本。
    assert payload.abstain_reason


def test_spec_to_payload_confirmed_carries_metadata():
    settings = ModelSettings(role="repro_agent", provider="anthropic", model="claude-test")
    spec = ReproSpec(
        status="confirmed",
        reasoning="复现成立",
        reproduction_steps=["step 1", "step 2"],
        expected_observation="500 error",
    )

    payload = _spec_to_payload(spec, settings=settings)

    assert payload.status == "confirmed"
    assert payload.reproduction_steps == ["step 1", "step 2"]
    assert payload.suggested_strength in {"medium", "weak"}


def test_run_repro_agent_missing_role_yields_inconclusive():
    finding = _make_finding()
    case = _make_cross_test_case(finding.finding_id)
    # 故意构造一个没有 repro_agent 角色的 ProjectConfig。
    config = ProjectConfig(model_config={})

    payload = run_repro_agent(
        finding=finding,
        test_case=case,
        code_context=None,
        config=config,
        trigger="unsupported_language",
    )

    assert payload.status == "inconclusive"
    assert "repro_agent" in payload.reasoning


def test_run_repro_agent_swallows_model_call_error(monkeypatch: pytest.MonkeyPatch):
    finding = _make_finding()
    case = _make_cross_test_case(finding.finding_id)
    config = load_project_config(mock_llm=False)

    def _explode(*args, **kwargs):  # noqa: ANN001 - test stub
        raise ModelCallError("simulated provider down")

    monkeypatch.setattr(
        "codecross.cross_verification.repro_agent.call_model_json",
        _explode,
    )

    payload = run_repro_agent(
        finding=finding,
        test_case=case,
        code_context=None,
        config=config,
        trigger="unsupported_language",
    )

    assert payload.status == "inconclusive"
    assert "ReproAgent 调用失败" in payload.reasoning
