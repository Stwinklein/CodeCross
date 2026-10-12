from __future__ import annotations

"""本地代码上下文检索。

V1.2 的核心目标是让 Reviewer 从“只看 diff”升级为“看 diff + 精准代码上下文”。
这个模块故意不调用 LLM，也不做风险判断；它只做确定性的本地检索，产出可以被
prompt、报告和测试复用的 CodeContext。
"""

import ast
import os
import re
from pathlib import Path

from .config import ProjectConfig
from .schemas import CodeContext, CodeContextSnippet, ReviewPlan


# 这些目录通常不是业务源码，扫描它们会变慢，也会把依赖/构建产物误当项目上下文。
# 这里是 Context Retriever 的第一道“降噪”：少看无关文件，Reviewer 才更聚焦。
SKIPPED_DIR_NAMES = {
    ".codecross",
    ".git",
    ".idea",
    ".pytest_cache",
    ".codecross-tmp",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".gradle",
    "node_modules",
}

MAX_CHANGED_FILE_LINES = 240
MAX_SYMBOL_LINES = 90
MAX_CONTEXT_RADIUS = 4
MAX_SNIPPETS = 36
MAX_SCANNED_SOURCE_FILES = 5000
MAX_CANDIDATE_SNIPPETS = 120
MAX_CONTEXT_CHARS = 60_000

# Context Retriever deliberately supports only source-like text files. This
# keeps reviewer prompts focused and prevents generated bundles from using up
# the bounded snippet budget before real application code is included.
SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".go",
    ".java",
    ".kt",
    ".kts",
    ".rs",
    ".rb",
    ".rake",
}
JAVASCRIPT_LIKE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue"}
BRACE_LIKE_SUFFIXES = JAVASCRIPT_LIKE_SUFFIXES | {".go", ".java", ".kt", ".kts", ".rs"}
RUBY_LIKE_SUFFIXES = {".rb", ".rake"}
PROJECT_MANIFESTS = (
    "pyproject.toml", "requirements.txt", "setup.cfg", "package.json", "tsconfig.json",
    "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Cargo.toml",
    "Gemfile", ".rspec",
)
JAVASCRIPT_TEST_MARKERS = (
    ".test.js",
    ".spec.js",
    ".test.jsx",
    ".spec.jsx",
    ".test.ts",
    ".spec.ts",
    ".test.tsx",
    ".spec.tsx",
)


def _relative_path(path: Path, root: Path) -> str:
    """把绝对路径转成项目内相对路径，失败时退回绝对路径字符串。"""

    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_inside_project(path: Path, root: Path) -> bool:
    """防止 diff 里出现 ../ 之类路径时读到项目外文件。"""

    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_skipped_path(path: Path) -> bool:
    """Return True for dependency/cache/build output paths.

    The check is based on path parts instead of substring matching. A file
    named ``src/distribution.js`` should still be valid source, while a file
    under ``webui/dist`` should be treated as generated context.
    """

    return any(part in SKIPPED_DIR_NAMES for part in path.parts)


def _read_text(path: Path) -> str | None:
    """用统一入口读取源码，无法解码时返回 None 而不是让检索中断。"""

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None


def _line_numbered_slice(lines: list[str], start_line: int, end_line: int) -> str:
    """提取一段带行号的代码。

    Reviewer 看到带行号的片段时，后续生成 Finding.line_range 更容易稳定；
    面试时也可以把这点解释成“减少模型猜行号”。
    """

    start = max(start_line, 1)
    end = min(end_line, len(lines))
    width = len(str(end))
    return "\n".join(
        f"{line_number:>{width}} | {lines[line_number - 1]}"
        for line_number in range(start, end + 1)
    )


def _make_snippet(
    *,
    kind: str,
    path: Path,
    root: Path,
    lines: list[str],
    start_line: int,
    end_line: int,
    reason: str,
    symbol: str | None = None,
) -> CodeContextSnippet:
    """创建 CodeContextSnippet，并统一裁剪内容格式。"""

    return CodeContextSnippet(
        kind=kind,  # type: ignore[arg-type]
        file_path=_relative_path(path, root),
        start_line=max(start_line, 1),
        end_line=max(end_line, start_line),
        reason=reason,
        content=_line_numbered_slice(lines, start_line, end_line),
        symbol=symbol,
    )


def _iter_source_files(root: Path, config: ProjectConfig) -> list[Path]:
    """按 ProjectConfig.source_roots 收集 Python 源文件。

    这里先只支持 Python，因为当前 executor/test generator 也是 pytest。未来做多语言时，
    这个函数可以替换成按 language 分发的文件枚举器。
    """

    # Multi-language scanner: this replaced the original Python-only rglob.
    # Keeping the logic here simple is safer than trying to infer project type
    # before every run.
    candidates: list[Path] = []
    # 测试通常不在 source_roots 下（如 src/+tests/、app/+spec/）。两组根目录
    # 必须合并扫描，否则 Reviewer 看得到实现却看不到项目已经定义的行为契约。
    configured_roots = list(dict.fromkeys([*(config.source_roots or ["."]), *config.test_roots]))
    resolved_roots: list[Path] = []
    for configured in configured_roots:
        base = (root / configured).resolve()
        if not base.exists() or not _is_inside_project(base, root):
            continue
        # 如果 '.' 已覆盖 tests/，不要再遍历 tests/ 第二次。
        if any(base == existing or base.is_relative_to(existing) for existing in resolved_roots):
            continue
        resolved_roots.append(base)

    for base in resolved_roots:
        for directory, dir_names, file_names in os.walk(base):
            # 与 rglob 后过滤不同，原地修改 dirs 会阻止 os.walk 进入依赖/缓存目录，
            # 大型 benchmark mirror 不再产生数秒乃至数十秒的无效目录枚举。
            dir_names[:] = [name for name in dir_names if name not in SKIPPED_DIR_NAMES]
            directory_path = Path(directory)
            for file_name in file_names:
                path = directory_path / file_name
                if path.suffix.lower() in SOURCE_SUFFIXES:
                    candidates.append(path)
    return sorted(dict.fromkeys(candidates))


def _find_symbol_ranges(path: Path, symbols: set[str]) -> list[tuple[str, int, int]]:
    """在一个 Python 文件里定位 def/class 的行号范围。

    AST 比正则更稳：它能识别 async def、class，也不会被注释或字符串里的 def 干扰。
    """

    if path.suffix.lower() in BRACE_LIKE_SUFFIXES:
        return _find_brace_language_symbol_ranges(path, symbols)
    if path.suffix.lower() in RUBY_LIKE_SUFFIXES:
        return _find_ruby_symbol_ranges(path, symbols)

    text = _read_text(path)
    if not text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    ranges: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in symbols:
                end_line = getattr(node, "end_lineno", node.lineno)
                ranges.append((node.name, node.lineno, end_line))
    return ranges


def _javascript_definition_pattern(symbol: str) -> re.Pattern[str]:
    """Build a bounded JS/TS/Vue symbol definition matcher.

    This is intentionally not a full parser. For review context we need the
    common shapes that appear in application diffs: function declarations,
    classes, and const/let/var bindings whose value is a function or arrow
    function. Plain constants are ignored so local values do not become noisy
    review targets.
    """

    escaped = re.escape(symbol)
    return re.compile(
        rf"\b(?:export\s+)?(?:async\s+)?function\s+{escaped}\s*\("
        rf"|\b(?:export\s+)?class\s+{escaped}\b"
        rf"|\b(?:export\s+)?(?:const|let|var)\s+{escaped}\s*=\s*"
        rf"(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
    )


def _line_defines_javascript_symbol(line: str, symbol: str) -> bool:
    """Prevent JS definitions from being reported again as call sites."""

    return bool(_javascript_definition_pattern(symbol).search(line))


def _brace_language_definition_pattern(symbol: str, suffix: str) -> re.Pattern[str]:
    """为 JS/TS、Go、Java/Kotlin、Rust 构建保守的函数/类定义匹配器。"""

    escaped = re.escape(symbol)
    if suffix in JAVASCRIPT_LIKE_SUFFIXES:
        return _javascript_definition_pattern(symbol)
    if suffix == ".go":
        return re.compile(rf"\bfunc\s+(?:\([^)]*\)\s*)?{escaped}\s*\(")
    if suffix == ".rs":
        return re.compile(
            rf"\b(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+{escaped}\s*\("
        )
    return re.compile(
        rf"\b(?:public|protected|private)\s+(?:static\s+|final\s+|synchronized\s+)*"
        rf"[\w<>,?\[\].]+\s+{escaped}\s*\(|\bclass\s+{escaped}\b"
    )


def _find_brace_block_end(lines: list[str], start_index: int) -> int:
    """Approximate a JS/Vue block end with brace balance.

    The context retriever only needs a useful bounded snippet, not perfect AST
    accuracy. If a file is minified or malformed, the MAX_SYMBOL_LINES cap still
    prevents a huge prompt payload.
    """

    depth = 0
    saw_open_brace = False
    max_index = min(len(lines), start_index + MAX_SYMBOL_LINES)
    for index in range(start_index, max_index):
        line = lines[index]
        depth += line.count("{")
        if "{" in line:
            saw_open_brace = True
        depth -= line.count("}")
        if saw_open_brace and depth <= 0 and index > start_index:
            return index + 1
    return max(start_index + 1, max_index)


def _find_brace_language_symbol_ranges(path: Path, symbols: set[str]) -> list[tuple[str, int, int]]:
    """用轻量正则和括号平衡定位多种 brace-based 语言的符号范围。"""

    text = _read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    ranges: list[tuple[str, int, int]] = []
    suffix = path.suffix.lower()
    for index, line in enumerate(lines):
        for symbol in symbols:
            if _brace_language_definition_pattern(symbol, suffix).search(line):
                ranges.append((symbol, index + 1, _find_brace_block_end(lines, index)))
    return ranges


def _find_ruby_symbol_ranges(path: Path, symbols: set[str]) -> list[tuple[str, int, int]]:
    """按 def/end 嵌套近似定位 Ruby 方法；复杂 metaprogramming 保守跳过。"""

    text = _read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    ranges: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"\s*def\s+(?:self\.)?([A-Za-z_][A-Za-z0-9_!?=]*)", line)
        if not match or match.group(1) not in symbols:
            continue
        depth = 0
        end_line = min(len(lines), index + MAX_SYMBOL_LINES)
        for cursor in range(index, end_line):
            stripped = lines[cursor].strip()
            if re.match(r"(?:def|class|module|if|unless|case|begin|do)\b", stripped):
                depth += 1
            if stripped == "end":
                depth -= 1
                if depth <= 0:
                    end_line = cursor + 1
                    break
        ranges.append((match.group(1), index + 1, end_line))
    return ranges


def _called_symbol_name(call: ast.Call) -> str | None:
    """从一次函数调用里提取被调用的符号名。

    - safe_divide(...)       -> safe_divide
    - module.safe_divide(...) -> safe_divide
    这对 V1.2 足够，因为我们只需要找直接调用点，而不是完整类型解析。
    """

    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _find_symbol_call_lines(path: Path, symbols: set[str]) -> list[tuple[str, int]]:
    """查找某文件里对 changed_symbols 的直接调用行。"""

    if path.suffix.lower() in BRACE_LIKE_SUFFIXES:
        return _find_brace_language_symbol_call_lines(path, symbols)
    if path.suffix.lower() in RUBY_LIKE_SUFFIXES:
        return _find_ruby_symbol_call_lines(path, symbols)

    text = _read_text(path)
    if not text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    calls: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            symbol = _called_symbol_name(node)
            if symbol in symbols:
                calls.append((symbol, node.lineno))
    return calls


def _find_brace_language_symbol_call_lines(path: Path, symbols: set[str]) -> list[tuple[str, int]]:
    """查找 brace-based 语言中的直接调用点。

    Regex call-site search is deliberately conservative: it looks for
    ``symbol(...)`` and ignores the declaration line. That is enough to surface
    important contract consumers such as routes calling ``parseRequest``.
    """

    text = _read_text(path)
    if not text:
        return []
    calls: list[tuple[str, int]] = []
    suffix = path.suffix.lower()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for symbol in symbols:
            if _brace_language_definition_pattern(symbol, suffix).search(line):
                continue
            if re.search(rf"\b{re.escape(symbol)}\s*\(", line):
                calls.append((symbol, line_number))
    return calls


def _find_ruby_symbol_call_lines(path: Path, symbols: set[str]) -> list[tuple[str, int]]:
    text = _read_text(path)
    if not text:
        return []
    calls: list[tuple[str, int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if re.match(r"\s*def\b", line):
            continue
        for symbol in symbols:
            if re.search(rf"(?<![\w!?=]){re.escape(symbol)}(?:\s*\(|\s+)", line):
                calls.append((symbol, line_number))
    return calls


def _looks_like_test_file(path: Path, root: Path, config: ProjectConfig) -> bool:
    """判断一个文件是否是测试文件。"""

    rel = _relative_path(path, root)
    file_name = path.name
    configured_test_root = any(
        rel.startswith(test_root.strip("./\\").replace("\\", "/"))
        for test_root in config.test_roots
    )
    is_python_test = file_name.startswith("test_") or file_name.endswith("_test.py")
    is_javascript_test = file_name.endswith(JAVASCRIPT_TEST_MARKERS) or "__tests__/" in rel
    is_go_test = file_name.endswith("_test.go")
    is_rust_or_java_test = rel.startswith(("tests/", "src/test/"))
    is_ruby_test = rel.startswith(("spec/", "test/")) or file_name.endswith("_spec.rb")
    return configured_test_root or is_python_test or is_javascript_test or is_go_test or is_rust_or_java_test or is_ruby_test


def _append_unique(snippets: list[CodeContextSnippet], snippet: CodeContextSnippet) -> None:
    """按 kind/file/range/symbol 去重，避免同一段代码被重复塞进 prompt。"""

    key = (snippet.kind, snippet.file_path, snippet.start_line, snippet.end_line, snippet.symbol)
    existing = {
        (item.kind, item.file_path, item.start_line, item.end_line, item.symbol)
        for item in snippets
    }
    if key not in existing and len(snippets) < MAX_CANDIDATE_SNIPPETS:
        snippets.append(snippet)


def _apply_context_budget(
    snippets: list[CodeContextSnippet], notes: list[str]
) -> list[CodeContextSnippet]:
    """按审查价值排序并施加片段数/字符数双预算。

    仅限制片段数量不足以控制 token：一个大型 manifest 或 90 行压缩源码仍可能很长。
    字符预算虽然不是精确 tokenizer，但跨模型稳定、无需绑定 provider，适合作为发送
    Prompt 前的最后一道硬限制。
    """

    priority = {
        "changed_hunk": 0,
        "changed_file": 1,
        "symbol_definition": 2,
        "call_site": 3,
        "existing_test": 4,
        "project_manifest": 5,
        "diagnostic": 6,
    }
    ordered = sorted(
        enumerate(snippets),
        key=lambda item: (priority.get(item[1].kind, 99), item[0]),
    )
    selected: list[CodeContextSnippet] = []
    used_chars = 0
    for _, snippet in ordered:
        size = len(snippet.content)
        if len(selected) >= MAX_SNIPPETS or used_chars + size > MAX_CONTEXT_CHARS:
            continue
        selected.append(snippet)
        used_chars += size
    if len(selected) < len(snippets):
        notes.append(
            f"Context budget kept {len(selected)}/{len(snippets)} candidate snippets "
            f"and {used_chars}/{MAX_CONTEXT_CHARS} characters."
        )
    return selected


def _clean_diff_path(path: str) -> str:
    """Normalize a unified diff path into a project-relative path."""

    cleaned = path.strip()
    if cleaned in {"/dev/null", "dev/null"}:
        return ""
    if cleaned.startswith(("a/", "b/")):
        cleaned = cleaned[2:]
    return cleaned


def _parse_changed_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Extract new-file hunk line ranges from a unified diff.

    Large files are too expensive to include completely. Hunk ranges let us
    include the exact changed area plus a few surrounding lines, which is the
    minimum useful context for reviewer and test generation.
    """

    ranges: dict[str, list[tuple[int, int]]] = {}
    current_path = ""
    for line in diff.splitlines():
        match = re.match(r"diff --git a/(.*?) b/(.*)", line)
        if match:
            current_path = match.group(2).strip()
            ranges.setdefault(current_path, [])
            continue
        if line.startswith("+++ "):
            candidate = _clean_diff_path(line.removeprefix("+++ "))
            if candidate:
                current_path = candidate
                ranges.setdefault(current_path, [])
            continue
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if hunk and current_path:
            start = int(hunk.group(1))
            length = int(hunk.group(2) or "1")
            end = start + max(length, 1) - 1
            ranges.setdefault(current_path, []).append((start, end))
    return ranges


def retrieve_code_context(
    *,
    diff: str,
    project_root: str | Path,
    review_plan: ReviewPlan,
    config: ProjectConfig,
) -> CodeContext:
    """根据 ReviewPlan 在本地项目中检索 reviewer 需要的客观上下文。

    输入仍然以 Planner 的 changed_files/changed_symbols 为准，这样 Context Retriever
    不会自己“发明审查方向”。它只是把这些客观目标映射到真实代码片段。
    """

    root = Path(project_root).resolve()
    symbols = set(review_plan.changed_symbols)
    snippets: list[CodeContextSnippet] = []
    missing_files: list[str] = []
    notes: list[str] = []
    changed_line_ranges = _parse_changed_line_ranges(diff)

    # 项目清单直接决定依赖版本、编译选项和测试框架，是判断兼容性问题的重要上下文。
    # 只读取少量已知文件并限制行数，避免 lockfile/大型配置耗尽 prompt 预算。
    for manifest_name in PROJECT_MANIFESTS:
        manifest = root / manifest_name
        text = _read_text(manifest) if manifest.is_file() else None
        if not text:
            continue
        lines = text.splitlines()
        _append_unique(
            snippets,
            _make_snippet(
                kind="project_manifest",
                path=manifest,
                root=root,
                lines=lines,
                start_line=1,
                end_line=min(len(lines), 160) or 1,
                reason="Project manifest used to verify dependencies, build settings, and test framework.",
            ),
        )

    for changed_file in review_plan.changed_files:
        path = (root / changed_file).resolve()
        if not _is_inside_project(path, root) or not path.exists():
            missing_files.append(changed_file)
            continue
        if _is_skipped_path(path):
            notes.append(f"{changed_file}: skipped because it is in a generated/dependency directory.")
            continue
        text = _read_text(path)
        if text is None:
            notes.append(f"{changed_file}: skipped because it is not UTF-8 text.")
            continue

        lines = text.splitlines()
        if len(lines) <= MAX_CHANGED_FILE_LINES:
            _append_unique(
                snippets,
                _make_snippet(
                    kind="changed_file",
                    path=path,
                    root=root,
                    lines=lines,
                    start_line=1,
                    end_line=len(lines) or 1,
                    reason="Complete changed file; small enough to review as a whole.",
                ),
            )
        else:
            hunks = changed_line_ranges.get(changed_file, [])
            if not hunks:
                notes.append(
                    f"{changed_file}: file has {len(lines)} lines; only symbol-level snippets are included."
                )
            for start, end in hunks[:3]:
                _append_unique(
                    snippets,
                    _make_snippet(
                        kind="changed_hunk",
                        path=path,
                        root=root,
                        lines=lines,
                        start_line=max(1, start - MAX_CONTEXT_RADIUS),
                        end_line=min(len(lines), end + MAX_CONTEXT_RADIUS),
                        reason="Changed hunk with nearby lines from a large file.",
                    ),
                )
            if len(hunks) > 3:
                notes.append(
                    f"{changed_file}: included first 3 of {len(hunks)} changed hunks to keep context bounded."
                )

        for symbol, start, end in _find_symbol_ranges(path, symbols):
            _append_unique(
                snippets,
                _make_snippet(
                    kind="symbol_definition",
                    path=path,
                    root=root,
                    lines=lines,
                    start_line=start,
                    end_line=min(end, start + MAX_SYMBOL_LINES - 1),
                    reason="Definition of a changed symbol.",
                    symbol=symbol,
                ),
            )

    # 调用点和现有测试从全项目扫描得到。它们是 reviewer 判断 contract 的关键：
    # 例如 safe_divide 的调用方是否已经保证 b != 0，现有测试是否定义了预期行为。
    seen_call_site_targets: set[tuple[str, str, str]] = set()
    source_files = _iter_source_files(root, config)
    if len(source_files) > MAX_SCANNED_SOURCE_FILES:
        notes.append(
            f"Repository has {len(source_files)} source files; call-site scan was capped at "
            f"{MAX_SCANNED_SOURCE_FILES} files."
        )
        source_files = source_files[:MAX_SCANNED_SOURCE_FILES]
    for path in source_files:
        text = _read_text(path)
        if not text:
            continue
        lines = text.splitlines()
        for symbol, line_number in _find_symbol_call_lines(path, symbols):
            kind = "existing_test" if _looks_like_test_file(path, root, config) else "call_site"
            rel_path = _relative_path(path, root)
            call_key = (kind, rel_path, symbol)
            if call_key in seen_call_site_targets:
                continue
            seen_call_site_targets.add(call_key)
            _append_unique(
                snippets,
                _make_snippet(
                    kind=kind,
                    path=path,
                    root=root,
                    lines=lines,
                    start_line=max(1, line_number - MAX_CONTEXT_RADIUS),
                    end_line=min(len(lines), line_number + MAX_CONTEXT_RADIUS),
                    reason=(
                        "Existing test referencing changed symbol."
                        if kind == "existing_test"
                        else "Call site referencing changed symbol."
                    ),
                    symbol=symbol,
                ),
            )

    if not snippets:
        notes.append(
            "No local code context was found; reviewers will rely on diff and review plan only."
        )

    snippets = _apply_context_budget(snippets, notes)
    return CodeContext(
        changed_files=review_plan.changed_files,
        changed_symbols=review_plan.changed_symbols,
        snippets=snippets,
        missing_files=missing_files,
        summary_notes=notes,
    )
