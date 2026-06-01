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
    LocalizedFindingCell,
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


# ---------------------------------------------------------------------------
# 方案 B：PR 评论双语 finding 表格
# ---------------------------------------------------------------------------
# 这一组测试聚焦"结构化数据 -> Markdown 表格"这一层，重点保护：
#   1. 表格结构由代码生成，不受 LLM 输出影响。
#   2. 中英文行序严格一致；翻译缺失时优雅回退到英文。
#   3. 危险字符（``|``、换行）不会破坏 Markdown 表格语法。


def test_build_pr_comment_renders_bilingual_finding_tables():
    """PR 评论应同时给出英文表格和中文表格。"""

    state = _sample_state()
    finding = state["findings"][0]
    state["localized_finding_cells"] = {
        finding.finding_id: LocalizedFindingCell(
            finding_id=finding.finding_id,
            problem_zh="新分支可能在调用方期望字符串时返回 None。",
            impact_zh="调用方可能在调用字符串方法时抛 AttributeError。",
            suggestion_zh="保持字符串契约，或同步更新所有调用方与测试。",
        )
    }

    comment = build_pr_comment(state)

    # 英文表格头出现一次。
    assert "| Severity | Verification | Location | Problem | Suggestion |" in comment
    # 英文行：severity 标签是代码生成的（不是 schema 字面量小写）。
    assert "| High | supported | `src/app.py:12-14` |" in comment

    # 中文表格头与中文标签都来自代码常量。
    assert "| 严重级别 | 验证状态 | 位置 | 问题 | 建议 |" in comment
    assert "| 高 | 已被证据支持 | `src/app.py:12-14` |" in comment
    # 中文 problem 文本来自 localizer 翻译产物。
    assert "新分支可能在调用方期望字符串时返回 None。" in comment


def test_build_pr_comment_falls_back_to_english_when_localization_missing():
    """没有 localized_finding_cells 时中文表格应回退到英文文本（不出现空格子）。"""

    state = _sample_state()
    state.pop("localized_finding_cells", None)

    comment = build_pr_comment(state)

    # 中文表格存在，但 problem/suggestion 列回退到英文 finding 原文。
    assert "| 严重级别 | 验证状态 | 位置 | 问题 | 建议 |" in comment
    assert "The new branch can return None where callers expect a string." in comment
    # 同一段英文同时出现在英文表格里——也就是行序对齐时双语都能看到。
    assert comment.count("The new branch can return None where callers expect a string.") >= 2


def test_build_pr_comment_escapes_pipes_in_table_cells():
    """finding 文本里出现 ``|`` 时不能破坏 Markdown 表格结构。"""

    state = _sample_state()
    finding = state["findings"][0]
    finding.problem = "Pipe in problem | should be escaped"
    finding.suggestion = "Use replace('|', '/')"
    state["localized_finding_cells"] = {
        finding.finding_id: LocalizedFindingCell(
            finding_id=finding.finding_id,
            problem_zh="problem 中含有 | 应当转义",
            impact_zh="impact 占位",
            suggestion_zh="建议中也含 | 字符",
        )
    }

    comment = build_pr_comment(state)

    # 转义后的反斜杠加竖线必须出现，而不是裸 ``|``。
    assert r"Pipe in problem \| should be escaped" in comment
    assert r"Use replace('\|', '/')" in comment
    assert r"problem 中含有 \| 应当转义" in comment

    # 验证表格仍然是合法 5 列：找到表格行数 = 1 标题 + 1 分隔 + N 行数据。
    # 这里只验证数据行的列数，因为单元格里转义后仍然是 5 个 ``|`` 分隔符。
    for line in comment.splitlines():
        if line.startswith("| High |") or line.startswith("| 高 |"):
            # 5 列产生 6 个 ``|``（含两端）。转义后的 ``\|`` 不能被算成分隔符。
            unescaped = line.replace(r"\|", "")
            assert unescaped.count("|") == 6, f"Broken table row: {line!r}"


def test_build_pr_comment_table_drops_refuted_findings():
    """已被证据反驳的 finding 不应出现在 PR 评论的表格里。"""

    state = _sample_state()
    finding = state["findings"][0]
    state["board_summary"].finding_verifications[finding.finding_id] = (
        FindingVerification(
            finding_id=finding.finding_id,
            status="refuted",
            reason="Test passed; problem could not be reproduced.",
        )
    )

    comment = build_pr_comment(state)

    # finding 行不应出现在英文/中文表格里。
    assert "| High | refuted |" not in comment
    assert "| 高 | 已被证据反驳 |" not in comment
    # 但 finding 文本仍然可能出现在 final_report_en 自由文本里——
    # 这里我们只关心表格区段。空表格会显示一句"暂无"。
    assert "_No remaining key findings to display._" in comment
    assert "_暂无需要展示的关键 finding。_" in comment
