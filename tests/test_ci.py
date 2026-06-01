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
        "final_report_en": (
            "# CodeCross Final Review Report\n\n"
            "## Risk Conclusion\n\n"
            "- Risk level: `high`\n"
            "- Recommended action: Fix the contract regression before merge.\n\n"
            "## Key Findings\n\n"
            "- [`high` / `supported`] `src/app.py:12`: The new branch can return None "
            "where callers expect a string.\n"
        ),
        "final_report_zh": (
            "# CodeCross 最终审查报告（中文）\n\n"
            "## 风险结论\n\n"
            "- 风险等级：`high`\n"
            "- 建议动作：合并前修复返回值契约回归。\n"
        ),
    }


def test_build_pr_comment_contains_marker_summary_and_findings():
    """The PR comment should be stable, compact, and useful at a glance."""

    state = _sample_state()
    state["review_plan"].changed_symbols.append("checkout_codecross step: removed continue-on-error")
    state["reviewer_results"][0].errors.append("ModelCallError: invalid JSON")

    comment = build_pr_comment(
        state,
        metadata=PullRequestMetadata(
            repository="owner/repo",
            pr_number=7,
            head_sha="abcdef1234567890",
            workflow_url="https://github.com/owner/repo/actions/runs/1",
        ),
    )

    assert COMMENT_MARKER in comment
    assert "CodeCross PR Review" in comment
    assert "| Risk level | `high` |" in comment
    assert "### English Report" in comment
    assert "### 中文报告" in comment
    assert "`src/app.py:12`" in comment
    assert "abcdef123456" in comment
    assert "deepseek/deepseek-chat" in comment
    assert "API keys and Base URLs" in comment
    assert "ModelCallError" not in comment
    assert "returned errors" in comment


def test_build_pr_comment_handles_empty_state():
    """CI 异常路径也应能渲染一条可读报告，而不是因为缺字段二次崩溃。"""

    comment = build_pr_comment({})

    assert COMMENT_MARKER in comment
    assert "| Risk level | `unknown` |" in comment
    assert "Workflow did not produce a final report." in comment
    assert "中文化 Agent 没有返回译文" in comment


def test_build_pr_comment_uses_workflow_rendered_reports():
    """CI no longer rebuilds findings; it posts the workflow's bilingual reports."""

    state = _sample_state()
    state["final_report_en"] = "# English\n\nWorkflow-rendered English body."
    state["final_report_zh"] = "# 中文\n\n工作流生成的中文正文。"

    comment = build_pr_comment(state)

    assert "Workflow-rendered English body." in comment
    assert "工作流生成的中文正文。" in comment


def test_build_pr_comment_posts_english_and_chinese_sections():
    """The PR comment posts both localized outputs produced by the workflow."""

    state = _sample_state()
    state["final_report_en"] = "# CodeCross Final Review Report\n\nOrder transaction bug."
    state["final_report_zh"] = "# CodeCross 最终审查报告\n\n订单事务问题。"

    comment = build_pr_comment(state)

    assert comment.index("### English Report") < comment.index("### 中文报告")
    assert "Order transaction bug." in comment
    assert "订单事务问题。" in comment


def test_build_pr_comment_preserves_report_markdown():
    """CI should not post-process report Markdown after workflow rendering."""

    state = _sample_state()
    state["final_report_en"] = "# English\n\nA table value may contain a|b."
    state["final_report_zh"] = "# 中文\n\n可以包含 value.replace('|', '/')。"

    comment = build_pr_comment(state)

    assert "a|b" in comment
    assert "value.replace('|', '/')" in comment



def test_truncate_comment_keeps_marker():
    """Even truncated comments must keep the marker for future updates."""

    comment = truncate_comment(COMMENT_MARKER + "\n" + ("x" * 500), 120)

    assert COMMENT_MARKER in comment
    assert "Comment truncated" in comment


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
