"""Agent 辅助解析逻辑测试。"""

import pytest

import codecross.agents as agents
from codecross.config import ModelSettings, load_project_config
from codecross.llm import ModelCallError
from codecross.schemas import (
    BoardSummary,
    EvaluatorReport,
    Finding,
    FindingVerification,
    LineRange,
    ReviewPlan,
    TestCase,
    TestIntent,
)


def test_parse_changed_files_handles_rename_and_hunk_notes():
    """V1.1 的轻量 diff parser 应能识别 rename 和 hunk 新行号。"""

    diff = """diff --git a/pkg/old_name.py b/pkg/new_name.py
similarity index 88%
rename from pkg/old_name.py
rename to pkg/new_name.py
--- a/pkg/old_name.py
+++ b/pkg/new_name.py
@@ -10,2 +20,3 @@
+def added():
+    return 1
"""

    assert agents.parse_changed_files(diff) == ["pkg/new_name.py"]
    notes = agents.parse_diff_context_notes(diff)
    assert "pkg/new_name.py: changed new-file lines 20-22" in notes[0]
    assert "rename:pkg/old_name.py->pkg/new_name.py" in notes[0]


def test_parse_changed_symbols_handles_javascript_and_hunk_headers():
    """Planner symbol extraction should work for real JS/Vue style diffs too."""

    diff = """diff --git a/src/server/api/openai/parse.js b/src/server/api/openai/parse.js
--- a/src/server/api/openai/parse.js
+++ b/src/server/api/openai/parse.js
@@ -81,8 +81,7 @@ export async function parseRequest(data, options) {
+function pushKey(keys, token, mode = 'chat', name = '') {
+const buildPayload = () => {
+const validModes = new Set(['chat', 'agent']);
"""

    symbols = agents.parse_changed_symbols(diff)

    assert "parseRequest" in symbols
    assert "pushKey" in symbols
    assert "buildPayload" in symbols
    assert "validModes" not in symbols


def test_reviewer_fallbacks_only_on_model_call_error(monkeypatch):
    """reviewer_agent 只吞 ModelCallError；本地代码错误要继续抛出。"""

    config = load_project_config(mock_llm=False)
    reviewer = ModelSettings(
        role="reviewer",
        provider="deepseek",
        model="deepseek-test",
        name="logic_reviewer",
    )
    plan = ReviewPlan()

    monkeypatch.setattr(agents, "call_model_json", lambda *args, **kwargs: (_ for _ in ()).throw(ModelCallError("timeout")))
    result = agents.reviewer_agent(
        reviewer=reviewer,
        diff="diff --git a/a.py b/a.py\n",
        review_plan=plan,
        config=config,
    )
    assert result.errors

    monkeypatch.setattr(agents, "call_model_json", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bug")))
    with pytest.raises(ValueError):
        agents.reviewer_agent(
            reviewer=reviewer,
            diff="diff --git a/a.py b/a.py\n",
            review_plan=plan,
            config=config,
        )


def test_reviewer_agent_uses_a2a_when_endpoint_is_configured(monkeypatch):
    """A2A reviewer should be called before the local LangChain reviewer path."""

    reviewer = ModelSettings(
        role="reviewer",
        provider="deepseek",
        model="deepseek-test",
        name="logic_reviewer",
        a2a_url="http://127.0.0.1:9100/a2a",
    )

    def fake_a2a_reviewer(**kwargs):
        return agents.ReviewerResult(
            reviewer_name="remote",
            model="remote/model",
            reviewer_notes=f"a2a called for {kwargs['reviewer'].display_name}",
        )

    monkeypatch.setattr(agents, "call_a2a_reviewer", fake_a2a_reviewer)

    result = agents.reviewer_agent(
        reviewer=reviewer,
        diff="diff --git a/a.py b/a.py\n",
        review_plan=ReviewPlan(),
        config=load_project_config(mock_llm=False),
    )

    assert result.reviewer_name == "logic_reviewer"
    assert result.reviewer_notes == "a2a called for logic_reviewer"


def test_mock_test_author_adds_normal_path_control_case():
    """V1.6 Test Author 应给零分母边界测试补一个正常路径对照。"""

    finding = Finding(
        finding_id="finding_zero",
        source_reviewer="logic_reviewer",
        model="mock",
        category="runtime",
        severity="high",
        file_path="examples/sample_project/math_utils.py",
        line_range=LineRange(start=1, end=3),
        problem="Division helper does not handle zero denominator.",
        evidence="a / b is called directly.",
        impact="ZeroDivisionError can escape.",
        suggestion="Handle zero denominator.",
    )
    intent = TestIntent(
        test_intent_id="intent_zero",
        source_reviewer="logic_reviewer",
        target_file=finding.file_path,
        target_symbol="safe_divide",
        scenario="zero denominator",
        reason="Boundary path should be tested.",
        expected_behavior="Return None for zero denominator.",
        risk_category="runtime",
        related_finding_ids=[finding.finding_id],
    )
    draft = TestCase(
        source_agent="mock_test_case_agent",
        source_test_intent_ids=[intent.test_intent_id],
        related_finding_ids=[finding.finding_id],
        target_file=finding.file_path,
        target_symbol="safe_divide",
        test_name="test_safe_divide_zero_denominator_returns_none",
        test_code=(
            "from examples.sample_project.math_utils import safe_divide\n\n\n"
            "def test_safe_divide_zero_denominator_returns_none():\n"
            "    assert safe_divide(10, 0) is None\n"
        ),
        expected_behavior="Zero denominator should be handled.",
    )

    batch = agents.test_author_agent(
        round_index=1,
        targets=[finding],
        test_intents=[intent],
        draft_test_cases=[draft],
        config=load_project_config(mock_llm=True),
    )

    names = [test_case.test_name for test_case in batch.test_cases]
    assert "test_safe_divide_zero_denominator_returns_none" in names
    assert "test_safe_divide_normal_division_returns_quotient" in names


def test_report_formatter_localizes_and_deduplicates_findings():
    """Final presentation agent should polish raw reviewer output without changing facts."""

    first = Finding(
        finding_id="finding_path_1",
        source_reviewer="logic_reviewer",
        model="deepseek/mock",
        category="security",
        severity="critical",
        file_path="src/utils/readUpload.js",
        line_range=LineRange(start=11, end=13),
        problem="Path traversal vulnerability: filename is concatenated without normalization.",
        evidence="rootDir + '/' + filename is used directly.",
        impact="Attackers can read files outside the upload root.",
        suggestion="Use path.resolve and ensure the resolved path stays within rootDir.",
    )
    duplicate = Finding(
        finding_id="finding_path_2",
        source_reviewer="security_maintainability_reviewer",
        model="anthropic/mock",
        category="security",
        severity="critical",
        file_path="src/utils/readUpload.js",
        line_range=LineRange(start=11, end=13),
        problem="Path traversal vulnerability: user-supplied filename is used without any sanitization.",
        evidence="filename is joined through string concatenation.",
        impact="A crafted ../ path can escape the intended directory.",
        suggestion="Normalize with path.resolve and reject paths outside rootDir.",
    )
    board = BoardSummary(
        finding_count=2,
        finding_verifications={
            first.finding_id: FindingVerification(finding_id=first.finding_id, status="supported"),
            duplicate.finding_id: FindingVerification(finding_id=duplicate.finding_id, status="supported"),
        },
    )

    presentation = agents.report_formatter_agent(
        review_plan=ReviewPlan(changed_files=["src/utils/readUpload.js"]),
        code_context=None,
        reviewer_results=[],
        findings=[first, duplicate],
        board_summary=board,
        evaluator_report=EvaluatorReport(
            risk_level="critical",
            confidence="high",
            recommended_action="Block merge until fixed.",
            reasoning="A critical finding has supporting evidence.",
        ),
        high_risk_review=None,
        config=load_project_config(mock_llm=True),
    )

    assert "严重风险" in presentation.conclusion_summary
    assert presentation.recommended_action == "暂缓合并，先修复高风险问题"
    assert len(presentation.finding_rows) == 1
    row = presentation.finding_rows[0]
    assert row.finding_ids == ["finding_path_1", "finding_path_2"]
    assert "路径穿越风险" in row.problem
    assert "path.resolve" in row.suggestion
