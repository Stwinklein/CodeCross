# `tests/test_api.py` 解析

## 文件作用
pytest 测试文件，覆盖 CodeCross 的 `api` 相关行为和回归场景。

- 源码行数：244
- 主要依赖：`time`、`collections`、`pathlib`、`pytest`、`fastapi`、`codecross`

## 函数与类

### `clear_run_store`（函数，第 18 行）
- 签名：`def clear_run_store() -> Iterator[None]`
- 作用：Keep tests independent across the memory cache and SQLite history.

### `_wait_for_terminal_run`（函数，第 30 行）
- 签名：`def _wait_for_terminal_run(client: TestClient, run_id: str) -> dict`
- 作用：Poll the API the same way the frontend does.

### `test_health_endpoint_reports_v1_6`（函数，第 49 行）
- 签名：`def test_health_endpoint_reports_v1_6()`
- 作用：The frontend can use this endpoint to check whether the API is reachable.

### `test_runtime_config_uses_env_models_without_exposing_keys`（函数，第 60 行）
- 签名：`def test_runtime_config_uses_env_models_without_exposing_keys(tmp_path: Path)`
- 作用：Frontend config should mirror .

### `test_reviewer_override_secrets_are_not_returned_or_persisted`（函数，第 92 行）
- 签名：`def test_reviewer_override_secrets_are_not_returned_or_persisted()`
- 作用：Per-run API keys may be used in memory, but must not leak to clients/history.

### `test_missing_run_returns_404`（函数，第 123 行）
- 签名：`def test_missing_run_returns_404()`
- 作用：Unknown run IDs should be a normal API miss, not a server error.

### `test_abort_missing_run_returns_404`（函数，第 133 行）
- 签名：`def test_abort_missing_run_returns_404()`
- 作用：Abort should use the same not-found semantics as normal run lookup.

### `test_create_poll_events_and_export_demo_run`（函数，第 143 行）
- 签名：`def test_create_poll_events_and_export_demo_run()`
- 作用：Browser-created run produces a snapshot plus lifecycle events.

### `test_abort_marks_pending_run_as_cancel_requested`（函数，第 199 行）
- 签名：`def test_abort_marks_pending_run_as_cancel_requested()`
- 作用：Abort is cooperative: pending/running records get a durable cancel flag.

### `test_abort_after_success_does_not_destroy_result`（函数，第 221 行）
- 签名：`def test_abort_after_success_does_not_destroy_result()`
- 作用：A finished run should stay succeeded if the user clicks abort afterward.

## 学习提示
阅读这个文件时，可以把每个 `test_*` 当成一条需求或回归保护：先看构造数据，再看断言想保护什么行为。
