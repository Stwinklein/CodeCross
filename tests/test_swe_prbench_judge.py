"""SWE-PRBench 官方核心口径的纯本地回归测试。"""

from codecross.evaluation.models import PredictedFinding, ReferenceFinding
from codecross.evaluation.swe_prbench_judge import (
    _SYSTEM_PROMPT,
    SWEAgentClassification,
    SWEJudgeBatch,
    score_swe_prbench_core,
    _build_structured_diff_snippet,
)


def test_judge_prompt_keeps_official_partial_match_semantics():
    """DR 对措辞差异很敏感，不能再把官方 Judge 规则压缩成更严格的自定义版本。"""

    assert "at a different abstraction level" in _SYSTEM_PROMPT
    assert "without citing the exact line number" in _SYSTEM_PROMPT
    assert "Partial matches count" in _SYSTEM_PROMPT
    assert "free(NULL)" in _SYSTEM_PROMPT


def _prediction(text: str) -> PredictedFinding:
    return PredictedFinding(description=text, severity="medium", file_path="a.py")


def test_plausible_comment_is_not_counted_as_official_false_positive():
    refs = [ReferenceFinding(description="human issue", file_path="a.py")]
    predictions = [_prediction("same issue"), _prediction("extra but grounded")]
    decision = SWEJudgeBatch(
        agent_classifications=[
            SWEAgentClassification(
                comment_id="agent-0",
                classification="CONFIRMED",
                matched_human_comment_id="human-0",
                actionability_score=5,
                confidence=0.9,
            ),
            SWEAgentClassification(
                comment_id="agent-1",
                classification="PLAUSIBLE",
                actionability_score=4,
            ),
        ]
    )

    metrics = score_swe_prbench_core(refs, predictions, decision)

    assert metrics.detection_rate == 1.0
    assert metrics.false_positive_rate == 0.0
    assert metrics.precision == 0.5
    assert metrics.plausible_count == 1
    assert metrics.overall_score is None


def test_duplicate_confirmed_matches_are_normalized_one_to_one():
    refs = [ReferenceFinding(description="one issue", file_path="a.py")]
    predictions = [_prediction("duplicate A"), _prediction("duplicate B")]
    decision = SWEJudgeBatch(
        agent_classifications=[
            SWEAgentClassification(
                comment_id="agent-0",
                classification="CONFIRMED",
                matched_human_comment_id="human-0",
                actionability_score=3,
                confidence=0.7,
            ),
            SWEAgentClassification(
                comment_id="agent-1",
                classification="CONFIRMED",
                matched_human_comment_id="human-0",
                actionability_score=5,
                confidence=0.9,
            ),
        ]
    )

    metrics = score_swe_prbench_core(refs, predictions, decision)

    assert metrics.confirmed_count == 1
    assert metrics.plausible_count == 1
    assert metrics.caught_human_comments == 1
    assert metrics.matched_pairs[0]["agent_comment_id"] == "agent-1"


def test_fabricated_comment_contributes_to_fpr():
    decision = SWEJudgeBatch(
        agent_classifications=[
            SWEAgentClassification(comment_id="agent-0", classification="FABRICATED")
        ]
    )

    metrics = score_swe_prbench_core([], [_prediction("invented")], decision)

    assert metrics.false_positive_rate == 1.0
    assert metrics.fabricated_count == 1


def test_structured_diff_prefers_logic_rich_implementation_chunk():
    noisy_test = "diff --git a/tests/test_a.py b/tests/test_a.py\n" + "+assert True\n" * 30
    implementation = (
        "diff --git a/src/service.py b/src/service.py\n"
        "+if error:\n+    return parse(error)\n"
    )

    selected = _build_structured_diff_snippet(noisy_test + implementation, max_chars=120)

    assert "src/service.py" in selected
    assert "tests/test_a.py" not in selected
