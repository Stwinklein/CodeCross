"""Finding 归一化、去重和 provenance 测试。"""

from codecross.deduplication import (
    deduplicate_findings,
    remap_test_intents,
    semantic_deduplicate_findings,
    merge_explicit_finding_groups,
)
from codecross.schemas import Finding, LineRange, TestIntent


def _finding(finding_id: str, reviewer: str, problem: str, *, line: int = 10) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_reviewer=reviewer,
        model="test/model",
        category="runtime",
        severity="high",
        file_path="src/service.py",
        line_range=LineRange(start=line, end=line + 1),
        problem=problem,
        evidence="nullable member is dereferenced before a guard",
        impact="A request raises AttributeError at runtime.",
        suggestion="Guard the nullable member before dereferencing it.",
    )


def test_deduplicates_same_root_cause_and_preserves_sources():
    first = _finding("f1", "logic", "Nullable member is dereferenced without a guard.")
    second = _finding("f2", "security", "Missing null guard causes member dereference to fail.")

    result = deduplicate_findings([first, second])

    assert len(result.findings) == 1
    canonical = result.findings[0]
    assert canonical.source_reviewers == ["logic", "security"]
    assert canonical.merged_finding_ids == ["f1", "f2"]
    assert canonical.fingerprint
    assert result.source_to_canonical == {"f1": canonical.finding_id, "f2": canonical.finding_id}


def test_keeps_nearby_but_different_root_causes_separate():
    nullable = _finding("f1", "logic", "Nullable member is dereferenced without a guard.")
    timeout = _finding(
        "f2", "security", "Outbound request has no timeout and can hang workers.", line=12
    )
    timeout.evidence = "HTTP client is called without a timeout parameter."
    timeout.impact = "A stalled upstream can exhaust all workers."

    result = deduplicate_findings([nullable, timeout])

    assert len(result.findings) == 2


def test_human_mode_ignores_template_fields_when_deduplicating_same_line():
    """相同 evidence 模板不能把同一行的不同 human concerns 传递式合并。"""

    brittle_match = _finding(
        "f1",
        "logic",
        "Comparing regex source strings is brittle and misses equivalent protocols.",
    )
    normalize_behavior = _finding(
        "f2",
        "maintainer",
        "Skipping validation when normalize is enabled accepts malformed URLs.",
    )
    # 模拟 lightweight benchmark mapping：辅助字段完全相同，只有 problem 是模型原文。
    normalize_behavior.evidence = brittle_match.evidence
    normalize_behavior.impact = brittle_match.impact
    normalize_behavior.suggestion = brittle_match.suggestion

    result = deduplicate_findings(
        [brittle_match, normalize_behavior],
        preserve_human_concerns=True,
    )

    assert len(result.findings) == 2


def test_human_mode_still_merges_high_overlap_paraphrases():
    """保守模式仍应合并真正高度重合的跨 Reviewer 重复。"""

    first = _finding(
        "f1",
        "logic",
        "Regex source string comparison is brittle for equivalent custom protocol patterns.",
    )
    second = _finding(
        "f2",
        "maintainer",
        "Regex source string comparison is brittle for equivalent custom protocol regex patterns.",
    )

    result = deduplicate_findings(
        [first, second],
        preserve_human_concerns=True,
    )

    assert len(result.findings) == 1


def test_remaps_test_intent_to_canonical_finding():
    result = deduplicate_findings(
        [
            _finding("f1", "logic", "Nullable member is dereferenced without a guard."),
            _finding("f2", "security", "Missing null guard causes member dereference to fail."),
        ]
    )
    intent = TestIntent(
        source_reviewer="logic",
        target_file="src/service.py",
        scenario="member is null",
        reason="reproduce failure",
        expected_behavior="request is handled",
        risk_category="runtime",
        related_finding_ids=["f1", "f2"],
    )

    [remapped] = remap_test_intents([intent], result.source_to_canonical)

    assert remapped.related_finding_ids == [result.findings[0].finding_id]


def test_semantic_second_stage_merges_paraphrases_and_keeps_raw_provenance():
    first = _finding("f1", "logic", "Negative slicing on a queryset raises at evaluation.")
    first.evidence = "The lower bound is below zero before query execution."
    second = _finding("f2", "security", "ORM pagination receives an unsupported reverse offset.")
    second.evidence = "Django rejects the computed offset used by the paginator."
    deterministic = deduplicate_findings([first, second])
    assert len(deterministic.findings) == 2

    semantic = semantic_deduplicate_findings(
        deterministic.findings,
        matcher=lambda left, right: True,
    )

    assert len(semantic.findings) == 1
    assert semantic.findings[0].merged_finding_ids == ["f1", "f2"]


def test_semantic_second_stage_can_merge_same_pattern_across_files():
    first = _finding("f1", "logic", "Async forEach callbacks are not awaited.")
    first.evidence = "forEach async callback creates fire-and-forget promises"
    second = _finding("f2", "security", "Async forEach callbacks are not awaited.")
    second.file_path = "src/other.ts"
    second.evidence = "forEach async callback creates fire-and-forget promises"

    result = semantic_deduplicate_findings(
        deduplicate_findings([first, second]).findings,
        matcher=lambda left, right: True,
    )

    assert len(result.findings) == 1


def test_batch_merge_ignores_overlapping_or_unknown_ids():
    first = _finding("f1", "logic", "Same async defect.")
    second = _finding("f2", "security", "Same async defect.")
    third = _finding("f3", "logic", "Different defect.", line=40)

    result = merge_explicit_finding_groups(
        [first, second, third],
        [["f1", "f2"], ["f2", "f3"], ["unknown", "f3"]],
    )

    assert len(result.findings) == 2
    assert result.source_to_canonical["f1"] == result.source_to_canonical["f2"]
    assert result.source_to_canonical["f3"] != result.source_to_canonical["f1"]
