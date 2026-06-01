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
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .config import ModelSettings, ProjectConfig, load_project_config
from .schemas import (
    BoardSummary,
    EvaluatorReport,
    Finding,
    LocalizedFindingCell,
    ReviewerResult,
)
from .workflow import CodeCrossState, run_workflow


COMMENT_MARKER = "<!-- codecross-pr-review -->"
DEFAULT_MAX_COMMENT_CHARS = 60_000

# 方案 C：PR 评论顶层可快速扫读的 finding 数量上限。
# 选 10 是因为新版评论会同时渲染一览表、Why 卡片和修复清单；超过这个数量
# 顶层就会重新变得拥挤。完整报告和 JSON artifact 仍然保留全部细节。
DEFAULT_PR_TABLE_FINDING_LIMIT = 10

# 严重等级显示用的中英映射。这里把它放到模块级常量是为了让 PR 评论的
# 中文/英文段永远输出同一组标签——不让 LLM 翻译这些枚举值，避免
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
SEVERITY_BADGES_EN: dict[str, str] = {
    "critical": "🔴 C",
    "high": "🔴 H",
    "medium": "🟠 M",
    "low": "🟡 L",
}
SEVERITY_BADGES_ZH: dict[str, str] = {
    "critical": "🔴 严重",
    "high": "🔴 高",
    "medium": "🟠 中",
    "low": "🟡 低",
}
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}
RISK_ICONS: dict[str, str] = {
    "critical": "🔴",
    "high": "🔴",
    "medium": "🟠",
    "low": "🟢",
    "unknown": "⚪",
}
VERDICT_ACTIONS: dict[str, str] = {
    "critical": "Block merge",
    "high": "Block merge",
    "medium": "Comment",
    "low": "Approve",
    "unknown": "Comment",
}

# 验证状态显示标签。和 schemas.VerificationStatus 字面量保持一一对应。
# 命名口径：英文 Why 卡片用 schema 原值，中文 Why 卡片用人类可读的短词。
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
# 方案 C：PR 评论"答题式"版面渲染
# ---------------------------------------------------------------------------
# 设计原则（核心，看代码先看这段注释）：
#   1. **结构由代码决定**：顶层 verdict、三列表格、Why 卡片、task list 和
#      details 折叠块都由本地代码生成，LLM 只提供 finding 文案和中文译文。
#   2. **中英段完整镜像**：先做一次"去重 + 排序 + 截断"，得到同一份
#      ``table_findings``；英文段、中文段都从这份列表渲染，顺序严格一致。
#   3. **缺翻译就回退英文**：如果 localized_finding_cells 没有某条 finding，
#      中文段仍然显示英文原文，保证不会出现空卡片、空清单或中英行数错位。
#   4. **表格单元格防御性转义**：finding 文本里如果出现 ``|``、``\n``，
#      会破坏 Markdown 表格语法，所以单独有一个 _escape_table_cell 处理。
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


def _truncate_inline(text: str, *, max_chars: int = 180) -> str:
    """把一段单行文本限制到适合 PR 评论顶层展示的长度。"""

    cleaned = _flatten_table_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _first_sentence(text: str, fallback: str = "—", *, max_chars: int = 180) -> str:
    """抽取一条 finding 文案的第一句，给 verdict/checklist/card 标题使用。

    这里不做 NLP，只做保守的标点切分。原因是 PR 评论渲染必须稳定、
    可测试，而不能依赖模型再总结一次。
    """

    cleaned = _flatten_table_text(text, fallback)
    cleaned = re.sub(r"^(?:[-*]\s+|\[[ xX]\]\s+)", "", cleaned).strip()
    # ``e.g.`` / ``i.e.`` often appear right before a code example. Treating
    # their periods as sentence boundaries produces useless checklist items like
    # "Use encodeURIComponent(), e.g.".
    protected = (
        cleaned.replace("e.g.", "e_g_")
        .replace("i.e.", "i_e_")
        .replace("E.g.", "E_g_")
        .replace("I.e.", "I_e_")
    )
    match = re.match(r"(.+?[.!?。！？])(?:\s|$)", protected)
    if match and len(match.group(1).strip()) >= 8:
        cleaned = match.group(1).strip()
        cleaned = (
            cleaned.replace("e_g_", "e.g.")
            .replace("i_e_", "i.e.")
            .replace("E_g_", "E.g.")
            .replace("I_e_", "I.e.")
        )
    return _truncate_inline(cleaned, max_chars=max_chars)


def _checklist_action(text: str, fallback: str = "—", *, max_chars: int = 180) -> str:
    """Extract a task-list action without dragging example code into the task."""

    cleaned = _flatten_table_text(text, fallback)
    cleaned = re.split(r"\s*(?:,?\s+e\.g\.|，?例如[:：]?)\s*", cleaned, maxsplit=1)[0]
    cleaned = cleaned.rstrip(" ,，;；:")
    if cleaned and cleaned[-1] not in ".。!?！？":
        cleaned += "."
    return _first_sentence(cleaned, fallback, max_chars=max_chars)


def _finding_topic_key(finding: Finding) -> str:
    """把相似 finding 归到同一个人工可读风险主题。

    两个 reviewer 很容易用不同 wording 报同一个风险。顶层 PR 评论应该
    帮读者聚合它们；完整报告仍保留所有原始 reviewer 输出。
    """

    text = " ".join(
        [
            finding.category,
            finding.file_path,
            finding.problem,
            finding.impact,
            finding.suggestion,
        ]
    ).lower()
    file_path = finding.file_path.lower()

    if "workflow" in file_path and "fallback" in text and "comment" in text:
        return f"{finding.file_path}:workflow-fallback-comment"
    if "workflow" in file_path and (
        ("runner" in text and "ref" in text)
        or "temporary test ref" in text
        or "pinned by sha" in text
    ):
        return f"{finding.file_path}:workflow-runner-ref"
    if "state" in text and (
        re.search(r"\b(log|logs|logged|logging|plaintext|console)\b", text) is not None
    ):
        return f"{finding.file_path}:oauth-state-log"
    if "provider" in text and any(
        word in text for word in ("unknown", "undefined", "invalid", "not found")
    ):
        return f"{finding.file_path}:oauth-provider-validation"
    if ("duplicate" in text or "maintenance" in text) and "provider" in text:
        return f"{finding.file_path}:provider-lookup-duplication"
    if any(word in text for word in ("redirect", "next", "query parameter")) and any(
        word in text for word in ("encode", "validation", "allowlist", "open redirect")
    ):
        return f"{finding.file_path}:oauth-redirect-validation"

    return (
        f"{finding.file_path}:{finding.line_range.start}:{finding.line_range.end}:"
        f"{finding.category.lower()}:{_first_sentence(finding.problem, max_chars=80).lower()}"
    )


TOPIC_TITLES_EN: dict[str, str] = {
    "workflow-fallback-comment": "Unsafe fallback PR comment",
    "workflow-runner-ref": "Temporary CodeCross runner ref",
    "oauth-state-log": "OAuth state logged in plaintext",
    "oauth-provider-validation": "Missing provider validation",
    "provider-lookup-duplication": "Duplicated provider lookup",
    "oauth-redirect-validation": "Unsafe OAuth redirect URL",
}
TOPIC_TITLES_ZH: dict[str, str] = {
    "workflow-fallback-comment": "回退 PR 评论未清理输入",
    "workflow-runner-ref": "临时 CodeCross runner ref",
    "oauth-state-log": "OAuth state 明文日志",
    "oauth-provider-validation": "缺少 provider 校验",
    "provider-lookup-duplication": "provider 查找逻辑重复",
    "oauth-redirect-validation": "不安全的 OAuth 重定向 URL",
}


def _topic_slug(finding: Finding) -> str:
    """Extract the normalized topic slug from ``_finding_topic_key``."""

    return _finding_topic_key(finding).rsplit(":", 1)[-1]


def _finding_title(finding: Finding, problem_text: str | None = None) -> str:
    """从 problem 字段里提取一段适合放在卡片标题里的短标题。"""

    topic_title = TOPIC_TITLES_EN.get(_topic_slug(finding))
    if topic_title:
        return topic_title

    problem = _flatten_table_text(problem_text or finding.problem)
    colon_title = re.split(r"[:：]", problem, maxsplit=1)[0].strip()
    if 4 <= len(colon_title) <= 80 and colon_title != problem:
        return colon_title
    return _first_sentence(problem, max_chars=80)


def _finding_title_zh(finding: Finding, problem_text: str | None = None) -> str:
    """中文卡片标题；没有已知主题时从中文 problem 中提取短标题。"""

    topic_title = TOPIC_TITLES_ZH.get(_topic_slug(finding))
    if topic_title:
        return topic_title

    problem = _flatten_table_text(problem_text or finding.problem)
    colon_title = re.split(r"[:：]", problem, maxsplit=1)[0].strip()
    if 4 <= len(colon_title) <= 80 and colon_title != problem:
        return colon_title
    return _first_sentence(problem, max_chars=80)


def _severity_badge_en(severity: str) -> str:
    """英文段 severity 的短标签：emoji + 首字母。"""

    return SEVERITY_BADGES_EN.get(severity, severity)


def _severity_badge_zh(severity: str) -> str:
    """中文段 severity 的短标签：emoji + 中文等级。"""

    return SEVERITY_BADGES_ZH.get(severity, severity)


def _localized_texts(
    finding: Finding,
    localized_cells: dict[str, LocalizedFindingCell],
) -> tuple[str, str, str]:
    """读取一条 finding 的中文 problem/impact/suggestion，缺失时回退英文。"""

    cell = localized_cells.get(finding.finding_id)
    problem = (cell.problem_zh.strip() if cell else "") or finding.problem
    impact = (cell.impact_zh.strip() if cell else "") or finding.impact
    suggestion = (cell.suggestion_zh.strip() if cell else "") or finding.suggestion
    return problem, impact, suggestion


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
    """挑出 PR 评论顶层速读区要展示的 finding 子集。

    这一步是"准备结构化数据"，**完全不依赖翻译产物**——这样英文/中文
    镜像段一定从同一组 finding 出发，行数一致、行序一致。规则：
      1. **过滤**：board 已经把它判为 refuted / cross_refuted 的，不展示
         （和 _render_key_findings 一致，避免假阳性占据有限的顶层空间）。
      2. **去重**：先按风险主题聚合（比如 open redirect / state 日志 /
         provider 校验）。多个 reviewer 命中同类问题时只显示严重度最高的
         代表项，避免顶层评论被重复 finding 淹没。
      3. **排序**：先按 severity（critical→low），再按 file 和起始行号。
      4. **截断**：最多 ``limit`` 条，剩下的让用户去 final report 里看。
    """

    seen: dict[str, Finding] = {}
    for finding in findings:
        verification_status = _verification_status_for(finding, board_summary)
        if verification_status in {"refuted", "cross_refuted"}:
            continue
        dedupe_key = _finding_topic_key(finding)
        current = seen.get(dedupe_key)
        if current is None:
            seen[dedupe_key] = finding
            continue

        # 同一主题里保留最严重、位置更靠前的代表项；完整报告仍保留所有
        # reviewer 原始输出，所以这里可以大胆为顶层阅读做降噪。
        current_rank = (
            SEVERITY_ORDER.get(current.severity, 9),
            current.line_range.start or 0,
        )
        candidate_rank = (
            SEVERITY_ORDER.get(finding.severity, 9),
            finding.line_range.start or 0,
        )
        if candidate_rank < current_rank:
            seen[dedupe_key] = finding

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
    """渲染英文一览表，只放定位问题需要的三列。"""

    if not findings:
        return "_No remaining key findings to display._\n"

    lines = [
        "| Sev | Location | Problem |",
        "| --- | --- | --- |",
    ]
    for finding in findings:
        severity_label = _severity_badge_en(finding.severity)
        location = _line_location(finding)
        problem = _escape_table_cell(
            f"{_finding_title(finding)}: "
            f"{_first_sentence(finding.problem, max_chars=140)}"
        )
        lines.append(
            f"| {severity_label} | `{_escape_table_cell(location)}` | {problem} |"
        )
    return "\n".join(lines) + "\n"


def _render_findings_table_zh(
    findings: list[Finding],
    board_summary: BoardSummary,
    localized_cells: dict[str, LocalizedFindingCell],
) -> str:
    """渲染中文一览表，和英文表共享同一组 finding 行序。

    severity / location 都用代码生成的标签。problem 优先取
    ``localized_cells[finding_id]``，缺失则回退到英文原文，这样：
      * 中文一览表永远有内容；
      * 翻译出错最坏的结果是某些行变成中英混排，而不是空白单元格 / 行数对不齐。
    """

    if not findings:
        return "_暂无需要展示的关键 finding。_\n"

    lines = [
        "| 严重 | 位置 | 问题 |",
        "| --- | --- | --- |",
    ]
    for finding in findings:
        severity_label = _severity_badge_zh(finding.severity)
        location = _line_location(finding)

        problem_text, _, _ = _localized_texts(finding, localized_cells)
        problem = _escape_table_cell(
            f"{_finding_title_zh(finding, problem_text)}："
            f"{_first_sentence(problem_text, max_chars=140)}"
        )
        lines.append(
            f"| {severity_label} | `{_escape_table_cell(location)}` | {problem} |"
        )
    return "\n".join(lines) + "\n"


def _display_finding_id(index: int) -> str:
    """给 PR 评论生成稳定、易引用的展示 ID。"""

    return f"FINDING-{index:03d}"


def _render_finding_cards_en(
    findings: list[Finding],
    board_summary: BoardSummary,
) -> str:
    """渲染英文 Why 卡片：What / Impact / Fix 三段式。"""

    if not findings:
        return "_No finding cards to display._\n"

    lines: list[str] = []
    for index, finding in enumerate(findings, start=1):
        verification = _verification_status_for(finding, board_summary)
        verification_label = VERIFICATION_LABELS_EN.get(verification, verification)
        severity = SEVERITY_LABELS_EN.get(finding.severity, finding.severity)
        lines.extend(
            [
                (
                    f"▸ {_severity_badge_en(finding.severity).split()[0]} "
                    f"**{_display_finding_id(index)} · {_finding_title(finding)}** "
                    f"({severity.lower()} · {verification_label})"
                ),
                "",
                f"- **What:** {_first_sentence(finding.problem, max_chars=260)}",
                f"- **Impact:** {_first_sentence(finding.impact, max_chars=260)}",
                f"- **Fix:** {_first_sentence(finding.suggestion, max_chars=260)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_finding_cards_zh(
    findings: list[Finding],
    board_summary: BoardSummary,
    localized_cells: dict[str, LocalizedFindingCell],
) -> str:
    """渲染中文 Why 卡片；翻译缺失时逐字段回退英文。"""

    if not findings:
        return "_暂无 finding 卡片。_\n"

    lines: list[str] = []
    for index, finding in enumerate(findings, start=1):
        problem, impact, suggestion = _localized_texts(finding, localized_cells)
        verification = _verification_status_for(finding, board_summary)
        verification_label = VERIFICATION_LABELS_ZH.get(verification, verification)
        severity = SEVERITY_LABELS_ZH.get(finding.severity, finding.severity)
        lines.extend(
            [
                (
                    f"▸ {_severity_badge_zh(finding.severity).split()[0]} "
                    f"**{_display_finding_id(index)} · {_finding_title_zh(finding, problem)}**"
                    f"（{severity} · {verification_label}）"
                ),
                "",
                f"- **问题：** {_first_sentence(problem, max_chars=260)}",
                f"- **影响：** {_first_sentence(impact, max_chars=260)}",
                f"- **修复：** {_first_sentence(suggestion, max_chars=260)}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _render_action_checklist_en(findings: list[Finding]) -> str:
    """从英文 suggestion 字段提取可勾选的修复清单。"""

    if not findings:
        return "- [ ] No action items were generated.\n"

    lines: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        action = _checklist_action(finding.suggestion, finding.problem, max_chars=180)
        key = action.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- [ ] {action}")
    return "\n".join(lines) + "\n"


def _render_action_checklist_zh(
    findings: list[Finding],
    localized_cells: dict[str, LocalizedFindingCell],
) -> str:
    """从中文 suggestion 字段提取可勾选的修复清单，缺翻译则回退英文。"""

    if not findings:
        return "- [ ] 暂无生成的修复项。\n"

    lines: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        _, _, suggestion = _localized_texts(finding, localized_cells)
        action = _checklist_action(suggestion, finding.suggestion, max_chars=180)
        key = action.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- [ ] {action}")
    return "\n".join(lines) + "\n"


def _blocking_findings(
    findings: list[Finding],
    board_summary: BoardSummary,
    *,
    limit: int = 4,
) -> list[Finding]:
    """Pick the small set of findings that should drive the merge decision."""

    strong_statuses = {"supported", "cross_supported", "unverified", "needs_human_review"}
    blocking = [
        finding
        for finding in findings
        if finding.severity in {"critical", "high"}
        and _verification_status_for(finding, board_summary) in strong_statuses
    ]
    return blocking[:limit]


def _render_blocking_summary_en(
    findings: list[Finding],
    board_summary: BoardSummary,
) -> str:
    """Render the short "why are we blocking?" summary before details."""

    blocking = _blocking_findings(findings, board_summary)
    if not blocking:
        return "_No high-severity blocking findings in the top set._\n"

    lines: list[str] = []
    for finding in blocking:
        icon = _severity_badge_en(finding.severity).split()[0]
        lines.append(
            f"- {icon} **{_finding_title(finding)}** at "
            f"`{_escape_table_cell(_line_location(finding))}` — "
            f"{_first_sentence(finding.impact or finding.problem, max_chars=150)}"
        )
    return "\n".join(lines) + "\n"


def _render_blocking_summary_zh(
    findings: list[Finding],
    board_summary: BoardSummary,
    localized_cells: dict[str, LocalizedFindingCell],
) -> str:
    """中文阻塞摘要，和英文摘要使用同一批代表 finding。"""

    blocking = _blocking_findings(findings, board_summary)
    if not blocking:
        return "_顶层集合里没有高严重度阻塞项。_\n"

    lines: list[str] = []
    for finding in blocking:
        problem, impact, _ = _localized_texts(finding, localized_cells)
        icon = _severity_badge_zh(finding.severity).split()[0]
        lines.append(
            f"- {icon} **{_finding_title_zh(finding, problem)}** at "
            f"`{_escape_table_cell(_line_location(finding))}` — "
            f"{_first_sentence(impact or problem, max_chars=150)}"
        )
    return "\n".join(lines) + "\n"


def _test_evidence_phrase(board_summary: BoardSummary | None) -> str:
    """把 BoardSummary 的测试统计压成 verdict 第二行里的短括号。"""

    if board_summary is None:
        return ""

    failed = board_summary.failed_count
    passed = board_summary.passed_count
    total = failed + passed + board_summary.error_count + board_summary.skipped_count
    if total <= 0:
        total = board_summary.test_case_count
    if total <= 0:
        return ""
    return f" ({failed}/{total} tests failed, {passed} passed)"


def _verdict_reason(
    *,
    evaluator: EvaluatorReport | None,
    board_summary: BoardSummary | None,
    findings: list[Finding],
) -> str:
    """生成顶部 verdict 的一句话理由。

    优先使用 evaluator.reasoning；没有时从结构化 finding/测试统计推导一条
    稳定文案，保证异常路径也能给出可读结论。
    """

    if evaluator and evaluator.reasoning.strip():
        return _first_sentence(evaluator.reasoning, max_chars=220)

    severe_findings = [
        finding for finding in findings if finding.severity in {"critical", "high"}
    ]
    if severe_findings:
        supported_statuses = {"supported", "cross_supported"}
        supported_count = 0
        if board_summary is not None:
            supported_count = sum(
                1
                for finding in severe_findings
                if _verification_status_for(finding, board_summary) in supported_statuses
            )
        count = supported_count or len(severe_findings)
        noun = "finding" if count == 1 else "findings"
        verb = "has" if count == 1 else "have"
        if supported_count:
            return (
                f"{count} high-severity {noun} {verb} reproducible test evidence"
                f"{_test_evidence_phrase(board_summary)}."
            )
        return f"{count} high-severity {noun} {verb} not been cleared for merge."

    if findings:
        noun = "finding" if len(findings) == 1 else "findings"
        return f"{len(findings)} {noun} need review before merge."

    if evaluator and evaluator.recommended_action.strip():
        return _first_sentence(evaluator.recommended_action, max_chars=220)
    return "No remaining key findings are shown."


def _render_verdict_block(
    *,
    evaluator: EvaluatorReport | None,
    board_summary: BoardSummary | None,
    findings: list[Finding],
) -> str:
    """渲染评论顶部的一眼结论 blockquote。"""

    risk_level = evaluator.risk_level if evaluator else "unknown"
    confidence = evaluator.confidence if evaluator else "medium"
    action = VERDICT_ACTIONS.get(risk_level, "Comment")
    icon = RISK_ICONS.get(risk_level, "⚪")
    reason = _verdict_reason(
        evaluator=evaluator,
        board_summary=board_summary,
        findings=findings,
    )
    return (
        f"> {icon} **{action}** · risk **{risk_level}** · confidence **{confidence}**\n"
        f"> {reason}\n"
    )


def _render_run_info(
    *,
    state: CodeCrossState,
    metadata: PullRequestMetadata,
    mode_label: str,
) -> str:
    """渲染默认折叠的 CI 元信息，避免干扰顶层阅读。"""

    lines = [
        "<details>",
        "<summary>Run info</summary>",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Run mode | {_escape_table_cell(mode_label)} |",
        f"| Reviewers | {_escape_table_cell(_model_label_from_state(state))} |",
    ]
    if metadata.repository:
        lines.append(f"| Repository | `{_escape_table_cell(metadata.repository)}` |")
    if metadata.pr_number is not None:
        lines.append(f"| Pull request | `#{metadata.pr_number}` |")
    if metadata.head_sha:
        lines.append(f"| Commit | `{_escape_table_cell(metadata.head_sha[:12])}` |")
    if metadata.base_ref or metadata.head_ref:
        base = metadata.base_ref or "unknown"
        head = metadata.head_ref or "unknown"
        lines.append(f"| Branches | `{_escape_table_cell(head)}` → `{_escape_table_cell(base)}` |")
    if metadata.workflow_url:
        lines.append(f"| CI run | [Open Actions run]({metadata.workflow_url}) |")
    lines.extend(["", "</details>"])
    return "\n".join(lines)


def _render_report_details(summary: str, report: str) -> str:
    """把 workflow 已生成的完整长报告折叠起来，保留深读入口。"""

    return (
        "<details>\n"
        f"<summary>{summary}</summary>\n\n"
        f"{report.strip()}\n\n"
        "</details>\n"
    )


def build_pr_comment(
    state: CodeCrossState,
    *,
    metadata: PullRequestMetadata | None = None,
    mock_llm: bool = False,
    max_chars: int = DEFAULT_MAX_COMMENT_CHARS,
) -> str:
    """Build the PR Conversation comment with the plan C answer-first layout.

    PR 评论的版面（从上到下）：
        1. 顶部 verdict blockquote：该不该合、风险等级、置信度和一句话理由。
        2. ``Run info`` 折叠块：CI 元数据默认收起，排查时再展开。
        3. 英文镜像段：阻塞摘要 → 三列一览表 → Why 卡片 → Action checklist → 完整报告折叠。
        4. 中文镜像段：同一批 finding、同一行序，文案优先用 localized cells。
        5. Reviewer Errors（如有）+ footer。

    Keep the first-line marker: ``github_pr_comment.py`` uses it to update the
    existing CodeCross comment instead of creating a new one on every push.
    """

    metadata = metadata or PullRequestMetadata()
    if not metadata.workflow_url:
        metadata.workflow_url = _workflow_url()

    evaluator = state.get("evaluator_report")
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

    # 方案 C：从 state 拿结构化数据，渲染顶层速读区。
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

    if board_summary is None:
        english_overview = "_No board summary available; see the JSON artifact._\n"
        chinese_overview = "_暂无 Board 汇总数据；请查看 JSON artifact。_\n"
        english_blocking = "_No board summary available; see the JSON artifact._\n"
        chinese_blocking = "_暂无 Board 汇总数据；请查看 JSON artifact。_\n"
        english_cards = "_No board summary available; see the JSON artifact._\n"
        chinese_cards = "_暂无 Board 汇总数据；请查看 JSON artifact。_\n"
    else:
        english_blocking = _render_blocking_summary_en(table_findings, board_summary)
        chinese_blocking = _render_blocking_summary_zh(
            table_findings,
            board_summary,
            localized_cells,
        )
        english_overview = _render_findings_table_en(table_findings, board_summary)
        chinese_overview = _render_findings_table_zh(
            table_findings,
            board_summary,
            localized_cells,
        )
        english_cards = _render_finding_cards_en(table_findings, board_summary)
        chinese_cards = _render_finding_cards_zh(
            table_findings,
            board_summary,
            localized_cells,
        )

    lines = [
        COMMENT_MARKER,
        "## CodeCross PR Review",
        "",
        _render_verdict_block(
            evaluator=evaluator,
            board_summary=board_summary,
            findings=table_findings,
        ).rstrip(),
        "",
        _render_run_info(
            state=state,
            metadata=metadata,
            mode_label=mode_label,
        ),
        "",
        "---",
        "",
        "## 🇬🇧 English",
        "",
        "### Blocking summary",
        "",
        english_blocking.rstrip(),
        "",
        "### Findings at a glance",
        "",
        english_overview.rstrip(),
        "",
        "### Why it matters",
        "",
        english_cards.rstrip(),
        "",
        "### Action checklist",
        "",
        _render_action_checklist_en(table_findings).rstrip(),
        "",
        _render_report_details("Full English report", english_report).rstrip(),
        "",
        "---",
        "",
        "## 🇨🇳 中文",
        "",
        "### 阻塞摘要",
        "",
        chinese_blocking.rstrip(),
        "",
        "### 一览",
        "",
        chinese_overview.rstrip(),
        "",
        "### 为什么风险高",
        "",
        chinese_cards.rstrip(),
        "",
        "### 修复清单",
        "",
        _render_action_checklist_zh(table_findings, localized_cells).rstrip(),
        "",
        _render_report_details("完整中文报告", chinese_report).rstrip(),
        _reviewer_error_block(state),
        "",
        "---",
        "_Generated by CodeCross. API keys and Base URLs are read from CI/runtime "
        "environment variables and are never written into this comment._",
    ]
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
