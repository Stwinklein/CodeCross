# `codecross/context.py` 解析

## 文件作用
本地代码上下文检索层，根据 diff 和 ReviewPlan 找变更片段、符号定义、调用点和测试片段。

- 源码行数：512
- 主要依赖：`ast`、`re`、`pathlib`、`config`、`schemas`

## 函数与类

### `_relative_path`（函数，第 62 行）
- 签名：`def _relative_path(path: Path, root: Path) -> str`
- 作用：把绝对路径转成项目内相对路径，失败时退回绝对路径字符串。

### `_is_inside_project`（函数，第 71 行）
- 签名：`def _is_inside_project(path: Path, root: Path) -> bool`
- 作用：防止 diff 里出现 ../ 之类路径时读到项目外文件。

### `_is_skipped_path`（函数，第 81 行）
- 签名：`def _is_skipped_path(path: Path) -> bool`
- 作用：Return True for dependency/cache/build output paths.

### `_read_text`（函数，第 92 行）
- 签名：`def _read_text(path: Path) -> str | None`
- 作用：用统一入口读取源码，无法解码时返回 None 而不是让检索中断。

### `_line_numbered_slice`（函数，第 103 行）
- 签名：`def _line_numbered_slice(lines: list[str], start_line: int, end_line: int) -> str`
- 作用：提取一段带行号的代码。

### `_make_snippet`（函数，第 119 行）
- 签名：`def _make_snippet(*, kind: str, path: Path, root: Path, lines: list[str], start_line: int, end_line: int, reason: str, symbol: str | None=None) -> CodeContextSnippet`
- 作用：创建 CodeContextSnippet，并统一裁剪内容格式。

### `_iter_source_files`（函数，第 143 行）
- 签名：`def _iter_source_files(root: Path, config: ProjectConfig) -> list[Path]`
- 作用：按 ProjectConfig.source_roots 收集 Python 源文件。

### `_find_symbol_ranges`（函数，第 167 行）
- 签名：`def _find_symbol_ranges(path: Path, symbols: set[str]) -> list[tuple[str, int, int]]`
- 作用：在一个 Python 文件里定位 def/class 的行号范围。

### `_javascript_definition_pattern`（函数，第 193 行）
- 签名：`def _javascript_definition_pattern(symbol: str) -> re.Pattern[str]`
- 作用：Build a bounded JS/TS/Vue symbol definition matcher.

### `_line_defines_javascript_symbol`（函数，第 212 行）
- 签名：`def _line_defines_javascript_symbol(line: str, symbol: str) -> bool`
- 作用：Prevent JS definitions from being reported again as call sites.

### `_find_brace_block_end`（函数，第 218 行）
- 签名：`def _find_brace_block_end(lines: list[str], start_index: int) -> int`
- 作用：Approximate a JS/Vue block end with brace balance.

### `_find_javascript_symbol_ranges`（函数，第 240 行）
- 签名：`def _find_javascript_symbol_ranges(path: Path, symbols: set[str]) -> list[tuple[str, int, int]]`
- 作用：Find JS/TS/Vue function-like symbol ranges with small regex parsing.

### `_called_symbol_name`（函数，第 255 行）
- 签名：`def _called_symbol_name(call: ast.Call) -> str | None`
- 作用：从一次函数调用里提取被调用的符号名。

### `_find_symbol_call_lines`（函数，第 270 行）
- 签名：`def _find_symbol_call_lines(path: Path, symbols: set[str]) -> list[tuple[str, int]]`
- 作用：查找某文件里对 changed_symbols 的直接调用行。

### `_find_javascript_symbol_call_lines`（函数，第 293 行）
- 签名：`def _find_javascript_symbol_call_lines(path: Path, symbols: set[str]) -> list[tuple[str, int]]`
- 作用：Find simple JS/TS/Vue call sites for changed symbols.

### `_looks_like_test_file`（函数，第 314 行）
- 签名：`def _looks_like_test_file(path: Path, root: Path, config: ProjectConfig) -> bool`
- 作用：判断一个文件是否是测试文件。

### `_append_unique`（函数，第 328 行）
- 签名：`def _append_unique(snippets: list[CodeContextSnippet], snippet: CodeContextSnippet) -> None`
- 作用：按 kind/file/range/symbol 去重，避免同一段代码被重复塞进 prompt。

### `_clean_diff_path`（函数，第 340 行）
- 签名：`def _clean_diff_path(path: str) -> str`
- 作用：Normalize a unified diff path into a project-relative path.

### `_parse_changed_line_ranges`（函数，第 351 行）
- 签名：`def _parse_changed_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]`
- 作用：Extract new-file hunk line ranges from a unified diff.

### `retrieve_code_context`（函数，第 382 行）
- 签名：`def retrieve_code_context(*, diff: str, project_root: str | Path, review_plan: ReviewPlan, config: ProjectConfig) -> CodeContext`
- 作用：根据 ReviewPlan 在本地项目中检索 reviewer 需要的客观上下文。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
