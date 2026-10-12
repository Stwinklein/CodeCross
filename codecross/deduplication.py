from __future__ import annotations

"""Finding 归一化与确定性去重。

Reviewer 的自然语言措辞不稳定，因此不能用完整字符串相等判断重复。这里采用保守的
两阶段规则：先用文件/行号判断是否可能指向同一代码位置，再用问题、证据和影响的
token 相似度确认。规则宁可少合并，也不把两个不同根因错误合成一条。

公开 benchmark 还可以在这个确定性结果之上使用独立 LLM 做语义去重，但运行时主链
始终保留这层可复现的结果和原始 ID 映射，方便审计。
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Callable

from .schemas import Finding, TestIntent


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "when", "with",
    "can", "could", "may", "might", "will", "would", "code", "issue", "problem",
}


@dataclass(slots=True)
class DeduplicationResult:
    """去重结果和 raw ID → canonical ID 映射。"""

    findings: list[Finding]
    source_to_canonical: dict[str, str]


def _normalise_path(path: str) -> str:
    value = path.strip().replace("\\", "/").lower()
    while value.startswith(("./", "a/", "b/")):
        value = value[2:]
    return value


def _tokens(finding: Finding, *, problem_only: bool = False) -> set[str]:
    # benchmark 轻量 comment 映射后的 evidence/impact/suggestion 是固定模板。如果把
    # 模板词计入 Jaccard，同一行的所有评论都会被误判相似；human 模式因此只比较
    # 模型真正生成的 problem 文本。产品 defect 模式仍可利用证据与影响补充语义。
    text = (
        finding.problem
        if problem_only
        else " ".join((finding.problem, finding.evidence, finding.impact))
    ).lower()
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", text)
        if len(token) > 1 and token not in _STOP_WORDS
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _locations_related(left: Finding, right: Finding) -> bool:
    """只有同文件且行区间相交/相邻时，才使用较低文本阈值合并。"""

    left_path, right_path = _normalise_path(left.file_path), _normalise_path(right.file_path)
    if not left_path or left_path != right_path:
        return False
    left_start, right_start = left.line_range.start, right.line_range.start
    if left_start is None or right_start is None:
        return False
    left_end = left.line_range.end or left_start
    right_end = right.line_range.end or right_start
    return max(left_start, right_start) <= min(left_end, right_end) + 4


def findings_are_duplicates(
    left: Finding, right: Finding, *, preserve_human_concerns: bool = False
) -> bool:
    """保守判断两条 Finding 是否描述同一根因。

    ``preserve_human_concerns`` 用于公开 human-review benchmark：同一代码行可以有
    多个不同修复方向，只有 problem 本文高度重合时才合并，避免宽泛主题吞掉具体评论。
    """

    left_path, right_path = _normalise_path(left.file_path), _normalise_path(right.file_path)
    if left_path and right_path and left_path != right_path:
        return False
    similarity = _jaccard(
        _tokens(left, problem_only=preserve_human_concerns),
        _tokens(right, problem_only=preserve_human_concerns),
    )
    if preserve_human_concerns:
        return similarity >= (0.62 if _locations_related(left, right) else 0.78)
    same_category = left.category.strip().lower() == right.category.strip().lower()
    if _locations_related(left, right):
        return similarity >= 0.28 or (same_category and similarity >= 0.20)
    # 没有可靠行号时提高阈值，避免把同文件中的两个空值问题误合并。
    return similarity >= 0.68 or (same_category and similarity >= 0.58)


def _severity_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2, "critical": 3}.get(value, 0)


def _canonical_fingerprint(items: list[Finding]) -> str:
    representative = items[0]
    stable_text = "|".join(
        (
            _normalise_path(representative.file_path),
            representative.category.strip().lower(),
            str(representative.line_range.start or 0),
            " ".join(sorted(_tokens(representative))[:16]),
        )
    )
    return hashlib.sha256(stable_text.encode("utf-8")).hexdigest()[:16]


def _merge_group(items: list[Finding], *, preserve_human_concerns: bool = False) -> Finding:
    """选择证据最完整的代表项，并合并严重级别、来源和关联测试。"""

    representative = max(
        items,
        key=lambda item: (
            # human 模式的辅助字段是固定模板，problem 长度更能代表评论信息量；
            # defect 模式仍优先严重度和完整证据。
            len(item.problem) if preserve_human_concerns else _severity_rank(item.severity),
            len(item.problem)
            if preserve_human_concerns
            else len(item.evidence) + len(item.impact) + len(item.suggestion),
        ),
    ).model_copy(deep=True)
    representative.severity = max(items, key=lambda item: _severity_rank(item.severity)).severity
    representative.source_reviewers = sorted(
        {
            reviewer
            for item in items
            for reviewer in (item.source_reviewers or [item.source_reviewer])
        }
    )
    representative.merged_finding_ids = list(
        dict.fromkeys(
            raw_id
            for item in items
            for raw_id in (item.merged_finding_ids or [item.finding_id])
        )
    )
    representative.related_test_intent_ids = list(
        dict.fromkeys(intent_id for item in items for intent_id in item.related_test_intent_ids)
    )
    representative.fingerprint = _canonical_fingerprint(items)
    return representative


def deduplicate_findings(
    findings: list[Finding], *, preserve_human_concerns: bool = False
) -> DeduplicationResult:
    """按输入顺序做稳定聚类，返回 canonical Finding 和可追溯映射。"""

    groups: list[list[Finding]] = []
    for finding in findings:
        for group in groups:
            if any(
                findings_are_duplicates(
                    finding,
                    existing,
                    preserve_human_concerns=preserve_human_concerns,
                )
                for existing in group
            ):
                group.append(finding)
                break
        else:
            groups.append([finding])

    canonical = [
        _merge_group(group, preserve_human_concerns=preserve_human_concerns)
        for group in groups
    ]
    source_to_canonical = {
        raw_id: item.finding_id
        for item in canonical
        for raw_id in item.merged_finding_ids
    }
    return DeduplicationResult(findings=canonical, source_to_canonical=source_to_canonical)


def semantic_deduplicate_findings(
    findings: list[Finding],
    *,
    matcher: Callable[[Finding, Finding], bool],
) -> DeduplicationResult:
    """对确定性结果做第二阶段语义聚类。

    同文件候选总是可比较；跨文件候选只有在词面已共享一定根因信号时才交给
    matcher。这样能合并散落在多个文件的同类缺陷，又不会产生无界 pair 数量。
    matcher 只能看到候选 Finding，不接触 benchmark golden。
    """

    groups: list[list[Finding]] = []
    for finding in findings:
        for group in groups:
            representative = group[0]
            same_file = _normalise_path(finding.file_path) == _normalise_path(representative.file_path)
            cross_file_similarity = _jaccard(_tokens(finding), _tokens(representative))
            comparable = same_file or cross_file_similarity >= 0.22
            if comparable and matcher(finding, representative):
                group.append(finding)
                break
        else:
            groups.append([finding])
    canonical = [_merge_group(group) for group in groups]
    return DeduplicationResult(
        findings=canonical,
        source_to_canonical={
            raw_id: item.finding_id
            for item in canonical
            for raw_id in item.merged_finding_ids
        },
    )


def merge_explicit_finding_groups(
    findings: list[Finding], groups: list[list[str]]
) -> DeduplicationResult:
    """应用一次 batch clustering 的 ID 分组，并保守忽略非法/重叠组。"""

    by_id = {item.finding_id: item for item in findings}
    used: set[str] = set()
    merged: list[Finding] = []
    for group_ids in groups:
        valid_ids = list(dict.fromkeys(item for item in group_ids if item in by_id and item not in used))
        if len(valid_ids) < 2:
            continue
        items = [by_id[item] for item in valid_ids]
        merged.append(_merge_group(items))
        used.update(valid_ids)
    merged.extend(item for item in findings if item.finding_id not in used)
    source_to_canonical = {
        raw_id: item.finding_id
        for item in merged
        for raw_id in (item.merged_finding_ids or [item.finding_id])
    }
    return DeduplicationResult(merged, source_to_canonical)


def remap_test_intents(
    intents: list[TestIntent], source_to_canonical: dict[str, str]
) -> list[TestIntent]:
    """把 Reviewer 生成的 raw Finding ID 改写成 canonical ID。"""

    remapped: list[TestIntent] = []
    for intent in intents:
        copied = intent.model_copy(deep=True)
        copied.related_finding_ids = list(
            dict.fromkeys(source_to_canonical.get(item, item) for item in intent.related_finding_ids)
        )
        remapped.append(copied)
    return remapped
