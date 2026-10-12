from __future__ import annotations

"""Minimal FastAPI surface for CodeCross Web runs.

V1.5 keeps the HTTP layer deliberately small: the browser can create a run,
receive lifecycle events, request a cooperative abort, export the final report,
and recover finished runs from SQLite-backed history.
"""

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import ProjectConfig, load_project_config
from .storage import SQLiteRunStore
from .workflow import WorkflowCancelled, run_workflow


RunStatus = Literal["pending", "running", "succeeded", "failed", "aborted"]
RunEventType = Literal[
    "created",
    "running",
    "node",
    "succeeded",
    "failed",
    "abort_requested",
    "aborted",
]
ExportFormat = Literal["markdown", "json"]


class ReviewerOverride(BaseModel):
    """Per-run reviewer connection settings coming from the frontend.

    `api_key` and `base_url` are excluded from serialization so they can be used
    by the in-memory worker but are not returned by the API or written into the
    SQLite run history.
    """

    provider: str | None = None
    model: str | None = None
    base_url: str | None = Field(default=None, exclude=True)
    api_key: str | None = Field(default=None, exclude=True)
    use_a2a: bool = False
    a2a_url: str | None = None


class CreateRunRequest(BaseModel):
    """Input accepted by `POST /api/runs`.

    The frontend sends the diff plus a few runtime knobs. We keep the shape close
    to the UI controls so the API boundary is obvious when reading App.tsx.
    """

    diff: str
    project_root: str = "."
    mock_llm: bool = False
    reviewers: list[str] = Field(default_factory=list)
    reviewer_overrides: dict[str, ReviewerOverride] = Field(default_factory=dict)
    runner_config: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    """A small append-only event shown by the frontend timeline.

    Event IDs are scoped to one run and increase by one. That simple rule is
    enough for V1.4 SSE clients to avoid replaying messages twice.
    """

    event_id: int
    run_id: str
    type: RunEventType
    message: str
    created_at: str
    node: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RunRecord(BaseModel):
    """Public run state stored in memory and returned to the frontend.

    `snapshot` holds the workflow state after success. It is already converted
    into JSON-safe primitives, which means FastAPI can return it directly.
    """

    run_id: str
    status: RunStatus = "pending"
    request: CreateRunRequest
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    final_report: str = ""
    snapshot: dict[str, Any] | None = None
    error: str = ""
    cancel_requested: bool = False
    events: list[RunEvent] = Field(default_factory=list)


class RunSummary(BaseModel):
    """Lightweight row used by the history page.

    The full RunRecord can be large because it contains the diff, snapshot, and
    event list. History only needs enough information to choose a run.
    """

    run_id: str
    status: RunStatus
    created_at: str
    updated_at: str
    finished_at: str | None = None
    project_root: str = ""
    mock_llm: bool = False
    risk_level: str = "unknown"
    finding_count: int = 0
    error: str = ""
    cancel_requested: bool = False


class RuntimeReviewer(BaseModel):
    """Reviewer configuration safe to expose to the browser.

    API keys and base URLs are intentionally omitted. The UI only needs display
    metadata and the reviewer ID used when creating a run.
    """

    id: str
    name: str
    provider: str
    model: str
    focus: str = ""
    enabled: bool = True


class RuntimeConfig(BaseModel):
    """Sanitized runtime model configuration for frontend labels."""

    reviewers: list[RuntimeReviewer]
    role_models: dict[str, str] = Field(default_factory=dict)


# V1.5 keeps the in-memory dict as a hot cache, while SQLite becomes the durable
# source used when the process restarts or a run falls out of memory.
RUNS: dict[str, RunRecord] = {}
_RUN_LOCK = threading.Lock()
RUN_STORE = SQLiteRunStore.from_environment()


app = FastAPI(title="CodeCross API", version="1.7.0")

# The Vite dev server proxies `/api`, but CORS makes direct manual testing from
# another local origin less surprising during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _utc_now() -> str:
    """Return a stable UTC timestamp for run lifecycle fields."""

    return datetime.now(timezone.utc).isoformat()


def _short_id() -> str:
    """Generate compact run IDs that are easy to copy from the UI."""

    return uuid4().hex[:8]


def _jsonable(value: Any) -> Any:
    """Convert workflow objects into primitives that JSON can serialize.

    LangGraph state contains Pydantic models, dataclasses, lists, and dicts.
    This recursive converter mirrors the CLI behavior while dropping `config`
    because config contains runtime objects and environment-derived fields that
    do not belong in an API response.
    """

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items() if key != "config"}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _persist_run(record: RunRecord) -> None:
    """Write the latest RunRecord to SQLite.

    The API still returns Pydantic models, but storage owns plain JSON dicts so
    it does not need to import API classes and create circular dependencies.
    """

    RUN_STORE.upsert_run(record.model_dump(mode="json"))


def _summary_from_record(record: RunRecord) -> RunSummary:
    """Build the compact history row from a full run record."""

    snapshot = record.snapshot or {}
    evaluator = snapshot.get("evaluator_report") or {}
    board = snapshot.get("board_summary") or {}
    return RunSummary(
        run_id=record.run_id,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finished_at=record.finished_at,
        project_root=record.request.project_root,
        mock_llm=record.request.mock_llm,
        risk_level=str(evaluator.get("risk_level") or "unknown"),
        finding_count=int(board.get("finding_count") or len(snapshot.get("findings") or [])),
        error=record.error,
        cancel_requested=record.cancel_requested,
    )


def _model_label(provider: str, model: str) -> str:
    """Display model identity without exposing any credential-bearing fields."""

    return f"{provider}/{model}"


def _runtime_config_from_project(project_root: Path) -> RuntimeConfig:
    """Build sanitized frontend configuration from the same loader as workflow."""

    config = load_project_config(project_root, mock_llm=False)
    reviewers = [
        RuntimeReviewer(
            id=reviewer.display_name,
            name=reviewer.display_name,
            provider=reviewer.provider,
            model=reviewer.model,
            focus=reviewer.focus,
            enabled=reviewer.enabled,
        )
        for reviewer in config.reviewers
    ]
    role_models = {
        role: _model_label(settings.provider, settings.model)
        for role, settings in config.model_config.items()
    }
    for reviewer in config.reviewers:
        role_models[f"reviewer:{reviewer.display_name}"] = _model_label(
            reviewer.provider,
            reviewer.model,
        )
    return RuntimeConfig(reviewers=reviewers, role_models=role_models)


def _get_run_or_404(run_id: str) -> RunRecord:
    """Read a run under the lock and raise a normal API 404 when missing."""

    with _RUN_LOCK:
        record = RUNS.get(run_id)
    if record is None:
        stored = RUN_STORE.get_run(run_id)
        if stored is not None:
            record = RunRecord.model_validate(stored)
            with _RUN_LOCK:
                RUNS[run_id] = record
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")
    return record


def _replace_run(run_id: str, **changes: Any) -> RunRecord:
    """Atomically update a run record.

    The lock matters because FastAPI request handlers and background workflow
    threads can touch the same in-memory dictionary at the same time.
    """

    with _RUN_LOCK:
        current = RUNS[run_id]
        next_record = current.model_copy(update={**changes, "updated_at": _utc_now()})
        RUNS[run_id] = next_record
    _persist_run(next_record)
    return next_record


def _append_event(
    run_id: str,
    event_type: RunEventType,
    message: str,
    *,
    node: str | None = None,
    payload: dict[str, Any] | None = None,
) -> RunEvent:
    """Append one event to a run under the same lock as status updates."""

    with _RUN_LOCK:
        current = RUNS[run_id]
        event = RunEvent(
            event_id=len(current.events) + 1,
            run_id=run_id,
            type=event_type,
            message=message,
            node=node,
            payload=payload or {},
            created_at=_utc_now(),
        )
        RUNS[run_id] = current.model_copy(
            update={"events": [*current.events, event], "updated_at": _utc_now()}
        )
        next_record = RUNS[run_id]
    _persist_run(next_record)
    return event


def _is_cancel_requested(run_id: str) -> bool:
    """Read the cooperative abort flag without exposing the RUNS dict."""

    with _RUN_LOCK:
        return RUNS.get(run_id).cancel_requested if run_id in RUNS else False


def _mark_aborted(run_id: str, message: str) -> RunRecord:
    """Move a run into the terminal aborted state and record why."""

    _append_event(run_id, "aborted", message, node="workflow")
    return _replace_run(
        run_id,
        status="aborted",
        finished_at=_utc_now(),
        error=message,
        final_report=f"# CodeCross Run Aborted\n\n{message}\n",
    )


def _sse_frame(event: RunEvent) -> str:
    """Encode one RunEvent as a standards-compatible Server-Sent Event frame."""

    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {data}\n\n"


def _apply_request_config(config: ProjectConfig, request: CreateRunRequest) -> None:
    """Apply Web request overrides to the normal project config.

    Reviewer names come from the frontend checkbox IDs and match
    `ModelSettings.display_name`. Runner config values are copied only when the
    dataclass already has that field; unknown keys are ignored so old frontends
    do not accidentally break newer backends.
    """

    if request.reviewers:
        requested = set(request.reviewers)
        for reviewer in config.reviewers:
            reviewer.enabled = reviewer.display_name in requested

    for reviewer in config.reviewers:
        override = request.reviewer_overrides.get(reviewer.display_name)
        if not override:
            continue
        # These overrides live only for this run. Secrets are excluded from
        # serialization by ReviewerOverride, so they are not written to history.
        if override.provider:
            reviewer.provider = override.provider
        if override.model:
            reviewer.model = override.model
        if override.base_url:
            reviewer.base_url_override = override.base_url
        if override.api_key:
            reviewer.api_key_override = override.api_key
        reviewer.a2a_url = override.a2a_url if override.use_a2a and override.a2a_url else None

    runner_config = config.runner_config
    for key, value in request.runner_config.items():
        if key in {"max_verification_rounds", "max_tests_per_round", "test_timeout_seconds", "cross_verification_max_per_round", "cross_verification_timeout_seconds"}:
            setattr(runner_config, key, value)


def _execute_run(run_id: str) -> None:
    """Background worker that turns a pending Run into a final snapshot.

    V1.4 still uses a plain daemon thread because there is only one process and
    no durable queue yet. The new part is cooperative cancellation: the worker
    checks `cancel_requested` between workflow checkpoints.
    """

    record = _get_run_or_404(run_id)
    _replace_run(run_id, status="running", started_at=_utc_now(), error="")
    _append_event(run_id, "running", "Run started.", node="workflow")

    try:
        if _is_cancel_requested(run_id):
            _mark_aborted(run_id, "Run was aborted before workflow execution started.")
            return

        project_root = Path(record.request.project_root).resolve()
        # API 与 CLI 必须使用同一检测契约。monorepo 只看根目录清单会选错语言，
        # 因此把本次请求 diff 一起交给 ProjectDetector。
        config = load_project_config(
            project_root,
            mock_llm=record.request.mock_llm,
            diff=record.request.diff,
        )
        _apply_request_config(config, record.request)
        # The browser/API is not a trusted-local execution control surface. A
        # project .env or request override must never enable host test execution.
        config.runner_config.execution_mode = "docker"

        def on_workflow_event(kind: str, payload: dict[str, Any]) -> None:
            """Translate workflow callbacks into user-visible run events."""

            node = str(payload.get("node") or "workflow")
            message = str(payload.get("message") or kind)
            _append_event(run_id, "node", message, node=node, payload=payload)

        state = run_workflow(
            record.request.diff,
            project_root=project_root,
            config=config,
            on_event=on_workflow_event,
            should_cancel=lambda: _is_cancel_requested(run_id),
        )
        snapshot = _jsonable(state)
        final_report = str(snapshot.get("final_report") or "")

        if _is_cancel_requested(run_id):
            _mark_aborted(run_id, "Run was aborted after workflow produced a partial result.")
            return

        _append_event(
            run_id,
            "succeeded",
            "Run finished successfully.",
            node="final_report",
            payload={"risk_level": snapshot.get("evaluator_report", {}).get("risk_level")},
        )
        _replace_run(
            run_id,
            status="succeeded",
            finished_at=_utc_now(),
            snapshot=snapshot,
            final_report=final_report,
        )
    except WorkflowCancelled as exc:
        _mark_aborted(run_id, str(exc))
    except Exception as exc:  # pragma: no cover - exact branch is tested through API status.
        # API consumers need a concise failure string; the full stack trace still
        # belongs in logs when we add structured logging later.
        _append_event(run_id, "failed", f"{type(exc).__name__}: {exc}", node="workflow")
        _replace_run(
            run_id,
            status="failed",
            finished_at=_utc_now(),
            error=f"{type(exc).__name__}: {exc}",
        )


@app.get("/api/health")
def health() -> dict[str, str]:
    """Small health endpoint used by tests and the frontend dev proxy."""

    return {"status": "ok", "version": "1.6"}


@app.get("/api/config/runtime", response_model=RuntimeConfig)
def get_runtime_config(project_root: str = Query(default=".")) -> RuntimeConfig:
    """Return real model labels used by the backend, without secrets."""

    return _runtime_config_from_project(Path(project_root).resolve())


@app.post("/api/runs", response_model=RunRecord)
def create_run(request: CreateRunRequest) -> RunRecord:
    """Create a run and start its workflow in the background."""

    if not request.diff.strip():
        raise HTTPException(status_code=400, detail="diff must not be empty.")
    allowed_runner_keys = {
        "max_verification_rounds", "max_tests_per_round", "test_timeout_seconds",
        "cross_verification_max_per_round", "cross_verification_timeout_seconds",
    }
    forbidden = sorted(set(request.runner_config) - allowed_runner_keys)
    if forbidden:
        raise HTTPException(status_code=400, detail=f"unsupported runner_config keys: {', '.join(forbidden)}")

    now = _utc_now()
    record = RunRecord(run_id=_short_id(), request=request, created_at=now, updated_at=now)
    with _RUN_LOCK:
        RUNS[record.run_id] = record
    _append_event(record.run_id, "created", "Run created.", node="api")

    thread = threading.Thread(target=_execute_run, args=(record.run_id,), daemon=True)
    thread.start()
    return _get_run_or_404(record.run_id)


@app.get("/api/runs/{run_id}", response_model=RunRecord)
def get_run(run_id: str) -> RunRecord:
    """Return the latest known state for a run."""

    return _get_run_or_404(run_id)


@app.get("/api/runs", response_model=list[RunSummary])
def list_runs(
    limit: int = Query(default=20, ge=1, le=100),
    status: RunStatus | None = Query(default=None),
) -> list[RunSummary]:
    """Return recent runs for the history page."""

    records = [RunRecord.model_validate(item) for item in RUN_STORE.list_runs(limit=limit, status=status)]
    return [_summary_from_record(record) for record in records]


@app.post("/api/runs/{run_id}/abort", response_model=RunRecord)
def abort_run(run_id: str) -> RunRecord:
    """Request cooperative cancellation for a pending or running run."""

    record = _get_run_or_404(run_id)
    if record.status in {"succeeded", "failed", "aborted"}:
        return record

    if not record.cancel_requested:
        _replace_run(run_id, cancel_requested=True)
        _append_event(run_id, "abort_requested", "Abort requested by user.", node="api")
    return _get_run_or_404(run_id)


@app.get("/api/runs/{run_id}/events", response_model=None)
def stream_run_events(
    run_id: str,
    last_event_id: int = Query(default=0),
) -> StreamingResponse:
    """Stream run events as SSE until the run reaches a terminal state."""

    _get_run_or_404(run_id)

    def event_stream():
        sent_event_id = last_event_id
        while True:
            with _RUN_LOCK:
                record = RUNS.get(run_id)
                events = list(record.events) if record else []
                status = record.status if record else "failed"

            for event in events:
                if event.event_id <= sent_event_id:
                    continue
                sent_event_id = event.event_id
                yield _sse_frame(event)

            if status in {"succeeded", "failed", "aborted"}:
                break
            time.sleep(0.4)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/export", response_model=None)
def export_run(
    run_id: str,
    format: ExportFormat = Query(default="markdown"),
) -> Any:
    """Export the final report after the run has succeeded."""

    record = _get_run_or_404(run_id)
    if record.status not in {"succeeded", "aborted"}:
        raise HTTPException(status_code=409, detail="Run is not ready for export.")

    if format == "json":
        return record.snapshot or {}

    return Response(content=record.final_report, media_type="text/markdown; charset=utf-8")
