# `tests/test_executor.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `executor` 相关行为和回归场景。

- 源码行数：306
- 主要依赖：`pathlib`、`codecross`

## 函数与类

### `test_executor_runs_pytest_and_reports_failure`（函数，第 16 行）
- 签名：`def test_executor_runs_pytest_and_reports_failure(tmp_path: Path)`
- 作用：真实执行一个会触发 ZeroDivisionError 的 pytest。

### `test_executor_blocks_unsafe_test_code`（函数，第 56 行）
- 签名：`def test_executor_blocks_unsafe_test_code(tmp_path: Path)`
- 作用：包含 subprocess 的模型生成测试应该被拦截。

### `test_executor_blocks_dynamic_import_escape`（函数，第 79 行）
- 签名：`def test_executor_blocks_dynamic_import_escape(tmp_path: Path)`
- 作用：AST sanitizer 应拦住 __import__('os').system(...) 这种字符串黑名单绕过。

### `test_executor_does_not_block_safe_string_mentions`（函数，第 102 行）
- 签名：`def test_executor_does_not_block_safe_string_mentions(tmp_path: Path)`
- 作用：旧字符串黑名单会误杀普通文本；AST sanitizer 不应拦截字符串里的 open(。

### `test_executor_does_not_treat_confirm_as_rm_command`（函数，第 123 行）
- 签名：`def test_executor_does_not_treat_confirm_as_rm_command(tmp_path: Path)`
- 作用：命令黑名单要匹配独立 token，不能把 confirm 里的 rm 当 shell 命令。

### `test_executor_skips_placeholder_assert_true_tests`（函数，第 148 行）
- 签名：`def test_executor_skips_placeholder_assert_true_tests(tmp_path: Path)`
- 作用：占位测试不能因为 pytest 通过就反驳 reviewer finding。

### `test_executor_allows_placeholder_comment_when_real_assert_exists`（函数，第 176 行）
- 签名：`def test_executor_allows_placeholder_comment_when_real_assert_exists(tmp_path: Path)`
- 作用：有真实断言时，注释里的 placeholder 字样不应让静态检查被跳过。

### `test_executor_records_over_budget_tests_as_skipped`（函数，第 202 行）
- 签名：`def test_executor_records_over_budget_tests_as_skipped(tmp_path: Path)`
- 作用：超过执行预算的生成测试要留下 skipped 记录，方便解释覆盖缺口。

### `test_executor_runs_static_check_as_pytest`（函数，第 230 行）
- 签名：`def test_executor_runs_static_check_as_pytest(tmp_path: Path)`
- 作用：static_check 是给 JS/TS 等项目用的 Python 静态断言，也应该被执行。

### `test_executor_marks_inconsistent_rerun_as_flaky_error`（函数，第 265 行）
- 签名：`def test_executor_marks_inconsistent_rerun_as_flaky_error(monkeypatch, tmp_path: Path)`
- 作用：第一次失败第二次通过时，不新增 flaky status，而是用 error_type 表达不稳定。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
