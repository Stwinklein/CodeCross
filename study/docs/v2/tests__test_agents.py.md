# `tests/test_agents.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `agents` 相关行为和回归场景。

- 源码行数：355
- 主要依赖：`pytest`、`codecross`

## 函数与类

### `test_parse_changed_files_handles_rename_and_hunk_notes`（函数，第 22 行）
- 签名：`def test_parse_changed_files_handles_rename_and_hunk_notes()`
- 作用：V1.1 的轻量 diff parser 应能识别 rename 和 hunk 新行号。

### `test_parse_changed_symbols_handles_javascript_and_hunk_headers`（函数，第 42 行）
- 签名：`def test_parse_changed_symbols_handles_javascript_and_hunk_headers()`
- 作用：Planner symbol extraction should work for real JS/Vue style diffs too.

### `test_reviewer_fallbacks_only_on_model_call_error`（函数，第 62 行）
- 签名：`def test_reviewer_fallbacks_only_on_model_call_error(monkeypatch)`
- 作用：reviewer_agent 只吞 ModelCallError；本地代码错误要继续抛出。

### `test_reviewer_agent_uses_a2a_when_endpoint_is_configured`（函数，第 93 行）
- 签名：`def test_reviewer_agent_uses_a2a_when_endpoint_is_configured(monkeypatch)`
- 作用：A2A reviewer should be called before the local LangChain reviewer path.

### `test_mock_test_author_adds_normal_path_control_case`（函数，第 124 行）
- 签名：`def test_mock_test_author_adds_normal_path_control_case()`
- 作用：V1.6 Test Author 应给零分母边界测试补一个正常路径对照。

### `test_final_report_renders_english_and_deduplicates_findings`（函数，第 179 行）
- 签名：`def test_final_report_renders_english_and_deduplicates_findings()`
- 作用：Final report stays English; the new localizer agent owns Chinese output.

### `test_infer_target_language_covers_known_extensions`（函数，第 268 行）
- 签名：`def test_infer_target_language_covers_known_extensions()`
- 作用：The deterministic inference table should cover the languages we ship triggers for.

### `test_infer_target_language_returns_none_for_unknown_or_empty`（函数，第 283 行）
- 签名：`def test_infer_target_language_returns_none_for_unknown_or_empty()`
- 作用：Unknown / suffixless paths must NOT default to a fake language.

### `test_normalize_test_batch_fills_target_language_from_target_file`（函数，第 298 行）
- 签名：`def test_normalize_test_batch_fills_target_language_from_target_file()`
- 作用：``_normalize_test_batch`` should backfill ``target_language`` for cases that forgot it.

### `test_normalize_test_batch_preserves_explicit_target_language`（函数，第 321 行）
- 签名：`def test_normalize_test_batch_preserves_explicit_target_language()`
- 作用：If the model already supplied a target_language we trust it, even when the file extension would map elsewhere.

### `test_report_localizer_mock_returns_chinese_section`（函数，第 344 行）
- 签名：`def test_report_localizer_mock_returns_chinese_section()`
- 作用：Mock runs still expose the new final-report localizer node output.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
