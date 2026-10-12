"""Agent 辅助解析逻辑测试。"""

import pytest

import codecross.agents as agents
from codecross.config import ModelSettings, load_project_config
from codecross.llm import ModelCallError
from codecross.schemas import (
    BoardSummary,
    CodeContext,
    CodeContextSnippet,
    EvaluatorReport,
    Finding,
    FindingCritiqueBatch,
    FindingCritiqueDecision,
    FindingVerification,
    HighRiskReviewReport,
    LineRange,
    ReviewPlan,
    ReviewerResult,
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


def test_parse_changed_symbols_handles_go_java_and_rust():
    diff = """diff --git a/a.go b/a.go
+func (s *Server) HandleRequest(ctx context.Context) error {
+pub async fn load_account(id: u64) -> Result<Account> {
+public static User findUser(String id) {
"""

    symbols = agents.parse_changed_symbols(diff)

    assert {"HandleRequest", "load_account", "findUser"} <= set(symbols)


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


def test_reviewer_prompt_requires_concrete_repository_level_findings(monkeypatch):
    """Prompt 必须抑制风格/猜测型意见，并要求检查跨文件契约。"""

    captured = {}

    def fake_call(settings, messages, schema):
        captured["messages"] = messages
        return ReviewerResult(reviewer_name="x", model="x")

    monkeypatch.setattr(agents, "call_model_json", fake_call)
    reviewer = ModelSettings(
        role="reviewer",
        provider="deepseek",
        model="test",
        focus="repository conventions and PR scope",
    )
    config = load_project_config(mock_llm=False, diff="diff --git a/a.py b/a.py\n")

    result = agents.reviewer_agent(
        reviewer=reviewer,
        diff="diff --git a/a.py b/a.py\n",
        review_plan=ReviewPlan(changed_files=["a.py"]),
        config=config,
    )

    prompt = "\n".join(content for _, content in captured["messages"])
    assert "repository-level" in prompt
    assert "Report each root cause exactly once" in prompt
    assert "Do not report style preferences" in prompt
    assert "concrete trigger condition" in prompt
    assert "same root-cause pattern occurs in several files" in prompt
    assert "re-enter the same wrapper" in prompt
    assert "fixture setup/cleanup identifiers" in prompt


def test_review_only_planner_never_calls_model(monkeypatch):
    monkeypatch.setattr(
        agents,
        "call_model_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model should not be called")),
    )
    config = load_project_config(mock_llm=False, evaluation_mode="review_only")

    plan = agents.planner_agent("diff --git a/a.py b/a.py\n+def changed():\n+    pass\n", config)

    assert plan.changed_files == ["a.py"]
    assert "changed" in plan.changed_symbols


def test_human_review_profile_includes_nonfunctional_but_actionable_concerns():
    config = load_project_config(
        mock_llm=True,
        evaluation_mode="review_only",
        review_profile="human_review_alignment",
    )

    policy = agents._review_profile_policy(config)

    assert "resource lifecycle" in policy
    assert "API idioms" in policy
    assert "vague taste" in policy
    assert config.finding_critic_enabled is False
    assert config.semantic_deduplication_enabled is False
    assert config.max_final_findings == 6
    assert all(reviewer.temperature == 0.0 for reviewer in config.enabled_reviewers())


def test_reviewer_does_not_repeat_diff_already_in_frozen_context(monkeypatch):
    """公开 benchmark 上下文已含 patch 时，Prompt 中不得再出现第二份原始 diff。"""

    captured = {}

    def fake_call(settings, messages, schema):
        captured["messages"] = messages
        captured["schema"] = schema
        return agents.BenchmarkReviewBatch(
            comments=[
                agents.BenchmarkReviewComment(
                    body="A concise actionable concern.",
                    file="a.py",
                    line=7,
                    severity="P1",
                )
            ]
        )

    monkeypatch.setattr(agents, "call_model_json", fake_call)
    reviewer = ModelSettings(
        role="reviewer",
        provider="deepseek",
        model="test",
        focus="repository conventions and PR scope",
    )
    diff = "diff --git a/a.py b/a.py\n+UNIQUE_PATCH_MARKER\n"
    config = load_project_config(
        mock_llm=False,
        diff=diff,
        review_profile="human_review_alignment",
    )
    context = CodeContext(
        contains_diff=True,
        snippets=[
            CodeContextSnippet(
                kind="diagnostic",
                file_path="<frozen>",
                content=f"official rendered context\n{diff}",
            )
        ],
    )

    result = agents.reviewer_agent(
        reviewer=reviewer,
        diff=diff,
        review_plan=ReviewPlan(changed_files=["a.py"]),
        config=config,
        code_context=context,
    )

    prompt = "\n".join(content for _, content in captured["messages"])
    assert prompt.count("UNIQUE_PATCH_MARKER") == 1
    assert "official rendered context" in prompt
    assert "This is the maintainer-fidelity pass" in prompt
    assert "defaults matching documentation and implementation" in prompt
    assert "brittle inline UI layout" in prompt
    assert "Correctness bugs, missing error handling" in prompt
    assert "Do not flag issues in files you have not been shown" in prompt
    assert "golden" not in prompt.lower()
    assert "Review plan:" not in prompt
    assert "Objective diff metadata:" not in prompt
    assert captured["schema"] is agents.BenchmarkReviewBatch
    assert len(result.findings) == 1
    assert result.findings[0].problem == "A concise actionable concern."
    assert result.findings[0].severity == "high"


def test_finding_critic_rejects_only_high_confidence_false_candidate(monkeypatch):
    first = Finding(
        finding_id="keep", source_reviewer="r", model="m", category="runtime",
        severity="high", file_path="a.py", problem="real", evidence="e", impact="i", suggestion="s"
    )
    second = first.model_copy(update={"finding_id": "reject", "problem": "contradicted"})

    monkeypatch.setattr(
        agents,
        "call_model_json",
        lambda *args, **kwargs: FindingCritiqueBatch(
            decisions=[
                FindingCritiqueDecision(finding_id="keep", keep=True, confidence=0.9),
                FindingCritiqueDecision(finding_id="reject", keep=False, confidence=0.95),
            ]
        ),
    )
    config = load_project_config(mock_llm=False, evaluation_mode="review_only")

    kept, rejected, error = agents.finding_critic_agent(
        [first, second], diff="diff", code_context=None, config=config
    )

    assert [item.finding_id for item in kept] == ["keep"]
    assert [item.finding_id for item in rejected] == ["reject"]
    assert error == ""


def test_final_finding_priority_prefers_cross_reviewer_consensus():
    base = Finding(
        source_reviewer="logic",
        model="m",
        category="runtime",
        severity="high",
        confidence="high",
        file_path="single.py",
        problem="single",
        evidence="e",
        impact="i",
        suggestion="s",
    )
    consensus = base.model_copy(
        update={
            "finding_id": "consensus",
            "file_path": "consensus.py",
            "source_reviewers": ["logic", "security"],
            "confidence": "medium",
        }
    )

    selected = agents.prioritize_final_findings([base, consensus], limit=1)

    assert selected[0].finding_id == "consensus"


def test_final_finding_priority_balances_independent_reviewers():
    """human review Top-K 不应被一路模型的高严重度推测全部占满。"""

    findings = []
    for index in range(5):
        findings.append(
            Finding(
                finding_id=f"logic-{index}",
                source_reviewer="logic",
                model="deepseek/test",
                category="runtime",
                severity="high",
                confidence="high",
                file_path=f"logic_{index}.py",
                problem=f"logic concern {index}",
                evidence="e",
                impact="i",
                suggestion="s",
            )
        )
    for index in range(3):
        findings.append(
            Finding(
                finding_id=f"maintainer-{index}",
                source_reviewer="maintainer",
                model="anthropic/test",
                category="maintainability",
                severity="medium",
                confidence="high",
                file_path=f"maintainer_{index}.py",
                problem=f"maintainer concern {index}",
                evidence="e",
                impact="i",
                suggestion="s",
            )
        )

    selected = agents.prioritize_final_findings(
        findings,
        limit=6,
        balance_reviewers=True,
    )

    counts = {}
    for finding in selected:
        counts[finding.source_reviewer] = counts.get(finding.source_reviewer, 0) + 1
    assert counts == {"logic": 3, "maintainer": 3}


def test_human_priority_covers_new_files_before_repeating_one_location():
    """Top-K 应优先覆盖测试文件，不能被同一实现行的近义候选占满。"""

    def make_finding(
        finding_id: str,
        reviewer: str,
        file_path: str,
        line: int,
    ) -> Finding:
        return Finding(
            finding_id=finding_id,
            source_reviewer=reviewer,
            model="fixture/model",
            category="human_review",
            severity="high",
            confidence="high",
            file_path=file_path,
            line_range=LineRange(start=line, end=line),
            problem=f"concern {finding_id}",
            evidence="e",
            impact="i",
            suggestion="s",
        )

    findings = [
        make_finding("impl-a", "logic", "src/manager.py", 100),
        make_finding("impl-b", "maintainer", "src/manager.py", 102),
        make_finding("impl-c", "logic", "src/manager.py", 103),
        make_finding("test", "maintainer", "tests/test_manager.py", 40),
    ]

    selected = agents.prioritize_final_findings(
        findings,
        limit=2,
        balance_reviewers=True,
    )

    assert {item.finding_id for item in selected} == {"impl-a", "test"}


def test_benchmark_maintainer_focus_requires_hunk_and_test_coverage():
    reviewer = ModelSettings(
        role="reviewer",
        provider="anthropic",
        model="test",
        focus="repository conventions, PR scope, and test organization",
    )

    focus = agents._benchmark_reviewer_focus(reviewer)

    assert "every shown changed file and hunk" in focus
    assert "signal/event assertions" in focus
    assert "speculative runtime failure" in focus



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


def test_final_report_renders_english_and_deduplicates_findings():
    """Final report stays English; the new localizer agent owns Chinese output."""

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

    report = agents.render_final_report(
        review_plan=ReviewPlan(
            changed_files=["src/utils/readUpload.js"],
            changed_symbols=["checkout_codecross step removed continue-on-error"],
            risk_areas=["secret handling", "regression risk"],
            testing_focus=["high severity findings first", "pytest evidence for reproducible issues"],
        ),
        code_context=None,
        reviewer_results=[
            ReviewerResult(
                reviewer_name="logic_reviewer",
                model="deepseek/mock",
                findings=[first],
                reviewer_notes="Reviewer identified a blocking issue.",
            ),
            ReviewerResult(
                reviewer_name="security_maintainability_reviewer",
                model="anthropic/mock",
                findings=[duplicate],
            ),
        ],
        board_summary=board,
        evaluator_report=EvaluatorReport(
            risk_level="critical",
            confidence="high",
            recommended_action="Block merge until fixed.",
            reasoning="A critical finding has supporting evidence.",
            evidence_summary="Tests and reviewers indicate a critical failure.",
            failed_tests_summary="The generated regression test fails before the fix.",
        ),
        high_risk_review=HighRiskReviewReport(
            analysis="High risk review confirms the finding is blocking.",
            recommended_action="Do not merge until the path traversal is fixed.",
            blocking_findings=[first.finding_id],
        ),
    )

    assert "CodeCross Final Review Report" in report
    assert "| Field | Value |" in report
    assert "| Risk level | `critical` |" in report
    assert "| Severity | Verification | Location | Finding |" in report
    assert "| Reviewer | Model | Findings | Test intents | Errors | Notes |" in report
    assert "Block merge until fixed." in report
    assert "Path traversal vulnerability" in report
    assert "Problem: Path traversal vulnerability" in report
    assert "<br>Impact: Attackers can read files outside the upload root." in report
    assert "Impact: Attackers can read files outside the upload root." in report
    assert "Recommendation: Use path.resolve" in report
    assert "similar findings merged" in report
    assert "path.resolve" in report
    assert "high severity findings first" in report
    assert "High risk review confirms" in report
    assert "Review Board" in report
    assert "| Metric | Value |" in report
    assert "checkout_codecross step removed" in report
    assert "A critical finding has supporting evidence" in report
    assert "Reviewer identified a blocking issue" in report
    assert "Tests and reviewers indicate" in report


def test_infer_target_language_covers_known_extensions():
    """The deterministic inference table should cover the languages we ship triggers for.

    The CodeCross Issue #2 fallback only fires when ``target_language`` is set, so the
    inference helper is the seam that decides whether a JS / Vue / Go file ever gets
    LLM cross-verification. Lock the contract for the languages we currently care about.
    """

    assert agents._infer_target_language("src/utils/oauthRedirect.js") == "javascript"
    assert agents._infer_target_language("src/utils/Helpers.TS") == "typescript"
    assert agents._infer_target_language("frontend/App.vue") == "vue"
    assert agents._infer_target_language("backend/main.go") == "go"
    assert agents._infer_target_language("examples/sample_project/math_utils.py") == "python"


def test_infer_target_language_returns_none_for_unknown_or_empty():
    """Unknown / suffixless paths must NOT default to a fake language.

    Returning a wrong language would fool ``detect_cross_verification_trigger`` into
    burning the LLM budget on a file pytest could have run, so we explicitly require
    ``None`` for ambiguous inputs.
    """

    assert agents._infer_target_language(None) is None
    assert agents._infer_target_language("") is None
    assert agents._infer_target_language("Makefile") is None
    assert agents._infer_target_language("src/some_dir/") is None
    assert agents._infer_target_language("weird.unknownext") is None


def test_normalize_test_batch_fills_target_language_from_target_file():
    """``_normalize_test_batch`` should backfill ``target_language`` for cases that
    forgot it. The real trigger (cross_verification.triggers) keys off this field
    and the LLM is unreliable about emitting it, so the deterministic backfill is
    what makes the unsupported_language path fire on JS PRs.
    """

    case = TestCase(
        source_agent="mock_test_author_agent",
        related_finding_ids=["finding_js"],
        target_file="src/utils/oauthRedirect.js",
        target_symbol="buildOAuthRedirect",
        test_name="test_build_oauth_redirect_open_redirect",
        test_code="def test_build_oauth_redirect_open_redirect():\n    assert True\n",
        expected_behavior="Open redirect should be blocked.",
    )
    from codecross.schemas import TestCaseBatch  # local import keeps file-level imports tidy

    batch = agents._normalize_test_batch(TestCaseBatch(test_cases=[case]), round_index=1)

    assert batch.test_cases[0].target_language == "javascript"


def test_normalize_test_batch_preserves_explicit_target_language():
    """If the model already supplied a target_language we trust it, even when the
    file extension would map elsewhere. This keeps the door open for hand-written
    Python tests that exercise non-Python code via subprocess.
    """

    case = TestCase(
        source_agent="mock_test_author_agent",
        related_finding_ids=["finding_mixed"],
        target_file="src/utils/oauthRedirect.js",
        target_symbol="buildOAuthRedirect",
        test_name="test_build_oauth_redirect_python_harness",
        test_code="def test_build_oauth_redirect_python_harness():\n    assert True\n",
        expected_behavior="Run the JS via a Python harness.",
        target_language="python",
    )
    from codecross.schemas import TestCaseBatch

    batch = agents._normalize_test_batch(TestCaseBatch(test_cases=[case]), round_index=1)

    assert batch.test_cases[0].target_language == "python"


def test_report_localizer_mock_returns_chinese_section():
    """Mock runs still expose the new final-report localizer node output."""

    localized = agents.report_localizer_agent(
        final_report="# CodeCross Final Review Report\n\nEnglish body.",
        config=load_project_config(mock_llm=True),
    )

    assert "CodeCross 最终审查报告（中文）" in localized
    assert "Mock 模式未调用中文化模型" in localized


# ---------------------------------------------------------------------------
# 方案 B 新增测试
# ---------------------------------------------------------------------------


def test_short_text_preserves_paragraph_breaks():
    """_short_text 修复后应保留段落（双换行）但折叠段内多余空白。"""

    text = (
        "F-001 (critical): JWT signature verification missing.\n\n"
        "F-002 (high): Expiration\n  check uses string comparison."
    )

    cleaned = agents._short_text(text)

    # 段间分隔保留为 \n\n。
    assert "\n\n" in cleaned
    # 段内多余空白被压成一个空格——"Expiration\n  check" -> "Expiration check"。
    assert "Expiration check" in cleaned
    # 第一段完整保留。
    assert cleaned.startswith("F-001 (critical): JWT signature verification missing.")


def test_short_text_falls_back_when_empty():
    """空字符串/纯空白应触发 fallback，避免 PR 评论里出现空格子。"""

    assert agents._short_text("") == "not provided"
    assert agents._short_text("   \n\n  ") == "not provided"
    assert agents._short_text(None) == "not provided"  # type: ignore[arg-type]


def test_finding_cells_localizer_mock_returns_per_finding_translation():
    """Mock 模式下应给每条 finding 都返回带 finding_id 的中文 cell。"""

    findings = [
        Finding(
            source_reviewer="logic_reviewer",
            model="mock",
            category="runtime",
            severity="high",
            file_path="src/app.py",
            line_range=LineRange(start=12, end=14),
            problem="Bad branch returns None",
            evidence="evidence",
            impact="callers crash",
            suggestion="preserve string contract",
        ),
        Finding(
            source_reviewer="logic_reviewer",
            model="mock",
            category="security",
            severity="critical",
            file_path="src/auth.py",
            line_range=LineRange(start=20),
            problem="No signature verification",
            evidence="evidence",
            impact="auth bypass",
            suggestion="use jsonwebtoken",
        ),
    ]
    config = load_project_config(mock_llm=True)

    cells = agents.finding_cells_localizer_agent(findings=findings, config=config)

    assert set(cells.keys()) == {finding.finding_id for finding in findings}
    for finding in findings:
        cell = cells[finding.finding_id]
        # mock 文本里要带原英文，证明 finding_id 和原文都正确路由到了对应 cell。
        assert finding.problem in cell.problem_zh
        assert finding.suggestion in cell.suggestion_zh
        # finding_id 必须原样回填（PR 评论要靠它做索引）。
        assert cell.finding_id == finding.finding_id


def test_finding_cells_localizer_returns_empty_when_no_findings():
    """没有 finding 时应直接返回空字典，不调用模型。"""

    cells = agents.finding_cells_localizer_agent(
        findings=[],
        config=load_project_config(mock_llm=True),
    )

    assert cells == {}
