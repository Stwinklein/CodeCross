# `codecross/api.py` 解析

## 文件作用
面向前端的 FastAPI 后端，管理 run 创建、后台执行、事件流、取消、历史记录、运行配置和报告导出。

- 源码行数：569
- 主要依赖：`json`、`threading`、`time`、`dataclasses`、`datetime`、`pathlib`、`typing`、`uuid`、`fastapi`、`pydantic`、`config`、`storage`、`workflow`

## 函数与类

### `ReviewerOverride`（类，第 42 行）
- 签名：`class ReviewerOverride(BaseModel)`
- 作用：Per-run reviewer connection settings coming from the frontend.
- 主要字段/属性：
  - `provider: str | None = None`
  - `model: str | None = None`
  - `base_url: str | None = Field(default=None, exclude=True)`
  - `api_key: str | None = Field(default=None, exclude=True)`
  - `use_a2a: bool = False`
  - `a2a_url: str | None = None`

### `CreateRunRequest`（类，第 58 行）
- 签名：`class CreateRunRequest(BaseModel)`
- 作用：Input accepted by `POST /api/runs`.
- 主要字段/属性：
  - `diff: str`
  - `project_root: str = '.'`
  - `mock_llm: bool = False`
  - `reviewers: list[str] = Field(default_factory=list)`
  - `reviewer_overrides: dict[str, ReviewerOverride] = Field(default_factory=dict)`
  - `runner_config: dict[str, Any] = Field(default_factory=dict)`

### `RunEvent`（类，第 73 行）
- 签名：`class RunEvent(BaseModel)`
- 作用：A small append-only event shown by the frontend timeline.
- 主要字段/属性：
  - `event_id: int`
  - `run_id: str`
  - `type: RunEventType`
  - `message: str`
  - `created_at: str`
  - `node: str | None = None`
  - `payload: dict[str, Any] = Field(default_factory=dict)`

### `RunRecord`（类，第 89 行）
- 签名：`class RunRecord(BaseModel)`
- 作用：Public run state stored in memory and returned to the frontend.
- 主要字段/属性：
  - `run_id: str`
  - `status: RunStatus = 'pending'`
  - `request: CreateRunRequest`
  - `created_at: str`
  - `updated_at: str`
  - `started_at: str | None = None`
  - `finished_at: str | None = None`
  - `final_report: str = ''`
  - `snapshot: dict[str, Any] | None = None`
  - `error: str = ''`
  - `cancel_requested: bool = False`
  - `events: list[RunEvent] = Field(default_factory=list)`

### `RunSummary`（类，第 110 行）
- 签名：`class RunSummary(BaseModel)`
- 作用：Lightweight row used by the history page.
- 主要字段/属性：
  - `run_id: str`
  - `status: RunStatus`
  - `created_at: str`
  - `updated_at: str`
  - `finished_at: str | None = None`
  - `project_root: str = ''`
  - `mock_llm: bool = False`
  - `risk_level: str = 'unknown'`
  - `finding_count: int = 0`
  - `error: str = ''`
  - `cancel_requested: bool = False`

### `RuntimeReviewer`（类，第 130 行）
- 签名：`class RuntimeReviewer(BaseModel)`
- 作用：Reviewer configuration safe to expose to the browser.
- 主要字段/属性：
  - `id: str`
  - `name: str`
  - `provider: str`
  - `model: str`
  - `focus: str = ''`
  - `enabled: bool = True`

### `RuntimeConfig`（类，第 145 行）
- 签名：`class RuntimeConfig(BaseModel)`
- 作用：Sanitized runtime model configuration for frontend labels.
- 主要字段/属性：
  - `reviewers: list[RuntimeReviewer]`
  - `role_models: dict[str, str] = Field(default_factory=dict)`

### `_utc_now`（函数，第 172 行）
- 签名：`def _utc_now() -> str`
- 作用：Return a stable UTC timestamp for run lifecycle fields.

### `_short_id`（函数，第 178 行）
- 签名：`def _short_id() -> str`
- 作用：Generate compact run IDs that are easy to copy from the UI.

### `_jsonable`（函数，第 184 行）
- 签名：`def _jsonable(value: Any) -> Any`
- 作用：Convert workflow objects into primitives that JSON can serialize.

### `_persist_run`（函数，第 204 行）
- 签名：`def _persist_run(record: RunRecord) -> None`
- 作用：Write the latest RunRecord to SQLite.

### `_summary_from_record`（函数，第 214 行）
- 签名：`def _summary_from_record(record: RunRecord) -> RunSummary`
- 作用：Build the compact history row from a full run record.

### `_model_label`（函数，第 235 行）
- 签名：`def _model_label(provider: str, model: str) -> str`
- 作用：Display model identity without exposing any credential-bearing fields.

### `_runtime_config_from_project`（函数，第 241 行）
- 签名：`def _runtime_config_from_project(project_root: Path) -> RuntimeConfig`
- 作用：Build sanitized frontend configuration from the same loader as workflow.

### `_get_run_or_404`（函数，第 268 行）
- 签名：`def _get_run_or_404(run_id: str) -> RunRecord`
- 作用：Read a run under the lock and raise a normal API 404 when missing.

### `_replace_run`（函数，第 284 行）
- 签名：`def _replace_run(run_id: str, **changes: Any) -> RunRecord`
- 作用：Atomically update a run record.

### `_append_event`（函数，第 299 行）
- 签名：`def _append_event(run_id: str, event_type: RunEventType, message: str, *, node: str | None=None, payload: dict[str, Any] | None=None) -> RunEvent`
- 作用：Append one event to a run under the same lock as status updates.

### `_is_cancel_requested`（函数，第 328 行）
- 签名：`def _is_cancel_requested(run_id: str) -> bool`
- 作用：Read the cooperative abort flag without exposing the RUNS dict.

### `_mark_aborted`（函数，第 335 行）
- 签名：`def _mark_aborted(run_id: str, message: str) -> RunRecord`
- 作用：Move a run into the terminal aborted state and record why.

### `_sse_frame`（函数，第 348 行）
- 签名：`def _sse_frame(event: RunEvent) -> str`
- 作用：Encode one RunEvent as a standards-compatible Server-Sent Event frame.

### `_apply_request_config`（函数，第 355 行）
- 签名：`def _apply_request_config(config: ProjectConfig, request: CreateRunRequest) -> None`
- 作用：Apply Web request overrides to the normal project config.

### `_execute_run`（函数，第 391 行）
- 签名：`def _execute_run(run_id: str) -> None`
- 作用：Background worker that turns a pending Run into a final snapshot.

### `health`（函数，第 462 行）
- 签名：`def health() -> dict[str, str]`
- 作用：Small health endpoint used by tests and the frontend dev proxy.

### `get_runtime_config`（函数，第 469 行）
- 签名：`def get_runtime_config(project_root: str=Query(default='.')) -> RuntimeConfig`
- 作用：Return real model labels used by the backend, without secrets.

### `create_run`（函数，第 476 行）
- 签名：`def create_run(request: CreateRunRequest) -> RunRecord`
- 作用：Create a run and start its workflow in the background.

### `get_run`（函数，第 494 行）
- 签名：`def get_run(run_id: str) -> RunRecord`
- 作用：Return the latest known state for a run.

### `list_runs`（函数，第 501 行）
- 签名：`def list_runs(limit: int=Query(default=20, ge=1, le=100), status: RunStatus | None=Query(default=None)) -> list[RunSummary]`
- 作用：Return recent runs for the history page.

### `abort_run`（函数，第 512 行）
- 签名：`def abort_run(run_id: str) -> RunRecord`
- 作用：Request cooperative cancellation for a pending or running run.

### `stream_run_events`（函数，第 526 行）
- 签名：`def stream_run_events(run_id: str, last_event_id: int=Query(default=0)) -> StreamingResponse`
- 作用：Stream run events as SSE until the run reaches a terminal state.

### `export_run`（函数，第 556 行）
- 签名：`def export_run(run_id: str, format: ExportFormat=Query(default='markdown')) -> Any`
- 作用：Export the final report after the run has succeeded.

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
