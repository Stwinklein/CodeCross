from __future__ import annotations

"""GitHub CI helpers for CodeCross pull request review.

The normal CLI prints a full Markdown report for humans. CI needs one extra
layer: it has to turn the workflow state into a bounded PR comment that can be
updated on every push. This module deliberately does not call the GitHub API;
it only runs CodeCross and writes local artifacts. The network write is kept in
``scripts/github_pr_comment.py`` so the review engine and the GitHub transport
can be tested independently.
"""

import argparse
import json
import os
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import ModelSettings, ProjectConfig, load_project_config
from .schemas import CodeContext, Finding, ReviewerResult
from .workflow import CodeCrossState, run_workflow


COMMENT_MARKER = "<!-- codecross-pr-review -->"
DEFAULT_MAX_COMMENT_CHARS = 60_000
DEFAULT_SUMMARY_FINDING_LIMIT = 10


@dataclass(slots=True)
class PullRequestMetadata:
    """Small set of GitHub fields that make the PR comment self-describing."""

    repository: str = ""
    pr_number: int | None = None
    head_sha: str = ""
    base_ref: str = ""
    head_ref: str = ""
    workflow_url: str = ""


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse common CI boolean spellings from environment variables."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _jsonable(value: Any) -> Any:
    """Convert workflow state objects into JSON-safe primitives.

    CI artifacts are useful for debugging failed comments, so we keep an
    optional JSON dump path. Pydantic models and dataclasses need explicit
    conversion before ``json.dumps`` can serialize them.
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


def load_github_metadata(event_path: str | None = None) -> PullRequestMetadata:
    """Load PR metadata from the GitHub Actions event payload.

    The workflow passes the diff as a file, but the comment should still show
    which PR/commit was reviewed. GitHub writes that information into
    ``GITHUB_EVENT_PATH`` for every action run.
    """

    metadata = PullRequestMetadata(
        repository=os.getenv("GITHUB_REPOSITORY", ""),
        head_sha=os.getenv("GITHUB_SHA", ""),
    )
    raw_event_path = event_path or os.getenv("GITHUB_EVENT_PATH", "")
    if not raw_event_path:
        return metadata

    path = Path(raw_event_path)
    if not path.exists():
        return metadata

    payload = json.loads(path.read_text(encoding="utf-8"))
    pull_request = payload.get("pull_request") or {}
    if pull_request:
        metadata.pr_number = int(pull_request.get("number") or payload.get("number") or 0) or None
        metadata.head_sha = (pull_request.get("head") or {}).get("sha") or metadata.head_sha
        metadata.base_ref = (pull_request.get("base") or {}).get("ref") or ""
        metadata.head_ref = (pull_request.get("head") or {}).get("ref") or ""
    elif payload.get("number"):
        metadata.pr_number = int(payload["number"])
    return metadata


def _workflow_url() -> str:
    """Build a clickable Actions run URL when GitHub exposes enough env vars."""

    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    if not repo or not run_id:
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"


def _model_label_from_state(state: CodeCrossState) -> str:
    """Summarize reviewer models without exposing API keys or base URLs."""

    reviewers: list[ReviewerResult] = state.get("reviewer_results", [])
    labels = [result.model for result in reviewers if result.model]
    return ", ".join(dict.fromkeys(labels)) or "none"


def _context_counts(context: CodeContext | None) -> str:
    """Render context snippet counts in a compact, reviewer-friendly shape."""

    if not context:
        return "none"
    counts: dict[str, int] = {}
    for snippet in context.snippets:
        counts[snippet.kind] = counts.get(snippet.kind, 0) + 1
    return ", ".join(f"{kind}: {count}" for kind, count in sorted(counts.items())) or "none"


def _shorten(text: str, limit: int = 180) -> str:
    """Keep table cells readable in a GitHub comment."""

    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def _severity_sort_key(finding: Finding) -> tuple[int, str]:
    """Sort findings by practical review urgency."""

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (severity_order.get(finding.severity, 9), finding.file_path)


def _render_findings_table(findings: list[Finding], limit: int) -> str:
    """Render the most important findings as a Markdown table."""

    if not findings:
        return "No findings were produced by the reviewers."

    rows = [
        "| Severity | File | Problem | Suggestion |",
        "| --- | --- | --- | --- |",
    ]
    for finding in sorted(findings, key=_severity_sort_key)[:limit]:
        line = finding.line_range.start
        file_label = finding.file_path if line is None else f"{finding.file_path}:{line}"
        rows.append(
            "| "
            f"{finding.severity} | "
            f"`{file_label}` | "
            f"{_shorten(finding.problem)} | "
            f"{_shorten(finding.suggestion)} |"
        )

    remaining = len(findings) - limit
    if remaining > 0:
        rows.append(f"\n_{remaining} more finding(s) are in the full CI artifact._")
    return "\n".join(rows)


def _reviewer_error_block(state: CodeCrossState) -> str:
    """Make model or transport failures visible instead of silently hiding them."""

    lines: list[str] = []
    for result in state.get("reviewer_results", []):
        if result.errors:
            joined = "; ".join(result.errors)
            lines.append(f"- `{result.reviewer_name}`: {_shorten(joined, 260)}")
    if not lines:
        return ""
    return "\n\n**Reviewer Errors**\n" + "\n".join(lines)


def truncate_comment(markdown: str, max_chars: int) -> str:
    """Bound comment size while preserving the marker needed for future updates."""

    if len(markdown) <= max_chars:
        return markdown
    suffix = (
        "\n\n---\n"
        "_Comment truncated to stay under the configured GitHub comment size. "
        "Open the CI artifact for the full report._\n"
    )
    budget = max(max_chars - len(suffix), len(COMMENT_MARKER) + 32)
    return markdown[:budget].rstrip() + suffix


def build_pr_comment(
    state: CodeCrossState,
    *,
    metadata: PullRequestMetadata | None = None,
    mock_llm: bool = False,
    max_chars: int = DEFAULT_MAX_COMMENT_CHARS,
) -> str:
    """Create the Markdown body that will be posted to the PR conversation.

    The first line is a stable HTML marker. The update script searches for this
    marker and patches the old comment instead of adding a new comment on every
    commit, which keeps noisy PRs readable.
    """

    metadata = metadata or PullRequestMetadata()
    if not metadata.workflow_url:
        metadata.workflow_url = _workflow_url()

    plan = state.get("review_plan")
    context = state.get("code_context")
    board = state.get("board_summary")
    evaluator = state.get("evaluator_report")
    findings: list[Finding] = state.get("findings", [])

    risk_level = evaluator.risk_level if evaluator else "unknown"
    confidence = evaluator.confidence if evaluator else "medium"
    recommended_action = evaluator.recommended_action if evaluator else "Review CI artifacts manually."
    changed_files = ", ".join(plan.changed_files[:12]) if plan else "none"
    if plan and len(plan.changed_files) > 12:
        changed_files += f", ... (+{len(plan.changed_files) - 12})"
    changed_symbols = ", ".join(plan.changed_symbols[:16]) if plan else "none"
    if plan and len(plan.changed_symbols) > 16:
        changed_symbols += f", ... (+{len(plan.changed_symbols) - 16})"

    mode_label = "mock/demo mode" if mock_llm else "real model mode"
    lines = [
        COMMENT_MARKER,
        "## CodeCross PR Review",
        "",
        f"- Mode: **{mode_label}**",
        f"- Risk: **{risk_level}** ({confidence} confidence)",
        f"- Reviewers: {_model_label_from_state(state)}",
        f"- Changed files: {changed_files or 'none'}",
        f"- Changed symbols: {changed_symbols or 'none'}",
        f"- Retrieved context: {_context_counts(context)}",
    ]
    if metadata.head_sha:
        lines.append(f"- Commit: `{metadata.head_sha[:12]}`")
    if metadata.workflow_url:
        lines.append(f"- CI run: [open run]({metadata.workflow_url})")
    if board:
        lines.extend(
            [
                f"- Findings: {board.finding_count}",
                f"- Tests: {board.passed_count} passed, {board.failed_count} failed, "
                f"{board.error_count} error/blocked",
                f"- Stop reason: `{board.stop_reason or 'not stopped'}`",
            ]
        )

    lines.extend(
        [
            "",
            "**Recommended Action**",
            "",
            recommended_action,
            "",
            "**Top Findings**",
            "",
            _render_findings_table(findings, DEFAULT_SUMMARY_FINDING_LIMIT),
            _reviewer_error_block(state),
            "",
            "---",
            "_Generated by CodeCross. Secrets are read from CI environment variables "
            "and are never rendered into this comment._",
        ]
    )
    return truncate_comment("\n".join(lines), max_chars)


def _required_settings_for_ci(config: ProjectConfig) -> list[ModelSettings]:
    """Return model roles that CI should verify before starting.

    The workflow can continue if optional high-risk follow-up models are absent,
    but planner, evaluator, reviewer and test-generation roles are part of the
    normal path. Checking them up front prevents a PR comment that looks green
    only because every model silently fell back or errored.
    """

    required_role_names = {
        "planner",
        "test_strategy_agent",
        "test_case_agent",
        "test_author_agent",
        "test_result_analyzer",
        "coverage_gap_agent",
        "evaluator",
    }
    settings = [
        setting
        for role, setting in config.model_config.items()
        if role in required_role_names
    ]
    settings.extend(config.enabled_reviewers())
    return settings


def validate_model_credentials(config: ProjectConfig) -> None:
    """Fail fast when real CI review is requested without model credentials."""

    missing: list[str] = []
    for setting in _required_settings_for_ci(config):
        if setting.api_key_env and not setting.api_key:
            missing.append(f"{setting.display_name}:{setting.api_key_env}")
    if missing:
        joined = ", ".join(sorted(dict.fromkeys(missing)))
        raise RuntimeError(
            "CodeCross CI is configured for real model mode but required model "
            f"credentials are missing: {joined}. Set CODECROSS_MOCK_LLM=true "
            "for a demo-only comment, or add the listed repository secrets."
        )


def run_ci_review(
    *,
    diff: str,
    project_root: Path,
    mock_llm: bool,
    require_models: bool,
) -> CodeCrossState:
    """Load config and run the normal CodeCross workflow for CI."""

    config = load_project_config(project_root, mock_llm=mock_llm)
    if require_models and not mock_llm:
        validate_model_credentials(config)
    return run_workflow(diff, project_root=project_root, config=config)


def read_diff_file(path: str | Path) -> str:
    """Read a unified diff and tolerate a UTF-8 BOM.

    Git itself does not write a BOM, but Windows tooling such as older
    PowerShell redirection can. If the first line starts with a BOM, the diff
    parser no longer sees ``diff --git`` at column zero, so CI normalizes it at
    the boundary.
    """

    return Path(path).read_text(encoding="utf-8").lstrip("\ufeff")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Define the small CI command-line surface."""

    parser = argparse.ArgumentParser(description="Run CodeCross for a GitHub pull request.")
    parser.add_argument("--diff", required=True, help="Path to the pull request unified diff.")
    parser.add_argument("--project-root", default=".", help="Repository root being reviewed.")
    parser.add_argument("--event-path", help="GitHub event JSON path. Defaults to GITHUB_EVENT_PATH.")
    parser.add_argument("--output-comment", required=True, help="Where to write the PR comment body.")
    parser.add_argument("--output-json", help="Optional JSON artifact path for the full workflow state.")
    parser.add_argument("--max-comment-chars", type=int, default=DEFAULT_MAX_COMMENT_CHARS)
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        default=_env_bool("CODECROSS_MOCK_LLM"),
        help="Use deterministic demo agents. Intended only for CI smoke tests.",
    )
    parser.add_argument(
        "--skip-model-preflight",
        action="store_true",
        default=_env_bool("CODECROSS_SKIP_MODEL_PREFLIGHT"),
        help="Do not fail early when model credentials are missing.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point used by GitHub Actions."""

    args = parse_args(argv)
    project_root = Path(args.project_root).resolve()
    diff = read_diff_file(args.diff)
    metadata = load_github_metadata(args.event_path)

    state = run_ci_review(
        diff=diff,
        project_root=project_root,
        mock_llm=args.mock_llm,
        require_models=not args.skip_model_preflight,
    )
    comment = build_pr_comment(
        state,
        metadata=metadata,
        mock_llm=args.mock_llm,
        max_chars=args.max_comment_chars,
    )
    Path(args.output_comment).write_text(comment, encoding="utf-8")

    if args.output_json:
        Path(args.output_json).write_text(
            json.dumps(_jsonable(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
