"""Tests for the GitHub CI comment layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from codecross.ci import (
    COMMENT_MARKER,
    PullRequestMetadata,
    build_pr_comment,
    truncate_comment,
    validate_model_credentials,
)
from codecross.config import ModelSettings, ProjectConfig
from codecross.schemas import (
    BoardSummary,
    CodeContext,
    CodeContextSnippet,
    EvaluatorReport,
    Finding,
    FindingVerification,
    LineRange,
    ReportFindingRow,
    ReportPresentation,
    ReviewPlan,
    ReviewerResult,
)


def _sample_state() -> dict:
    """Build a compact workflow-like state for comment rendering tests."""

    finding = Finding(
        source_reviewer="logic_reviewer",
        model="deepseek/deepseek-chat",
        category="runtime",
        severity="high",
        file_path="src/app.py",
        line_range=LineRange(start=12, end=14),
        problem="The new branch can return None where callers expect a string.",
        evidence="The changed function now returns None on an empty input.",
        impact="Callers may raise AttributeError when they call string methods.",
        suggestion="Preserve the string contract or update all callers and tests.",
    )
    return {
        "review_plan": ReviewPlan(
            changed_files=["src/app.py"],
            changed_symbols=["format_name"],
        ),
        "code_context": CodeContext(
            changed_files=["src/app.py"],
            changed_symbols=["format_name"],
            snippets=[
                CodeContextSnippet(
                    kind="changed_file",
                    file_path="src/app.py",
                    start_line=1,
                    end_line=20,
                    reason="small file",
                    content="1 | def format_name(value): ...",
                )
            ],
        ),
        "reviewer_results": [
            ReviewerResult(
                reviewer_name="logic_reviewer",
                model="deepseek/deepseek-chat",
                findings=[finding],
            )
        ],
        "findings": [finding],
        "board_summary": BoardSummary(
            reviewer_count=1,
            finding_count=1,
            test_intent_count=0,
            test_case_count=0,
            passed_count=0,
            failed_count=0,
            error_count=0,
            finding_verifications={
                finding.finding_id: FindingVerification(
                    finding_id=finding.finding_id,
                    status="supported",
                    reason="Reproduced by generated test.",
                )
            },
        ),
        "evaluator_report": EvaluatorReport(
            risk_level="high",
            confidence="medium",
            recommended_action="Fix the contract regression before merge.",
        ),
    }


def test_build_pr_comment_contains_marker_summary_and_findings():
    """The PR comment should be stable, compact, and useful at a glance."""

    comment = build_pr_comment(
        _sample_state(),
        metadata=PullRequestMetadata(
            repository="owner/repo",
            pr_number=7,
            head_sha="abcdef1234567890",
            workflow_url="https://github.com/owner/repo/actions/runs/1",
        ),
    )

    assert COMMENT_MARKER in comment
    assert "CodeCross PR 审查报告" in comment
    assert "风险等级 | **高风险 (`high`)**" in comment
    assert "验证状态 | 已被证据支持 1" in comment
    assert "`src/app.py:12`" in comment
    assert "abcdef123456" in comment
    assert "deepseek/deepseek-chat" in comment
    assert "API Key 和 Base URL" in comment


def test_build_pr_comment_uses_formatter_presentation():
    """CI comment should render the final formatter output instead of raw reviewer prose."""

    state = _sample_state()
    state["report_presentation"] = ReportPresentation(
        conclusion_summary="本次 PR 由报告格式化节点整理为中文结论。",
        recommended_action="先修复格式化节点列出的阻断问题。",
        finding_rows=[
            ReportFindingRow(
                finding_ids=["finding_a", "finding_b"],
                severity="high",
                verification_status="已被证据支持",
                location="src/app.py:12",
                problem="格式化后的中文问题",
                suggestion="格式化后的中文建议",
            )
        ],
    )

    comment = build_pr_comment(state)

    assert "### 结论摘要" in comment
    assert "本次 PR 由报告格式化节点整理为中文结论。" in comment
    assert "格式化后的中文问题（已合并 2 条相近发现）" in comment
    assert "The new branch can return None" not in comment


def test_build_pr_comment_handles_empty_state():
    """CI 异常路径也应能渲染一条可读报告，而不是因为缺字段二次崩溃。"""

    comment = build_pr_comment({})

    assert COMMENT_MARKER in comment
    assert "风险等级 | **未知风险 (`unknown`)**" in comment
    assert "变更文件：无" in comment
    assert "Reviewer 没有产出 finding。" in comment


def test_build_pr_comment_renders_complex_chinese_dashboard():
    """复杂 PR 摘要应把多 reviewer、多状态和测试结果都直观展示出来。"""

    high_finding = Finding(
        source_reviewer="logic_reviewer",
        model="deepseek/deepseek-chat",
        category="data_consistency",
        severity="high",
        confidence="high",
        file_path="webapi/services/order_service.py",
        line_range=LineRange(start=88, end=110),
        problem="库存扣减和订单创建之间没有事务边界，异常时可能出现库存已扣但订单未创建。",
        evidence="create_order 先调用 decrement_stock，再调用 repository.save_order。",
        impact="用户会看到下单失败，但库存已经减少，后续对账会出现不一致。",
        suggestion="把库存扣减和订单写入放进同一个事务，或使用补偿逻辑回滚库存。",
    )
    medium_finding = Finding(
        source_reviewer="security_maintainability_reviewer",
        model="anthropic/claude-opus-4-6",
        category="auth",
        severity="medium",
        confidence="medium",
        file_path="webapi/controllers/admin.py",
        line_range=LineRange(start=42, end=42),
        problem="新增管理接口只校验登录态，没有校验管理员角色。",
        evidence="route handler 只调用 require_user。",
        impact="普通登录用户可能访问管理入口。",
        suggestion="在 handler 前增加 require_admin 或等价权限检查。",
    )

    state = _sample_state()
    state["review_plan"] = ReviewPlan(
        changed_files=[
            "webapi/services/order_service.py",
            "webapi/controllers/admin.py",
            "webapi/repositories/order_repository.py",
        ],
        changed_symbols=["create_order", "decrement_stock", "admin_rebuild_index"],
    )
    state["code_context"] = CodeContext(
        changed_files=["webapi/services/order_service.py", "webapi/controllers/admin.py"],
        changed_symbols=["create_order", "admin_rebuild_index"],
        snippets=[
            CodeContextSnippet(
                kind="changed_file",
                file_path="webapi/services/order_service.py",
                reason="changed file",
                content="...",
            ),
            CodeContextSnippet(
                kind="call_site",
                file_path="webapi/controllers/order.py",
                reason="caller",
                content="...",
            ),
            CodeContextSnippet(
                kind="existing_test",
                file_path="tests/test_orders.py",
                reason="existing coverage",
                content="...",
            ),
        ],
    )
    state["reviewer_results"] = [
        ReviewerResult(
            reviewer_name="logic_reviewer",
            model="deepseek/deepseek-chat",
            findings=[high_finding],
        ),
        ReviewerResult(
            reviewer_name="security_maintainability_reviewer",
            model="anthropic/claude-opus-4-6",
            findings=[medium_finding],
        ),
    ]
    state["findings"] = [medium_finding, high_finding]
    state["board_summary"] = BoardSummary(
        reviewer_count=2,
        finding_count=2,
        test_intent_count=2,
        test_case_count=3,
        test_round_count=2,
        passed_count=1,
        failed_count=1,
        error_count=1,
        skipped_count=1,
        stop_reason="max_verification_rounds_reached",
        finding_verifications={
            high_finding.finding_id: FindingVerification(
                finding_id=high_finding.finding_id,
                status="supported",
                reason="A generated regression test reproduced the issue.",
            ),
            medium_finding.finding_id: FindingVerification(
                finding_id=medium_finding.finding_id,
                status="inconclusive",
                reason="The generated permission test needs a real auth fixture.",
            ),
        },
    )
    state["evaluator_report"] = EvaluatorReport(
        risk_level="high",
        confidence="high",
        recommended_action="Block the merge until the order transaction bug is fixed.",
    )

    comment = build_pr_comment(state)

    assert "暂缓合并，先修复高风险问题" in comment
    assert "通过 1，失败 1，错误/阻塞 1，跳过 1" in comment
    assert "达到最大验证轮次 (`max_verification_rounds_reached`)" in comment
    assert "变更文件 1" in comment
    assert "调用点 1" in comment
    assert "现有测试 1" in comment
    assert "已被证据支持" in comment
    assert "证据不足" in comment
    assert comment.index("库存扣减") < comment.index("新增管理接口")


def test_build_pr_comment_escapes_markdown_table_pipes():
    """模型输出里的竖线不能破坏 GitHub Markdown 表格列数。"""

    state = _sample_state()
    finding = state["findings"][0]
    finding.problem = "输入 a|b 会让表格错列"
    finding.suggestion = "先执行 value.replace('|', '/') 再渲染"

    comment = build_pr_comment(state)

    assert "a\\|b" in comment
    assert "replace('\\|', '/')" in comment



def test_truncate_comment_keeps_marker():
    """Even truncated comments must keep the marker for future updates."""

    comment = truncate_comment(COMMENT_MARKER + "\n" + ("x" * 500), 120)

    assert COMMENT_MARKER in comment
    assert "评论内容已截断" in comment


def test_validate_model_credentials_reports_missing_env(monkeypatch):
    """Real CI mode should fail loudly if required model secrets are absent."""

    monkeypatch.delenv("MISSING_API_KEY", raising=False)
    setting = ModelSettings(
        role="planner",
        provider="deepseek",
        model="deepseek-chat",
        api_key_env="MISSING_API_KEY",
    )
    config = ProjectConfig(
        model_config={
            "planner": setting,
            "test_strategy_agent": setting,
            "test_case_agent": setting,
            "test_author_agent": setting,
            "test_result_analyzer": setting,
            "coverage_gap_agent": setting,
            "evaluator": setting,
        },
        reviewers=[setting],
    )

    with pytest.raises(RuntimeError, match="MISSING_API_KEY"):
        validate_model_credentials(config)


def test_load_github_metadata_from_event_file(tmp_path: Path, monkeypatch):
    """GitHub event JSON should populate PR metadata used by comments."""

    from codecross.ci import load_github_metadata

    event = tmp_path / "event.json"
    event.write_text(
        """
        {
          "number": 3,
          "pull_request": {
            "number": 3,
            "head": {"sha": "headsha", "ref": "feature"},
            "base": {"ref": "main"}
          }
        }
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")

    metadata = load_github_metadata(str(event))

    assert metadata.repository == "owner/repo"
    assert metadata.pr_number == 3
    assert metadata.head_sha == "headsha"
    assert metadata.base_ref == "main"
    assert metadata.head_ref == "feature"


def test_load_github_metadata_without_event_path_is_safe(monkeypatch):
    """Local CI dry-runs should not try to read the current directory as JSON."""

    from codecross.ci import load_github_metadata

    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_SHA", "localsha")

    metadata = load_github_metadata()

    assert metadata.repository == "owner/repo"
    assert metadata.head_sha == "localsha"
    assert metadata.pr_number is None


def test_read_diff_file_strips_utf8_bom(tmp_path: Path):
    """Windows-written diff files should still parse correctly in CI."""

    from codecross.ci import read_diff_file

    diff_path = tmp_path / "pr.diff"
    diff_path.write_text("\ufeffdiff --git a/a.py b/a.py\n", encoding="utf-8")

    assert read_diff_file(diff_path).startswith("diff --git")
