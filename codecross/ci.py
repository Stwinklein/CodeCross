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
from .reporting import (
    SEVERITY_LABELS,
    build_default_report_presentation,
    confidence_text,
    context_counts,
    merge_advice,
    needs_chinese_fallback,
    risk_text,
    shorten,
    stop_reason_text,
    table_cell,
    test_summary,
    verification_counts,
)
from .schemas import Finding, ReportFindingRow, ReviewerResult
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
    """汇总 reviewer 使用的模型名称，但绝不展示 API key 或 base URL。"""

    reviewers: list[ReviewerResult] = state.get("reviewer_results", [])
    labels = [result.model for result in reviewers if result.model]
    return ", ".join(dict.fromkeys(labels)) or "未记录"


def _render_report_findings_table(rows_to_render: list[ReportFindingRow], limit: int) -> str:
    """Render final-report finding rows into a compact Markdown table."""

    rows_to_render = [
        row
        for row in rows_to_render
        if "反驳" not in (row.verification_status or "")
    ]
    if not rows_to_render:
        return "当前没有剩余已被证据支持或待人工确认的重点问题；已被测试反驳的问题保留在完整 CI 产物中。"

    rows = [
        "| 严重级别 | 验证状态 | 位置 | 问题 | 建议 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows_to_render[:limit]:
        problem = row.problem
        if len(row.finding_ids) > 1:
            problem += f"（已合并 {len(row.finding_ids)} 条相近发现）"
        rows.append(
            "| "
            f"{SEVERITY_LABELS.get(row.severity, row.severity)} | "
            f"{row.verification_status or '未进入验证'} | "
            f"`{row.location or '未知位置'}` | "
            f"{table_cell(problem)} | "
            f"{table_cell(row.suggestion)} |"
        )

    remaining = len(rows_to_render) - limit
    if remaining > 0:
        rows.append(f"\n_还有 {remaining} 条问题在完整 CI 产物里。_")
    return "\n".join(rows)


def _looks_like_code_symbol(value: str) -> bool:
    """Keep identifiers visible, but hide English prose from the PR summary."""

    value = (value or "").strip()
    if not value:
        return False
    return " " not in value and len(value) <= 80


def _display_changed_symbols(symbols: list[str] | None) -> str:
    """Render changed symbols without turning planner English prose into UI copy."""

    if not symbols:
        return "无"
    visible = [
        symbol
        for symbol in symbols[:16]
        if not needs_chinese_fallback(symbol) or _looks_like_code_symbol(symbol)
    ]
    hidden = len(symbols) - len(visible)
    if visible:
        rendered = ", ".join(visible)
        if hidden > 0:
            rendered += f"，另有 {hidden} 条变更说明见 CI 产物"
        return rendered
    return f"{len(symbols)} 条变更说明，完整列表见 CI 产物"


def _reviewer_error_block(state: CodeCrossState) -> str:
    """展示模型调用或传输错误，避免 CI 评论看起来像“正常无问题”。"""

    lines: list[str] = []
    for result in state.get("reviewer_results", []):
        if result.errors:
            lines.append(
                f"- `{result.reviewer_name}`: 审查器返回结果无法解析，"
                "本次已降级使用其他审查结果；完整错误见 CI 产物。"
            )
    if not lines:
        return ""
    return "\n\n**审查器错误**\n" + "\n".join(lines)


def truncate_comment(markdown: str, max_chars: int) -> str:
    """限制评论长度，同时保留用于更新旧评论的 HTML marker。"""

    if len(markdown) <= max_chars:
        return markdown
    suffix = (
        "\n\n---\n"
        "_评论内容已截断，以避免超过 GitHub 评论长度限制。完整结果请查看 CI 产物。_\n"
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
    """生成会被贴到 PR Conversation 的中文 Markdown 报告。

    第一行的 HTML marker 不能删：``github_pr_comment.py`` 会用它找到上一条
    CodeCross 评论并原地更新。这样同一个 PR 多次 push 时，只会保留一条最新报告，
    不会在对话区刷出一长串重复评论。

    报告结构刻意分成四层：
    1. “结论看板”让 reviewer、作者和面试官都能一眼看到风险与合并建议。
    2. “结论摘要”把表格压缩成人能直接转述的中文结论。
    3. “变更与上下文”说明模型到底看到了哪些文件、符号和检索上下文。
    4. “重点问题”只列最重要 findings，完整细节仍保存在 JSON artifact。
    """

    metadata = metadata or PullRequestMetadata()
    if not metadata.workflow_url:
        metadata.workflow_url = _workflow_url()

    plan = state.get("review_plan")
    context = state.get("code_context")
    board = state.get("board_summary")
    evaluator = state.get("evaluator_report")
    findings: list[Finding] = state.get("findings", [])
    presentation = build_default_report_presentation(
        findings=findings,
        board_summary=board,
        evaluator_report=evaluator,
        high_risk_review=state.get("high_risk_review"),
    )

    risk_level = evaluator.risk_level if evaluator else "unknown"
    confidence = evaluator.confidence if evaluator else "medium"
    changed_files = ", ".join(plan.changed_files[:12]) if plan else "无"
    if plan and len(plan.changed_files) > 12:
        changed_files += f", ... (+{len(plan.changed_files) - 12})"
    changed_symbols = _display_changed_symbols(plan.changed_symbols if plan else None)

    mode_label = "演示/Mock 模式" if mock_llm else "真实模型模式"
    lines = [
        COMMENT_MARKER,
        "## CodeCross PR 审查报告",
        "",
        "### 结论看板",
        "",
        "| 项目 | 结果 |",
        "| --- | --- |",
        f"| 风险等级 | **{risk_text(risk_level)}** |",
        f"| 合并建议 | **{merge_advice(risk_level)}** |",
        f"| 置信度 | {confidence_text(confidence)} |",
        f"| 运行模式 | {mode_label} |",
        f"| 审查模型 | {_model_label_from_state(state)} |",
    ]
    if metadata.head_sha:
        lines.append(f"| 提交 | `{metadata.head_sha[:12]}` |")
    if metadata.workflow_url:
        lines.append(f"| CI 运行 | [打开 Actions 运行]({metadata.workflow_url}) |")
    if board:
        lines.extend(
            [
                f"| 问题数 | {board.finding_count} |",
                f"| 测试结果 | {test_summary(board)} |",
                f"| 验证状态 | {verification_counts(board)} |",
                f"| 验证停止原因 | {stop_reason_text(board.stop_reason)} |",
            ]
        )

    lines.extend(
        [
            "",
            "### 结论摘要",
            "",
            presentation.conclusion_summary,
            "",
            "### 建议动作",
            "",
            presentation.recommended_action,
            "",
            "### 变更与上下文",
            "",
            f"- 变更文件：{changed_files or '无'}",
            f"- 变更符号：{changed_symbols or '无'}",
            f"- 检索到的上下文：{context_counts(context)}",
            "",
            "### 重点问题",
            "",
            _render_report_findings_table(
                presentation.finding_rows,
                DEFAULT_SUMMARY_FINDING_LIMIT,
            ),
            _reviewer_error_block(state),
            "",
            "---",
            "_由 CodeCross 生成。API Key 和 Base URL 只从 CI 环境变量读取，不会写入本评论。_",
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
