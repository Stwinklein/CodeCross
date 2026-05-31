from __future__ import annotations

"""Shared report localization and presentation helpers."""

import re
from typing import Any

from .schemas import (
    BoardSummary,
    CodeContext,
    EvaluatorReport,
    Finding,
    HighRiskReviewReport,
    ReportFindingRow,
    ReportPresentation,
)


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")


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

CATEGORY_LABELS = {
    "auth": "权限",
    "data_consistency": "数据一致性",
    "maintainability": "可维护性",
    "performance": "性能",
    "runtime": "运行时",
    "security": "安全",
}

COMMON_FINDING_TRANSLATIONS = [
    (
        ("path traversal",),
        "路径穿越风险：代码直接拼接用户提供的文件名，攻击者可能通过 `../` 读取上传目录外的文件。",
        "使用 `path.resolve(rootDir, filename)` 规范化路径，并校验解析后的路径仍位于 `rootDir` 内；拒绝越界路径。",
    ),
    (
        ("user-supplied filename", "without any sanitization"),
        "路径穿越风险：代码直接拼接用户提供的文件名，缺少清理和规范化，可能读取到根目录之外的文件。",
        "先规范化路径，再校验目标路径没有逃逸出允许目录；非法路径应直接拒绝。",
    ),
    (
        ("non-main branch", "codecross"),
        "集成配置仍引用 CodeCross 临时分支，后续如果忘记切回主分支，CI 会继续运行未正式发布的代码。",
        "验证完成后把 CodeCross runner 引用切回 `main`，或先将中文报告分支合入 `main` 后再更新集成配置。",
    ),
    (
        ("branch reference", "codex/chinese-pr-report"),
        "集成配置仍引用 `codex/chinese-pr-report` 临时分支，容易在验证结束后遗留测试配置。",
        "确认 CodeCross 主分支已包含中文报告优化后，把 workflow 中的 runner 引用改回 `main`。",
    ),
    (
        ("git diff command", "interpolates"),
        "workflow 在 shell 命令中直接插入 PR 的 base/head SHA，写法偏脆弱，不利于后续维护和审计。",
        "将 `BASE_SHA` 和 `HEAD_SHA` 放入 `env`，shell 中只引用环境变量，减少表达式直接插入命令的风险。",
    ),
    (
        ("readfilesync",),
        "同步文件读取会阻塞 Node.js 事件循环；如果该函数处在请求路径上，会影响并发请求的响应。",
        "改用 `fs.promises.readFile` 或流式读取，并让调用方处理异步结果。",
    ),
    (
        ("loose equality",),
        "使用 `==` 会触发类型强制转换，内容类型判断可能出现非预期匹配。",
        "改用 `===`；也可以使用 `Set` 白名单维护允许的 MIME 类型。",
    ),
    (
        ("path module", "never used"),
        "导入了 `path` 模块但当前没有使用，说明路径处理逻辑还没有真正落到实现里。",
        "如果按路径规范化方案修复，就实际使用 `path.resolve`；否则移除未使用导入。",
    ),
    (
        ("return none", "callers expect a string"),
        "返回值契约回归：新增分支可能返回 `None`，但调用方仍按字符串处理，容易触发运行时异常。",
        "保持原有字符串返回契约，或同步更新所有调用方和测试用例。",
    ),
    (
        ("zero denominator",),
        "除零边界没有被安全处理，调用方传入 0 作为分母时会触发运行时异常。",
        "明确除零时的返回契约，并在函数入口增加分母为 0 的保护和回归测试。",
    ),
    (
        ("command injection",),
        "命令执行路径存在注入风险，用户输入可能被解释为额外 shell 命令。",
        "避免 `shell=True`，改用参数数组传递命令，并对外部输入做白名单校验。",
    ),
    (
        ("secret material",),
        "源码变更中疑似出现密钥或敏感令牌，合入后可能造成凭据泄露。",
        "将敏感值迁移到环境变量或密钥管理系统，并轮换已经暴露的凭据。",
    ),
]


def contains_chinese(text: str) -> bool:
    """Return whether text already contains Chinese characters."""

    return bool(_CJK_RE.search(text or ""))


def needs_chinese_fallback(text: str) -> bool:
    """Detect English model prose that would make the Chinese report uneven."""

    if not text or contains_chinese(text):
        return False
    return bool(_ASCII_WORD_RE.search(text))


def polish_report_terms(text: str) -> str:
    """Normalize common internal English terms inside otherwise Chinese prose."""

    polished = re.sub(r"\b[Ff]indings\b", "问题", text or "")
    polished = re.sub(r"\b[Ff]inding\b", "问题", polished)
    polished = re.sub(r"\b[Rr]eviewer\b", "审查器", polished)
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", polished)


def shorten(text: str, limit: int = 180) -> str:
    """Compress long text for PR comments and summaries."""

    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def table_cell(text: str, limit: int = 180) -> str:
    """Escape Markdown table separators after shortening text."""

    return shorten(text, limit).replace("|", r"\|")


def risk_text(risk_level: str) -> str:
    """Return a Chinese risk label while keeping the raw enum visible."""

    label = RISK_LABELS.get(risk_level, risk_level)
    return f"{label} (`{risk_level}`)"


def confidence_text(confidence: str) -> str:
    """Return a Chinese confidence label."""

    return CONFIDENCE_LABELS.get(confidence, confidence)


def verification_text(status: str) -> str:
    """Return a Chinese verification status label."""

    return VERIFICATION_LABELS.get(status, status)


def stop_reason_text(stop_reason: str) -> str:
    """Return a Chinese verification loop stop reason."""

    raw = stop_reason or "not stopped"
    label = STOP_REASON_LABELS.get(raw, raw)
    return f"{label} (`{raw}`)"


def merge_advice(risk_level: str) -> str:
    """Return a short, stable merge recommendation from the risk level."""

    if risk_level in {"critical", "high"}:
        return "暂缓合并，先修复高风险问题"
    if risk_level == "medium":
        return "建议修复或人工确认后再合并"
    if risk_level == "low":
        return "可以合并，但建议保留人工抽查"
    return "风险未知，请先查看 CI 日志和完整产物"


def verification_counts(board: Any) -> str:
    """Count findings by verification status."""

    if not board or not board.finding_verifications:
        return "暂无验证状态"

    counts: dict[str, int] = {}
    for verification in board.finding_verifications.values():
        counts[verification.status] = counts.get(verification.status, 0) + 1
    return ", ".join(
        f"{verification_text(status)} {count}"
        for status, count in sorted(counts.items())
    )


def test_summary(board: Any) -> str:
    """Return a compact Chinese test execution summary."""

    if not board:
        return "未生成测试执行摘要"
    return (
        f"通过 {board.passed_count}，失败 {board.failed_count}，"
        f"错误/阻塞 {board.error_count}，跳过 {board.skipped_count}"
    )


def context_counts(context: CodeContext | None) -> str:
    """Return compact Chinese context retrieval counts."""

    if not context:
        return "未检索到"
    counts: dict[str, int] = {}
    for snippet in context.snippets:
        counts[snippet.kind] = counts.get(snippet.kind, 0) + 1
    return ", ".join(
        f"{CONTEXT_KIND_LABELS.get(kind, kind)} {count}"
        for kind, count in sorted(counts.items())
    ) or "未检索到"


def severity_sort_key(finding: Finding) -> tuple[int, str]:
    """Sort findings by review priority."""

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return (severity_order.get(finding.severity, 9), finding.file_path)


def _fallback_problem(finding: Finding) -> str:
    category = CATEGORY_LABELS.get(finding.category, finding.category or "代码")
    severity = SEVERITY_LABELS.get(finding.severity, finding.severity)
    return (
        f"审查器将这里标记为{severity}级{category}风险；详细说明未能稳定中文化，"
        "请结合完整产物中的原始记录复核。"
    )


def _fallback_suggestion(finding: Finding) -> str:
    category = CATEGORY_LABELS.get(finding.category, finding.category or "相关")
    return f"先按{category}风险人工复核该位置，补充必要测试，修复后重新运行 CodeCross。"


def _common_finding_translation(finding: Finding) -> tuple[str, str] | None:
    haystack = " ".join(
        [
            finding.category,
            finding.problem,
            finding.evidence,
            finding.impact,
            finding.suggestion,
        ]
    ).lower()
    for keywords, problem, suggestion in COMMON_FINDING_TRANSLATIONS:
        if all(keyword in haystack for keyword in keywords):
            return problem, suggestion
    return None


def localized_finding_text(finding: Finding) -> tuple[str, str]:
    """Return Chinese problem/suggestion text for a finding."""

    translation = _common_finding_translation(finding)
    if translation:
        return translation

    problem = _fallback_problem(finding) if needs_chinese_fallback(finding.problem) else finding.problem
    suggestion = (
        _fallback_suggestion(finding)
        if needs_chinese_fallback(finding.suggestion)
        else finding.suggestion
    )
    return problem, suggestion


def _location(finding: Finding) -> str:
    line = finding.line_range.start
    return finding.file_path if line is None else f"{finding.file_path}:{line}"


def _display_recommended_action(
    evaluator_report: EvaluatorReport | None,
    high_risk_review: HighRiskReviewReport | None,
    risk_level: str,
) -> str:
    action = (
        high_risk_review.recommended_action
        if high_risk_review and high_risk_review.recommended_action
        else evaluator_report.recommended_action if evaluator_report else ""
    )
    if not action or needs_chinese_fallback(action):
        return merge_advice(risk_level)
    return polish_report_terms(action)


def _display_reasoning(evaluator_report: EvaluatorReport | None) -> str:
    if not evaluator_report or not evaluator_report.reasoning:
        return ""
    if needs_chinese_fallback(evaluator_report.reasoning):
        return "风险判断来自已收集的测试结果、验证状态和审查器发现"
    return polish_report_terms(evaluator_report.reasoning).rstrip("。.!！")


def build_default_report_presentation(
    *,
    findings: list[Finding],
    board_summary: BoardSummary | None,
    evaluator_report: EvaluatorReport | None,
    high_risk_review: HighRiskReviewReport | None = None,
) -> ReportPresentation:
    """Build a deterministic Chinese presentation used by mock and fallback paths."""

    risk_level = evaluator_report.risk_level if evaluator_report else "unknown"
    confidence = evaluator_report.confidence if evaluator_report else "medium"
    recommended_action = _display_recommended_action(
        evaluator_report,
        high_risk_review,
        risk_level,
    )

    evidence_parts: list[str] = []
    if board_summary:
        evidence_parts.append(f"共发现 {board_summary.finding_count} 个问题")
        evidence_parts.append(f"测试执行：{test_summary(board_summary)}")
        evidence_parts.append(f"验证状态：{verification_counts(board_summary)}")
    elif findings:
        evidence_parts.append(f"共发现 {len(findings)} 个问题")
    reasoning = _display_reasoning(evaluator_report)
    if reasoning:
        evidence_parts.append(f"判断依据：{shorten(reasoning, 120)}")

    conclusion_lines = [
        (
            f"本次 PR 判定为 **{RISK_LABELS.get(risk_level, risk_level)}**，"
            f"置信度为 **{confidence_text(confidence)}**；当前合并建议是 "
            f"**{merge_advice(risk_level)}**。"
        )
    ]
    if evidence_parts:
        conclusion_lines.append("；".join(evidence_parts) + "。")

    rows: list[ReportFindingRow] = []
    seen: dict[tuple[str, str, str], ReportFindingRow] = {}
    verifications = board_summary.finding_verifications if board_summary else {}
    for finding in sorted(findings, key=severity_sort_key):
        problem, suggestion = localized_finding_text(finding)
        location = _location(finding)
        key = (finding.severity, location, problem)
        existing = seen.get(key)
        if existing:
            existing.finding_ids.append(finding.finding_id)
            if finding.source_reviewer not in existing.source_reviewers:
                existing.source_reviewers.append(finding.source_reviewer)
            continue

        verification = verifications.get(finding.finding_id)
        row = ReportFindingRow(
            finding_ids=[finding.finding_id],
            severity=finding.severity,
            verification_status=verification_text(verification.status) if verification else "未进入验证",
            location=location,
            problem=problem,
            suggestion=suggestion,
            source_reviewers=[finding.source_reviewer],
        )
        seen[key] = row
        rows.append(row)

    return ReportPresentation(
        conclusion_summary="\n".join(conclusion_lines),
        recommended_action=recommended_action,
        finding_rows=rows,
        formatter_notes="已完成中文化、去重和 PR 评论格式化。",
    )


def normalize_report_presentation(
    presentation: ReportPresentation,
    fallback: ReportPresentation,
) -> ReportPresentation:
    """Keep model-formatted reports structured and Chinese-readable."""

    if needs_chinese_fallback(presentation.conclusion_summary):
        presentation.conclusion_summary = fallback.conclusion_summary
    else:
        presentation.conclusion_summary = polish_report_terms(presentation.conclusion_summary)
    if needs_chinese_fallback(presentation.recommended_action):
        presentation.recommended_action = fallback.recommended_action
    else:
        presentation.recommended_action = polish_report_terms(presentation.recommended_action)
    if not presentation.finding_rows and fallback.finding_rows:
        presentation.finding_rows = fallback.finding_rows
    for index, row in enumerate(presentation.finding_rows):
        fallback_row = fallback.finding_rows[index] if index < len(fallback.finding_rows) else None
        if fallback_row:
            if not row.finding_ids:
                row.finding_ids = fallback_row.finding_ids
            if not row.location:
                row.location = fallback_row.location
            if not row.verification_status:
                row.verification_status = fallback_row.verification_status
            if needs_chinese_fallback(row.problem):
                row.problem = fallback_row.problem
            else:
                row.problem = polish_report_terms(row.problem)
            if needs_chinese_fallback(row.suggestion):
                row.suggestion = fallback_row.suggestion
            else:
                row.suggestion = polish_report_terms(row.suggestion)
        if not row.source_reviewers and fallback_row:
            row.source_reviewers = fallback_row.source_reviewers
    return presentation
