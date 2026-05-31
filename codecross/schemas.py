from __future__ import annotations

"""CodeCross 的核心数据结构。

这个文件尽量只描述“数据长什么样”，不放业务流程。
这样读代码时可以先从这里建立心智模型：Reviewer 产出 Finding/TestIntent，
Executor 产出 TestResult，Board 维护 Evidence 和 Verification，最后 Evaluator 输出报告。
"""

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# 下面这些 Literal 是系统里常用的枚举值。用 Literal 而不是散落的字符串，
# 可以让 Pydantic 校验 Agent 输出，也方便 IDE/类型检查器提示合法取值。
RiskLevel = Literal["low", "medium", "high", "critical", "unknown"]
Severity = Literal["low", "medium", "high", "critical"]
Confidence = Literal["low", "medium", "high"]
Priority = Literal["low", "medium", "high", "critical"]
VerificationStatus = Literal[
    "unverified",
    "supported",
    "refuted",
    "inconclusive",
    "not_testable",
]
EvidenceType = Literal[
    "test_failure",
    "test_pass",
    "executor_error",
    "static_evidence",
    "targeted_review_note",
    "coverage_gap",
    "manual_note",
]
EvidenceStrength = Literal["weak", "medium", "strong"]
TestType = Literal["pytest", "function_call", "property_based", "static_check", "manual"]
TestStatus = Literal["passed", "failed", "error", "skipped", "timeout", "duplicate", "unsafe"]
CodeContextKind = Literal[
    "changed_file",
    "changed_hunk",
    "symbol_definition",
    "call_site",
    "existing_test",
    "diagnostic",
]


def new_id(prefix: str) -> str:
    """生成带业务前缀的短 ID，方便在报告和 Board 里追踪对象来源。"""

    return f"{prefix}_{uuid4().hex[:12]}"


def utc_now() -> str:
    """统一使用 UTC 时间，避免本地时区差异影响证据记录。"""

    return datetime.now(timezone.utc).isoformat()


class CodeCrossModel(BaseModel):
    """所有 schema 的共同基类。

    extra="ignore" 让 LLM 偶尔多吐字段时不会直接炸掉；
    validate_assignment=True 让后续修改字段时仍然触发类型校验。
    __test__=False 是为了避免 pytest 把 TestCase/TestResult 当测试类收集。
    """

    __test__ = False

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class LineRange(CodeCrossModel):
    """Finding 在文件中的行号范围；行号未知时允许为空。"""

    start: int | None = None
    end: int | None = None


class ReviewPlan(CodeCrossModel):
    """Planner 的输出：本次 diff 应该重点审什么、测什么。"""

    plan_id: str = Field(default_factory=lambda: new_id("plan"))
    changed_files: list[str] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)
    risk_areas: list[str] = Field(default_factory=list)
    review_focus: list[str] = Field(default_factory=list)
    testing_focus: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)


class CodeContextSnippet(CodeCrossModel):
    """Context Retriever 找到的一段客观代码上下文。

    V1.2 的重点是让 reviewer 不再“盲审 diff”。这里的 snippet 不表达判断，
    只表达“这段代码为什么被拿来给 reviewer 看”：变更文件、符号定义、调用点、
    现有测试或诊断信息。这样可以保持 reviewer 独立性，也方便后续审计 prompt。
    """

    kind: CodeContextKind
    file_path: str
    start_line: int = 1
    end_line: int = 1
    reason: str = ""
    content: str = ""
    symbol: str | None = None


class CodeContext(CodeCrossModel):
    """一次 Run 的代码上下文检索结果。

    Context Retriever 只读取本地项目文件，不调用模型、不做风险判断。它给 Reviewer
    提供更接近真实 code review 的输入：完整变更文件、小范围符号定义、调用点和现有测试。
    """

    changed_files: list[str] = Field(default_factory=list)
    changed_symbols: list[str] = Field(default_factory=list)
    snippets: list[CodeContextSnippet] = Field(default_factory=list)
    missing_files: list[str] = Field(default_factory=list)
    summary_notes: list[str] = Field(default_factory=list)


class Finding(CodeCrossModel):
    """Reviewer 提出的一个风险假设。

    注意：Finding 只表达“Reviewer 怀疑什么”，不表达最终是否成立。
    是否成立由 Board 通过测试证据更新 FindingVerification。
    """

    finding_id: str = Field(default_factory=lambda: new_id("finding"))
    source_reviewer: str
    model: str
    category: str
    severity: Severity
    confidence: Confidence = "medium"
    file_path: str
    line_range: LineRange = Field(default_factory=LineRange)
    problem: str
    evidence: str
    impact: str
    suggestion: str
    reproduction_hint: str = ""
    related_test_intent_ids: list[str] = Field(default_factory=list)


class FindingVerification(CodeCrossModel):
    """Board 为每条 Finding 维护的验证状态。"""

    finding_id: str
    status: VerificationStatus = "unverified"
    evidence_ids: list[str] = Field(default_factory=list)
    related_test_result_ids: list[str] = Field(default_factory=list)
    related_targeted_review_ids: list[str] = Field(default_factory=list)
    last_updated_round: int = 0
    reason: str = "Waiting for verification."
    confidence: Confidence = "medium"
    next_action: str = "Generate targeted tests or collect additional evidence."


class EvidenceItem(CodeCrossModel):
    """Board 黑板里的证据条目。

    证据可以来自静态审查、pytest 失败、pytest 通过、executor 错误或定向复审。
    """

    evidence_id: str = Field(default_factory=lambda: new_id("evidence"))
    evidence_type: EvidenceType
    source: str
    related_finding_ids: list[str] = Field(default_factory=list)
    related_test_case_id: str | None = None
    related_test_result_id: str | None = None
    related_targeted_review_id: str | None = None
    summary: str
    raw_reference: str = ""
    strength: EvidenceStrength = "medium"
    created_round: int = 0
    created_at: str = Field(default_factory=utc_now)


class TestIntent(CodeCrossModel):
    """Reviewer 给 Test Runner 的测试意图，不要求直接可执行。"""

    test_intent_id: str = Field(default_factory=lambda: new_id("intent"))
    source_reviewer: str
    target_file: str
    target_symbol: str = ""
    scenario: str
    reason: str
    expected_behavior: str
    risk_category: str
    priority: Priority = "medium"
    related_finding_ids: list[str] = Field(default_factory=list)


class TestCase(CodeCrossModel):
    """Test Case Agent 生成的可执行或半可执行测试描述。"""

    test_case_id: str = Field(default_factory=lambda: new_id("test"))
    source_agent: str
    source_test_intent_ids: list[str] = Field(default_factory=list)
    related_finding_ids: list[str] = Field(default_factory=list)
    round_index: int = 1
    test_type: TestType = "pytest"
    target_file: str
    target_symbol: str = ""
    test_name: str
    test_code: str
    expected_behavior: str
    assertions: list[str] = Field(default_factory=list)
    timeout_seconds: int = 15
    requires_external_service: bool = False
    safety_level: Literal["safe", "review_required", "unsafe"] = "safe"


class TestResult(CodeCrossModel):
    """Deterministic Executor 真实执行测试后返回的结构化结果。"""

    test_result_id: str = Field(default_factory=lambda: new_id("result"))
    test_case_id: str
    round_index: int
    status: TestStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    failure_summary: str = ""
    error_type: str = ""
    is_reproducible: bool = False
    related_finding_ids: list[str] = Field(default_factory=list)


class TestRound(CodeCrossModel):
    """Evidence Verification Loop 的一轮记录。"""

    round_index: int
    input_focus: list[str] = Field(default_factory=list)
    generated_test_case_ids: list[str] = Field(default_factory=list)
    executed_test_case_ids: list[str] = Field(default_factory=list)
    new_failures: list[str] = Field(default_factory=list)
    new_coverage_notes: list[str] = Field(default_factory=list)
    verification_status_changes: dict[str, str] = Field(default_factory=dict)
    continue_verification: bool = False
    stop_reason: str = ""


class TargetedReview(CodeCrossModel):
    """证据不足或冲突时触发的定向复审结果。"""

    targeted_review_id: str = Field(default_factory=lambda: new_id("targeted"))
    round_index: int
    target_finding_ids: list[str] = Field(default_factory=list)
    input_test_result_ids: list[str] = Field(default_factory=list)
    reviewer_name: str
    model: str
    analysis: str
    suggested_status_updates: dict[str, VerificationStatus] = Field(default_factory=dict)
    new_test_intents: list[TestIntent] = Field(default_factory=list)
    not_testable_reasons: dict[str, str] = Field(default_factory=dict)


class FindingCluster(CodeCrossModel):
    """轻量去重/聚类结果，用来判断多个 Reviewer 是否命中同类问题。"""

    cluster_id: str = Field(default_factory=lambda: new_id("cluster"))
    finding_ids: list[str] = Field(default_factory=list)
    representative_problem: str = ""
    reviewers: list[str] = Field(default_factory=list)
    severity: Severity = "low"


class BoardSummary(CodeCrossModel):
    """Review Board 对当前全部证据和状态的汇总视图。"""

    reviewer_count: int = 0
    finding_count: int = 0
    test_intent_count: int = 0
    test_case_count: int = 0
    test_round_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    finding_clusters: list[FindingCluster] = Field(default_factory=list)
    finding_verifications: dict[str, FindingVerification] = Field(default_factory=dict)
    verification_queue: list[str] = Field(default_factory=list)
    failed_tests: list[TestResult] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    evidence_map: dict[str, EvidenceItem] = Field(default_factory=dict)
    stop_reason: str = ""


class EvaluatorReport(CodeCrossModel):
    """Evaluator 的最终风险判断。

    V1 的风险等级优先由规则决定，LLM 以后可以辅助润色 reasoning/report。
    """

    risk_level: RiskLevel
    confidence: Confidence = "medium"
    top_findings: list[str] = Field(default_factory=list)
    evidence_summary: str = ""
    failed_tests_summary: str = ""
    coverage_gaps: list[str] = Field(default_factory=list)
    verification_summary: str = ""
    recommended_action: str = ""
    needs_high_risk_review: bool = False
    reasoning: str = ""


class ReviewerResult(CodeCrossModel):
    """单个 reviewer 的完整输出。"""

    reviewer_name: str
    model: str
    findings: list[Finding] = Field(default_factory=list)
    test_intents: list[TestIntent] = Field(default_factory=list)
    reviewer_notes: str = ""
    errors: list[str] = Field(default_factory=list)


class TestStrategy(CodeCrossModel):
    """Test Strategy Agent 为某一轮验证生成的测试策略。"""

    strategy_id: str = Field(default_factory=lambda: new_id("strategy"))
    round_index: int
    target_finding_ids: list[str] = Field(default_factory=list)
    focus: list[str] = Field(default_factory=list)
    coverage_goals: list[str] = Field(default_factory=list)
    stop_if: list[str] = Field(default_factory=list)
    notes: str = ""


class TestCaseBatch(CodeCrossModel):
    """Test Case Agent 一次返回的一批测试。"""

    test_cases: list[TestCase] = Field(default_factory=list)
    skipped_targets: dict[str, str] = Field(default_factory=dict)


class TestResultAnalysis(CodeCrossModel):
    """Test Result Analyzer 对 executor 结果的解释和下一步建议。"""

    round_index: int
    result_notes: list[str] = Field(default_factory=list)
    suggested_status_updates: dict[str, VerificationStatus] = Field(default_factory=dict)
    coverage_gaps: list[str] = Field(default_factory=list)
    need_targeted_review: bool = False


class HighRiskReviewReport(CodeCrossModel):
    """High Risk Review 对高风险结果的二次确认。"""

    analysis: str
    recommended_action: str
    blocking_findings: list[str] = Field(default_factory=list)


class ReportFindingRow(CodeCrossModel):
    """报告展示层的一行 finding。

    Reviewer 原始输出仍保留在 ``Finding`` 里；这里是最后展示给 PR/前端的
    本地化、去重和压缩结果。
    """

    finding_ids: list[str] = Field(default_factory=list)
    severity: Severity
    verification_status: str = ""
    location: str = ""
    problem: str = ""
    suggestion: str = ""
    source_reviewers: list[str] = Field(default_factory=list)


class ReportPresentation(CodeCrossModel):
    """最终报告展示层。

    这个对象只负责“怎么说清楚”，不改变 Evaluator 的风险判断、Board 的证据状态
    或 Reviewer 的原始 findings。
    """

    conclusion_summary: str = ""
    recommended_action: str = ""
    finding_rows: list[ReportFindingRow] = Field(default_factory=list)
    presentation_notes: str = ""
