from __future__ import annotations

"""Convert CodeCross review output to SWE-PRBench v0.4.1 AgentOutput JSON.

The official harness owns final judging and aggregation. This module exports
its input contract only; it never labels a local custom-judge score official.
"""

import json
import re
from typing import Any

from .models import EvaluationCase, PredictedFinding


def _diff_line_numbers(diff: str) -> set[int]:
    # Mirrors eval_harness.runner._extract_diff_line_numbers (v0.4.1).
    lines: set[int] = set()
    current: int | None = None
    for line in diff.splitlines():
        match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            current = int(match.group(1))
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            lines.add(current)
            current += 1
        elif current is not None and not line.startswith("-"):
            current += 1
    return lines


def official_agent_output(
    case: EvaluationCase,
    predictions: list[PredictedFinding],
    *,
    context_config: str,
    model_label: str,
    parse_error: str | None = None,
) -> dict[str, Any]:
    """Return the exact public harness AgentOutput field shape for one PR."""

    if context_config not in {"config_A", "config_B", "config_C"}:
        raise ValueError("invalid SWE-PRBench context config")
    if len(predictions) > 30:
        raise ValueError("official harness supports at most 30 agent comments per PR")
    task_id = str(case.metadata.get("task_id") or "")
    if not task_id:
        raise ValueError("SWE-PRBench task_id is required")
    line_numbers = _diff_line_numbers(case.diff)
    severity_map = {"critical": "P0", "high": "P1", "medium": "P2", "low": "P2"}
    comments: list[dict[str, Any]] = []
    raw: list[dict[str, Any]] = []
    if parse_error is None:
        for index, finding in enumerate(predictions):
            body = finding.description.strip()
            if not body:
                raise ValueError("empty CodeCross finding cannot be exported")
            line = finding.line_start
            severity = severity_map.get(finding.severity)
            comments.append({
                "comment_id": f"{task_id}_{context_config}_{index}",
                "body": body,
                "file_reference": finding.file_path or None,
                "line_reference": line,
                "severity_claim": severity,
                "is_outside_diff": line is not None and line not in line_numbers,
            })
            raw.append({"body": body, "file": finding.file_path or None, "line": line, "severity": severity})
    return {
        "task_id": task_id,
        "config_name": context_config,
        "model": model_label,
        "raw_response": json.dumps(raw, ensure_ascii=False),
        "comments": comments,
        "parse_success": parse_error is None,
        "parse_error": parse_error,
    }
