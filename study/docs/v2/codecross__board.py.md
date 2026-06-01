# `codecross/board.py` 解析

## 文件作用
Review Board 规则层，把 reviewer finding、测试结果、定向复审和证据统一写入黑板，并决定验证循环是否继续。

- 源码行数：598
- 主要依赖：`re`、`collections`、`config`、`schemas`

## 函数与类

### `severity_rank`（函数，第 56 行）
- 签名：`def severity_rank(finding: Finding) -> int`
- 作用：把严重等级转换成可排序数字。

### `collect_review_outputs`（函数，第 62 行）
- 签名：`def collect_review_outputs(reviewer_results: list[ReviewerResult]) -> tuple[list[Finding], list[TestIntent]]`
- 作用：把多个 reviewer 的输出拆成全局 findings 和 test intents。

### `init_finding_verifications`（函数，第 75 行）
- 签名：`def init_finding_verifications(findings: list[Finding]) -> dict[str, FindingVerification]`
- 作用：为每条 Finding 初始化 Board 维护的验证状态。

### `init_static_evidence`（函数，第 87 行）
- 签名：`def init_static_evidence(findings: list[Finding]) -> dict[str, EvidenceItem]`
- 作用：把 reviewer 原始 evidence 也登记为 weak static_evidence。

### `_cluster_key`（函数，第 105 行）
- 签名：`def _cluster_key(finding: Finding) -> str`
- 作用：生成轻量聚类 key，用于 V1 的简单去重。

### `cluster_findings`（函数，第 113 行）
- 签名：`def cluster_findings(findings: list[Finding]) -> list[FindingCluster]`
- 作用：按文件、类别、行号和问题关键词做轻量 finding cluster。

### `_finding_by_id`（函数，第 134 行）
- 签名：`def _finding_by_id(findings: list[Finding]) -> dict[str, Finding]`
- 作用：把 findings 列表转成按 ID 查询的 dict。

### `build_verification_queue`（函数，第 140 行）
- 签名：`def build_verification_queue(findings: list[Finding], verifications: dict[str, FindingVerification], clusters: list[FindingCluster]) -> list[str]`
- 作用：根据 Board 状态生成下一轮验证队列。

### `build_board_summary`（函数，第 180 行）
- 签名：`def build_board_summary(*, reviewer_results: list[ReviewerResult], findings: list[Finding], test_intents: list[TestIntent], test_cases: list[TestCase], test_results: list[TestResult], test_rounds: list[TestRound], verifications: dict[str, FindingVerification], evidence_map: dict[str, EvidenceItem], coverage_gaps: list[str], stop_reason: str) -> BoardSummary`
- 作用：构造 Board 的汇总视图，供 evaluator/report/workflow 使用。

### `select_next_verification_targets`（函数，第 217 行）
- 签名：`def select_next_verification_targets(findings: list[Finding], board_summary: BoardSummary, runner_config: RunnerConfig) -> list[Finding]`
- 作用：从 verification_queue 中挑选本轮要处理的 Finding。

### `_result_to_evidence`（函数，第 236 行）
- 签名：`def _result_to_evidence(result: TestResult) -> EvidenceItem`
- 作用：把 executor 的 TestResult 映射成 Board EvidenceItem。

### `_apply_cross_decision`（函数，第 312 行）
- 签名：`def _apply_cross_decision(*, verification: FindingVerification, counts: dict[str, int], previous_status: str, round_index: int) -> None`
- 作用：根据本 finding 上累计的 cross verification 计数更新 verification。

### `update_finding_verifications_from_results`（函数，第 372 行）
- 签名：`def update_finding_verifications_from_results(verifications: dict[str, FindingVerification], evidence_map: dict[str, EvidenceItem], test_results: list[TestResult]) -> tuple[dict[str, FindingVerification], dict[str, EvidenceItem], dict[str, str]]`
- 作用：根据测试结果更新 finding verification。

### `apply_analysis_updates`（函数，第 486 行）
- 签名：`def apply_analysis_updates(verifications: dict[str, FindingVerification], analysis: TestResultAnalysis) -> dict[str, str]`
- 作用：应用 Test Result Analyzer 的状态建议。

### `apply_targeted_review`（函数，第 515 行）
- 签名：`def apply_targeted_review(verifications: dict[str, FindingVerification], evidence_map: dict[str, EvidenceItem], targeted_review: TargetedReview) -> tuple[dict[str, FindingVerification], dict[str, EvidenceItem], dict[str, str]]`
- 作用：把 Targeted Reviewer 的复审结果登记为 evidence 并更新状态。

### `should_continue_verification`（函数，第 553 行）
- 签名：`def should_continue_verification(board_summary: BoardSummary, round_index: int, runner_config: RunnerConfig, status_changes: dict[str, str] | None=None) -> tuple[bool, str]`
- 作用：由 Board 状态决定 Evidence Verification Loop 是否继续。

### `render_board_markdown`（函数，第 572 行）
- 签名：`def render_board_markdown(summary: BoardSummary) -> str`
- 作用：把 BoardSummary 渲染成英文最终报告中的 Review Board 段落；中文版本由 Report Localizer Agent 统一翻译。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
