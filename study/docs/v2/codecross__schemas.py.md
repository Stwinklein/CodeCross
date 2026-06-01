# `codecross/schemas.py` 解析

## 文件作用
数据契约层，定义 Agent、Board、Executor、API、报告之间共享的 Pydantic schema。

- 源码行数：468
- 主要依赖：`datetime`、`typing`、`uuid`、`pydantic`

## 函数与类

### `new_id`（函数，第 93 行）
- 签名：`def new_id(prefix: str) -> str`
- 作用：生成带业务前缀的短 ID，方便在报告和 Board 里追踪对象来源。

### `utc_now`（函数，第 99 行）
- 签名：`def utc_now() -> str`
- 作用：统一使用 UTC 时间，避免本地时区差异影响证据记录。

### `CodeCrossModel`（类，第 105 行）
- 签名：`class CodeCrossModel(BaseModel)`
- 作用：所有 schema 的共同基类。
- 主要字段/属性：
  - `model_config = ConfigDict(extra='ignore', validate_assignment=True)`

### `LineRange`（类，第 118 行）
- 签名：`class LineRange(CodeCrossModel)`
- 作用：Finding 在文件中的行号范围；行号未知时允许为空。
- 主要字段/属性：
  - `start: int | None = None`
  - `end: int | None = None`

### `ReviewPlan`（类，第 125 行）
- 签名：`class ReviewPlan(CodeCrossModel)`
- 作用：Planner 的输出：本次 diff 应该重点审什么、测什么。
- 主要字段/属性：
  - `plan_id: str = Field(default_factory=lambda: new_id('plan'))`
  - `changed_files: list[str] = Field(default_factory=list)`
  - `changed_symbols: list[str] = Field(default_factory=list)`
  - `risk_areas: list[str] = Field(default_factory=list)`
  - `review_focus: list[str] = Field(default_factory=list)`
  - `testing_focus: list[str] = Field(default_factory=list)`
  - `context_notes: list[str] = Field(default_factory=list)`

### `CodeContextSnippet`（类，第 137 行）
- 签名：`class CodeContextSnippet(CodeCrossModel)`
- 作用：Context Retriever 找到的一段客观代码上下文。
- 主要字段/属性：
  - `kind: CodeContextKind`
  - `file_path: str`
  - `start_line: int = 1`
  - `end_line: int = 1`
  - `reason: str = ''`
  - `content: str = ''`
  - `symbol: str | None = None`

### `CodeContext`（类，第 154 行）
- 签名：`class CodeContext(CodeCrossModel)`
- 作用：一次 Run 的代码上下文检索结果。
- 主要字段/属性：
  - `changed_files: list[str] = Field(default_factory=list)`
  - `changed_symbols: list[str] = Field(default_factory=list)`
  - `snippets: list[CodeContextSnippet] = Field(default_factory=list)`
  - `missing_files: list[str] = Field(default_factory=list)`
  - `summary_notes: list[str] = Field(default_factory=list)`

### `Finding`（类，第 168 行）
- 签名：`class Finding(CodeCrossModel)`
- 作用：Reviewer 提出的一个风险假设。
- 主要字段/属性：
  - `finding_id: str = Field(default_factory=lambda: new_id('finding'))`
  - `source_reviewer: str`
  - `model: str`
  - `category: str`
  - `severity: Severity`
  - `confidence: Confidence = 'medium'`
  - `file_path: str`
  - `line_range: LineRange = Field(default_factory=LineRange)`
  - `problem: str`
  - `evidence: str`
  - `impact: str`
  - `suggestion: str`
  - `reproduction_hint: str = ''`
  - `related_test_intent_ids: list[str] = Field(default_factory=list)`

### `FindingVerification`（类，第 191 行）
- 签名：`class FindingVerification(CodeCrossModel)`
- 作用：Board 为每条 Finding 维护的验证状态。
- 主要字段/属性：
  - `finding_id: str`
  - `status: VerificationStatus = 'unverified'`
  - `evidence_ids: list[str] = Field(default_factory=list)`
  - `related_test_result_ids: list[str] = Field(default_factory=list)`
  - `related_targeted_review_ids: list[str] = Field(default_factory=list)`
  - `last_updated_round: int = 0`
  - `reason: str = 'Waiting for verification.'`
  - `confidence: Confidence = 'medium'`
  - `next_action: str = 'Generate targeted tests or collect additional evidence.'`

### `EvidenceItem`（类，第 205 行）
- 签名：`class EvidenceItem(CodeCrossModel)`
- 作用：Board 黑板里的证据条目。
- 主要字段/属性：
  - `evidence_id: str = Field(default_factory=lambda: new_id('evidence'))`
  - `evidence_type: EvidenceType`
  - `source: str`
  - `related_finding_ids: list[str] = Field(default_factory=list)`
  - `related_test_case_id: str | None = None`
  - `related_test_result_id: str | None = None`
  - `related_targeted_review_id: str | None = None`
  - `summary: str`
  - `raw_reference: str = ''`
  - `strength: EvidenceStrength = 'medium'`
  - `created_round: int = 0`
  - `created_at: str = Field(default_factory=utc_now)`

### `TestIntent`（类，第 225 行）
- 签名：`class TestIntent(CodeCrossModel)`
- 作用：Reviewer 给 Test Runner 的测试意图，不要求直接可执行。
- 主要字段/属性：
  - `test_intent_id: str = Field(default_factory=lambda: new_id('intent'))`
  - `source_reviewer: str`
  - `target_file: str`
  - `target_symbol: str = ''`
  - `scenario: str`
  - `reason: str`
  - `expected_behavior: str`
  - `risk_category: str`
  - `priority: Priority = 'medium'`
  - `related_finding_ids: list[str] = Field(default_factory=list)`

### `TestCase`（类，第 240 行）
- 签名：`class TestCase(CodeCrossModel)`
- 作用：Test Case Agent 生成的可执行或半可执行测试描述。
- 主要字段/属性：
  - `test_case_id: str = Field(default_factory=lambda: new_id('test'))`
  - `source_agent: str`
  - `source_test_intent_ids: list[str] = Field(default_factory=list)`
  - `related_finding_ids: list[str] = Field(default_factory=list)`
  - `round_index: int = 1`
  - `test_type: TestType = 'pytest'`
  - `target_file: str`
  - `target_symbol: str = ''`
  - `test_name: str`
  - `test_code: str`
  - `expected_behavior: str`
  - `assertions: list[str] = Field(default_factory=list)`
  - `timeout_seconds: int = 15`
  - `requires_external_service: bool = False`
  - `safety_level: Literal['safe', 'review_required', 'unsafe'] = 'safe'`
  - `cross_verification_trigger: Literal['unsupported_language', 'environment_error', 'executor_blocked'] | None = None`
  - `target_language: str | None = None`

### `CrossVerificationPayload`（类，第 269 行）
- 签名：`class CrossVerificationPayload(CodeCrossModel)`
- 作用：LLM 交叉验证一次调用的结构化产物。
- 主要字段/属性：
  - `layer: Literal['repro_agent', 'refute_agent', 'behavior_oracle'] = 'repro_agent'`
  - `model: str = ''`
  - `status: Literal['confirmed', 'supported', 'refuted', 'inconclusive', 'abstain'] = 'inconclusive'`
  - `reasoning: str = ''`
  - `reproduction_steps: list[str] = Field(default_factory=list)`
  - `expected_observation: str = ''`
  - `counter_examples: list[str] = Field(default_factory=list)`
  - `abstain_reason: str = ''`
  - `suggested_strength: EvidenceStrength = 'weak'`

### `TestResult`（类，第 292 行）
- 签名：`class TestResult(CodeCrossModel)`
- 作用：Deterministic Executor 真实执行测试后返回的结构化结果。
- 主要字段/属性：
  - `test_result_id: str = Field(default_factory=lambda: new_id('result'))`
  - `test_case_id: str`
  - `round_index: int`
  - `status: TestStatus`
  - `exit_code: int | None = None`
  - `stdout: str = ''`
  - `stderr: str = ''`
  - `duration_ms: int = 0`
  - `failure_summary: str = ''`
  - `error_type: str = ''`
  - `is_reproducible: bool = False`
  - `related_finding_ids: list[str] = Field(default_factory=list)`
  - `evidence_kind: EvidenceKind = 'pytest'`
  - `cross_payload: 'CrossVerificationPayload | None' = None`

### `TestRound`（类，第 316 行）
- 签名：`class TestRound(CodeCrossModel)`
- 作用：Evidence Verification Loop 的一轮记录。
- 主要字段/属性：
  - `round_index: int`
  - `input_focus: list[str] = Field(default_factory=list)`
  - `generated_test_case_ids: list[str] = Field(default_factory=list)`
  - `executed_test_case_ids: list[str] = Field(default_factory=list)`
  - `new_failures: list[str] = Field(default_factory=list)`
  - `new_coverage_notes: list[str] = Field(default_factory=list)`
  - `verification_status_changes: dict[str, str] = Field(default_factory=dict)`
  - `continue_verification: bool = False`
  - `stop_reason: str = ''`

### `TargetedReview`（类，第 330 行）
- 签名：`class TargetedReview(CodeCrossModel)`
- 作用：证据不足或冲突时触发的定向复审结果。
- 主要字段/属性：
  - `targeted_review_id: str = Field(default_factory=lambda: new_id('targeted'))`
  - `round_index: int`
  - `target_finding_ids: list[str] = Field(default_factory=list)`
  - `input_test_result_ids: list[str] = Field(default_factory=list)`
  - `reviewer_name: str`
  - `model: str`
  - `analysis: str`
  - `suggested_status_updates: dict[str, VerificationStatus] = Field(default_factory=dict)`
  - `new_test_intents: list[TestIntent] = Field(default_factory=list)`
  - `not_testable_reasons: dict[str, str] = Field(default_factory=dict)`

### `FindingCluster`（类，第 345 行）
- 签名：`class FindingCluster(CodeCrossModel)`
- 作用：轻量去重/聚类结果，用来判断多个 Reviewer 是否命中同类问题。
- 主要字段/属性：
  - `cluster_id: str = Field(default_factory=lambda: new_id('cluster'))`
  - `finding_ids: list[str] = Field(default_factory=list)`
  - `representative_problem: str = ''`
  - `reviewers: list[str] = Field(default_factory=list)`
  - `severity: Severity = 'low'`

### `BoardSummary`（类，第 355 行）
- 签名：`class BoardSummary(CodeCrossModel)`
- 作用：Review Board 对当前全部证据和状态的汇总视图。
- 主要字段/属性：
  - `reviewer_count: int = 0`
  - `finding_count: int = 0`
  - `test_intent_count: int = 0`
  - `test_case_count: int = 0`
  - `test_round_count: int = 0`
  - `passed_count: int = 0`
  - `failed_count: int = 0`
  - `error_count: int = 0`
  - `skipped_count: int = 0`
  - `finding_clusters: list[FindingCluster] = Field(default_factory=list)`
  - `finding_verifications: dict[str, FindingVerification] = Field(default_factory=dict)`
  - `verification_queue: list[str] = Field(default_factory=list)`
  - `failed_tests: list[TestResult] = Field(default_factory=list)`
  - `coverage_gaps: list[str] = Field(default_factory=list)`
  - `evidence_map: dict[str, EvidenceItem] = Field(default_factory=dict)`
  - `stop_reason: str = ''`

### `EvaluatorReport`（类，第 376 行）
- 签名：`class EvaluatorReport(CodeCrossModel)`
- 作用：Evaluator 的最终风险判断。
- 主要字段/属性：
  - `risk_level: RiskLevel`
  - `confidence: Confidence = 'medium'`
  - `top_findings: list[str] = Field(default_factory=list)`
  - `evidence_summary: str = ''`
  - `failed_tests_summary: str = ''`
  - `coverage_gaps: list[str] = Field(default_factory=list)`
  - `verification_summary: str = ''`
  - `recommended_action: str = ''`
  - `needs_high_risk_review: bool = False`
  - `reasoning: str = ''`

### `ReviewerResult`（类，第 394 行）
- 签名：`class ReviewerResult(CodeCrossModel)`
- 作用：单个 reviewer 的完整输出。
- 主要字段/属性：
  - `reviewer_name: str`
  - `model: str`
  - `findings: list[Finding] = Field(default_factory=list)`
  - `test_intents: list[TestIntent] = Field(default_factory=list)`
  - `reviewer_notes: str = ''`
  - `errors: list[str] = Field(default_factory=list)`

### `TestStrategy`（类，第 405 行）
- 签名：`class TestStrategy(CodeCrossModel)`
- 作用：Test Strategy Agent 为某一轮验证生成的测试策略。
- 主要字段/属性：
  - `strategy_id: str = Field(default_factory=lambda: new_id('strategy'))`
  - `round_index: int`
  - `target_finding_ids: list[str] = Field(default_factory=list)`
  - `focus: list[str] = Field(default_factory=list)`
  - `coverage_goals: list[str] = Field(default_factory=list)`
  - `stop_if: list[str] = Field(default_factory=list)`
  - `notes: str = ''`

### `TestCaseBatch`（类，第 417 行）
- 签名：`class TestCaseBatch(CodeCrossModel)`
- 作用：Test Case Agent 一次返回的一批测试。
- 主要字段/属性：
  - `test_cases: list[TestCase] = Field(default_factory=list)`
  - `skipped_targets: dict[str, str] = Field(default_factory=dict)`

### `TestResultAnalysis`（类，第 424 行）
- 签名：`class TestResultAnalysis(CodeCrossModel)`
- 作用：Test Result Analyzer 对 executor 结果的解释和下一步建议。
- 主要字段/属性：
  - `round_index: int`
  - `result_notes: list[str] = Field(default_factory=list)`
  - `suggested_status_updates: dict[str, VerificationStatus] = Field(default_factory=dict)`
  - `coverage_gaps: list[str] = Field(default_factory=list)`
  - `need_targeted_review: bool = False`

### `HighRiskReviewReport`（类，第 434 行）
- 签名：`class HighRiskReviewReport(CodeCrossModel)`
- 作用：High Risk Review 对高风险结果的二次确认。
- 主要字段/属性：
  - `analysis: str`
  - `recommended_action: str`
  - `blocking_findings: list[str] = Field(default_factory=list)`

### `LocalizedReport`（类，第 442 行）
- 签名：`class LocalizedReport(CodeCrossModel)`
- 作用：Report Localizer Agent 的结构化输出。
- 主要字段/属性：
  - `markdown: str = Field(default='', description='Simplified Chinese Markdown translation of the final report.')`

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
