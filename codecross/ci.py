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


RISK_LABELS = {
    "critical": "严重风险",
    "high": "高风险",
    "medium": "中风险",
    "low": "低风险",
    "unknown": "未知风险",
}

SEVERITY_LABELS = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}

CONFIDENCE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

VERIFICATION_LABELS = {
    "supported": "已被证据支持",
    "refuted": "已被测试反驳",
    "unverified": "尚未验证",
    "inconclusive": "证据不足",
    "not_testable": "无法自动测试",
}

CONTEXT_KIND_LABELS = {
    "changed_file": "变更文件",
    "changed_hunk": "变更片段",
    "symbol_definition": "符号定义",
    "call_site": "调用点",
    "existing_test": "现有测试",
    "diagnostic": "诊断信息",
}

STOP_REASON_LABELS = {
    "not stopped": "未提前停止",
    "verification_queue_empty": "验证队列已清空",
    "max_verification_rounds_reached": "达到最大验证轮次",
    "no_verification_progress": "本轮验证没有新增进展",
    "verification_queue_has_unresolved_targets": "仍有待验证目标",
}


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


def _context_counts(context: CodeContext | None) -> str:
    """把上下文检索结果压缩成中文计数。

    CodeCross 的上下文检索会把 changed file、changed hunk、调用点、现有测试
    等片段都放进 ``CodeContext.snippets``。PR 评论不适合贴全部代码，所以这里只
    展示“拿到了哪些类型的上下文、各有多少段”，让读者快速判断 reviewer 是否看
    到了足够材料。
    """

    if not context:
        return "未检索到"
    counts: dict[str, int] = {}
    for snippet in context.snippets:
        counts[snippet.kind] = counts.get(snippet.kind, 0) + 1
    return ", ".join(
        f"{CONTEXT_KIND_LABELS.get(kind, kind)} {count}"
        for kind, count in sorted(counts.items())
    ) or "未检索到"


def _shorten(text: str, limit: int = 180) -> str:
    """压缩长文本，避免 GitHub 表格单元格被大段模型输出撑爆。"""

    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def _severity_sort_key(finding: Finding) -> tuple[int, str]:
    """按审查优先级排序，严重问题永远排在低风险问题前面。"""

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (severity_order.get(finding.severity, 9), finding.file_path)


def _risk_text(risk_level: str) -> str:
    """返回给用户看的中文风险等级，同时保留原始枚举方便排查。"""

    label = RISK_LABELS.get(risk_level, risk_level)
    return f"{label} (`{risk_level}`)"


def _confidence_text(confidence: str) -> str:
    """返回中文置信度；未知值直接透传，避免报告因为新枚举崩溃。"""

    return CONFIDENCE_LABELS.get(confidence, confidence)


def _verification_text(status: str) -> str:
    """把 Board 的 finding verification 状态翻译成业务读者能理解的话。"""

    return VERIFICATION_LABELS.get(status, status)


def _stop_reason_text(stop_reason: str) -> str:
    """把 evidence loop 的停止原因翻译成中文。

    ``stop_reason`` 是内部调度信号，直接展示会比较像日志。这里保留原始值的
    同时给出中文解释，方便以后你面试时讲清楚“验证循环为什么停下来”。
    """

    raw = stop_reason or "not stopped"
    label = STOP_REASON_LABELS.get(raw, raw)
    return f"{label} (`{raw}`)"


def _merge_advice(risk_level: str, recommended_action: str) -> str:
    """根据风险等级给出一眼可读的合并建议。

    Evaluator 的 ``recommended_action`` 可能是一段自然语言，不一定适合放在摘要
    第一行。这里用规则先给一个稳定、直观的判断，再在下方保留完整建议。
    """

    if risk_level in {"critical", "high"}:
        return "暂缓合并，先修复高风险问题"
    if risk_level == "medium":
        return "建议修复或人工确认后再合并"
    if risk_level == "low":
        return "可以合并，但建议保留人工抽查"
    return "风险未知，请先查看 CI 日志和完整产物"


def _verification_counts(board: Any) -> str:
    """统计每种 finding 验证状态的数量，帮助读者判断证据强弱。"""

    if not board or not board.finding_verifications:
        return "暂无验证状态"

    counts: dict[str, int] = {}
    for verification in board.finding_verifications.values():
        counts[verification.status] = counts.get(verification.status, 0) + 1
    return ", ".join(
        f"{_verification_text(status)} {count}"
        for status, count in sorted(counts.items())
    )


def _test_summary(board: Any) -> str:
    """把测试执行结果浓缩成一句中文摘要。"""

    if not board:
        return "未生成测试执行摘要"
    return (
        f"通过 {board.passed_count}，失败 {board.failed_count}，"
        f"错误/阻塞 {board.error_count}，跳过 {board.skipped_count}"
    )


def _render_findings_table(findings: list[Finding], board: Any, limit: int) -> str:
    """渲染最重要的 findings。

    表格只展示前 ``limit`` 条，是为了让 PR 首页评论保持可读；完整 JSON 仍会作为
    CI artifact 上传。验证状态来自 Review Board，而不是 reviewer 自己判断，这
    能体现 CodeCross “reviewer 提假设、board 管证据”的核心设计。
    """

    if not findings:
        return "Reviewer 没有产出 finding。"

    rows = [
        "| 严重级别 | 验证状态 | 位置 | 问题 | 建议 |",
        "| --- | --- | --- | --- | --- |",
    ]
    verifications = board.finding_verifications if board else {}
    for finding in sorted(findings, key=_severity_sort_key)[:limit]:
        line = finding.line_range.start
        file_label = finding.file_path if line is None else f"{finding.file_path}:{line}"
        verification = verifications.get(finding.finding_id)
        verification_status = (
            _verification_text(verification.status) if verification else "未进入验证"
        )
        rows.append(
            "| "
            f"{SEVERITY_LABELS.get(finding.severity, finding.severity)} | "
            f"{verification_status} | "
            f"`{file_label}` | "
            f"{_shorten(finding.problem)} | "
            f"{_shorten(finding.suggestion)} |"
        )

    remaining = len(findings) - limit
    if remaining > 0:
        rows.append(f"\n_还有 {remaining} 条 finding 在完整 CI artifact 里。_")
    return "\n".join(rows)


def _reviewer_error_block(state: CodeCrossState) -> str:
    """展示模型调用或传输错误，避免 CI 评论看起来像“正常无问题”。"""

    lines: list[str] = []
    for result in state.get("reviewer_results", []):
        if result.errors:
            joined = "; ".join(result.errors)
            lines.append(f"- `{result.reviewer_name}`: {_shorten(joined, 260)}")
    if not lines:
        return ""
    return "\n\n**Reviewer 错误**\n" + "\n".join(lines)


def truncate_comment(markdown: str, max_chars: int) -> str:
    """限制评论长度，同时保留用于更新旧评论的 HTML marker。"""

    if len(markdown) <= max_chars:
        return markdown
    suffix = (
        "\n\n---\n"
        "_评论内容已截断，以避免超过 GitHub 评论长度限制。完整结果请查看 CI artifact。_\n"
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

    报告结构刻意分成三层：
    1. “结论看板”让 reviewer、作者和面试官都能一眼看到风险与合并建议。
    2. “变更与上下文”说明模型到底看到了哪些文件、符号和检索上下文。
    3. “重点问题”只列最重要 findings，完整细节仍保存在 JSON artifact。
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
    recommended_action = evaluator.recommended_action if evaluator else "请人工查看 CI artifact。"
    changed_files = ", ".join(plan.changed_files[:12]) if plan else "无"
    if plan and len(plan.changed_files) > 12:
        changed_files += f", ... (+{len(plan.changed_files) - 12})"
    changed_symbols = ", ".join(plan.changed_symbols[:16]) if plan else "无"
    if plan and len(plan.changed_symbols) > 16:
        changed_symbols += f", ... (+{len(plan.changed_symbols) - 16})"

    mode_label = "演示/Mock 模式" if mock_llm else "真实模型模式"
    lines = [
        COMMENT_MARKER,
        "## CodeCross PR 审查报告",
        "",
        "### 结论看板",
        "",
        "| 项目 | 结果 |",
        "| --- | --- |",
        f"| 风险等级 | **{_risk_text(risk_level)}** |",
        f"| 合并建议 | **{_merge_advice(risk_level, recommended_action)}** |",
        f"| 置信度 | {_confidence_text(confidence)} |",
        f"| 运行模式 | {mode_label} |",
        f"| Reviewer 模型 | {_model_label_from_state(state)} |",
    ]
    if metadata.head_sha:
        lines.append(f"| Commit | `{metadata.head_sha[:12]}` |")
    if metadata.workflow_url:
        lines.append(f"| CI 运行 | [打开 Actions 运行]({metadata.workflow_url}) |")
    if board:
        lines.extend(
            [
                f"| Findings | {board.finding_count} |",
                f"| 测试结果 | {_test_summary(board)} |",
                f"| 验证状态 | {_verification_counts(board)} |",
                f"| 验证停止原因 | {_stop_reason_text(board.stop_reason)} |",
            ]
        )

    lines.extend(
        [
            "",
            "### 建议动作",
            "",
            recommended_action,
            "",
            "### 变更与上下文",
            "",
            f"- 变更文件：{changed_files or '无'}",
            f"- 变更符号：{changed_symbols or '无'}",
            f"- 检索到的上下文：{_context_counts(context)}",
            "",
            "### 重点问题",
            "",
            _render_findings_table(findings, board, DEFAULT_SUMMARY_FINDING_LIMIT),
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
