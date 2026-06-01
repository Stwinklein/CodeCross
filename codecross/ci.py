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
from .schemas import (
    BoardSummary,
    Finding,
    LocalizedFindingCell,
    ReviewerResult,
)
from .workflow import CodeCrossState, run_workflow


COMMENT_MARKER = "<!-- codecross-pr-review -->"
DEFAULT_MAX_COMMENT_CHARS = 60_000

# 方案 B：PR 评论双语 finding 表格的最大行数。
# 选 10 是因为 Markdown 表格在 GitHub Web 端 / 邮件通知里再多就不好读了；
# 真正需要看完整 finding 的人可以下载 JSON artifact。和 _render_key_findings
# 默认值保持一致，避免英文 final report 和 PR 评论的"前 10 条"不是同一组。
DEFAULT_PR_TABLE_FINDING_LIMIT = 10

# 严重等级显示用的中英映射。这里把它放到模块级常量是为了让 PR 评论的
# 中文/英文表格永远输出同一组标签——不让 LLM 翻译这些枚举值，避免
# "严重 / Critical / 严重风险"这种不一致出现在不同 PR 上。
SEVERITY_LABELS_EN: dict[str, str] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}
SEVERITY_LABELS_ZH: dict[str, str] = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
}
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

# 验证状态显示标签。和 schemas.VerificationStatus 字面量保持一一对应。
# 命名口径：英文表格用 schema 原值，中文表格用人类可读的短词。
VERIFICATION_LABELS_EN: dict[str, str] = {
    "unverified": "unverified",
    "supported": "supported",
    "refuted": "refuted",
    "inconclusive": "inconclusive",
    "not_testable": "not testable",
    "cross_supported": "cross-supported",
    "cross_refuted": "cross-refuted",
    "cross_inconclusive": "cross-inconclusive",
    "needs_human_review": "needs human review",
}
VERIFICATION_LABELS_ZH: dict[str, str] = {
    "unverified": "未验证",
    "supported": "已被证据支持",
    "refuted": "已被证据反驳",
    "inconclusive": "证据不足",
    "not_testable": "无法测试",
    "cross_supported": "模型交叉支持",
    "cross_refuted": "模型交叉反驳",
    "cross_inconclusive": "模型交叉无定论",
    "needs_human_review": "需人工复核",
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
    """Summarize reviewer model names without exposing API keys or base URLs."""

    reviewers: list[ReviewerResult] = state.get("reviewer_results", [])
    labels = [result.model for result in reviewers if result.model]
    return ", ".join(dict.fromkeys(labels)) or "not recorded"


def _reviewer_error_block(state: CodeCrossState) -> str:
    """Show model/transport errors without leaking raw provider details."""

    lines: list[str] = []
    for result in state.get("reviewer_results", []):
        if result.errors:
            lines.append(
                f"- `{result.reviewer_name}` returned errors; the workflow kept "
                "other available review evidence. See the JSON artifact for details."
            )
    if not lines:
        return ""
    return "\n\n### Reviewer Errors\n\n" + "\n".join(lines)


def truncate_comment(markdown: str, max_chars: int) -> str:
    """Limit comment length while keeping the HTML marker used for updates."""

    if len(markdown) <= max_chars:
        return markdown
    suffix = (
        "\n\n---\n"
        "_Comment truncated to stay under GitHub's comment length limit. "
        "See the CI artifact for the complete result._\n"
    )
    budget = max(max_chars - len(suffix), len(COMMENT_MARKER) + 32)
    return markdown[:budget].rstrip() + suffix


# ---------------------------------------------------------------------------
# 方案 B：PR 评论双语 finding 表格渲染
# ---------------------------------------------------------------------------
# 设计原则（核心，看代码先看这段注释）：
#   1. **结构由代码决定**：表格的列数、表头分隔线、每一行的列数都由本地
#      代码生成，永远不会被 LLM 误改。LLM 只负责翻译三个长字段的中文版
#      （problem/impact/suggestion）。
#   2. **中英行序严格对齐**：先做一次"去重 + 排序 + 截断"，得到一份
#      ``finding_rows``，英文表格、中文表格都从这一份遍历。这样可以保证
#      "中文表格第 3 行" == "英文表格第 3 行"，方便用户对照。
#   3. **缺翻译就回退英文**：如果 finding cells localizer 没翻、或某条
#      finding 缺翻译，中文表格会直接显示英文短文本。这条 row 仍然存在，
#      不会留空，避免出现"中文表格少一行"的诡异情况。
#   4. **单元格防御性转义**：finding 文本里如果出现 ``|``、``\n``，会破坏
#      Markdown 表格语法，所以单独有一个 _escape_table_cell 处理。
# ---------------------------------------------------------------------------


def _line_location(finding: Finding) -> str:
    """与 agents._line_location 对应的轻量本地实现。

    放一份在 ci.py 是为了避免 ci -> agents 的循环依赖（agents 依赖
    config，ci 也依赖 config，把渲染函数共享在 agents 会让导入图变复杂）。
    返回值要保持稳定：``path`` / ``path:start`` / ``path:start-end``。
    """

    file_path = finding.file_path or "unknown location"
    start = finding.line_range.start
    if start is None:
        return file_path
    end = finding.line_range.end
    if end is None or end == start:
        return f"{file_path}:{start}"
    return f"{file_path}:{start}-{end}"


def _flatten_table_text(text: str, fallback: str = "—") -> str:
    """把多行/多余空白压成单行，专给 Markdown 表格单元格用。

    表格单元格不允许真换行（GitHub 渲染器看到 ``\\n`` 会断行 / 折掉单元格），
    所以这里和 agents._short_text 不一样——这里要用最严格的"全部压成一行"，
    保留 _short_text 的"段落感"反而会破坏表格。
    """

    if text is None:
        return fallback
    cleaned = " ".join(str(text).split())
    return cleaned or fallback


def _escape_table_cell(text: str) -> str:
    """转义单元格里可能破坏 Markdown 表格的字符。

    具体来说：
      * ``|`` 会被表格解析器当列分隔符，必须替换或转义。这里用反斜杠转义
        （``\\|``），GitHub 会渲染成普通 ``|``。
      * ``\\r`` / ``\\n`` 在 Markdown 表格里会断行，统一替换为空格。
    （\\t 不会破坏表格但读起来奇怪，顺便压成空格。）
    """

    return (
        text.replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace("|", "\\|")
    )


def _verification_status_for(finding: Finding, board_summary: BoardSummary) -> str:
    """从 BoardSummary 里查这条 finding 的当前验证状态。"""

    verification = board_summary.finding_verifications.get(finding.finding_id)
    return verification.status if verification else "unverified"


def _select_table_findings(
    findings: list[Finding],
    board_summary: BoardSummary,
    *,
    limit: int = DEFAULT_PR_TABLE_FINDING_LIMIT,
) -> list[Finding]:
    """挑出 PR 评论双语表格要展示的 finding 子集。

    这一步是"准备结构化数据"，**完全不依赖翻译产物**——这样英文/中文
    表格一定从同一组 finding 出发，行数一致、行序一致。规则：
      1. **过滤**：board 已经把它判为 refuted / cross_refuted 的，不展示
         （和 _render_key_findings 一致，避免假阳性占据有限的表格行）。
      2. **去重**：用 (severity, file, line range, category) 当 key，多个
         reviewer 命中同一处时只显示第一条，后面的并入"similar findings
         merged" 提示（这里 PR 评论简化成只展示第一条，提示放回 final
         report 里）。
      3. **排序**：先按 severity（critical→low），再按 file 和起始行号。
      4. **截断**：最多 ``limit`` 条，剩下的让用户去 final report 里看。
    """

    seen: dict[tuple[str, str, int | None, int | None, str], Finding] = {}
    for finding in findings:
        verification_status = _verification_status_for(finding, board_summary)
        if verification_status in {"refuted", "cross_refuted"}:
            continue
        dedupe_key = (
            finding.severity,
            finding.file_path,
            finding.line_range.start,
            finding.line_range.end,
            finding.category.lower(),
        )
        seen.setdefault(dedupe_key, finding)

    sorted_findings = sorted(
        seen.values(),
        key=lambda f: (
            SEVERITY_ORDER.get(f.severity, 9),
            f.file_path,
            f.line_range.start or 0,
        ),
    )
    return sorted_findings[:limit]


def _render_findings_table_en(
    findings: list[Finding],
    board_summary: BoardSummary,
) -> str:
    """渲染英文 finding 表格。

    列设计参考老版 ci.py 的 _render_report_findings_table：
        | Severity | Verification | Location | Problem | Suggestion |
    Impact 不放进表格（5 列已经接近 GitHub Web UI 单行可读上限）；它会
    出现在 final report 的 Key Findings bullet 段，已经够了。
    """

    if not findings:
        return "_No remaining key findings to display._\n"

    lines = [
        "| Severity | Verification | Location | Problem | Suggestion |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        severity_label = SEVERITY_LABELS_EN.get(finding.severity, finding.severity)
        verification = _verification_status_for(finding, board_summary)
        verification_label = VERIFICATION_LABELS_EN.get(verification, verification)
        location = _line_location(finding)
        problem = _escape_table_cell(_flatten_table_text(finding.problem))
        suggestion = _escape_table_cell(_flatten_table_text(finding.suggestion))
        lines.append(
            f"| {severity_label} | {verification_label} | "
            f"`{_escape_table_cell(location)}` | {problem} | {suggestion} |"
        )
    return "\n".join(lines) + "\n"


def _render_findings_table_zh(
    findings: list[Finding],
    board_summary: BoardSummary,
    localized_cells: dict[str, LocalizedFindingCell],
) -> str:
    """渲染中文 finding 表格。

    severity / verification / location 都用代码生成的中文标签——这些是
    枚举类的列，绝不交给 LLM。problem / suggestion 优先取
    ``localized_cells[finding_id]``，缺失则回退到英文原文，这样：
      * 中文表格永远有内容；
      * 翻译出错最坏的结果是某些行变成中英混排，而不是空白单元格 / 行数对不齐。
    """

    if not findings:
        return "_暂无需要展示的关键 finding。_\n"

    lines = [
        "| 严重级别 | 验证状态 | 位置 | 问题 | 建议 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for finding in findings:
        severity_label = SEVERITY_LABELS_ZH.get(finding.severity, finding.severity)
        verification = _verification_status_for(finding, board_summary)
        verification_label = VERIFICATION_LABELS_ZH.get(verification, verification)
        location = _line_location(finding)

        cell = localized_cells.get(finding.finding_id)
        # 中文文本优先取翻译产物。模型偶尔会返回空字符串，这种也按"翻译缺失"
        # 处理 → 回退到英文，避免空格子。
        problem_text = (cell.problem_zh.strip() if cell else "") or finding.problem
        suggestion_text = (cell.suggestion_zh.strip() if cell else "") or finding.suggestion

        problem = _escape_table_cell(_flatten_table_text(problem_text))
        suggestion = _escape_table_cell(_flatten_table_text(suggestion_text))
        lines.append(
            f"| {severity_label} | {verification_label} | "
            f"`{_escape_table_cell(location)}` | {problem} | {suggestion} |"
        )
    return "\n".join(lines) + "\n"


def build_pr_comment(
    state: CodeCrossState,
    *,
    metadata: PullRequestMetadata | None = None,
    mock_llm: bool = False,
    max_chars: int = DEFAULT_MAX_COMMENT_CHARS,
) -> str:
    """Build the PR Conversation comment with English and Chinese reports.

    PR 评论的版面（从上到下）：
        1. ``CodeCross PR Review`` 标题 + Run Summary 元信息表格。
        2. **方案 B 新增** ``Key Findings`` 段：英文表格 + 中文表格，
           两份表格行序严格一致；都从结构化 ``Finding`` + ``BoardSummary``
           渲染，LLM 只负责翻译三个文本字段（problem/impact/suggestion）。
        3. ``English Report`` ：workflow 渲染好的英文 Markdown 全文。
        4. ``中文报告``：workflow 翻译好的中文 Markdown 全文。
        5. Reviewer Errors（如有）+ 法律 footer。

    把 finding 表格放在两份长报告**前面**是有意为之：用户在 GitHub 邮件
    通知/手机端 review 时往往只看顶部几屏，结构化的双语表格就是给
    "扫一眼"用的。完整文字版报告留给真正深读的人。

    Keep the first-line marker: ``github_pr_comment.py`` uses it to update the
    existing CodeCross comment instead of creating a new one on every push.
    """

    metadata = metadata or PullRequestMetadata()
    if not metadata.workflow_url:
        metadata.workflow_url = _workflow_url()

    evaluator = state.get("evaluator_report")
    risk_level = evaluator.risk_level if evaluator else "unknown"
    confidence = evaluator.confidence if evaluator else "medium"
    mode_label = "mock/demo mode" if mock_llm else "real model mode"
    english_report = (
        str(state.get("final_report_en") or "").strip()
        or str(state.get("final_report") or "").strip()
        or "# CodeCross Final Review Report\n\nWorkflow did not produce a final report."
    )
    chinese_report = (
        str(state.get("final_report_zh") or "").strip()
        or "中文化 Agent 没有返回译文；请查看英文报告。"
    )

    # 方案 B：从 state 拿结构化数据，渲染双语 finding 表格。
    # 注意 ``findings`` 取自 state 而不是从 reviewer_results 重新展平：
    # workflow 里 initial_board_node 已经把所有 reviewer 的 finding 合并到
    # state["findings"] 里了，从那里取等价但更省一次 list comprehension。
    findings: list[Finding] = state.get("findings") or [
        finding
        for result in state.get("reviewer_results", [])
        for finding in result.findings
    ]
    board_summary: BoardSummary | None = state.get("board_summary")
    localized_cells: dict[str, LocalizedFindingCell] = (
        state.get("localized_finding_cells") or {}
    )
    table_findings = (
        _select_table_findings(findings, board_summary)
        if findings and board_summary is not None
        else []
    )

    lines = [
        COMMENT_MARKER,
        "## CodeCross PR Review",
        "",
        "### Run Summary",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Risk level | `{risk_level}` |",
        f"| Confidence | `{confidence}` |",
        f"| Run mode | {mode_label} |",
        f"| Reviewer models | {_model_label_from_state(state)} |",
    ]
    if metadata.head_sha:
        lines.append(f"| Commit | `{metadata.head_sha[:12]}` |")
    if metadata.workflow_url:
        lines.append(f"| CI run | [Open Actions run]({metadata.workflow_url}) |")

    # ------------------------------------------------------------------
    # 双语 Key Findings 表格段。即使 ``table_findings`` 为空也要保留这个
    # 标题，给读者一个稳定的版面预期；空时下方会渲染一句"无 finding"。
    # ------------------------------------------------------------------
    lines.extend(
        [
            "",
            "### Key Findings",
            "",
            "_Top findings shown in both English and Chinese. Tables share the "
            "same row order so you can compare side by side._",
            "",
            "#### English",
            "",
            _render_findings_table_en(table_findings, board_summary)
            if board_summary is not None
            else "_No board summary available; see the JSON artifact._\n",
            "",
            "#### 中文",
            "",
            _render_findings_table_zh(
                table_findings,
                board_summary,
                localized_cells,
            )
            if board_summary is not None
            else "_暂无 Board 汇总数据；请查看 JSON artifact。_\n",
        ]
    )

    lines.extend(
        [
            "",
            "### English Report",
            "",
            english_report,
            "",
            "---",
            "",
            "### 中文报告",
            "",
            chinese_report,
            _reviewer_error_block(state),
            "",
            "---",
            "_Generated by CodeCross. API keys and Base URLs are read from CI/runtime "
            "environment variables and are never written into this comment._",
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
        "report_localizer",
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
