from __future__ import annotations

"""公开代码审查数据集使用的语义 Finding Judge。

确定性 scorer 适合本地回归，但真实 reviewer 往往会用完全不同的措辞描述同一个
问题。这里复用 Code Review Bench 的核心判定问题：“两条评论是否指向同一个底层
问题？”，并通过 CodeCross 自己的结构化模型调用层取得稳定 JSON。

提示词方法来源（MIT License）：
https://github.com/withmartian/code-review-benchmark/blob/main/offline/code_review_benchmark/step3_judge_comments.py
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean

from pydantic import Field

from ..config import ModelSettings
from ..llm import call_model_json, json_schema_instruction
from ..schemas import CodeCrossModel
from .models import PredictedFinding, ReferenceFinding


JUDGE_PROMPT = """你正在评估一个 AI 代码审查工具。

Golden Comment（期望发现的问题）：
{golden_comment}

Candidate Issue（CodeCross 实际发现的问题）：
{candidate}

请判断 Candidate 是否识别了与 Golden Comment 相同的底层问题：
- 允许措辞不同，但必须是同一个 bug、风险或代码问题；
- 仅仅位于同一文件、同属一个大类，不算匹配；
- 如果 Candidate 更宽泛，只有它明确覆盖 Golden 的核心因果关系时才算匹配；
- 不要根据你自己的代码知识补充两段文本都没有表达的事实。
"""


class SemanticMatchDecision(CodeCrossModel):
    """Judge 对一对 golden/candidate 的结构化判断。"""

    reasoning: str
    match: bool
    confidence: float = Field(ge=0.0, le=1.0)


class MultiJudgeDecision(CodeCrossModel):
    """多个独立 Judge 对同一 pair 的投票和一致性。"""

    match: bool
    confidence: float = Field(ge=0.0, le=1.0)
    agreement_rate: float = Field(ge=0.0, le=1.0)
    judge_models: list[str] = Field(default_factory=list)
    decisions: list[SemanticMatchDecision] = Field(default_factory=list)


class JudgeCalibrationExample(CodeCrossModel):
    """一条人工标注的 Judge 校准样本；不包含完整仓库或 golden 之外的信息。"""

    calibration_id: str
    reference: ReferenceFinding
    prediction: PredictedFinding
    expected_match: bool


class JudgeCalibrationReport(CodeCrossModel):
    """小规模人工校准集上的单 Judge 准确率、集成准确率和一致率。"""

    example_count: int
    per_judge_accuracy: dict[str, float] = Field(default_factory=dict)
    ensemble_accuracy: float = 0.0
    mean_agreement_rate: float = 0.0


@dataclass(frozen=True)
class SemanticMetrics:
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def build_judge_settings() -> ModelSettings:
    """从独立环境变量构造 judge，默认复用已验证的 Anthropic 通道。

    独立的 CODECROSS_EVAL_JUDGE_* 变量让后续可以使用与被测 reviewer 不同的模型，
    减少“同一个模型既答题又阅卷”的相关性偏差。
    """

    provider = os.getenv("CODECROSS_EVAL_JUDGE_PROVIDER", "anthropic")
    model = os.getenv("CODECROSS_EVAL_JUDGE_MODEL") or os.getenv(
        "ANTHROPIC_MODEL", "claude-opus-4-6"
    )
    api_key_env = os.getenv("CODECROSS_EVAL_JUDGE_API_KEY_ENV", "ANTHROPIC_API_KEY")
    base_url_env = os.getenv("CODECROSS_EVAL_JUDGE_BASE_URL_ENV", "ANTHROPIC_BASE_URL")
    return ModelSettings(
        role="evaluation_judge",
        name="code_review_bench_judge",
        provider=provider,
        model=model,
        temperature=0.0,
        max_tokens=600,
        timeout_seconds=60,
        retry_count=1,
        api_key_env=api_key_env,
        base_url_env=base_url_env,
    )


def build_multi_judge_settings() -> list[ModelSettings]:
    """构造主 Judge + 显式配置的第二 Judge；未配置时拒绝伪装成多 Judge。

    第二 Judge 使用独立环境变量，既可以是另一家 provider，也可以是同 provider 的
    不同模型版本。API key 仍只保存环境变量名，不会写入校准报告或 LangSmith metadata。
    """

    primary = build_judge_settings()
    secondary_model = os.getenv("CODECROSS_EVAL_SECONDARY_JUDGE_MODEL")
    if not secondary_model:
        raise RuntimeError(
            "启用 multi-judge 前必须配置 CODECROSS_EVAL_SECONDARY_JUDGE_MODEL"
        )
    secondary_provider = os.getenv(
        "CODECROSS_EVAL_SECONDARY_JUDGE_PROVIDER", primary.provider
    )
    secondary = ModelSettings(
        role="secondary_evaluation_judge",
        name="code_review_bench_secondary_judge",
        provider=secondary_provider,
        model=secondary_model,
        temperature=0.0,
        max_tokens=600,
        timeout_seconds=60,
        retry_count=1,
        api_key_env=os.getenv(
            "CODECROSS_EVAL_SECONDARY_JUDGE_API_KEY_ENV",
            "ANTHROPIC_API_KEY" if secondary_provider == "anthropic" else "OPENAI_API_KEY",
        ),
        base_url_env=os.getenv("CODECROSS_EVAL_SECONDARY_JUDGE_BASE_URL_ENV"),
    )
    return [primary, secondary]


def judge_same_issue(
    reference: ReferenceFinding,
    prediction: PredictedFinding,
    *,
    settings: ModelSettings,
) -> SemanticMatchDecision:
    """调用一次结构化 judge；输入只包含两条问题描述，不上传整个代码仓库。"""

    prompt = JUDGE_PROMPT.format(
        golden_comment=reference.description,
        candidate=(
            f"Problem: {prediction.description}\n"
            f"Evidence: {prediction.evidence}\n"
            f"Severity: {prediction.severity}\n"
            f"Path: {prediction.file_path}"
        ),
    )
    result = call_model_json(
        settings,
        [
            ("system", "你是严格、保守的代码审查评测员，只返回符合 schema 的 JSON。"),
            ("user", f"{prompt}\n\n{json_schema_instruction(SemanticMatchDecision)}"),
        ],
        SemanticMatchDecision,
    )
    return result  # type: ignore[return-value]


def judge_same_issue_multi(
    reference: ReferenceFinding,
    prediction: PredictedFinding,
    *,
    settings: list[ModelSettings],
) -> MultiJudgeDecision:
    """让多个 Judge 独立判断，再用多数决输出最终 match 和一致率。"""

    if len(settings) < 2:
        raise ValueError("multi-judge 至少需要两个 Judge 配置")
    decisions = [
        judge_same_issue(reference, prediction, settings=item) for item in settings
    ]
    positive = sum(decision.match for decision in decisions)
    negative = len(decisions) - positive
    # 偶数 Judge 打平时保守判为不匹配；agreement=0.5 会清楚显示需要人工抽检。
    match = positive > negative
    majority = max(positive, negative)
    winning_confidences = [
        decision.confidence for decision in decisions if decision.match == match
    ]
    return MultiJudgeDecision(
        match=match,
        confidence=round(fmean(winning_confidences), 6) if winning_confidences else 0.0,
        agreement_rate=round(majority / len(decisions), 6),
        judge_models=[f"{item.provider}/{item.model}" for item in settings],
        decisions=decisions,
    )


def load_judge_calibration_jsonl(path: str | Path) -> list[JudgeCalibrationExample]:
    """逐行加载人工校准集，并在错误信息中保留行号。"""

    source = Path(path)
    examples: list[JudgeCalibrationExample] = []
    for line_number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            examples.append(JudgeCalibrationExample.model_validate_json(raw))
        except Exception as exc:
            raise ValueError(f"Judge 校准集 {source} 第 {line_number} 行无效: {exc}") from exc
    if not examples:
        raise ValueError("Judge 校准集不能为空")
    return examples


def calibrate_judges(
    examples: list[JudgeCalibrationExample],
    *,
    settings: list[ModelSettings],
) -> JudgeCalibrationReport:
    """运行小规模人工校准，报告单模型与多数决准确率。"""

    if not examples:
        raise ValueError("Judge 校准集不能为空")
    if len(settings) < 2:
        raise ValueError("人工一致性校准至少需要两个 Judge")
    correct_by_judge = {f"{item.provider}/{item.model}": 0 for item in settings}
    ensemble_correct = 0
    agreements: list[float] = []
    for example in examples:
        decision = judge_same_issue_multi(
            example.reference,
            example.prediction,
            settings=settings,
        )
        ensemble_correct += int(decision.match == example.expected_match)
        agreements.append(decision.agreement_rate)
        for model_name, item_decision in zip(
            decision.judge_models, decision.decisions, strict=True
        ):
            correct_by_judge[model_name] += int(
                item_decision.match == example.expected_match
            )
    return JudgeCalibrationReport(
        example_count=len(examples),
        per_judge_accuracy={
            model: round(correct / len(examples), 6)
            for model, correct in correct_by_judge.items()
        },
        ensemble_accuracy=round(ensemble_correct / len(examples), 6),
        mean_agreement_rate=round(fmean(agreements), 6),
    )


def score_semantic_findings(
    references: list[ReferenceFinding],
    predictions: list[PredictedFinding],
    *,
    matcher: Callable[[ReferenceFinding, PredictedFinding], SemanticMatchDecision],
    confidence_threshold: float = 0.5,
) -> SemanticMetrics:
    """按 Code Review Bench 口径做语义匹配并计算 precision/recall/F1。

    每个 reference 和 prediction 最多使用一次。先计算全部 pair，再按 confidence
    从高到低贪心匹配，避免一条含糊 candidate 同时“命中”多个 golden。
    """

    accepted: list[tuple[float, int, int]] = []
    for reference_index, reference in enumerate(references):
        for prediction_index, prediction in enumerate(predictions):
            decision = matcher(reference, prediction)
            if decision.match and decision.confidence >= confidence_threshold:
                accepted.append((decision.confidence, reference_index, prediction_index))
    accepted.sort(key=lambda item: (-item[0], item[1], item[2]))

    matched_references: set[int] = set()
    matched_predictions: set[int] = set()
    for _, reference_index, prediction_index in accepted:
        if reference_index in matched_references or prediction_index in matched_predictions:
            continue
        matched_references.add(reference_index)
        matched_predictions.add(prediction_index)

    true_positives = len(matched_references)
    false_positives = len(predictions) - len(matched_predictions)
    false_negatives = len(references) - len(matched_references)
    if not references and not predictions:
        precision = recall = 1.0
    else:
        precision = true_positives / len(predictions) if predictions else 0.0
        recall = true_positives / len(references) if references else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return SemanticMetrics(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
    )


def make_semantic_finding_evaluator() -> Callable[..., list[dict[str, float]]]:
    """创建可直接传给 LangSmith ``evaluate`` 的语义 evaluator。"""

    from .langsmith_adapter import case_from_langsmith
    from .models import EvaluationOutput

    settings = build_judge_settings()

    def evaluator(inputs, outputs, reference_outputs):
        case = case_from_langsmith(inputs, reference_outputs)
        output = EvaluationOutput.model_validate(outputs)
        metrics = score_semantic_findings(
            case.reference_findings,
            output.findings,
            matcher=lambda reference, prediction: judge_same_issue(
                reference,
                prediction,
                settings=settings,
            ),
        )
        return [
            {"key": "semantic_finding_precision", "score": metrics.precision},
            {"key": "semantic_finding_recall", "score": metrics.recall},
            {"key": "semantic_finding_f1", "score": metrics.f1},
        ]

    evaluator.__name__ = "code_review_bench_semantic_evaluator"
    return evaluator


def make_multi_judge_semantic_evaluator() -> Callable[..., list[dict[str, float]]]:
    """创建带一致率反馈的 LangSmith 多 Judge 语义 evaluator。"""

    from .langsmith_adapter import case_from_langsmith
    from .models import EvaluationOutput

    settings = build_multi_judge_settings()

    def evaluator(inputs, outputs, reference_outputs):
        case = case_from_langsmith(inputs, reference_outputs)
        output = EvaluationOutput.model_validate(outputs)
        agreements: list[float] = []

        def matcher(reference, prediction):
            decision = judge_same_issue_multi(
                reference,
                prediction,
                settings=settings,
            )
            agreements.append(decision.agreement_rate)
            return SemanticMatchDecision(
                reasoning="multi-judge majority vote",
                match=decision.match,
                confidence=decision.confidence,
            )

        metrics = score_semantic_findings(
            case.reference_findings,
            output.findings,
            matcher=matcher,
        )
        return [
            {"key": "semantic_finding_precision", "score": metrics.precision},
            {"key": "semantic_finding_recall", "score": metrics.recall},
            {"key": "semantic_finding_f1", "score": metrics.f1},
            {
                "key": "semantic_judge_agreement_rate",
                "score": fmean(agreements) if agreements else 1.0,
            },
        ]

    evaluator.__name__ = "code_review_bench_multi_judge_evaluator"
    return evaluator
