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
from .schemas import (
    BoardSummary,
    CodeContext,
    EvaluatorReport,
    Finding,
    HighRiskReviewReport,
    LineRange,
    LocalizedReport,
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
        return _normalize_test_batch(_mock_test_cases(round_index, targets, test_intents), round_index)

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
        return _normalize_test_batch(
            _mock_test_cases(round_index, targets, test_intents), round_index
        )


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
        return _normalize_test_batch(
            _mock_test_author_cases(round_index, targets, test_intents, draft_test_cases),
            round_index,
        )

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
        return _normalize_test_batch(
            _mock_test_author_cases(round_index, targets, test_intents, draft_test_cases),
            round_index,
        )


def _normalize_test_batch(batch: TestCaseBatch, round_index: int) -> TestCaseBatch:
    """修正模型返回的 test case 元数据，确保 round/timeout 合法。"""

    for test_case in batch.test_cases:
        test_case.round_index = round_index
        if test_case.timeout_seconds <= 0:
            test_case.timeout_seconds = 15
        # Issue #2 fallback：模型经常忘记填 target_language，导致下游
        # detect_cross_verification_trigger 永远命不中 unsupported_language。
        # 这里按 target_file 后缀确定性补齐，让 trigger 不依赖模型自觉。
        if not test_case.target_language:
            test_case.target_language = _infer_target_language(test_case.target_file)
    return batch


# 把“文件后缀 -> 语言”集中在一张表里，trigger 与 author 共用同一份认知。
# 表 key 是不带点的小写后缀；value 是 triggers.py 里使用的语言名称（小写）。
# 想加 go/rust 时只要在这里加一行；executor / trigger 不用改。
_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "vue": "vue",
    "go": "go",
    "rs": "rust",
    "java": "java",
    "kt": "kotlin",
    "rb": "ruby",
    "php": "php",
    "cs": "csharp",
    "swift": "swift",
    "scala": "scala",
    "c": "c",
    "h": "c",
    "cc": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "hpp": "cpp",
    "m": "objc",
    "mm": "objcpp",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "ps1": "powershell",
    "yml": "yaml",
    "yaml": "yaml",
    "json": "json",
    "toml": "toml",
    "html": "html",
    "css": "css",
    "scss": "scss",
}


def _infer_target_language(target_file: str | None) -> str | None:
    """根据 target_file 后缀推断 target_language。

    覆盖不到的情况返回 None：宁可让 trigger 走 pytest 默认分支，也不要乱填一个
    伪语言把 fallback 误触发。比如 target_file 是空、是目录、或者后缀完全陌生。
    """

    if not target_file:
        return None
    suffix = target_file.rsplit(".", 1)
    if len(suffix) != 2 or not suffix[1]:
        return None
    return _LANGUAGE_BY_SUFFIX.get(suffix[1].lower())


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


# Issue #3: 当 PR 仅修改 CodeCross 自身的集成 workflow / runner 代码时，
# reviewer 仍会客观挑出 fallback / shell 边界这类通用 CI 风险，evaluator
# 默认会判 high 进而触发高风险二次复核，导致外部"修-审-修-审"循环不收敛。
# 这里只在 evaluator 阶段做语义降级，不改变 reviewer 链路本身。
SELF_REFERENCE_PATH_PREFIXES: tuple[str, ...] = (
    ".github/workflows/codecross",
    ".codecross-runner/",
    ".codecross-runner",
)
SELF_REFERENCE_PATH_EXACT: frozenset[str] = frozenset(
    {".github/workflows/codecross-pr-review.yml"}
)
SELF_REFERENCE_NOTE = (
    "This PR only changes CodeCross integration entrypoints or runner files "
    "(for example .github/workflows/codecross-pr-review.yml or .codecross-runner/). "
    "To avoid a self-referential review loop, the evaluator downgraded blocking "
    "risk to medium and skipped the high-risk follow-up. Original findings remain "
    "available in the structured JSON artifact for human review."
)


def _normalize_self_reference_path(path: str) -> str:
    cleaned = (path or "").strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def is_self_referential_review(review_plan: ReviewPlan | None) -> bool:
    """判断本次 PR 是否仅修改 CodeCross 自身的集成 / 运行载体代码。

    判据：review_plan.changed_files 非空，且每一条都落在
    SELF_REFERENCE_PATH_PREFIXES 或 SELF_REFERENCE_PATH_EXACT 中。
    空 changed_files 不视为自指（避免 planner 失败时误降级）。
    """

    if review_plan is None:
        return False
    files = [_normalize_self_reference_path(item) for item in review_plan.changed_files]
    files = [item for item in files if item]
    if not files:
        return False
    for path in files:
        if path in SELF_REFERENCE_PATH_EXACT:
            continue
        if any(path.startswith(prefix) for prefix in SELF_REFERENCE_PATH_PREFIXES):
            continue
        return False
    return True


def evaluator_agent(
    *,
    reviewer_results: list[ReviewerResult],
    findings: list[Finding],
    board_summary: BoardSummary,
    config: ProjectConfig,
    review_plan: ReviewPlan | None = None,
) -> EvaluatorReport:
    """Rule-first Evaluator。

    风险等级先由 Board 状态和 evidence 决定，确保多次运行结果稳定。
    Issue #3：自指 PR（仅改 CodeCross 自身集成代码）会被强制降级到 medium，
    避免外部循环。
    """

    verifications = board_summary.finding_verifications
    finding_map = {finding.finding_id: finding for finding in findings}
    supported = [
        finding_map[finding_id]
        for finding_id, verification in verifications.items()
        if verification.status == "supported" and finding_id in finding_map
    ]
    # Issue #2：cross_supported / needs_human_review 是 LLM 交叉验证产出的状态，
    # 证据强度比真实 pytest 失败弱。这里单独列出来，避免 evaluator 直接当成
    # pytest-supported 给出 high/critical，但同时不能像 unverified 一样视而不见。
    cross_supported = [
        finding_map[finding_id]
        for finding_id, verification in verifications.items()
        if verification.status == "cross_supported" and finding_id in finding_map
    ]
    unresolved_high = [
        finding_map[finding_id]
        for finding_id, verification in verifications.items()
        if verification.status
        in {"unverified", "inconclusive", "cross_inconclusive", "needs_human_review"}
        and finding_id in finding_map
        and finding_map[finding_id].severity in {"high", "critical"}
    ]
    reviewer_failures = sum(1 for result in reviewer_results if result.errors)

    # 下面是设计文档里的 rule-first 风险裁决：测试证据优先于模型话术。
    if reviewer_failures > len(reviewer_results) / 2:
        risk_level = "unknown"
        reasoning = "More than half of the reviewers failed."
    elif any(finding.severity == "critical" for finding in supported):
        risk_level = "critical"
        reasoning = "A critical finding is supported by test evidence."
    elif any(finding.severity == "high" for finding in supported):
        risk_level = "high"
        reasoning = "A high-severity finding is supported by test evidence."
    elif any(finding.severity == "critical" for finding in unresolved_high):
        risk_level = "high"
        reasoning = "A critical finding remains unresolved after evidence verification."
    elif unresolved_high:
        risk_level = "medium"
        reasoning = "At least one high-severity finding still has insufficient evidence."
    elif any(finding.severity in {"high", "critical"} for finding in cross_supported):
        # Issue #2：cross 路径只能给到 medium，因为模型推理证据不能等同于真实测试。
        risk_level = "medium"
        reasoning = "A high-severity finding is only supported by LLM cross-verification; human review is recommended."
    elif any(finding.severity == "medium" for finding in supported):
        risk_level = "medium"
        reasoning = "A medium-severity finding is supported by evidence."
    elif any(finding.severity == "medium" for finding in cross_supported):
        risk_level = "medium"
        reasoning = "A medium-severity finding is supported by LLM cross-verification."
    elif board_summary.error_count:
        risk_level = "medium"
        reasoning = "The verification executor reported errors or blocked tests."
    else:
        risk_level = "low"
        reasoning = "No remaining medium-or-higher findings are supported by evidence."

    self_reference = is_self_referential_review(review_plan)
    if self_reference and risk_level in {"high", "critical"}:
        # 仅降级阻断级评分，medium/low/unknown 维持原判
        risk_level = "medium"
        reasoning = f"{SELF_REFERENCE_NOTE} Original reasoning: {reasoning}"
    elif self_reference:
        reasoning = f"{SELF_REFERENCE_NOTE} Original reasoning: {reasoning}"

    top_findings = [
        finding.finding_id
        for finding in sorted(
            supported + cross_supported + unresolved_high,
            key=lambda item: item.severity,
            reverse=True,
        )
    ][:5]
    needs_high_risk_review = risk_level in {"high", "critical"} and not self_reference
    if self_reference:
        recommended_action = (
            "This is a CodeCross self-integration review and was downgraded by "
            "the self-reference rule. Review the original findings in the JSON "
            "artifact before deciding whether to merge."
        )
    elif risk_level in {"high", "critical"}:
        recommended_action = "Block the merge until the supported findings are fixed."
    else:
        recommended_action = "Review any unresolved items, then merge according to project policy."
    return EvaluatorReport(
        risk_level=risk_level,  # type: ignore[arg-type]
        confidence="high" if risk_level in {"low", "high", "critical"} else "medium",
        top_findings=top_findings,
        evidence_summary=(
            f"{board_summary.failed_count} failed tests, "
            f"{board_summary.passed_count} passed tests."
        ),
        failed_tests_summary="\n".join(result.failure_summary for result in board_summary.failed_tests),
        coverage_gaps=board_summary.coverage_gaps,
        verification_summary=render_board_markdown(board_summary),
        recommended_action=recommended_action,
        needs_high_risk_review=needs_high_risk_review,
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
            analysis="High-risk follow-up confirms that evidence-supported findings should block this change.",
            recommended_action="Fix the blocking findings, then rerun CodeCross.",
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
            analysis="High-risk reviewer call failed; keeping the rule-first evaluator result.",
            recommended_action=evaluator_report.recommended_action,
            blocking_findings=blocking,
        )


RISK_LABELS = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "unknown": "unknown",
}


def _join_or_none(items: list[str], *, limit: int | None = None) -> str:
    cleaned = [" ".join((item or "").split()) for item in items]
    cleaned = [item for item in cleaned if item]
    if limit is not None and len(cleaned) > limit:
        visible = cleaned[:limit]
        visible.append(f"... (+{len(cleaned) - limit} more)")
        return ", ".join(visible)
    return ", ".join(cleaned) if cleaned else "none"


def _short_text(text: str, fallback: str = "not provided") -> str:
    cleaned = " ".join((text or "").split())
    return cleaned or fallback


def _line_location(finding: Finding) -> str:
    start = finding.line_range.start
    if start is None:
        return finding.file_path or "unknown location"
    end = finding.line_range.end
    if end is None or end == start:
        return f"{finding.file_path}:{start}"
    return f"{finding.file_path}:{start}-{end}"


def _merge_advice(risk_level: str) -> str:
    if risk_level in {"critical", "high"}:
        return "Block the merge until the supported findings are fixed."
    if risk_level == "medium":
        return "Review and fix or explicitly accept the remaining risk before merging."
    if risk_level == "low":
        return "Merge is acceptable after normal human review."
    return "Risk is unknown; inspect CI logs and the full workflow artifact before merging."


def _finding_sort_key(finding: Finding) -> tuple[int, str, int]:
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (
        severity_order.get(finding.severity, 9),
        finding.file_path,
        finding.line_range.start or 0,
    )


def _dedupe_key(finding: Finding) -> tuple[str, str, int | None, int | None, str]:
    return (
        finding.severity,
        finding.file_path,
        finding.line_range.start,
        finding.line_range.end,
        finding.category.lower(),
    )


def _render_key_findings(
    findings: list[Finding],
    board_summary: BoardSummary,
    *,
    limit: int = 10,
) -> str:
    lines: list[str] = []
    seen: dict[tuple[str, str, int | None, int | None, str], list[Finding]] = {}
    for finding in sorted(findings, key=_finding_sort_key):
        verification = board_summary.finding_verifications.get(finding.finding_id)
        if verification and verification.status in {"refuted", "cross_refuted"}:
            continue
        seen.setdefault(_dedupe_key(finding), []).append(finding)

    for grouped in list(seen.values())[:limit]:
        finding = grouped[0]
        verification = board_summary.finding_verifications.get(finding.finding_id)
        status = verification.status if verification else "unverified"
        duplicate_note = f" ({len(grouped)} similar findings merged)" if len(grouped) > 1 else ""
        lines.append(
            f"- [`{finding.severity}` / `{status}`] `{_line_location(finding)}`: "
            f"{_short_text(finding.problem)}{duplicate_note}\n"
            f"  - Impact: {_short_text(finding.impact)}\n"
            f"  - Recommendation: {_short_text(finding.suggestion)}"
        )

    if not lines:
        return "No remaining key findings."
    if len(seen) > limit:
        lines.append(f"- ... {len(seen) - limit} more findings are available in the JSON artifact.")
    return "\n".join(lines)


def render_final_report(
    *,
    review_plan: ReviewPlan,
    reviewer_results: list[ReviewerResult],
    board_summary: BoardSummary,
    evaluator_report: EvaluatorReport,
    high_risk_review: HighRiskReviewReport | None,
    code_context: CodeContext | None = None,
) -> str:
    """Render the workflow result as the canonical English Markdown report."""

    # Keep the source report in English. A dedicated Report Localizer Agent runs
    # after this node and produces the Chinese translation from this canonical
    # artifact, which prevents two independent localization systems from drifting.
    reviewer_sections: list[str] = []
    for result in reviewer_results:
        reviewer_notes = _short_text(
            result.reviewer_notes,
            "Reviewer notes are available in the structured JSON artifact.",
        )
        reviewer_sections.append(
            "\n".join(
                [
                    f"### {result.reviewer_name}",
                    f"- Model: {result.model}",
                    f"- Findings: {len(result.findings)}",
                    f"- Test intents: {len(result.test_intents)}",
                    f"- Errors: {len(result.errors)}",
                    reviewer_notes,
                ]
            )
        )

    high_risk_section = ""
    if high_risk_review:
        blocking = ", ".join(high_risk_review.blocking_findings) or "none"
        high_risk_analysis = _short_text(
            high_risk_review.analysis,
            "High-risk follow-up completed; see the structured JSON artifact for details.",
        )
        high_risk_action = _short_text(
            high_risk_review.recommended_action,
            _merge_advice(evaluator_report.risk_level),
        )
        high_risk_section = (
            "\n## High-Risk Follow-Up\n\n"
            f"{high_risk_analysis}\n\n"
            f"Recommended action: {high_risk_action}\n\n"
            f"Blocking findings: {blocking}\n"
        )

    context_section = ""
    if code_context:
        context_section = (
            "\n## Code Context\n\n"
            f"- Changed files: {_join_or_none(code_context.changed_files)}\n"
            f"- Changed symbols: {_join_or_none(code_context.changed_symbols)}\n"
            f"- Context snippets: {len(code_context.snippets)}\n"
            f"- Missing files: {_join_or_none(code_context.missing_files)}\n\n"
        )

    findings = [finding for result in reviewer_results for finding in result.findings]
    summary_section = (
        "\n## Executive Summary\n\n"
        f"Risk is **{RISK_LABELS.get(evaluator_report.risk_level, evaluator_report.risk_level)}** "
        f"with **{evaluator_report.confidence}** confidence. "
        f"{_short_text(evaluator_report.reasoning)}\n\n"
        f"Recommended action: {_short_text(evaluator_report.recommended_action, _merge_advice(evaluator_report.risk_level))}\n\n"
        + (
            f"> Self-reference note: {SELF_REFERENCE_NOTE}\n\n"
            if is_self_referential_review(review_plan)
            else ""
        )
        + "## Key Findings\n\n"
        + _render_key_findings(findings, board_summary)
        + "\n\n"
    )
    evidence_summary = _short_text(
        evaluator_report.evidence_summary,
        "Evidence details are available in the structured JSON artifact.",
    )
    failed_tests_summary = _short_text(
        evaluator_report.failed_tests_summary,
        "No failed-test details were reported.",
    )

    return (
        "# CodeCross Final Review Report\n\n"
        f"## Risk Conclusion\n\n"
        f"- Risk level: `{evaluator_report.risk_level}`\n"
        f"- Confidence: `{evaluator_report.confidence}`\n"
        f"- Reasoning: {_short_text(evaluator_report.reasoning)}\n"
        f"- Recommended action: {_short_text(evaluator_report.recommended_action, _merge_advice(evaluator_report.risk_level))}\n\n"
        "## Review Plan\n\n"
        f"- Changed files: {_join_or_none(review_plan.changed_files)}\n"
        f"- Changed symbols: {_join_or_none(review_plan.changed_symbols)}\n"
        f"- Risk areas: {_join_or_none(review_plan.risk_areas)}\n"
        f"- Testing focus: {_join_or_none(review_plan.testing_focus)}\n\n"
        + context_section
        + summary_section
        + "## Reviewer Outputs\n\n"
        + "\n\n".join(reviewer_sections)
        + "\n\n"
        + render_board_markdown(board_summary)
        + "\n## Evidence Summary\n\n"
        f"{evidence_summary}\n\n"
        f"{failed_tests_summary}\n"
        + high_risk_section
    )


def report_localizer_agent(*, final_report: str, config: ProjectConfig) -> str:
    """Translate the canonical English final report into Simplified Chinese."""

    if config.mock_llm:
        return (
            "# CodeCross 最终审查报告（中文）\n\n"
            "> Mock 模式未调用中文化模型；真实模式会由 Report Localizer Agent "
            "把英文最终报告完整翻译为中文。\n\n"
            + final_report
        )

    settings = config.model_config.get("report_localizer")
    if settings is None:
        return (
            "# 中文报告生成失败\n\n"
            "未配置 `report_localizer` 模型角色；请检查 ProjectConfig.model_config。\n"
        )

    try:
        result = call_model_json(
            settings,
            [
                (
                    "system",
                    (
                        "You are CodeCross Report Localizer. Translate the given "
                        "English Markdown report into Simplified Chinese. Preserve "
                        "Markdown structure, code blocks, file paths, identifiers, "
                        "finding IDs, enum values, and risk labels. Do not add new "
                        "findings or change the recommendation. Return only JSON."
                    ),
                ),
                (
                    "user",
                    f"English report:\n\n{final_report}\n\n{json_schema_instruction(LocalizedReport)}",
                ),
            ],
            LocalizedReport,
        )
        markdown = result.markdown.strip()
        return markdown or "# 中文报告生成失败\n\nReport Localizer Agent returned an empty translation."
    except ModelCallError as exc:
        return (
            "# 中文报告生成失败\n\n"
            f"Report Localizer Agent 调用失败：{exc}\n"
        )


def combine_bilingual_report(*, english_report: str, chinese_report: str) -> str:
    """Combine English and Chinese reports while keeping each version intact."""

    return (
        "# CodeCross Bilingual Final Review Report\n\n"
        "## English Report\n\n"
        f"{english_report.strip()}\n\n"
        "---\n\n"
        "## 中文报告\n\n"
        f"{chinese_report.strip() or '中文化 Agent 未返回译文。'}\n"
    )
