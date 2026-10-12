from __future__ import annotations

"""LangSmith 适配层。

核心原则是“平台只负责存储和展示，评分口径仍由仓库版本控制”。因此本模块只做
EvaluationCase 与 LangSmith example 的映射，并把本地 scorer 包装成 evaluator。
"""

import os
from pathlib import Path
from typing import Any, Callable, Literal, Sequence
from uuid import NAMESPACE_URL, uuid5

from langsmith import Client

from ..config import load_project_config
from .models import EvaluationCase, EvaluationOutput
from .runner import _codecross_version_metadata, state_to_evaluation_output
from .scorers import score_output


def _example_id(dataset_name: str, case_id: str) -> str:
    """生成稳定 UUID；重复上传同一 case 会覆盖，而不是制造重复行。"""

    return str(uuid5(NAMESPACE_URL, f"codecross:{dataset_name}:{case_id}"))


def create_langsmith_client() -> Client:
    """只在显式配置凭据后创建客户端，避免匿名请求产生难懂的 401。"""

    if not os.getenv("LANGSMITH_API_KEY") and not os.getenv("LANGCHAIN_API_KEY"):
        raise RuntimeError(
            "缺少 LANGSMITH_API_KEY：本地评测已可运行，但上传 dataset/experiment 前"
            "需要在被 gitignore 的 .env 中配置 LangSmith Key。"
        )
    return Client()


def build_langsmith_examples(
    cases: Sequence[EvaluationCase],
    *,
    dataset_name: str,
) -> list[dict[str, Any]]:
    """把本地 case 映射为 LangSmith 的 inputs/reference outputs/metadata。"""

    examples: list[dict[str, Any]] = []
    for case in cases:
        example = {
            "id": _example_id(dataset_name, case.case_id),
            "inputs": {
                "case_id": case.case_id,
                "name": case.name,
                "diff": case.diff,
                "project_root": case.project_root,
                "prebuilt_context": case.prebuilt_context,
                "prebuilt_context_name": case.prebuilt_context_name,
                "prebuilt_context_owns_diff": case.prebuilt_context_owns_diff,
                "review_profile": case.review_profile,
                # LangSmith target 默认拿不到 example.metadata；显式放进 inputs，平台
                # 重跑时才能恢复 dataset_version/language/repository 等可复现信息。
                "case_metadata": case.metadata,
            },
            "outputs": {
                "expected_risk_level": case.expected_risk_level,
                "reference_findings": [
                    finding.model_dump(mode="json") for finding in case.reference_findings
                ],
            },
            "metadata": {"case_id": case.case_id, **case.metadata},
        }
        # LangSmith 的 split 是一等字段，不能只塞进 metadata；显式映射后可在 UI
        # 按 local/public、安全/运行时等切片比较实验结果。
        if case.metadata.get("split"):
            example["split"] = case.metadata["split"]
        examples.append(example)
    return examples


def upload_cases(
    client: Client,
    dataset_name: str,
    cases: Sequence[EvaluationCase],
    *,
    description: str = "CodeCross code-review evaluation dataset",
) -> Any:
    """创建或更新数据集；重复运行时更新稳定 UUID 对应的 example。

    LangSmith 的批量 ``create_examples`` 对重复 ID 返回 409，并不自动 upsert。
    因此先查询本批 ID：不存在的走 create，已存在的走 update。这个显式分支也让
    控制台/CI 可以准确看到本次究竟新增还是更新了多少条数据。
    """

    if not client.has_dataset(dataset_name=dataset_name):
        client.create_dataset(
            dataset_name,
            description=description,
            metadata={"application": "codecross", "schema_version": 1},
        )
    examples = build_langsmith_examples(cases, dataset_name=dataset_name)
    requested_ids = [example["id"] for example in examples]
    existing_ids = {
        str(example.id)
        for example in client.list_examples(
            dataset_name=dataset_name,
            example_ids=requested_ids,
            limit=len(requested_ids),
        )
    }
    creates = [example for example in examples if str(example["id"]) not in existing_ids]
    updates = [example for example in examples if str(example["id"]) in existing_ids]
    if creates:
        client.create_examples(
            dataset_name=dataset_name,
            examples=creates,
            max_concurrency=1,
        )
    if updates:
        client.update_examples(dataset_name=dataset_name, updates=updates)
    return {"created": len(creates), "updated": len(updates), "total": len(examples)}


def case_from_langsmith(
    inputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> EvaluationCase:
    reference_outputs = reference_outputs or {}
    return EvaluationCase(
        case_id=str(inputs.get("case_id") or "unknown"),
        name=str(inputs.get("name") or inputs.get("case_id") or "unnamed"),
        diff=str(inputs.get("diff") or ""),
        project_root=inputs.get("project_root"),
        prebuilt_context=inputs.get("prebuilt_context"),
        prebuilt_context_name=inputs.get("prebuilt_context_name"),
        prebuilt_context_owns_diff=bool(inputs.get("prebuilt_context_owns_diff", False)),
        review_profile=inputs.get("review_profile") or "defect_detection",
        metadata=dict(inputs.get("case_metadata") or {}),
        expected_risk_level=reference_outputs.get("expected_risk_level"),
        reference_findings=reference_outputs.get("reference_findings") or [],
    )


def risk_accuracy_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> dict[str, Any]:
    """LangSmith custom evaluator：风险等级是否与 reference 一致。"""

    case = case_from_langsmith(inputs, reference_outputs)
    score = score_output(case, EvaluationOutput.model_validate(outputs)).risk_accuracy
    return {
        "key": "risk_accuracy",
        "score": score,
        "comment": "reference 未提供风险等级" if score is None else None,
    }


def finding_metrics_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """一次返回 precision/recall/F1，三者共用同一份一对一匹配结果。"""

    metrics = outputs.get("board_metrics") or {}
    reviewer_count = int(metrics.get("reviewer_count") or 0)
    reviewer_success_count = int(metrics.get("reviewer_success_count") or 0)
    critic_error = str(metrics.get("critic_error") or "")
    dedup_error = str(metrics.get("semantic_dedup_error") or "")
    if (reviewer_count > 0 and reviewer_success_count < reviewer_count) or critic_error or dedup_error:
        # 空输出不能因为没有 FP 获得漂亮 Precision，也不应被当成模型能力为 0；
        # 它属于工程有效性失败，由 valid_review_run 单独统计。
        return [
            {
                "key": key,
                "score": None,
                "comment": "Reviewer 运行无效；质量分不参与聚合，请查看 valid_review_run。",
            }
            for key in ("finding_precision", "finding_recall", "finding_f1")
        ]
    case = case_from_langsmith(inputs, reference_outputs)
    scores = score_output(case, EvaluationOutput.model_validate(outputs))
    return [
        {"key": "finding_precision", "score": scores.precision},
        {"key": "finding_recall", "score": scores.recall},
        {"key": "finding_f1", "score": scores.f1},
    ]


def finding_layer_metrics_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """同时展示三层确定性 F1，直观看出去重/验证究竟改善还是损伤结果。"""

    metrics = outputs.get("board_metrics") or {}
    reviewer_count = int(metrics.get("reviewer_count") or 0)
    reviewer_success_count = int(metrics.get("reviewer_success_count") or 0)
    critic_error = str(metrics.get("critic_error") or "")
    dedup_error = str(metrics.get("semantic_dedup_error") or "")
    if (reviewer_count and reviewer_success_count < reviewer_count) or critic_error or dedup_error:
        return [
            {"key": f"{layer}_finding_f1", "score": None, "comment": "invalid reviewer run"}
            for layer in ("raw", "deduplicated", "verified")
        ]
    case = case_from_langsmith(inputs, reference_outputs)
    output = EvaluationOutput.model_validate(outputs)
    results = []
    for layer, findings in (
        ("raw", output.raw_findings),
        ("deduplicated", output.deduplicated_findings or output.findings),
        ("verified", output.verified_findings),
    ):
        scores = score_output(case, output.model_copy(update={"findings": findings}))
        results.append({"key": f"{layer}_finding_f1", "score": scores.f1})
    return results


def run_validity_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """区分“Python 函数成功返回”和“Reviewer 真正完成了审查”。

    CodeCross 的 fail-open 会把模型失败转成结构化 ReviewerResult.errors，因此顶层
    Target 仍是 success。这个 evaluator 单独记录 Reviewer 成功率，并要求所有已
    配置 Reviewer 都成功，才把本次样例视为有效的质量评测运行。
    """

    metrics = outputs.get("board_metrics") or {}
    reviewer_count = int(metrics.get("reviewer_count") or 0)
    reviewer_success_count = int(metrics.get("reviewer_success_count") or 0)
    success_rate = reviewer_success_count / reviewer_count if reviewer_count else 0.0
    critic_ok = not bool(str(metrics.get("critic_error") or ""))
    dedup_ok = not bool(str(metrics.get("semantic_dedup_error") or ""))
    return [
        {"key": "reviewer_success_rate", "score": success_rate},
        {
            "key": "valid_review_run",
            "score": float(
                reviewer_count > 0 and success_rate == 1.0 and critic_ok and dedup_ok
            ),
        },
    ]


def verification_metrics_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """把逐 Finding 证据状态转成 LangSmith 可聚合反馈。

    `not_reproduced` 与 `refuted` 必须分开：前者只是本轮没复现，后者会从 verified
    输出移除候选。错误否决率使用 golden 匹配计算，直接暴露证据系统误杀真实问题。
    """

    output = EvaluationOutput.model_validate(outputs)
    statuses = [item.verification_status for item in output.findings]
    total = len(statuses)
    supported = sum(status in {"supported", "cross_supported"} for status in statuses)
    not_reproduced = statuses.count("not_reproduced")
    not_testable = statuses.count("not_testable")
    refuted_findings = [
        item
        for item in output.findings
        if item.verification_status in {"refuted", "cross_refuted"}
    ]
    refuted = len(refuted_findings)
    inconclusive = total - supported - not_reproduced - not_testable - refuted

    def rate(value: int, denominator: int = total) -> float:
        return value / denominator if denominator else 0.0

    erroneous_refutations = 0
    if refuted_findings:
        case = case_from_langsmith(inputs, reference_outputs)
        erroneous_refutations = score_output(
            case,
            output.model_copy(update={"findings": refuted_findings}),
        ).true_positives
    return [
        {"key": "verification_support_rate", "score": rate(supported)},
        {"key": "verification_not_reproduced_rate", "score": rate(not_reproduced)},
        {"key": "verification_not_testable_rate", "score": rate(not_testable)},
        {"key": "verification_refutation_rate", "score": rate(refuted)},
        {"key": "verification_inconclusive_rate", "score": rate(inconclusive)},
        {
            "key": "verification_erroneous_refutation_rate",
            "score": rate(erroneous_refutations, refuted),
        },
    ]


def build_experiment_metadata(
    *,
    dataset_name: str,
    evaluation_mode: str,
    default_project_root: str | Path,
    mock_llm: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """自动补齐 LangSmith 实验的版本、模型和数据集元数据。"""

    supplied = dict(metadata or {})
    config = load_project_config(default_project_root, mock_llm=mock_llm)
    # 延迟导入避免普通本地评测只因读取 metadata 就初始化 Judge 模块。
    from .judges import build_judge_settings

    judge = build_judge_settings()
    automatic: dict[str, Any] = {
        "application": "codecross",
        "evaluation_mode": evaluation_mode,
        "output_schema_version": 3,
        "dataset_name": dataset_name,
        "dataset_version": supplied.get("dataset_version") or dataset_name,
        "prompt_version": config.prompt_version,
        "reviewer_models": [
            f"{reviewer.provider}/{reviewer.model}" for reviewer in config.enabled_reviewers()
        ],
        "judge_model": f"{judge.provider}/{judge.model}",
        "language": supplied.get("language") or "mixed",
        **_codecross_version_metadata(),
    }
    # 调用方可以补充 benchmark/context/partition，也可以在明确需要时覆盖默认值。
    return {**automatic, **supplied}


def swe_prbench_core_evaluator(
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """SWE-PRBench 官方三分类核心指标的 LangSmith evaluator。

    未匹配 golden 的正确补充意见是 PLAUSIBLE，不作为 FPR；只有事实捏造才计入
    FPR。无效 Reviewer run 返回 None，避免平台把工程失败聚合成模型能力 0 分。
    """

    metrics = outputs.get("board_metrics") or {}
    reviewer_count = int(metrics.get("reviewer_count") or 0)
    reviewer_success_count = int(metrics.get("reviewer_success_count") or 0)
    if (
        reviewer_count <= 0
        or reviewer_success_count != reviewer_count
        or metrics.get("critic_error")
        or metrics.get("semantic_dedup_error")
    ):
        return [
            {"key": key, "score": None, "comment": "invalid reviewer run"}
            for key in (
                "swe_detection_rate",
                "swe_false_positive_rate",
                "swe_precision",
                "swe_f1",
            )
        ]

    from .swe_prbench_judge import run_swe_prbench_judge, score_swe_prbench_core

    case = case_from_langsmith(inputs, reference_outputs)
    output = EvaluationOutput.model_validate(outputs)
    decision = run_swe_prbench_judge(
        diff=case.diff,
        references=case.reference_findings,
        predictions=output.findings,
    )
    score = score_swe_prbench_core(case.reference_findings, output.findings, decision)
    return [
        {"key": "swe_detection_rate", "score": score.detection_rate},
        {"key": "swe_false_positive_rate", "score": score.false_positive_rate},
        {"key": "swe_precision", "score": score.precision},
        {"key": "swe_f1", "score": score.f1_score},
        {
            "key": "swe_plausible_rate",
            "score": (
                score.plausible_count / score.total_agent_comments
                if score.total_agent_comments
                else 0.0
            ),
        },
    ]


def build_target(
    *,
    mock_llm: bool,
    default_project_root: str | Path,
    evaluation_mode: Literal["review_only", "evidence_verification"] = "review_only",
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """把 ``run_workflow`` 包装成 LangSmith evaluate() 需要的 target 函数。"""

    from .runner import run_case

    default_root = Path(default_project_root).resolve()

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        case = case_from_langsmith(inputs, None)
        result = run_case(
            case,
            mock_llm=mock_llm,
            default_project_root=default_root,
            evaluation_mode=evaluation_mode,
        )
        return result.output.model_dump(mode="json")

    return target


def run_langsmith_experiment(
    *,
    dataset_name: str,
    experiment_prefix: str,
    mock_llm: bool,
    default_project_root: str | Path,
    metadata: dict[str, Any] | None = None,
    semantic_judge: bool = False,
    multi_judge: bool = False,
    swe_prbench_core: bool = False,
    evaluation_mode: Literal["review_only", "evidence_verification"] = "review_only",
) -> Any:
    """运行并上传实验；缺少 API key 时明确失败，不悄悄退回本地模式。"""

    if not os.getenv("LANGSMITH_API_KEY") and not os.getenv("LANGCHAIN_API_KEY"):
        raise RuntimeError("缺少 LANGSMITH_API_KEY，无法上传 LangSmith 实验。")
    from langsmith.evaluation import evaluate

    evaluators: list[Callable[..., Any]] = [
        risk_accuracy_evaluator,
        finding_metrics_evaluator,
        finding_layer_metrics_evaluator,
        run_validity_evaluator,
        verification_metrics_evaluator,
    ]
    if semantic_judge:
        # 公开 benchmark 的 golden/candidate 往往只是语义等价，词面 scorer 只能作为
        # 辅助指标；语义 judge 使用 Code Review Bench 的同问题匹配方法。
        from .judges import (
            make_multi_judge_semantic_evaluator,
            make_semantic_finding_evaluator,
        )

        evaluators.append(
            make_multi_judge_semantic_evaluator()
            if multi_judge
            else make_semantic_finding_evaluator()
        )
    if swe_prbench_core:
        evaluators.append(swe_prbench_core_evaluator)
    return evaluate(
        build_target(
            mock_llm=mock_llm,
            default_project_root=default_project_root,
            evaluation_mode=evaluation_mode,
        ),
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix=experiment_prefix,
        metadata=build_experiment_metadata(
            dataset_name=dataset_name,
            evaluation_mode=evaluation_mode,
            default_project_root=default_project_root,
            mock_llm=mock_llm,
            metadata=metadata,
        ),
        max_concurrency=1,
    )
