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


def _webapi_pr10_state() -> dict:
    """Build the WebAPI PR #10 review shape from the layout proposal.

    这个夹具专门保护真实 PR 示例里的 5 个风险项：以后排版再重构时，
    只要少渲染一个 location/problem/action，这个测试就会失败。
    """

    rows = [
        (
            "finding_webapi_001",
            "high",
            "src/utils/oauthRedirect.js",
            12,
            14,
            "Open redirect: `next` concatenated into URL without validation.",
            "Attackers can redirect users from a trusted login flow to a phishing site.",
            "Validate `next` against an allowlist of trusted origins.",
            "开放重定向：`next` 未经校验直接拼接到 URL。",
            "攻击者可把用户从受信登录流程跳到钓鱼站。",
            "把 `next` 与受信 origin 白名单做匹配。",
        ),
        (
            "finding_webapi_002",
            "medium",
            "src/utils/oauthRedirect.js",
            19,
            21,
            "OAuth `state` logged in plaintext, defeating CSRF protection.",
            "Leaked logs can expose the CSRF token that binds the browser session.",
            "Stop logging the OAuth `state` value (or store only a truncated hash).",
            "OAuth `state` 明文记录，CSRF 保护失效。",
            "日志泄露会暴露绑定浏览器会话的 CSRF token。",
            "不再打印 OAuth `state` 值（或只保留截断哈希）。",
        ),
        (
            "finding_webapi_003",
            "medium",
            "src/utils/oauthRedirect.js",
            27,
            33,
            "Unknown provider silently returns `undefined`, masking misconfig.",
            "Typos and missing provider entries become indistinguishable from rejected requests.",
            "Throw on unknown OAuth providers in `resolveProvider`.",
            "未知 provider 静默返回 `undefined`，掩盖配置错误。",
            "拼写错误或漏配会和被拒绝请求混在一起，难以排查。",
            "`resolveProvider` 对未知 provider 抛错。",
        ),
        (
            "finding_webapi_004",
            "low",
            ".github/workflows/codecross-pr-review.yml",
            40,
            None,
            "Runner ref pinned by SHA but not integrity-verified.",
            "A raw SHA pin without a signed tag or integrity check is harder to audit.",
            "Pin CodeCross runner via a signed tag (or add a post-checkout integrity check).",
            "Runner ref 仅按 SHA 固定，未做完整性校验。",
            "裸 SHA 固定缺少签名 tag 或完整性检查，审计成本更高。",
            "用签名 tag 固定 CodeCross runner（或加检出后完整性校验）。",
        ),
        (
            "finding_webapi_005",
            "low",
            ".github/workflows/codecross-pr-review.yml",
            118,
            167,
            "Fallback comment interpolates outcome vars without sanitization.",
            "Unexpected workflow values can leak into the fallback PR comment body.",
            "Validate workflow outcome variables against an explicit allowlist.",
            "回退评论步骤未清理 outcome 变量直接拼接。",
            "异常 workflow 值可能直接进入回退 PR 评论正文。",
            "用显式白名单校验 workflow outcome 变量。",
        ),
    ]

    findings = [
        Finding(
            finding_id=finding_id,
            source_reviewer="security_reviewer",
            model="deepseek/deepseek-chat",
            category="security",
            severity=severity,
            file_path=file_path,
            line_range=LineRange(start=start, end=end),
            problem=problem,
            evidence=f"Observed in {file_path}.",
            impact=impact,
            suggestion=suggestion,
        )
        for (
            finding_id,
            severity,
            file_path,
            start,
            end,
            problem,
            impact,
            suggestion,
            _problem_zh,
            _impact_zh,
            _suggestion_zh,
        ) in rows
    ]
    localized_cells = {
        finding_id: LocalizedFindingCell(
            finding_id=finding_id,
            problem_zh=problem_zh,
            impact_zh=impact_zh,
            suggestion_zh=suggestion_zh,
        )
        for (
            finding_id,
            _severity,
            _file_path,
            _start,
            _end,
            _problem,
            _impact,
            _suggestion,
            problem_zh,
            impact_zh,
            suggestion_zh,
        ) in rows
    }
    return {
        "reviewer_results": [
            ReviewerResult(
                reviewer_name="security_reviewer",
                model="deepseek/deepseek-chat",
                findings=findings,
            ),
            ReviewerResult(
                reviewer_name="workflow_reviewer",
                model="anthropic/claude-opus-4-6",
                findings=[],
            ),
        ],
        "findings": findings,
        "localized_finding_cells": localized_cells,
        "board_summary": BoardSummary(
            reviewer_count=2,
            finding_count=len(findings),
            test_case_count=6,
            passed_count=0,
            failed_count=6,
            finding_verifications={
                finding.finding_id: FindingVerification(
                    finding_id=finding.finding_id,
                    status="supported",
                    reason="Reproduced by generated tests.",
                )
                for finding in findings
            },
        ),
        "evaluator_report": EvaluatorReport(
            risk_level="high",
            confidence="high",
            recommended_action="Block merge until the OAuth redirect risks are fixed.",
        ),
        "final_report_en": (
            "# CodeCross Final Review Report\n\n"
            "## Risk Conclusion\n\n"
            "- Risk level: `high`\n"
            "- Confidence: `high`\n\n"
            "## Key Findings\n\n"
            "- Open redirect and OAuth state handling require fixes.\n"
        ),
        "final_report_zh": (
            "# CodeCross 最终审查报告（中文）\n\n"
            "## 风险结论\n\n"
            "- 风险等级：`high`\n"
            "- 建议动作：合并前修复 OAuth redirect 风险。\n"
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
    assert "> 🔴 **Block merge** · risk **high** · confidence **medium**" in comment
    assert "<summary>Run info</summary>" in comment
    assert "## 🇬🇧 English" in comment
    assert "## 🇨🇳 中文" in comment
    assert "### Findings at a glance" in comment
    assert "### 一览" in comment
    assert "`src/app.py:12-14`" in comment
    assert "FINDING-001" in comment
    assert "abcdef123456" in comment
    assert "deepseek/deepseek-chat" in comment
    assert comment.count("<details>") == 3
    assert "API keys and Base URLs" in comment
    assert "ModelCallError" not in comment
    assert "returned errors" in comment


def test_build_pr_comment_handles_empty_state():
    """CI 异常路径也应能渲染一条可读报告，而不是因为缺字段二次崩溃。"""

    comment = build_pr_comment({})

    assert COMMENT_MARKER in comment
    assert "> ⚪ **Comment** · risk **unknown** · confidence **medium**" in comment
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

    assert comment.index("## 🇬🇧 English") < comment.index("## 🇨🇳 中文")
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
# 方案 C：PR 评论答题式布局
# ---------------------------------------------------------------------------
# 这一组测试聚焦"结构化数据 -> Markdown 评论"这一层，重点保护：
#   1. 一览表、Why 卡片、修复清单的结构由代码生成，不受 LLM 输出影响。
#   2. 中英文段使用同一批 finding，行序严格一致；翻译缺失时优雅回退英文。
#   3. 危险字符（``|``、换行）不会破坏 Markdown 表格语法。


def test_build_pr_comment_renders_bilingual_finding_tables():
    """PR 评论应同时给出中英文一览表、Why 卡片和修复清单。"""

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

    # 英文一览表只保留三列，severity 标签是代码生成的短 badge。
    assert "| Sev | Location | Problem |" in comment
    assert "| 🔴 H | `src/app.py:12-14` |" in comment

    # 中文一览表与中文标签都来自代码常量。
    assert "| 严重 | 位置 | 问题 |" in comment
    assert "| 🔴 高 | `src/app.py:12-14` |" in comment
    # 中文 problem 文本来自 localizer 翻译产物。
    assert "新分支可能在调用方期望字符串时返回 None。" in comment
    assert "### Why it matters" in comment
    assert "- **What:** The new branch can return None where callers expect a string." in comment
    assert "- **问题：** 新分支可能在调用方期望字符串时返回 None。" in comment
    assert "- [ ] Preserve the string contract or update all callers and tests." in comment
    assert "- [ ] 保持字符串契约，或同步更新所有调用方与测试。" in comment


def test_build_pr_comment_falls_back_to_english_when_localization_missing():
    """没有 localized_finding_cells 时中文镜像段应回退到英文文本。"""

    state = _sample_state()
    state.pop("localized_finding_cells", None)

    comment = build_pr_comment(state)

    # 中文段存在，但 problem/impact/suggestion 回退到英文 finding 原文。
    assert "| 严重 | 位置 | 问题 |" in comment
    assert "The new branch can return None where callers expect a string." in comment
    # 同一段英文同时出现在英文段和中文回退段里——也就是行序对齐时双语都能看到。
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
    assert r"problem 中含有 \| 应当转义" in comment
    # suggestion 不在表格里，保留原 Markdown 文本即可。
    assert "Use replace('|', '/')" in comment

    # 验证表格仍然是合法 3 列：数据行应只有 4 个未转义 ``|``（含两端）。
    for line in comment.splitlines():
        if line.startswith("| 🔴 H |") or line.startswith("| 🔴 高 |"):
            unescaped = line.replace(r"\|", "")
            assert unescaped.count("|") == 4, f"Broken table row: {line!r}"


def test_build_pr_comment_table_drops_refuted_findings():
    """已被证据反驳的 finding 不应出现在 PR 评论顶层速读区里。"""

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

    # finding 行不应出现在英文/中文一览表里。
    assert "| 🔴 H | `src/app.py:12-14` |" not in comment
    assert "| 🔴 高 | `src/app.py:12-14` |" not in comment
    # 但 finding 文本仍然可能出现在 final_report_en 自由文本里——
    # 这里我们只关心表格区段。空表格会显示一句"暂无"。
    assert "_No remaining key findings to display._" in comment
    assert "_暂无需要展示的关键 finding。_" in comment


def test_build_pr_comment_webapi_pr10_keeps_all_risk_items():
    """WebAPI PR #10 的 5 个风险项都应出现在新版 PR 评论顶层。"""

    comment = build_pr_comment(
        _webapi_pr10_state(),
        metadata=PullRequestMetadata(
            repository="private/webapi",
            pr_number=10,
            head_sha="bdf2e1aa4974ffff",
            workflow_url="https://github.example/actions/runs/10",
        ),
    )

    assert "> 🔴 **Block merge** · risk **high** · confidence **high**" in comment
    assert (
        "1 high-severity finding has reproducible test evidence "
        "(6/6 tests failed, 0 passed)."
    ) in comment
    assert comment.count("<details>") == 3
    assert "FINDING-005" in comment

    expected_locations = [
        "src/utils/oauthRedirect.js:12-14",
        "src/utils/oauthRedirect.js:19-21",
        "src/utils/oauthRedirect.js:27-33",
        ".github/workflows/codecross-pr-review.yml:40",
        ".github/workflows/codecross-pr-review.yml:118-167",
    ]
    for location in expected_locations:
        assert f"`{location}`" in comment

    expected_problems = [
        "Open redirect: `next` concatenated into URL without validation.",
        "OAuth `state` logged in plaintext, defeating CSRF protection.",
        "Unknown provider silently returns `undefined`, masking misconfig.",
        "Runner ref pinned by SHA but not integrity-verified.",
        "Fallback comment interpolates outcome vars without sanitization.",
    ]
    for problem in expected_problems:
        assert problem in comment

    expected_actions = [
        "Validate `next` against an allowlist of trusted origins.",
        "Stop logging the OAuth `state` value (or store only a truncated hash).",
        "Throw on unknown OAuth providers in `resolveProvider`.",
        "Pin CodeCross runner via a signed tag (or add a post-checkout integrity check).",
        "Validate workflow outcome variables against an explicit allowlist.",
    ]
    for action in expected_actions:
        assert f"- [ ] {action}" in comment

    assert "开放重定向：`next` 未经校验直接拼接到 URL。" in comment
    assert "回退评论步骤未清理 outcome 变量直接拼接。" in comment
    assert "- [ ] 用显式白名单校验 workflow outcome 变量。" in comment
