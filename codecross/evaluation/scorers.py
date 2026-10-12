from __future__ import annotations

"""无需模型和平台的确定性评分器。

公开 code-review benchmark 通常用 LLM 判断两段描述是否指向同一问题；但本地 CI
不能依赖另一次模型调用。因此这里先按路径、类别、严重度和文本重叠做稳定的一对一
匹配。LLM judge 是额外评分器，而不是替换这条可重复的基础线。
"""

import re
from dataclasses import dataclass

from .models import (
    EvaluationCase,
    EvaluationOutput,
    EvaluationScores,
    FindingMatch,
    PredictedFinding,
    ReferenceFinding,
)


TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def _normalise_path(path: str) -> str:
    return path.strip().replace("\\", "/").removeprefix("./").lower()


def _tokens(text: str) -> set[str]:
    """使用轻量 token 集合；不做 stemming，保证跨机器结果完全一致。"""

    return {token.lower() for token in TOKEN_PATTERN.findall(text) if len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _line_overlap(reference: ReferenceFinding, prediction: PredictedFinding) -> float | None:
    """只有两边都提供行号时才让行号参与评分。"""

    if reference.line_start is None or prediction.line_start is None:
        return None
    ref_end = reference.line_end or reference.line_start
    pred_end = prediction.line_end or prediction.line_start
    return 1.0 if max(reference.line_start, prediction.line_start) <= min(ref_end, pred_end) else 0.0


def finding_similarity(reference: ReferenceFinding, prediction: PredictedFinding) -> float:
    """计算可解释的加权相似度，并按实际存在的字段重新归一化。"""

    components: list[tuple[float, float]] = []
    if reference.file_path and prediction.file_path:
        components.append((0.30, float(_normalise_path(reference.file_path) == _normalise_path(prediction.file_path))))
    if reference.category and prediction.category:
        components.append((0.15, float(reference.category.lower() == prediction.category.lower())))
    if reference.severity:
        components.append((0.10, float(reference.severity == prediction.severity)))

    expected_text = " ".join([reference.description, *reference.keywords])
    actual_text = " ".join([prediction.description, prediction.evidence])
    components.append((0.45, _jaccard(_tokens(expected_text), _tokens(actual_text))))

    line_score = _line_overlap(reference, prediction)
    if line_score is not None:
        components.append((0.15, line_score))

    total_weight = sum(weight for weight, _ in components)
    return sum(weight * score for weight, score in components) / total_weight


@dataclass(frozen=True)
class _CandidateMatch:
    score: float
    reference_index: int
    prediction_index: int


def match_findings(
    references: list[ReferenceFinding],
    predictions: list[PredictedFinding],
    *,
    threshold: float = 0.45,
) -> list[FindingMatch]:
    """按最高分贪心做一对一匹配，防止一个宽泛评论命中多个 golden。"""

    candidates = [
        _CandidateMatch(finding_similarity(reference, prediction), ref_index, pred_index)
        for ref_index, reference in enumerate(references)
        for pred_index, prediction in enumerate(predictions)
    ]
    candidates.sort(key=lambda item: (-item.score, item.reference_index, item.prediction_index))

    used_references: set[int] = set()
    used_predictions: set[int] = set()
    matches: list[FindingMatch] = []
    for candidate in candidates:
        if candidate.score < threshold:
            break
        if candidate.reference_index in used_references or candidate.prediction_index in used_predictions:
            continue
        used_references.add(candidate.reference_index)
        used_predictions.add(candidate.prediction_index)
        matches.append(
            FindingMatch(
                reference_index=candidate.reference_index,
                prediction_index=candidate.prediction_index,
                score=round(candidate.score, 6),
            )
        )
    return matches


def _safe_precision(true_positives: int, false_positives: int, false_negatives: int) -> float:
    # 对“干净 PR + 没有输出”定义 precision=1；这正是我们希望系统学会的克制行为。
    if true_positives == false_positives == false_negatives == 0:
        return 1.0
    denominator = true_positives + false_positives
    return true_positives / denominator if denominator else 0.0


def _safe_recall(true_positives: int, false_positives: int, false_negatives: int) -> float:
    if true_positives == false_positives == false_negatives == 0:
        return 1.0
    denominator = true_positives + false_negatives
    return true_positives / denominator if denominator else 0.0


def score_output(case: EvaluationCase, output: EvaluationOutput) -> EvaluationScores:
    """计算 finding 指标和风险等级准确率。"""

    matches = match_findings(case.reference_findings, output.findings)
    true_positives = len(matches)
    false_positives = len(output.findings) - true_positives
    false_negatives = len(case.reference_findings) - true_positives
    precision = _safe_precision(true_positives, false_positives, false_negatives)
    recall = _safe_recall(true_positives, false_positives, false_negatives)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    risk_accuracy = (
        None
        if case.expected_risk_level is None
        else float(output.risk_level == case.expected_risk_level)
    )
    # 本地 gate 使用严格口径：标准 finding 全部找全、没有噪声，且风险等级正确。
    passed = precision == 1.0 and recall == 1.0 and risk_accuracy in {None, 1.0}
    return EvaluationScores(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        risk_accuracy=risk_accuracy,
        matches=matches,
        passed=passed,
    )

