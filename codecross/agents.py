from __future__ import annotations

"""CodeCross 的 Agent 层。

这个文件只负责“让模型思考并生成结构化输出”，不直接执行 pytest，
也不直接修改 Board 状态。为了自测稳定，V1 同时提供 mock_llm 路径：
打开 --mock-llm 时不会调用真实模型，但仍然走完整 workflow。
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field

from pydantic import BaseModel

from .a2a import call_a2a_reviewer
from .board import render_board_markdown
from .config import ModelSettings, ProjectConfig
from .context import retrieve_code_context
from .llm import ModelCallError, call_model_json, json_schema_instruction
from .reporting import (
    build_default_report_presentation,
    category_text,
    confidence_text,
    merge_advice,
    needs_chinese_fallback,
    polish_report_terms,
    risk_text,
)
from .schemas import (
    BoardSummary,
    CodeContext,
    EvaluatorReport,
    Finding,
    HighRiskReviewReport,
    LineRange,
    ReviewPlan,
    ReviewerResult,
    TargetedReview,
    TestCase,
    TestCaseBatch,
    TestIntent,
    TestResult,
    TestResultAnalysis,
    TestStrategy,
    new_id,
)


def _json(data: object) -> str:
    """把对象转成缩进 JSON，主要用于 prompt 拼装。"""

    if isinstance(data, BaseModel):
        return data.model_dump_json(indent=2)
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


@dataclass(slots=True)
class DiffFileMeta:
    """轻量 diff 元数据。

    V1.1 先不引入 unidiff 依赖，而是把 reviewer 最需要的客观信息抽出来：
    文件路径、rename/binary 标记、hunk 新行号范围。后续如果接 unidiff，可以保持
    parse_changed_files/parse_diff_context_notes 的外部接口不变。
    """

    old_path: str = ""
    new_path: str = ""
    is_rename: bool = False
    is_binary: bool = False
    hunks: list[tuple[int, int]] = field(default_factory=list)


def _clean_diff_path(path: str) -> str:
    """去掉 unified diff 中常见的 a/、b/ 前缀和 /dev/null。"""

    cleaned = path.strip()
    if cleaned in {"/dev/null", "dev/null"}:
        return ""
    if cleaned.startswith(("a/", "b/")):
        cleaned = cleaned[2:]
    return cleaned


def parse_diff_files(diff: str) -> list[DiffFileMeta]:
    """解析 unified diff 的文件级元数据。

    这个 parser 故意保持“够用但不复杂”：它覆盖普通 diff、rename、binary 和 hunk
    新行号，避免 V1.1 为了完整 patch 语义引入较大改动。
    """

    files: list[DiffFileMeta] = []
    current: DiffFileMeta | None = None

    for line in diff.splitlines():
        match = re.match(r"diff --git a/(.*?) b/(.*)", line)
        if match:
            current = DiffFileMeta(old_path=match.group(1).strip(), new_path=match.group(2).strip())
            files.append(current)
            continue
        if current is None:
            continue

        if line.startswith("rename from "):
            current.old_path = _clean_diff_path(line.removeprefix("rename from "))
            current.is_rename = True
        elif line.startswith("rename to "):
            current.new_path = _clean_diff_path(line.removeprefix("rename to "))
            current.is_rename = True
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            current.is_binary = True
        elif line.startswith("+++ "):
            candidate = _clean_diff_path(line.removeprefix("+++ "))
            if candidate:
                current.new_path = candidate
        elif line.startswith("--- "):
            candidate = _clean_diff_path(line.removeprefix("--- "))
            if candidate:
                current.old_path = candidate
        else:
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if hunk:
                start = int(hunk.group(1))
                length = int(hunk.group(2) or "1")
                current.hunks.append((start, start + max(length, 1) - 1))

    return files


def parse_changed_files(diff: str) -> list[str]:
    """从 unified diff 中提取变更文件路径。"""

    paths = [meta.new_path or meta.old_path for meta in parse_diff_files(diff)]
    return sorted(dict.fromkeys(path for path in paths if path))


def parse_diff_context_notes(diff: str) -> list[str]:
    """把 diff 元数据渲染成 planner/reviewer 可读的客观上下文。

    注意这里不做风险判断，只写“文件发生了什么”。这样不会像 risk_areas 那样提前
    给 reviewer 定调，适合作为 V1.1 的轻量上下文增强。
    """

    notes: list[str] = []
    for meta in parse_diff_files(diff):
        path = meta.new_path or meta.old_path or "unknown"
        flags = []
        if meta.is_rename:
            flags.append(f"rename:{meta.old_path}->{meta.new_path}")
        if meta.is_binary:
            flags.append("binary")
        hunk_text = ", ".join(f"{start}-{end}" for start, end in meta.hunks) or "unknown"
        suffix = f" ({'; '.join(flags)})" if flags else ""
        notes.append(f"{path}: changed new-file lines {hunk_text}{suffix}")
    return notes



def _extract_symbol_names_from_text(text: str) -> list[str]:
    """Extract review-relevant Python/JS/Vue symbol names from one diff line.

    This parser is intentionally shallow. It catches declarations that define a
    callable or class-level review target, while ignoring ordinary local
    constants such as ``const validModes = new Set(...)``.
    """

    patterns = [
        r"\b(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
        r"\b(?:export\s+)?class\s+([A-Za-z_$][\w$]*)\b",
        (
            r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
            r"(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
        ),
    ]
    names: list[str] = []
    for pattern in patterns:
        names.extend(match.group(1) for match in re.finditer(pattern, text))
    return names


def _looks_like_generated_diff_path(path: str) -> bool:
    """Detect generated/dependency paths before mining symbols from diff text."""

    parts = path.replace("\\", "/").split("/")
    return any(part in {"dist", "node_modules", ".venv", "__pycache__"} for part in parts)


def parse_changed_symbols(diff: str) -> list[str]:
    """从新增行中粗略提取 def/class 符号名。"""

    symbols: list[str] = []
    current_path = ""
    for line in diff.splitlines():
        file_match = re.match(r"diff --git a/(.*?) b/(.*)", line)
        if file_match:
            current_path = file_match.group(2).strip()
            continue
        if line.startswith("+++ "):
            candidate = _clean_diff_path(line.removeprefix("+++ "))
            if candidate:
                current_path = candidate
            continue
        if current_path and _looks_like_generated_diff_path(current_path):
            continue

        # Git hunk headers often contain the surrounding function name:
        # ``@@ ... @@ export function parseRequest(...)``. That symbol is
        # usually more important than a single added line inside the function.
        if line.startswith("@@"):
            header_context = line.split("@@", 2)[-1]
            symbols.extend(_extract_symbol_names_from_text(header_context))
            continue

        if not line.startswith("+") or line.startswith("+++"):
            continue
        symbols.extend(_extract_symbol_names_from_text(line[1:]))
    return sorted(dict.fromkeys(symbols))


def _default_target_file(diff: str) -> str:
    """没有明确上下文时，取 diff 里的第一个变更文件作为默认目标。"""

    files = parse_changed_files(diff)
    return files[0] if files else "unknown.py"


def _module_path_from_file(file_path: str) -> str:
    """把文件路径转换成 Python import module 路径。"""

    path = file_path.replace("\\", "/")
    if path.startswith("b/") or path.startswith("a/"):
        path = path[2:]
    if path.endswith(".py"):
        path = path[:-3]
    return path.strip("/").replace("/", ".")


def _infer_risk_areas(diff: str) -> list[str]:
    """给 mock planner 用的轻量风险识别规则。"""

    lowered = diff.lower()
    areas: list[str] = []
    if "shell=true" in lowered or "subprocess" in lowered:
        areas.append("command injection")
    if "api_key" in lowered or "token" in lowered or "secret" in lowered:
        areas.append("secret handling")
    if "/ b" in lowered or "/b" in lowered or "divide" in lowered:
        areas.append("boundary conditions")
    if "except" in lowered or "raise" in lowered:
        areas.append("exception flow")
    return areas or ["logic correctness", "regression risk"]


def planner_agent(diff: str, config: ProjectConfig) -> ReviewPlan:
    """Planner：根据 diff 生成 ReviewPlan。"""

    if config.mock_llm:
        return _mock_planner(diff)

    settings = config.model_config["planner"]
    context_notes = parse_diff_context_notes(diff)
    try:
        return call_model_json(
            settings,
            [
                (
                    "system",
                    "You are the CodeCross Planner. Create a concise review and testing plan.",
                ),
                (
                    "user",
                    f"{json_schema_instruction(ReviewPlan)}\n\n"
                    f"Objective diff metadata:\n{_json(context_notes)}\n\n"
                    f"Commit diff:\n{diff}",
                ),
            ],
            ReviewPlan,
        )  # type: ignore[return-value]
    except ModelCallError:
        return _mock_planner(diff)


def context_retriever_agent(
    *,
    diff: str,
    project_root: str | Path,
    review_plan: ReviewPlan,
    config: ProjectConfig,
) -> CodeContext:
    """Context Retriever：在本地项目中收集客观代码上下文。

    它不调用模型，也不判断风险，只做“把 Planner 的 changed_files/changed_symbols
    映射成真实代码片段”。这保证 reviewer 看到更多上下文，同时不会提前被另一个
    agent 的判断影响。
    """

    return retrieve_code_context(
        diff=diff,
        project_root=project_root,
        review_plan=review_plan,
        config=config,
    )


def _mock_planner(diff: str) -> ReviewPlan:
    """不调用模型的 planner，用于单测和本地快速自测。"""

    changed_files = parse_changed_files(diff)
    changed_symbols = parse_changed_symbols(diff)
    risk_areas = _infer_risk_areas(diff)
    return ReviewPlan(
        changed_files=changed_files,
        changed_symbols=changed_symbols,
        risk_areas=risk_areas,
        review_focus=[
            "independent reviewer pass",
            "runtime behavior",
            "security and maintainability",
        ],
        testing_focus=[
            "high severity findings first",
            "edge cases and exception paths",
            "pytest evidence for reproducible issues",
        ],
        context_notes=[
            "V1 uses bounded diff context and evidence-driven verification.",
            *parse_diff_context_notes(diff),
        ],
    )


def reviewer_agent(
    *,
    reviewer: ModelSettings,
    diff: str,
    review_plan: ReviewPlan,
    config: ProjectConfig,
    code_context: CodeContext | None = None,
) -> ReviewerResult:
    """单个独立 Reviewer。

    workflow 会为每个 enabled reviewer 创建一个节点。每个 reviewer 只读取
    diff + review_plan，不读取其他 reviewer 的输出，保持第一轮独立性。
    """

    try:
        if reviewer.a2a_url:
            result = call_a2a_reviewer(
                reviewer=reviewer,
                diff=diff,
                review_plan=review_plan,
                code_context=code_context,
                mock_llm=config.mock_llm,
            )
            return _normalize_reviewer_result(result, reviewer)

        if config.mock_llm:
            return _mock_reviewer(reviewer, diff, code_context)

        result = call_model_json(
            reviewer,
            [
                (
                    "system",
                    "You are an independent CodeCross reviewer. "
                    "Do not assume other reviewers' conclusions. "
                    "Return findings and test intents only.",
                ),
                (
                    "user",
                    f"Reviewer name: {reviewer.display_name}\n"
                    f"Model: {reviewer.provider}/{reviewer.model}\n"
                    f"Focus: {reviewer.focus}\n\n"
                    f"Review plan:\n{review_plan.model_dump_json(indent=2)}\n\n"
                    f"Objective diff metadata:\n{_json(parse_diff_context_notes(diff))}\n\n"
                    f"Code context retrieved from local project:\n"
                    f"{code_context.model_dump_json(indent=2) if code_context else 'none'}\n\n"
                    f"Commit diff:\n{diff}\n\n"
                    f"{json_schema_instruction(ReviewerResult)}",
                ),
            ],
            ReviewerResult,
        )
        return _normalize_reviewer_result(result, reviewer)  # type: ignore[arg-type]
    except ModelCallError as exc:
        # reviewer 节点允许单个模型失败后继续整体 workflow。
        # 但这里只捕获 ModelCallError：如果是代码 bug、schema 字段写错或类型错误，
        # 应该让异常冒泡，避免把开发问题伪装成“模型不给力”。
        return ReviewerResult(
            reviewer_name=reviewer.display_name,
            model=f"{reviewer.provider}/{reviewer.model}",
            reviewer_notes="Reviewer failed; workflow continues with other reviewers.",
            errors=[f"{type(exc).__name__}: {exc}"],
        )


def _normalize_reviewer_result(result: ReviewerResult, reviewer: ModelSettings) -> ReviewerResult:
    """补齐/覆盖 reviewer 元信息，避免模型输出的来源字段不一致。"""

    result.reviewer_name = reviewer.display_name
    transport_prefix = "a2a/" if reviewer.a2a_url else ""
    result.model = f"{transport_prefix}{reviewer.provider}/{reviewer.model}"
    for finding in result.findings:
        finding.source_reviewer = reviewer.display_name
        finding.model = result.model
    for intent in result.test_intents:
        intent.source_reviewer = reviewer.display_name
    return result


def _mock_reviewer(
    reviewer: ModelSettings,
    diff: str,
    code_context: CodeContext | None = None,
) -> ReviewerResult:
    """确定性 reviewer。

    DeepSeek mock 偏逻辑/runtime；Anthropic mock 偏安全/维护性。
    这样不接模型也能演示“两个 reviewer 独立提出不同视角”的产品形态。
    """

    target_file = _default_target_file(diff)
    model = f"{reviewer.provider}/{reviewer.model}"
    lowered = diff.lower()
    findings: list[Finding] = []
    intents: list[TestIntent] = []

    if reviewer.provider == "deepseek":
        # 逻辑 reviewer 的 demo 重点：发现除零边界，并给 Test Runner 一个可执行意图。
        if "safe_divide" in diff or "divide" in diff:
            finding_id = new_id("finding")
            intent_id = new_id("intent")
            findings.append(
                Finding(
                    finding_id=finding_id,
                    source_reviewer=reviewer.display_name,
                    model=model,
                    category="runtime",
                    severity="high",
                    confidence="high",
                    file_path=target_file,
                    line_range=LineRange(start=1, end=3),
                    problem="Division helper does not handle a zero denominator.",
                    evidence="The diff adds a divide-like function that directly evaluates a / b.",
                    impact="Callers can receive ZeroDivisionError instead of a controlled safe result.",
                    suggestion="Define the zero-denominator contract and handle it explicitly.",
                    reproduction_hint="Call the function with denominator 0.",
                    related_test_intent_ids=[intent_id],
                )
            )
            intents.append(
                TestIntent(
                    test_intent_id=intent_id,
                    source_reviewer=reviewer.display_name,
                    target_file=target_file,
                    target_symbol="safe_divide" if "safe_divide" in diff else "divide",
                    scenario="zero denominator",
                    reason="The finding is reproducible with denominator 0.",
                    expected_behavior="The function should return None for zero denominator instead of raising.",
                    risk_category="runtime",
                    priority="high",
                    related_finding_ids=[finding_id],
                )
            )
    else:
        # 第二 reviewer 的 demo 重点：安全和维护性。这里故意和逻辑 reviewer 关注点错开。
        if "shell=true" in lowered or "subprocess" in lowered:
            finding_id = new_id("finding")
            findings.append(
                Finding(
                    finding_id=finding_id,
                    source_reviewer=reviewer.display_name,
                    model=model,
                    category="security",
                    severity="critical",
                    confidence="high",
                    file_path=target_file,
                    line_range=LineRange(start=1, end=20),
                    problem="User-controlled command execution may allow command injection.",
                    evidence="The diff includes subprocess execution with shell=True.",
                    impact="Attackers may run arbitrary shell commands in the host environment.",
                    suggestion="Avoid shell=True and pass validated argument lists to subprocess APIs.",
                    reproduction_hint="Pass shell metacharacters as user input.",
                )
            )
        elif "api_token" in lowered or "secret" in lowered:
            finding_id = new_id("finding")
            findings.append(
                Finding(
                    finding_id=finding_id,
                    source_reviewer=reviewer.display_name,
                    model=model,
                    category="security",
                    severity="high",
                    confidence="medium",
                    file_path=target_file,
                    problem="Potential secret material appears in source changes.",
                    evidence="The diff includes token-like variable names.",
                    impact="Secrets committed to source control can be leaked.",
                    suggestion="Move secrets to environment variables and rotate exposed values.",
                )
            )
        else:
            findings.append(
                Finding(
                    source_reviewer=reviewer.display_name,
                    model=model,
                    category="maintainability",
                    severity="low",
                    confidence="medium",
                    file_path=target_file,
                    problem="No clear security issue found in the bounded diff context.",
                    evidence="The reviewer did not find command execution, network, or secret changes.",
                    impact="Residual risk remains because V1 does not inspect the full repository.",
                    suggestion="Run the generated tests and keep manual review for public APIs.",
                )
            )

    return ReviewerResult(
        reviewer_name=reviewer.display_name,
        model=model,
        findings=findings,
        test_intents=intents,
        reviewer_notes=(
            f"Mock reviewer focus: {reviewer.focus}. "
            f"Context snippets: {len(code_context.snippets) if code_context else 0}."
        ),
    )


def test_strategy_agent(
    *,
    round_index: int,
    targets: list[Finding],
    board_summary: BoardSummary,
    config: ProjectConfig,
) -> TestStrategy:
    """Test Strategy Agent：为当前 verification targets 生成测试策略。"""

    if not targets:
        return TestStrategy(
            round_index=round_index,
            notes="No verification targets were selected.",
        )
    if config.mock_llm:
        return _mock_test_strategy(round_index, targets)

    settings = config.model_config["test_strategy_agent"]
    try:
        return call_model_json(
            settings,
            [
                ("system", "You are the CodeCross Test Strategy Agent."),
                (
                    "user",
                    f"Round: {round_index}\n"
                    f"Targets:\n{_json([target.model_dump() for target in targets])}\n\n"
                    f"Board summary:\n{board_summary.model_dump_json(indent=2)}\n\n"
                    f"{json_schema_instruction(TestStrategy)}",
                ),
            ],
            TestStrategy,
        )  # type: ignore[return-value]
    except ModelCallError:
        return _mock_test_strategy(round_index, targets)


def _mock_test_strategy(round_index: int, targets: list[Finding]) -> TestStrategy:
    """确定性测试策略：为每个 target 生成最小可复现测试。"""

    return TestStrategy(
        round_index=round_index,
        target_finding_ids=[target.finding_id for target in targets],
        focus=[target.problem for target in targets],
        coverage_goals=[
            "Generate the smallest pytest that can support or refute each target finding."
        ],
        stop_if=["target finding becomes supported, refuted, not_testable, or max rounds is reached"],
        notes="Deterministic V1 strategy.",
    )


def test_case_agent(
    *,
    round_index: int,
    strategy: TestStrategy,
    targets: list[Finding],
    test_intents: list[TestIntent],
    config: ProjectConfig,
) -> TestCaseBatch:
    """Test Case Agent：把 TestIntent/Finding 转成具体 TestCase。"""

    if not targets:
        return TestCaseBatch()
    if config.mock_llm:
        return _mock_test_cases(round_index, targets, test_intents)

    settings = config.model_config["test_case_agent"]
    try:
        batch = call_model_json(
            settings,
            [
                (
                    "system",
                    "You are the CodeCross Test Case Agent. Generate safe pytest tests only. "
                    "Do not use network, subprocess, filesystem mutation, eval, or exec. "
                    "Never emit placeholder tests, pass-only tests, or `assert True` tests. "
                    "If a target is JavaScript/TypeScript/Vue, do not import it as a Python module; "
                    "either write a concrete static pytest that reads the source file and asserts "
                    "specific text/structure, or put the finding in skipped_targets.",
                ),
                (
                    "user",
                    f"Round: {round_index}\n"
                    f"Strategy:\n{strategy.model_dump_json(indent=2)}\n\n"
                    f"Target findings:\n{_json([target.model_dump() for target in targets])}\n\n"
                    f"Test intents:\n{_json([intent.model_dump() for intent in test_intents])}\n\n"
                    f"{json_schema_instruction(TestCaseBatch)}",
                ),
            ],
            TestCaseBatch,
        )
        return _normalize_test_batch(batch, round_index)  # type: ignore[arg-type]
    except ModelCallError:
        # 生成测试失败时可以回退到 mock/minimal 用例；其它异常保留堆栈，
        # 因为测试生成器是证据链核心，吞掉代码 bug 会让后续 Board 判断失真。
        return _mock_test_cases(round_index, targets, test_intents)


def test_author_agent(
    *,
    round_index: int,
    targets: list[Finding],
    test_intents: list[TestIntent],
    draft_test_cases: list[TestCase],
    config: ProjectConfig,
) -> TestCaseBatch:
    """Test Author Agent：审校并补强 Test Case Agent 的草稿。

    V1.6 的关键点是把“生成一个能复现 finding 的测试”和“写一组更可靠的测试”
    分成两个角色。Test Author 不直接执行测试，只负责让最终测试包更像人工会提交的
    最小 pytest：有失败边界，也尽量有正常路径对照。
    """

    if not draft_test_cases:
        return TestCaseBatch()
    if config.mock_llm:
        return _mock_test_author_cases(round_index, targets, test_intents, draft_test_cases)

    settings = config.model_config.get("test_author_agent") or config.model_config["test_case_agent"]
    try:
        batch = call_model_json(
            settings,
            [
                (
                    "system",
                    "You are the CodeCross Test Author Agent. Review draft pytest cases, "
                    "keep them safe, add narrow control tests when useful, and never use "
                    "network, subprocess, filesystem mutation, eval, or exec. "
                    "Reject placeholder tests, pass-only tests, and `assert True` tests. "
                    "For JavaScript/TypeScript/Vue targets, do not convert file paths into Python imports; "
                    "use concrete static source assertions or return skipped_targets instead.",
                ),
                (
                    "user",
                    f"Round: {round_index}\n"
                    f"Target findings:\n{_json([target.model_dump() for target in targets])}\n\n"
                    f"Test intents:\n{_json([intent.model_dump() for intent in test_intents])}\n\n"
                    f"Draft test cases:\n{_json([case.model_dump() for case in draft_test_cases])}\n\n"
                    f"{json_schema_instruction(TestCaseBatch)}",
                ),
            ],
            TestCaseBatch,
        )
        return _normalize_test_batch(batch, round_index)  # type: ignore[arg-type]
    except ModelCallError:
        return _mock_test_author_cases(round_index, targets, test_intents, draft_test_cases)


def _normalize_test_batch(batch: TestCaseBatch, round_index: int) -> TestCaseBatch:
    """修正模型返回的 test case 元数据，确保 round/timeout 合法。"""

    for test_case in batch.test_cases:
        test_case.round_index = round_index
        if test_case.timeout_seconds <= 0:
            test_case.timeout_seconds = 15
    return batch


def _mock_test_cases(
    round_index: int,
    targets: list[Finding],
    test_intents: list[TestIntent],
) -> TestCaseBatch:
    """确定性测试生成器。

    当前只内置 divide/safe_divide 的零分母用例；其他 finding 会被标记为
    manual/review_required，交给 Board 作为 coverage gap 处理。
    """

    intents_by_finding: dict[str, list[TestIntent]] = {}
    for intent in test_intents:
        for finding_id in intent.related_finding_ids:
            intents_by_finding.setdefault(finding_id, []).append(intent)

    test_cases: list[TestCase] = []
    skipped: dict[str, str] = {}

    for target in targets:
        intents = intents_by_finding.get(target.finding_id, [])
        intent = intents[0] if intents else None
        symbol = (intent.target_symbol if intent else target.reproduction_hint).strip()
        if symbol not in {"safe_divide", "divide"}:
            # V1 的 mock 生成器能力有限：不能确定安全生成的测试就转成人工复核缺口。
            skipped[target.finding_id] = "No deterministic pytest generator for this finding."
            test_cases.append(
                TestCase(
                    source_agent="mock_test_case_agent",
                    related_finding_ids=[target.finding_id],
                    round_index=round_index,
                    test_type="manual",
                    target_file=target.file_path,
                    target_symbol=symbol,
                    test_name="manual_review_required",
                    test_code="def test_manual_review_required():\n    assert True\n",
                    expected_behavior="Manual review required.",
                    assertions=[],
                    safety_level="review_required",
                )
            )
            continue

        module_path = _module_path_from_file(target.file_path)
        test_name = f"test_{symbol}_zero_denominator_returns_none"
        test_code = (
            f"from {module_path} import {symbol}\n\n\n"
            f"def {test_name}():\n"
            f"    assert {symbol}(10, 0) is None\n"
        )
        test_cases.append(
            TestCase(
                source_agent="mock_test_case_agent",
                source_test_intent_ids=[intent.test_intent_id] if intent else [],
                related_finding_ids=[target.finding_id],
                round_index=round_index,
                test_type="pytest",
                target_file=target.file_path,
                target_symbol=symbol,
                test_name=test_name,
                test_code=test_code,
                expected_behavior="Zero denominator should be handled without raising.",
                assertions=[f"{symbol}(10, 0) is None"],
                timeout_seconds=10,
                safety_level="safe",
            )
        )

    return TestCaseBatch(test_cases=test_cases, skipped_targets=skipped)


def _mock_test_author_cases(
    round_index: int,
    targets: list[Finding],
    test_intents: list[TestIntent],
    draft_test_cases: list[TestCase],
) -> TestCaseBatch:
    """Deterministic Test Author used by tests and local demos.

    It keeps every draft case and adds a normal-path control for divide-like
    zero-denominator tests. The control test is valuable interview material too:
    it shows why evidence should isolate a boundary bug instead of proving only
    that "something about this function is broken".
    """

    authored_cases = list(draft_test_cases)
    seen_names = {case.test_name for case in authored_cases}
    intents_by_finding: dict[str, list[TestIntent]] = {}
    for intent in test_intents:
        for finding_id in intent.related_finding_ids:
            intents_by_finding.setdefault(finding_id, []).append(intent)

    for draft in draft_test_cases:
        symbol = draft.target_symbol.strip()
        if symbol not in {"safe_divide", "divide"}:
            continue
        if "zero_denominator" not in draft.test_name:
            continue

        test_name = f"test_{symbol}_normal_division_returns_quotient"
        if test_name in seen_names:
            continue

        # Prefer the draft target file because it already passed through Test
        # Case Agent normalization. Fall back to target metadata only if needed.
        target_file = draft.target_file or next(
            (target.file_path for target in targets if target.finding_id in draft.related_finding_ids),
            "",
        )
        module_path = _module_path_from_file(target_file)
        related_intent_ids = [
            intent.test_intent_id
            for finding_id in draft.related_finding_ids
            for intent in intents_by_finding.get(finding_id, [])
        ]
        authored_cases.append(
            TestCase(
                source_agent="mock_test_author_agent",
                source_test_intent_ids=related_intent_ids,
                related_finding_ids=draft.related_finding_ids,
                round_index=round_index,
                test_type="pytest",
                target_file=target_file,
                target_symbol=symbol,
                test_name=test_name,
                test_code=(
                    f"from {module_path} import {symbol}\n\n\n"
                    f"def {test_name}():\n"
                    f"    assert {symbol}(10, 2) == 5\n"
                ),
                expected_behavior="Normal denominator should keep returning the quotient.",
                assertions=[f"{symbol}(10, 2) == 5"],
                timeout_seconds=min(draft.timeout_seconds or 10, 10),
                safety_level="safe",
            )
        )
        seen_names.add(test_name)

    return TestCaseBatch(test_cases=authored_cases)


def test_result_analyzer_agent(
    *,
    round_index: int,
    results: list[TestResult],
    config: ProjectConfig,
) -> TestResultAnalysis:
    """Test Result Analyzer：解释 executor 结果并给 Board 状态建议。

    V1 这里先规则化，避免让 LLM 决定测试是否真的通过。
    """

    suggested: dict[str, str] = {}
    gaps: list[str] = []
    need_targeted = False

    for result in results:
        for finding_id in result.related_finding_ids:
            if result.status == "failed" and result.is_reproducible:
                suggested[finding_id] = "supported"
            elif result.status == "failed":
                # 一次性失败或非可复现失败只说明“测试有信号”，不说明 finding 成立。
                # Board 会把这种情况保留为 inconclusive，等待更窄的测试或人工复核。
                suggested.setdefault(finding_id, "inconclusive")
                gaps.append(f"{finding_id}: failed test was not reproducible")
                need_targeted = True
            elif result.status == "passed":
                suggested[finding_id] = "refuted"
            elif result.status in {"error", "timeout", "unsafe", "skipped"}:
                suggested.setdefault(finding_id, "inconclusive")
                gaps.append(f"{finding_id}: executor status {result.status}")
                need_targeted = True

    return TestResultAnalysis(
        round_index=round_index,
        result_notes=[result.failure_summary for result in results if result.failure_summary],
        suggested_status_updates=suggested,  # type: ignore[arg-type]
        coverage_gaps=gaps,
        need_targeted_review=need_targeted,
    )


def coverage_gap_agent(
    *,
    board_summary: BoardSummary,
    config: ProjectConfig,
) -> list[str]:
    """Coverage Gap Agent：汇总仍未覆盖或未收敛的验证缺口。"""

    gaps = list(board_summary.coverage_gaps)
    for finding_id in board_summary.verification_queue:
        gaps.append(f"{finding_id}: still requires verification evidence")
    return sorted(dict.fromkeys(gaps))


def targeted_reviewer_agent(
    *,
    round_index: int,
    targets: list[Finding],
    results: list[TestResult],
    config: ProjectConfig,
) -> TargetedReview | None:
    """Targeted Reviewer：只处理证据不足或执行不清楚的目标。"""

    unresolved = [
        target
        for target in targets
        if not any(result.status == "failed" for result in results if target.finding_id in result.related_finding_ids)
    ]
    if not unresolved:
        return None

    settings = config.model_config["targeted_reviewer"]
    if config.mock_llm:
        return TargetedReview(
            round_index=round_index,
            target_finding_ids=[target.finding_id for target in unresolved],
            input_test_result_ids=[result.test_result_id for result in results],
            reviewer_name=settings.display_name,
            model=f"{settings.provider}/{settings.model}",
            analysis="Automated evidence was inconclusive; keep the finding open for manual review.",
            suggested_status_updates={
                target.finding_id: "inconclusive" for target in unresolved
            },
        )

    try:
        return call_model_json(
            settings,
            [
                (
                    "system",
                    "You are CodeCross Targeted Reviewer. Only inspect the listed findings and test evidence.",
                ),
                (
                    "user",
                    f"Round: {round_index}\n"
                    f"Targets:\n{_json([target.model_dump() for target in unresolved])}\n\n"
                    f"Test results:\n{_json([result.model_dump() for result in results])}\n\n"
                    f"{json_schema_instruction(TargetedReview)}",
                ),
            ],
            TargetedReview,
        )  # type: ignore[return-value]
    except ModelCallError:
        return TargetedReview(
            round_index=round_index,
            target_finding_ids=[target.finding_id for target in unresolved],
            input_test_result_ids=[result.test_result_id for result in results],
            reviewer_name=settings.display_name,
            model=f"{settings.provider}/{settings.model}",
            analysis="Targeted reviewer failed; retain inconclusive verification status.",
            suggested_status_updates={target.finding_id: "inconclusive" for target in unresolved},
        )


def evaluator_agent(
    *,
    reviewer_results: list[ReviewerResult],
    findings: list[Finding],
    board_summary: BoardSummary,
    config: ProjectConfig,
) -> EvaluatorReport:
    """Rule-first Evaluator。

    风险等级先由 Board 状态和 evidence 决定，确保多次运行结果稳定。
    """

    verifications = board_summary.finding_verifications
    finding_map = {finding.finding_id: finding for finding in findings}
    supported = [
        finding_map[finding_id]
        for finding_id, verification in verifications.items()
        if verification.status == "supported" and finding_id in finding_map
    ]
    unresolved_high = [
        finding_map[finding_id]
        for finding_id, verification in verifications.items()
        if verification.status in {"unverified", "inconclusive"}
        and finding_id in finding_map
        and finding_map[finding_id].severity in {"high", "critical"}
    ]
    reviewer_failures = sum(1 for result in reviewer_results if result.errors)

    # 下面是设计文档里的 rule-first 风险裁决：测试证据优先于模型话术。
    if reviewer_failures > len(reviewer_results) / 2:
        risk_level = "unknown"
        reasoning = "超过半数 reviewer 执行失败。"
    elif any(finding.severity == "critical" for finding in supported):
        risk_level = "critical"
        reasoning = "严重 finding 已被测试证据支持。"
    elif any(finding.severity == "high" for finding in supported):
        risk_level = "high"
        reasoning = "高风险 finding 已被测试证据支持。"
    elif any(finding.severity == "critical" for finding in unresolved_high):
        risk_level = "high"
        reasoning = "严重 finding 在证据验证后仍未解决。"
    elif unresolved_high:
        risk_level = "medium"
        reasoning = "仍有高风险 finding 处于证据不足状态。"
    elif any(finding.severity == "medium" for finding in supported):
        risk_level = "medium"
        reasoning = "中风险 finding 已被证据支持。"
    elif board_summary.error_count:
        risk_level = "medium"
        reasoning = "验证执行器报告了错误或被阻断的测试。"
    else:
        risk_level = "low"
        reasoning = "没有剩余已被证据支持的中高风险 finding。"

    top_findings = [
        finding.finding_id
        for finding in sorted(supported + unresolved_high, key=lambda item: item.severity, reverse=True)
    ][:5]
    return EvaluatorReport(
        risk_level=risk_level,  # type: ignore[arg-type]
        confidence="high" if risk_level in {"low", "high", "critical"} else "medium",
        top_findings=top_findings,
        evidence_summary=f"失败测试 {board_summary.failed_count} 个，通过测试 {board_summary.passed_count} 个。",
        failed_tests_summary="\n".join(result.failure_summary for result in board_summary.failed_tests),
        coverage_gaps=board_summary.coverage_gaps,
        verification_summary=render_board_markdown(board_summary),
        recommended_action=(
            "阻断合并，或要求修复后再合并。"
            if risk_level in {"high", "critical"}
            else "先人工确认未解决事项，再按项目策略决定是否合并。"
        ),
        needs_high_risk_review=risk_level in {"high", "critical"},
        reasoning=reasoning,
    )


def high_risk_review_agent(
    *,
    evaluator_report: EvaluatorReport,
    findings: list[Finding],
    board_summary: BoardSummary,
    config: ProjectConfig,
) -> HighRiskReviewReport | None:
    """High Risk Review：high/critical 时做二次确认和修复建议。"""

    if not evaluator_report.needs_high_risk_review:
        return None

    settings = config.model_config["high_risk_reviewer"]
    blocking = evaluator_report.top_findings
    if config.mock_llm:
        return HighRiskReviewReport(
            analysis="高风险二次确认认为：已有证据支持的 finding 应阻断本次变更。",
            recommended_action="先修复阻断级 finding，然后重新运行 CodeCross。",
            blocking_findings=blocking,
        )

    try:
        return call_model_json(
            settings,
            [
                (
                    "system",
                    "You are CodeCross High Risk Reviewer. Confirm blocking risk and give concise remediation.",
                ),
                (
                    "user",
                    f"Evaluator report:\n{evaluator_report.model_dump_json(indent=2)}\n\n"
                    f"Findings:\n{_json([finding.model_dump() for finding in findings])}\n\n"
                    f"Board:\n{board_summary.model_dump_json(indent=2)}\n\n"
                    f"{json_schema_instruction(HighRiskReviewReport)}",
                ),
            ],
            HighRiskReviewReport,
        )  # type: ignore[return-value]
    except ModelCallError:
        return HighRiskReviewReport(
            analysis="高风险 reviewer 调用失败；沿用规则优先的 Evaluator 结果。",
            recommended_action=evaluator_report.recommended_action,
            blocking_findings=blocking,
        )


_FINAL_REPORT_TERM_LABELS = {
    "boundary conditions": "边界条件",
    "command injection": "命令注入",
    "edge cases and exception paths": "覆盖边界条件和异常路径",
    "exception flow": "异常流程",
    "high severity findings first": "优先验证高严重度问题",
    "logic correctness": "逻辑正确性",
    "pytest evidence for reproducible issues": "用 pytest 固化可复现证据",
    "regression risk": "回归风险",
    "secret handling": "密钥处理",
}


def _localized_report_text(text: str, fallback: str) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return fallback

    mapped = _FINAL_REPORT_TERM_LABELS.get(cleaned.lower())
    if mapped:
        return mapped
    if needs_chinese_fallback(cleaned):
        return fallback
    return polish_report_terms(cleaned)


def _localized_report_list(items: list[str], *, empty: str = "无") -> str:
    visible: list[str] = []
    hidden_count = 0
    for item in items:
        cleaned = " ".join((item or "").split())
        if not cleaned:
            continue

        mapped = _FINAL_REPORT_TERM_LABELS.get(cleaned.lower())
        if not mapped:
            mapped = category_text(cleaned)
            if mapped == "代码" and needs_chinese_fallback(cleaned):
                hidden_count += 1
                continue
            if mapped == cleaned:
                mapped = polish_report_terms(cleaned)
        if mapped not in visible:
            visible.append(mapped)

    if hidden_count:
        visible.append(f"{hidden_count} 项原始计划已保留在结构化 JSON 产物中")
    return ", ".join(visible) if visible else empty


def _display_changed_symbols(items: list[str]) -> str:
    cleaned_items = [" ".join((item or "").split()) for item in items]
    cleaned_items = [item for item in cleaned_items if item]
    if not cleaned_items:
        return "无"

    visible: list[str] = []
    prose_count = 0
    for item in cleaned_items:
        if " " in item and needs_chinese_fallback(item):
            prose_count += 1
            continue
        visible.append(polish_report_terms(item))

    if prose_count:
        visible.append(f"{prose_count} 条变更说明已保留在结构化 JSON 产物中")
    return ", ".join(visible) if visible else f"{len(cleaned_items)} 条变更说明已保留在结构化 JSON 产物中"


def render_final_report(
    *,
    review_plan: ReviewPlan,
    reviewer_results: list[ReviewerResult],
    board_summary: BoardSummary,
    evaluator_report: EvaluatorReport,
    high_risk_review: HighRiskReviewReport | None,
    code_context: CodeContext | None = None,
) -> str:
    """把 workflow 的关键结果汇总成 Markdown 报告。"""

    # 最终报告比 PR 评论更完整，但仍然只放摘要：每个 reviewer 的完整 finding、
    # test_intent 和错误对象已经在结构化 state 中，前端或 JSON artifact 可以展开。
    reviewer_sections: list[str] = []
    for result in reviewer_results:
        reviewer_notes = _localized_report_text(
            result.reviewer_notes,
            "审查器原始备注已保留在结构化 JSON 产物中。",
        )
        reviewer_sections.append(
            "\n".join(
                [
                    f"### {result.reviewer_name}",
                    f"- 模型：{result.model}",
                    f"- 发现数：{len(result.findings)}",
                    f"- 测试意图：{len(result.test_intents)}",
                    f"- 错误数：{len(result.errors)}",
                    reviewer_notes,
                ]
            )
        )

    # 高风险二次确认是可选节点：只有 evaluator 认为 high/critical 时才会出现。
    # 因此这里先准备空字符串，避免低风险 PR 报告里出现空标题。
    high_risk_section = ""
    if high_risk_review:
        blocking = ", ".join(high_risk_review.blocking_findings) or "无"
        high_risk_analysis = _localized_report_text(
            high_risk_review.analysis,
            "高风险二次确认已完成；原始复核细节已保留在结构化 JSON 产物中，最终结论以本报告的结论摘要和重点问题为准。",
        )
        high_risk_action = _localized_report_text(
            high_risk_review.recommended_action,
            merge_advice(evaluator_report.risk_level),
        )
        high_risk_section = (
            "\n## 高风险二次确认\n\n"
            f"{high_risk_analysis}\n\n"
            f"建议动作：{high_risk_action}\n\n"
            f"阻断级问题：{blocking}\n"
        )

    context_section = ""
    if code_context:
        # 报告里只放摘要，不展开所有代码片段，避免 Markdown 过长。
        # 真正完整的 snippets 仍保留在 JSON state 里，适合前端抽屉或调试工具展示。
        context_section = (
            "\n## 代码上下文\n\n"
            f"- 变更文件：{', '.join(code_context.changed_files) or '无'}\n"
            f"- 变更符号：{_display_changed_symbols(code_context.changed_symbols)}\n"
            f"- 上下文片段数：{len(code_context.snippets)}\n"
            f"- 未能读取的文件：{', '.join(code_context.missing_files) or '无'}\n\n"
        )

    presentation = build_default_report_presentation(
        findings=[
            finding
            for result in reviewer_results
            for finding in result.findings
        ],
        board_summary=board_summary,
        evaluator_report=evaluator_report,
        high_risk_review=high_risk_review,
    )
    finding_lines = []
    for row in presentation.finding_rows[:10]:
        problem = row.problem
        if len(row.finding_ids) > 1:
            problem += f"（已合并 {len(row.finding_ids)} 条相近发现）"
        finding_lines.append(
            f"- [{row.verification_status or '未进入验证'}] "
            f"{row.location or '未知位置'}：{problem} 建议：{row.suggestion}"
        )
    presentation_section = (
        "\n## 结论摘要\n\n"
        f"{presentation.conclusion_summary}\n\n"
        f"建议动作：{presentation.recommended_action or '无'}\n\n"
        "## 重点问题\n\n"
        + ("\n".join(finding_lines) if finding_lines else "无剩余重点问题。")
        + "\n\n"
    )
    risk_reasoning = _localized_report_text(
        evaluator_report.reasoning,
        "风险判断来自已收集的测试结果、验证状态和审查器发现。",
    )
    evidence_summary = _localized_report_text(
        evaluator_report.evidence_summary,
        "完整证据摘要已保留在结构化 JSON 产物中；报告上方已汇总测试和验证状态。",
    )
    failed_tests_summary = _localized_report_text(
        evaluator_report.failed_tests_summary,
        "没有失败测试详情。",
    )

    return (
        "# CodeCross 最终审查报告\n\n"
        f"## 风险结论\n\n"
        f"- 风险等级：{risk_text(evaluator_report.risk_level)}\n"
        f"- 置信度：{confidence_text(evaluator_report.confidence)}\n"
        f"- 判断理由：{risk_reasoning}\n"
        f"- 建议动作：{presentation.recommended_action or merge_advice(evaluator_report.risk_level)}\n\n"
        "## 审查计划\n\n"
        f"- 变更文件：{', '.join(review_plan.changed_files) or '无'}\n"
        f"- 变更符号：{_display_changed_symbols(review_plan.changed_symbols)}\n"
        f"- 风险方向：{_localized_report_list(review_plan.risk_areas)}\n"
        f"- 测试重点：{_localized_report_list(review_plan.testing_focus)}\n\n"
        + context_section
        + presentation_section
        + "## Reviewer 输出\n\n"
        + "\n\n".join(reviewer_sections)
        + "\n\n"
        + render_board_markdown(board_summary)
        + "\n## 证据摘要\n\n"
        f"{evidence_summary}\n\n"
        f"{failed_tests_summary}\n"
        + high_risk_section
    )
