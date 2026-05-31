"""FastAPI smoke tests for the Web run loop.

These tests exercise the same contract the React frontend uses: create a run,
observe events, optionally abort it, then export the result.
"""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codecross.api import RUNS, RUN_STORE, CreateRunRequest, RunRecord, _RUN_LOCK, _utc_now, app


@pytest.fixture(autouse=True)
def clear_run_store() -> Iterator[None]:
    """Keep tests independent across the memory cache and SQLite history."""

    with _RUN_LOCK:
        RUNS.clear()
    RUN_STORE.clear()
    yield
    with _RUN_LOCK:
        RUNS.clear()
    RUN_STORE.clear()


def _wait_for_terminal_run(client: TestClient, run_id: str) -> dict:
    """Poll the API the same way the frontend does.

    The workflow runs in a background thread, so the test waits briefly instead
    of assuming the result is ready immediately after POST.
    """

    deadline = time.time() + 15
    last_payload: dict = {}
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] in {"succeeded", "failed", "aborted"}:
            return last_payload
        time.sleep(0.1)
    pytest.fail(f"Run {run_id} did not finish in time. Last payload: {last_payload}")


def test_health_endpoint_reports_v1_6():
    """The frontend can use this endpoint to check whether the API is reachable."""

    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "1.6"}


def test_runtime_config_uses_env_models_without_exposing_keys(tmp_path: Path):
    """Frontend config should mirror .env model names but never leak secrets."""

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=secret_deepseek",
                "DEEPSEEK_MODEL=deepseek-test",
                "ANTHROPIC_API_KEY=secret_anthropic",
                "ANTHROPIC_MODEL=claude-opus-4-6",
            ]
        ),
        encoding="utf-8",
    )
    client = TestClient(app)

    response = client.get(f"/api/config/runtime?project_root={tmp_path}")

    assert response.status_code == 200
    payload = response.json()
    security = next(
        reviewer
        for reviewer in payload["reviewers"]
        if reviewer["id"] == "security_maintainability_reviewer"
    )
    assert security["model"] == "claude-opus-4-6"
    assert payload["role_models"]["reviewer:security_maintainability_reviewer"] == (
        "anthropic/claude-opus-4-6"
    )
    assert "secret_" not in response.text


def test_reviewer_override_secrets_are_not_returned_or_persisted():
    """Per-run API keys may be used in memory, but must not leak to clients/history."""

    client = TestClient(app)
    response = client.post(
        "/api/runs",
        json={
            "diff": "diff --git a/a.py b/a.py\n+++ b/a.py\n+def f():\n+    return 1\n",
            "mock_llm": True,
            "reviewers": ["logic_reviewer"],
            "reviewer_overrides": {
                "logic_reviewer": {
                    "provider": "deepseek",
                    "model": "deepseek-chat",
                    "base_url": "https://secret-gateway.example",
                    "api_key": "secret-reviewer-key",
                }
            },
        },
    )

    assert response.status_code == 200
    assert "secret-reviewer-key" not in response.text
    assert "secret-gateway" not in response.text
    run_id = response.json()["run_id"]
    final_payload = _wait_for_terminal_run(client, run_id)
    assert final_payload["status"] == "succeeded"
    assert "secret-reviewer-key" not in client.get(f"/api/runs/{run_id}").text
    assert "secret-reviewer-key" not in client.get("/api/runs?limit=10").text


def test_missing_run_returns_404():
    """Unknown run IDs should be a normal API miss, not a server error."""

    client = TestClient(app)

    response = client.get("/api/runs/not_here")

    assert response.status_code == 404


def test_abort_missing_run_returns_404():
    """Abort should use the same not-found semantics as normal run lookup."""

    client = TestClient(app)

    response = client.post("/api/runs/not_here/abort")

    assert response.status_code == 404


def test_create_poll_events_and_export_demo_run():
    """Browser-created run produces a snapshot plus lifecycle events."""

    root = Path.cwd()
    diff = (root / "examples" / "diffs" / "safe_divide.diff").read_text(encoding="utf-8")
    client = TestClient(app)

    create_response = client.post(
        "/api/runs",
        json={
            "diff": diff,
            "project_root": str(root),
            "mock_llm": True,
            "reviewers": ["logic_reviewer", "security_maintainability_reviewer"],
            "runner_config": {"max_verification_rounds": 3, "test_timeout_seconds": 20},
        },
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]

    final_payload = _wait_for_terminal_run(client, run_id)

    assert final_payload["status"] == "succeeded", final_payload.get("error")
    assert final_payload["snapshot"]["evaluator_report"]["risk_level"] == "high"
    assert "CodeCross 最终审查报告" in final_payload["final_report"]
    event_types = [event["type"] for event in final_payload["events"]]
    assert "created" in event_types
    assert "running" in event_types
    assert "succeeded" in event_types

    events_response = client.get(f"/api/runs/{run_id}/events")
    assert events_response.status_code == 200
    assert "event: succeeded" in events_response.text

    history_response = client.get("/api/runs?limit=5")
    assert history_response.status_code == 200
    history = history_response.json()
    assert history[0]["run_id"] == run_id
    assert history[0]["risk_level"] == "high"

    markdown_response = client.get(f"/api/runs/{run_id}/export?format=markdown")
    assert markdown_response.status_code == 200
    assert "CodeCross 最终审查报告" in markdown_response.text

    json_response = client.get(f"/api/runs/{run_id}/export?format=json")
    assert json_response.status_code == 200
    assert json_response.json()["evaluator_report"]["risk_level"] == "high"

    with _RUN_LOCK:
        RUNS.clear()
    restored_response = client.get(f"/api/runs/{run_id}")
    assert restored_response.status_code == 200
    assert restored_response.json()["run_id"] == run_id


def test_abort_marks_pending_run_as_cancel_requested():
    """Abort is cooperative: pending/running records get a durable cancel flag."""

    client = TestClient(app)
    now = _utc_now()
    record = RunRecord(
        run_id="manual_abort",
        request=CreateRunRequest(diff="diff --git a/a.py b/a.py\n"),
        created_at=now,
        updated_at=now,
    )
    with _RUN_LOCK:
        RUNS[record.run_id] = record

    response = client.post("/api/runs/manual_abort/abort")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cancel_requested"] is True
    assert payload["events"][-1]["type"] == "abort_requested"


def test_abort_after_success_does_not_destroy_result():
    """A finished run should stay succeeded if the user clicks abort afterward."""

    client = TestClient(app)
    now = _utc_now()
    record = RunRecord(
        run_id="already_done",
        status="succeeded",
        request=CreateRunRequest(diff="diff --git a/a.py b/a.py\n"),
        created_at=now,
        updated_at=now,
        finished_at=now,
        final_report="# Done",
        snapshot={"final_report": "# Done"},
    )
    with _RUN_LOCK:
        RUNS[record.run_id] = record

    response = client.post("/api/runs/already_done/abort")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["cancel_requested"] is False
