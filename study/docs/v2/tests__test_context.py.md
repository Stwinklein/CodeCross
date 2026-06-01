# `tests/test_context.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `context` 相关行为和回归场景。

- 源码行数：152
- 主要依赖：`pathlib`、`codecross`

## 函数与类

### `test_context_retriever_collects_changed_file_symbol_calls_and_tests`（函数，第 15 行）
- 签名：`def test_context_retriever_collects_changed_file_symbol_calls_and_tests(tmp_path: Path)`
- 作用：Context Retriever 应围绕 changed_symbols 收集最关键的客观上下文。

### `test_context_retriever_collects_javascript_symbols_calls_and_tests`（函数，第 70 行）
- 签名：`def test_context_retriever_collects_javascript_symbols_calls_and_tests(tmp_path: Path)`
- 作用：JS/Vue projects should get the same core context shape as Python repos.

### `test_context_retriever_includes_changed_hunks_for_large_javascript_files`（函数，第 117 行）
- 签名：`def test_context_retriever_includes_changed_hunks_for_large_javascript_files(tmp_path: Path)`
- 作用：Large JS files should still expose the changed hunk to reviewers.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
