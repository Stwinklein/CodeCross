# `tests/test_ci.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `ci` 相关行为和回归场景。

- 源码行数：288
- 主要依赖：`pathlib`、`pytest`、`codecross`

## 函数与类

### `_sample_state`（函数，第 30 行）
- 签名：`def _sample_state() -> dict`
- 作用：Build a compact workflow-like state for comment rendering tests.

### `test_build_pr_comment_contains_marker_summary_and_findings`（函数，第 111 行）
- 签名：`def test_build_pr_comment_contains_marker_summary_and_findings()`
- 作用：The PR comment should be stable, compact, and useful at a glance.

### `test_build_pr_comment_handles_empty_state`（函数，第 141 行）
- 签名：`def test_build_pr_comment_handles_empty_state()`
- 作用：CI 异常路径也应能渲染一条可读报告，而不是因为缺字段二次崩溃。

### `test_build_pr_comment_uses_workflow_rendered_reports`（函数，第 152 行）
- 签名：`def test_build_pr_comment_uses_workflow_rendered_reports()`
- 作用：CI no longer rebuilds findings; it posts the workflow's bilingual reports.

### `test_build_pr_comment_posts_english_and_chinese_sections`（函数，第 165 行）
- 签名：`def test_build_pr_comment_posts_english_and_chinese_sections()`
- 作用：The PR comment posts both localized outputs produced by the workflow.

### `test_build_pr_comment_preserves_report_markdown`（函数，第 179 行）
- 签名：`def test_build_pr_comment_preserves_report_markdown()`
- 作用：CI should not post-process report Markdown after workflow rendering.

### `test_truncate_comment_keeps_marker`（函数，第 193 行）
- 签名：`def test_truncate_comment_keeps_marker()`
- 作用：Even truncated comments must keep the marker for future updates.

### `test_validate_model_credentials_reports_missing_env`（函数，第 202 行）
- 签名：`def test_validate_model_credentials_reports_missing_env(monkeypatch)`
- 作用：Real CI mode should fail loudly if required model secrets are absent.

### `test_load_github_metadata_from_event_file`（函数，第 229 行）
- 签名：`def test_load_github_metadata_from_event_file(tmp_path: Path, monkeypatch)`
- 作用：GitHub event JSON should populate PR metadata used by comments.

### `test_load_github_metadata_without_event_path_is_safe`（函数，第 259 行）
- 签名：`def test_load_github_metadata_without_event_path_is_safe(monkeypatch)`
- 作用：Local CI dry-runs should not try to read the current directory as JSON.

### `test_read_diff_file_strips_utf8_bom`（函数，第 275 行）
- 签名：`def test_read_diff_file_strips_utf8_bom(tmp_path: Path)`
- 作用：Windows-written diff files should still parse correctly in CI.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
