# `codecross/agents.py` 解析

## 文件作用
智能体能力层核心文件：解析 diff、组装 prompt、调用 LLM、提供 deterministic mock，并产出 Planner、Reviewer、测试、评估、英文最终报告和中文化 Report Localizer 输出。

- 源码行数：1493
- 主要依赖：`json`、`re`、`pathlib`、`dataclasses`、`pydantic`、`a2a`、`board`、`config`、`context`、`llm`、`schemas`

## 函数与类

### `_json`（函数，第 51 行）
- 签名：`def _json(data: object) -> str`
- 作用：把对象转成缩进 JSON，主要用于 prompt 拼装。

### `DiffFileMeta`（类，第 60 行）
- 签名：`class DiffFileMeta`
- 作用：轻量 diff 元数据。
- 主要字段/属性：
  - `old_path: str = ''`
  - `new_path: str = ''`
  - `is_rename: bool = False`
  - `is_binary: bool = False`
  - `hunks: list[tuple[int, int]] = field(default_factory=list)`

### `_clean_diff_path`（函数，第 75 行）
- 签名：`def _clean_diff_path(path: str) -> str`
- 作用：去掉 unified diff 中常见的 a/、b/ 前缀和 /dev/null。

### `parse_diff_files`（函数，第 86 行）
- 签名：`def parse_diff_files(diff: str) -> list[DiffFileMeta]`
- 作用：解析 unified diff 的文件级元数据。

### `parse_changed_files`（函数，第 131 行）
- 签名：`def parse_changed_files(diff: str) -> list[str]`
- 作用：从 unified diff 中提取变更文件路径。

### `parse_diff_context_notes`（函数，第 138 行）
- 签名：`def parse_diff_context_notes(diff: str) -> list[str]`
- 作用：把 diff 元数据渲染成 planner/reviewer 可读的客观上下文。

### `_extract_symbol_names_from_text`（函数，第 160 行）
- 签名：`def _extract_symbol_names_from_text(text: str) -> list[str]`
- 作用：Extract review-relevant Python/JS/Vue symbol names from one diff line.

### `_looks_like_generated_diff_path`（函数，第 184 行）
- 签名：`def _looks_like_generated_diff_path(path: str) -> bool`
- 作用：Detect generated/dependency paths before mining symbols from diff text.

### `parse_changed_symbols`（函数，第 191 行）
- 签名：`def parse_changed_symbols(diff: str) -> list[str]`
- 作用：从新增行中粗略提取 def/class 符号名。

### `_default_target_file`（函数，第 223 行）
- 签名：`def _default_target_file(diff: str) -> str`
- 作用：没有明确上下文时，取 diff 里的第一个变更文件作为默认目标。

### `_module_path_from_file`（函数，第 230 行）
- 签名：`def _module_path_from_file(file_path: str) -> str`
- 作用：把文件路径转换成 Python import module 路径。

### `_infer_risk_areas`（函数，第 241 行）
- 签名：`def _infer_risk_areas(diff: str) -> list[str]`
- 作用：给 mock planner 用的轻量风险识别规则。

### `planner_agent`（函数，第 257 行）
- 签名：`def planner_agent(diff: str, config: ProjectConfig) -> ReviewPlan`
- 作用：Planner：根据 diff 生成 ReviewPlan。

### `context_retriever_agent`（函数，第 286 行）
- 签名：`def context_retriever_agent(*, diff: str, project_root: str | Path, review_plan: ReviewPlan, config: ProjectConfig) -> CodeContext`
- 作用：Context Retriever：在本地项目中收集客观代码上下文。

### `_mock_planner`（函数，第 308 行）
- 签名：`def _mock_planner(diff: str) -> ReviewPlan`
- 作用：不调用模型的 planner，用于单测和本地快速自测。

### `reviewer_agent`（函数，第 335 行）
- 签名：`def reviewer_agent(*, reviewer: ModelSettings, diff: str, review_plan: ReviewPlan, config: ProjectConfig, code_context: CodeContext | None=None) -> ReviewerResult`
- 作用：单个独立 Reviewer。

### `_normalize_reviewer_result`（函数，第 400 行）
- 签名：`def _normalize_reviewer_result(result: ReviewerResult, reviewer: ModelSettings) -> ReviewerResult`
- 作用：补齐/覆盖 reviewer 元信息，避免模型输出的来源字段不一致。

### `_mock_reviewer`（函数，第 414 行）
- 签名：`def _mock_reviewer(reviewer: ModelSettings, diff: str, code_context: CodeContext | None=None) -> ReviewerResult`
- 作用：确定性 reviewer。

### `test_strategy_agent`（函数，第 534 行）
- 签名：`def test_strategy_agent(*, round_index: int, targets: list[Finding], board_summary: BoardSummary, config: ProjectConfig) -> TestStrategy`
- 作用：Test Strategy Agent：为当前 verification targets 生成测试策略。

### `_mock_test_strategy`（函数，第 571 行）
- 签名：`def _mock_test_strategy(round_index: int, targets: list[Finding]) -> TestStrategy`
- 作用：确定性测试策略：为每个 target 生成最小可复现测试。

### `test_case_agent`（函数，第 586 行）
- 签名：`def test_case_agent(*, round_index: int, strategy: TestStrategy, targets: list[Finding], test_intents: list[TestIntent], config: ProjectConfig) -> TestCaseBatch`
- 作用：Test Case Agent：把 TestIntent/Finding 转成具体 TestCase。

### `test_author_agent`（函数，第 635 行）
- 签名：`def test_author_agent(*, round_index: int, targets: list[Finding], test_intents: list[TestIntent], draft_test_cases: list[TestCase], config: ProjectConfig) -> TestCaseBatch`
- 作用：Test Author Agent：审校并补强 Test Case Agent 的草稿。

### `_normalize_test_batch`（函数，第 691 行）
- 签名：`def _normalize_test_batch(batch: TestCaseBatch, round_index: int) -> TestCaseBatch`
- 作用：修正模型返回的 test case 元数据，确保 round/timeout 合法。

### `_infer_target_language`（函数，第 750 行）
- 签名：`def _infer_target_language(target_file: str | None) -> str | None`
- 作用：根据 target_file 后缀推断 target_language。

### `_mock_test_cases`（函数，第 765 行）
- 签名：`def _mock_test_cases(round_index: int, targets: list[Finding], test_intents: list[TestIntent]) -> TestCaseBatch`
- 作用：确定性测试生成器。

### `_mock_test_author_cases`（函数，第 836 行）
- 签名：`def _mock_test_author_cases(round_index: int, targets: list[Finding], test_intents: list[TestIntent], draft_test_cases: list[TestCase]) -> TestCaseBatch`
- 作用：Deterministic Test Author used by tests and local demos.

### `test_result_analyzer_agent`（函数，第 906 行）
- 签名：`def test_result_analyzer_agent(*, round_index: int, results: list[TestResult], config: ProjectConfig) -> TestResultAnalysis`
- 作用：Test Result Analyzer：解释 executor 结果并给 Board 状态建议。

### `coverage_gap_agent`（函数，第 947 行）
- 签名：`def coverage_gap_agent(*, board_summary: BoardSummary, config: ProjectConfig) -> list[str]`
- 作用：Coverage Gap Agent：汇总仍未覆盖或未收敛的验证缺口。

### `targeted_reviewer_agent`（函数，第 960 行）
- 签名：`def targeted_reviewer_agent(*, round_index: int, targets: list[Finding], results: list[TestResult], config: ProjectConfig) -> TargetedReview | None`
- 作用：Targeted Reviewer：只处理证据不足或执行不清楚的目标。

### `_normalize_self_reference_path`（函数，第 1042 行）
- 签名：`def _normalize_self_reference_path(path: str) -> str`
- 作用：把外部输入或模型输出规范化为系统内部格式。

### `is_self_referential_review`（函数，第 1049 行）
- 签名：`def is_self_referential_review(review_plan: ReviewPlan | None) -> bool`
- 作用：判断本次 PR 是否仅修改 CodeCross 自身的集成 / 运行载体代码。

### `evaluator_agent`（函数，第 1072 行）
- 签名：`def evaluator_agent(*, reviewer_results: list[ReviewerResult], findings: list[Finding], board_summary: BoardSummary, config: ProjectConfig, review_plan: ReviewPlan | None=None) -> EvaluatorReport`
- 作用：Rule-first Evaluator。

### `high_risk_review_agent`（函数，第 1185 行）
- 签名：`def high_risk_review_agent(*, evaluator_report: EvaluatorReport, findings: list[Finding], board_summary: BoardSummary, config: ProjectConfig) -> HighRiskReviewReport | None`
- 作用：High Risk Review：high/critical 时做二次确认和修复建议。

### `_join_or_none`（函数，第 1237 行）
- 签名：`def _join_or_none(items: list[str], *, limit: int | None=None) -> str`
- 作用：把列表渲染成英文报告中的逗号分隔文本，空列表显示 `none`。

### `_short_text`（函数，第 1247 行）
- 签名：`def _short_text(text: str, fallback: str='not provided') -> str`
- 作用：压缩空白并提供英文 fallback，避免最终报告出现空字段。

### `_line_location`（函数，第 1252 行）
- 签名：`def _line_location(finding: Finding) -> str`
- 作用：把 finding 的文件和行号格式化成 `path:start-end`。

### `_merge_advice`（函数，第 1262 行）
- 签名：`def _merge_advice(risk_level: str) -> str`
- 作用：根据风险等级生成英文合并建议。

### `_finding_sort_key`（函数，第 1272 行）
- 签名：`def _finding_sort_key(finding: Finding) -> tuple[int, str, int]`
- 作用：按严重度、文件和行号排序重点 finding。

### `_dedupe_key`（函数，第 1281 行）
- 签名：`def _dedupe_key(finding: Finding) -> tuple[str, str, int | None, int | None, str]`
- 作用：用严重度、位置和类别合并相近 finding。

### `_render_key_findings`（函数，第 1291 行）
- 签名：`def _render_key_findings(findings: list[Finding], board_summary: BoardSummary, *, limit: int=10) -> str`
- 作用：渲染英文最终报告中的 Key Findings，并过滤已 refuted 的问题。

### `render_final_report`（函数，第 1324 行）
- 签名：`def render_final_report(*, review_plan: ReviewPlan, reviewer_results: list[ReviewerResult], board_summary: BoardSummary, evaluator_report: EvaluatorReport, high_risk_review: HighRiskReviewReport | None, code_context: CodeContext | None=None) -> str`
- 作用：生成 canonical English Markdown 最终报告；中文翻译不在这里做，而是交给后续 `report_localizer_agent`。

### `report_localizer_agent`（函数，第 1435 行）
- 签名：`def report_localizer_agent(*, final_report: str, config: ProjectConfig) -> str`
- 作用：调用 `report_localizer` 模型角色，把英文最终报告翻译成简体中文 Markdown。默认使用 DeepSeek，可通过配置切换到其他 provider/model。

### `combine_bilingual_report`（函数，第 1483 行）
- 签名：`def combine_bilingual_report(*, english_report: str, chinese_report: str) -> str`
- 作用：把英文报告和中文译文合并成对外导出的双语最终报告。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
