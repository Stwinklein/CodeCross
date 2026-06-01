# `tests/test_cross_verification.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `cross / verification` 相关行为和回归场景。

- 源码行数：436
- 主要依赖：`pathlib`、`pytest`、`codecross`

## 函数与类

### `_make_finding`（函数，第 55 行）
- 签名：`def _make_finding(file_path: str='src/server/routes.js') -> Finding`
- 作用：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。

### `_make_cross_test_case`（函数，第 71 行）
- 签名：`def _make_cross_test_case(finding_id: str='finding_test', *, target_language: str | None='javascript', trigger: str | None='unsupported_language') -> TestCase`
- 作用：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。

### `test_trigger_unsupported_language_detected`（函数，第 96 行）
- 签名：`def test_trigger_unsupported_language_detected()`
- 作用：pytest 用例，验证 `trigger unsupported language detected` 这个行为或回归场景。

### `test_trigger_environment_error_from_stderr`（函数，第 106 行）
- 签名：`def test_trigger_environment_error_from_stderr()`
- 作用：pytest 用例，验证 `trigger environment error from stderr` 这个行为或回归场景。

### `test_trigger_default_pytest_path_returns_none`（函数，第 126 行）
- 签名：`def test_trigger_default_pytest_path_returns_none()`
- 作用：pytest 用例，验证 `trigger default pytest path returns none` 这个行为或回归场景。

### `test_trigger_disabled_by_runner_config`（函数，第 138 行）
- 签名：`def test_trigger_disabled_by_runner_config()`
- 作用：pytest 用例，验证 `trigger disabled by runner config` 这个行为或回归场景。

### `test_trigger_executor_blocked_lowest_priority`（函数，第 146 行）
- 签名：`def test_trigger_executor_blocked_lowest_priority()`
- 作用：pytest 用例，验证 `trigger executor blocked lowest priority` 这个行为或回归场景。

### `test_executor_disabled_cross_verification_skips`（函数，第 162 行）
- 签名：`def test_executor_disabled_cross_verification_skips(tmp_path: Path)`
- 作用：pytest 用例，验证 `executor disabled cross verification skips` 这个行为或回归场景。

### `test_executor_cross_verification_budget_exhausted`（函数，第 182 行）
- 签名：`def test_executor_cross_verification_budget_exhausted(tmp_path: Path)`
- 作用：pytest 用例，验证 `executor cross verification budget exhausted` 这个行为或回归场景。

### `test_executor_cross_verification_missing_project_config`（函数，第 206 行）
- 签名：`def test_executor_cross_verification_missing_project_config(tmp_path: Path)`
- 作用：pytest 用例，验证 `executor cross verification missing project config` 这个行为或回归场景。

### `test_executor_cross_verification_missing_finding`（函数，第 224 行）
- 签名：`def test_executor_cross_verification_missing_finding(tmp_path: Path)`
- 作用：pytest 用例，验证 `executor cross verification missing finding` 这个行为或回归场景。

### `test_executor_cross_verification_mock_supported`（函数，第 243 行）
- 签名：`def test_executor_cross_verification_mock_supported(tmp_path: Path)`
- 作用：pytest 用例，验证 `executor cross verification mock supported` 这个行为或回归场景。

### `_result_with_status`（函数，第 270 行）
- 签名：`def _result_with_status(finding_id: str, status: str, *, round_index: int=1) -> TestResult`
- 作用：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。

### `test_board_cross_supported_promotes_unverified`（函数，第 300 行）
- 签名：`def test_board_cross_supported_promotes_unverified()`
- 作用：pytest 用例，验证 `board cross supported promotes unverified` 这个行为或回归场景。

### `test_board_cross_refuted_marks_finding_refuted`（函数，第 315 行）
- 签名：`def test_board_cross_refuted_marks_finding_refuted()`
- 作用：pytest 用例，验证 `board cross refuted marks finding refuted` 这个行为或回归场景。

### `test_board_cross_conflict_falls_to_human_review`（函数，第 329 行）
- 签名：`def test_board_cross_conflict_falls_to_human_review()`
- 作用：pytest 用例，验证 `board cross conflict falls to human review` 这个行为或回归场景。

### `test_board_does_not_overwrite_pytest_supported`（函数，第 346 行）
- 签名：`def test_board_does_not_overwrite_pytest_supported()`
- 作用：pytest 用例，验证 `board does not overwrite pytest supported` 这个行为或回归场景。

### `test_spec_to_payload_abstain_falls_back_to_inconclusive`（函数，第 369 行）
- 签名：`def test_spec_to_payload_abstain_falls_back_to_inconclusive()`
- 作用：pytest 用例，验证 `spec to payload abstain falls back to inconclusive` 这个行为或回归场景。

### `test_spec_to_payload_confirmed_carries_metadata`（函数，第 380 行）
- 签名：`def test_spec_to_payload_confirmed_carries_metadata()`
- 作用：pytest 用例，验证 `spec to payload confirmed carries metadata` 这个行为或回归场景。

### `test_run_repro_agent_missing_role_yields_inconclusive`（函数，第 396 行）
- 签名：`def test_run_repro_agent_missing_role_yields_inconclusive()`
- 作用：pytest 用例，验证 `run repro agent missing role yields inconclusive` 这个行为或回归场景。

### `test_run_repro_agent_swallows_model_call_error`（函数，第 414 行）
- 签名：`def test_run_repro_agent_swallows_model_call_error(monkeypatch: pytest.MonkeyPatch)`
- 作用：pytest 用例，验证 `run repro agent swallows model call error` 这个行为或回归场景。

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
