from __future__ import annotations

"""Minimal A2A client used to call remote reviewer agents.

CodeCross keeps workflow orchestration local, but a reviewer can now be hosted
behind an A2A-compatible HTTP JSON-RPC endpoint. The important boundary is:
CodeCross sends review input and optional per-run LLM config; the remote agent
returns a normal `ReviewerResult`, so the rest of the Board/Executor pipeline is
unchanged.
"""

import json
import urllib.error
import urllib.request
from typing import Any
from uuid import uuid4

from .config import ModelSettings
from .context import CodeContext
from .llm import ModelCallError
from .schemas import ReviewPlan, ReviewerResult


def _http_post_json(url: str, payload: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    """POST JSON with stdlib urllib to avoid adding a new runtime dependency."""

    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise ModelCallError(f"A2A reviewer request failed: {exc}") from exc


def _extract_reviewer_result(response: dict[str, Any]) -> ReviewerResult:
    """Extract ReviewerResult from a minimal A2A JSON-RPC response.

    The demo server returns the result inside `artifacts[0].parts[0].data`, which
    follows the A2A idea of task artifacts while keeping the parser compact.
    """

    if response.get("error"):
        raise ModelCallError(f"A2A reviewer returned error: {response['error']}")
    result = response.get("result") or {}
    artifacts = result.get("artifacts") or []
    for artifact in artifacts:
        for part in artifact.get("parts") or []:
            data = part.get("data")
            if isinstance(data, dict):
                return ReviewerResult.model_validate(data)
    raise ModelCallError("A2A reviewer response did not contain a ReviewerResult artifact.")


def call_a2a_reviewer(
    *,
    reviewer: ModelSettings,
    diff: str,
    review_plan: ReviewPlan,
    code_context: CodeContext | None,
    mock_llm: bool,
) -> ReviewerResult:
    """Call a remote A2A reviewer and normalize the returned reviewer result."""

    if not reviewer.a2a_url:
        raise ModelCallError("Reviewer has no A2A endpoint configured.")

    payload = {
        "jsonrpc": "2.0",
        "id": uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "reviewer": {
                                "name": reviewer.display_name,
                                "provider": reviewer.provider,
                                "model": reviewer.model,
                                "focus": reviewer.focus,
                            },
                            "llm": {
                                "provider": reviewer.provider,
                                "model": reviewer.model,
                                "base_url": reviewer.base_url,
                                "api_key": reviewer.api_key,
                            },
                            "mock_llm": mock_llm,
                            "diff": diff,
                            "review_plan": review_plan.model_dump(mode="json"),
                            "code_context": code_context.model_dump(mode="json") if code_context else None,
                        },
                    }
                ],
            }
        },
    }
    response = _http_post_json(
        reviewer.a2a_url,
        payload,
        timeout_seconds=reviewer.timeout_seconds,
    )
    return _extract_reviewer_result(response)
