from __future__ import annotations

"""本地评测 runner：读取 JSONL，运行 CodeCross，再聚合指标。"""

import json
import subprocess
import time
import random
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from typing import Any, Literal

from ..config import load_project_config
from ..llm import capture_model_call_metrics
from ..schemas import CodeContext, CodeContextSnippet
from ..workflow import run_workflow
from .models import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationOutput,
    EvaluationScores,
    AggregateMetrics,
    ConfidenceInterval,
    PredictedFinding,
    SuiteReport,
    VerificationMetrics,
)
from .scorers import score_output


def load_cases_jsonl(path: str | Path) -> list[EvaluationCase]:
    """逐行校验评测集；错误信息保留文件和行号，方便维护 golden 数据。"""

    source = Path(path)
    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        try:
            case = EvaluationCase.model_validate_json(raw_line)
        except Exception as exc:
            raise ValueError(f"评测集 {source} 第 {line_number} 行无效: {exc}") from exc
        if case.case_id in seen_ids:
            raise ValueError(f"评测集 {source} 第 {line_number} 行包含重复 case_id: {case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    return cases


def write_cases_jsonl(cases: list[EvaluationCase], path: str | Path) -> None:
    """以一行一个 case 的形式写出，便于 Git diff 和流式处理大型公开集。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(case.model_dump_json() for case in cases) + "\n"
    target.write_text(content, encoding="utf-8")


@lru_cache(maxsize=1)
def _codecross_version_metadata() -> dict[str, str]:
    """读取当前 CodeCross 包版本和源码 commit；失败时返回显式 unknown。

    commit 必须从 CodeCross 自己的仓库读取，不能从被评测项目目录读取，否则报告
    会把 benchmark repo 的 SHA 误当成评测器版本。该函数缓存一次，避免每个 case
    都启动 Git 子进程。
    """

    try:
        package_version = version("codecross")
    except PackageNotFoundError:
        package_version = "unknown"
    codecross_root = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=codecross_root,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        git_commit = completed.stdout.strip() if completed.returncode == 0 else "unknown"
    except (OSError, subprocess.TimeoutExpired):
        git_commit = "unknown"
    return {"codecross_version": package_version, "git_commit": git_commit}


def state_to_evaluation_output(
    state: dict[str, Any],
    *,
    run_metadata: dict[str, Any] | None = None,
) -> EvaluationOutput:
    """把庞大的 LangGraph state 收敛成稳定、可版本化的评测输出。"""

    board = state.get("board_summary")
    verifications = board.finding_verifications if board else {}
    def convert(findings, *, include_verification: bool) -> list[PredictedFinding]:
        """统一转换三层 Finding，避免字段映射在各分支复制后发生漂移。"""

        converted: list[PredictedFinding] = []
        for finding in findings:
            verification = verifications.get(finding.finding_id) if include_verification else None
            converted.append(
                PredictedFinding(
                    description=finding.problem,
                    severity=finding.severity,
                    category=finding.category,
                    # 评测分层不能丢掉模型来源，否则双 Reviewer 合并后无法判断是谁
                    # 带来了 TP/FP，也无法做可信的单模型消融实验。
                    source_reviewer=finding.source_reviewer,
                    model=finding.model,
                    file_path=finding.file_path,
                    line_start=finding.line_range.start,
                    line_end=finding.line_range.end,
                    evidence=finding.evidence,
                    verification_status=verification.status if verification else "unverified",
                )
            )
        return converted

    raw_predictions = convert(state.get("raw_findings", state.get("findings", [])), include_verification=False)
    deduplicated_predictions = convert(
        state.get("deduplicated_findings", state.get("findings", [])),
        include_verification=False,
    )
    predictions = convert(state.get("findings", []), include_verification=True)
    # 只有明确 refuted/cross_refuted 才从“已验证候选”移除。not_reproduced 仍保留，
    # 因为它表示测试覆盖不足，而不是 Reviewer 已被严格证伪。
    verified_predictions = [
        item for item in predictions if item.verification_status not in {"refuted", "cross_refuted"}
    ]
    evaluator = state.get("evaluator_report")
    board_metrics: dict[str, int | str] = {}
    if board:
        reviewer_results = state.get("reviewer_results", [])
        reviewer_error_count = sum(1 for result in reviewer_results if result.errors)
        reviewer_error_details = [
            {
                "reviewer": result.reviewer_name,
                "errors": result.errors,
            }
            for result in reviewer_results
            if result.errors
        ]
        board_metrics = {
            "reviewer_count": board.reviewer_count,
            "reviewer_error_count": reviewer_error_count,
            "reviewer_success_count": max(board.reviewer_count - reviewer_error_count, 0),
            "reviewer_error_details": reviewer_error_details,
            "raw_finding_count": len(raw_predictions),
            "deduplicated_finding_count": len(deduplicated_predictions),
            "critic_rejected_count": len(state.get("rejected_findings", [])),
            "critic_error": state.get("finding_critic_error", ""),
            "semantic_dedup_error": state.get("semantic_dedup_error", ""),
            "finding_count": board.finding_count,
            "test_case_count": board.test_case_count,
            "passed_count": board.passed_count,
            "failed_count": board.failed_count,
            "error_count": board.error_count,
            "stop_reason": board.stop_reason,
        }
    config = state.get("config")
    code_context = state.get("code_context")
    context_bytes = sum(len(snippet.content.encode("utf-8")) for snippet in code_context.snippets) if code_context else 0
    context_mode = "repository_context" if code_context and code_context.snippets else "diff_only"
    return EvaluationOutput(
        risk_level=evaluator.risk_level if evaluator else None,
        findings=predictions,
        raw_findings=raw_predictions,
        deduplicated_findings=deduplicated_predictions,
        verified_findings=verified_predictions,
        evaluation_mode=config.evaluation_mode if config else "evidence_verification",
        context_mode=context_mode,
        project_profile={
            "language": config.language if config else "unknown",
            "test_framework": config.test_framework if config else "unknown",
            "package_manager": config.package_manager if config else "unknown",
            "detection_confidence": config.detection_confidence if config else "low",
            "context_bytes": context_bytes,
            "test_command": list(config.test_command) if config else [],
            "prompt_version": config.prompt_version if config else "unknown",
        },
        board_metrics=board_metrics,
        run_metadata=run_metadata or {},
    )


def run_case(
    case: EvaluationCase,
    *,
    mock_llm: bool,
    default_project_root: str | Path | None = None,
    evaluation_mode: Literal["review_only", "evidence_verification"] = "evidence_verification",
) -> CaseEvaluationResult:
    """运行单个 case；真实模型与 mock 模式共享完全相同的输出/评分路径。"""

    project_root = Path(case.project_root or default_project_root or Path.cwd()).resolve()
    config = load_project_config(
        project_root,
        mock_llm=mock_llm,
        diff=case.diff,
        evaluation_mode=evaluation_mode,
        review_profile=case.review_profile,
    )
    context_override = None
    if case.prebuilt_context:
        context_override = CodeContext(
            changed_files=list(case.metadata.get("changed_files") or []),
            snippets=[
                CodeContextSnippet(
                    kind="diagnostic",
                    file_path=f"<{case.prebuilt_context_name or 'prebuilt_context'}>",
                    reason="Frozen benchmark context; versioned by the source dataset.",
                    content=case.prebuilt_context,
                    start_line=1,
                    end_line=max(1, len(case.prebuilt_context.splitlines())),
                )
            ],
            summary_notes=[
                f"Using frozen benchmark context: {case.prebuilt_context_name or 'unknown'}"
            ],
            contains_diff=case.prebuilt_context_owns_diff,
        )
    started = time.perf_counter()
    # ContextVar 让并发 case 的模型统计互不污染；JSON repair 重试会按真实调用次数计数。
    with capture_model_call_metrics() as model_metrics:
        state = run_workflow(
            case.diff,
            project_root=project_root,
            config=config,
            code_context_override=context_override,
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    reviewer_models = [
        f"{reviewer.provider}/{reviewer.model}" for reviewer in config.enabled_reviewers()
    ]
    output = state_to_evaluation_output(
        state,
        run_metadata={
            **_codecross_version_metadata(),
            "prompt_version": config.prompt_version,
            "reviewer_models": reviewer_models,
            "dataset_version": str(case.metadata.get("dataset_version") or "unknown"),
            "dataset_source": str(case.metadata.get("source") or "local"),
            "duration_ms": duration_ms,
            "model_call_count": model_metrics.call_count,
            "model_call_failure_count": model_metrics.failure_count,
            "model_call_duration_ms": model_metrics.duration_ms,
            "model_calls_by_role": dict(model_metrics.calls_by_role),
        },
    )
    return CaseEvaluationResult(
        case_id=case.case_id,
        name=case.name,
        output=output,
        scores=score_output(case, output),
    )


def run_local_suite(
    cases: list[EvaluationCase],
    *,
    mock_llm: bool = True,
    default_project_root: str | Path | None = None,
    evaluation_mode: Literal["review_only", "evidence_verification"] = "evidence_verification",
) -> SuiteReport:
    """顺序运行本地评测；顺序执行让 API 成本和失败定位更可控。"""

    if not cases:
        raise ValueError("评测集不能为空")
    results = [
        run_case(
            case,
            mock_llm=mock_llm,
            default_project_root=default_project_root,
            evaluation_mode=evaluation_mode,
        )
        for case in cases
    ]
    def is_valid(result: CaseEvaluationResult) -> bool:
        metrics = result.output.board_metrics
        reviewer_count = int(metrics.get("reviewer_count") or 0)
        reviewer_success_count = int(metrics.get("reviewer_success_count") or 0)
        # 手工构造的旧输出没有 reviewer_count 时保持向后兼容；真实 workflow 一定有值。
        critic_ok = not bool(str(metrics.get("critic_error") or ""))
        dedup_ok = not bool(str(metrics.get("semantic_dedup_error") or ""))
        return (
            (reviewer_count == 0 or reviewer_count == reviewer_success_count)
            and critic_ok
            and dedup_ok
        )

    valid_results = [result for result in results if is_valid(result)]
    invalid_results = [result for result in results if not is_valid(result)]

    def aggregate(scores: list[EvaluationScores]) -> AggregateMetrics:
        if not scores:
            return AggregateMetrics()
        tp = sum(item.true_positives for item in scores)
        fp = sum(item.false_positives for item in scores)
        fn = sum(item.false_negatives for item in scores)
        precision = tp / (tp + fp) if tp + fp else float(fn == 0)
        recall = tp / (tp + fn) if tp + fn else float(fp == 0)
        micro_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return AggregateMetrics(
            true_positives=tp,
            false_positives=fp,
            false_negatives=fn,
            micro_precision=round(precision, 6),
            micro_recall=round(recall, 6),
            micro_f1=round(micro_f1, 6),
            macro_precision=round(fmean(item.precision for item in scores), 6),
            macro_recall=round(fmean(item.recall for item in scores), 6),
            macro_f1=round(fmean(item.f1 for item in scores), 6),
        )

    def bootstrap_intervals(
        scores: list[EvaluationScores],
        *,
        resamples: int = 1000,
        seed: int = 20260713,
    ) -> dict[str, ConfidenceInterval]:
        """对 case 做有放回重采样，报告 micro P/R/F1 的 95% 百分位区间。

        固定随机种子保证同一输入跨机器结果一致。只有一个有效 case 时区间自然退化
        为点估计，这比伪造宽度更诚实；报告中的 case_count 会同时暴露样本量不足。
        """

        if not scores:
            return {
                name: ConfidenceInterval(resamples=0)
                for name in ("micro_precision", "micro_recall", "micro_f1")
            }
        rng = random.Random(seed)
        distributions: dict[str, list[float]] = {
            "micro_precision": [],
            "micro_recall": [],
            "micro_f1": [],
        }
        for _ in range(resamples):
            sample = [scores[rng.randrange(len(scores))] for _ in range(len(scores))]
            metrics = aggregate(sample)
            for name in distributions:
                distributions[name].append(float(getattr(metrics, name)))

        point = aggregate(scores)

        def percentile(values: list[float], probability: float) -> float:
            ordered = sorted(values)
            position = (len(ordered) - 1) * probability
            lower_index = int(position)
            upper_index = min(lower_index + 1, len(ordered) - 1)
            fraction = position - lower_index
            return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction

        return {
            name: ConfidenceInterval(
                estimate=float(getattr(point, name)),
                lower=round(percentile(values, 0.025), 6),
                upper=round(percentile(values, 0.975), 6),
                confidence_level=0.95,
                resamples=resamples,
            )
            for name, values in distributions.items()
        }

    def aggregate_verification() -> VerificationMetrics:
        """聚合有效 case 的证据状态，并用 golden 计算真实 finding 误否决率。"""

        statuses: list[str] = []
        erroneous_refutations = 0
        case_by_id = {case.case_id: case for case in cases}
        for result in valid_results:
            findings = result.output.findings
            statuses.extend(item.verification_status for item in findings)
            refuted = [
                item
                for item in findings
                if item.verification_status in {"refuted", "cross_refuted"}
            ]
            if refuted:
                # 被 refuted 的候选仍命中人工 golden，说明证据系统误杀了真实问题。
                refuted_output = result.output.model_copy(update={"findings": refuted})
                erroneous_refutations += score_output(
                    case_by_id[result.case_id], refuted_output
                ).true_positives

        total = len(statuses)
        supported = sum(status in {"supported", "cross_supported"} for status in statuses)
        not_reproduced = statuses.count("not_reproduced")
        not_testable = statuses.count("not_testable")
        refuted = sum(status in {"refuted", "cross_refuted"} for status in statuses)
        inconclusive = total - supported - not_reproduced - not_testable - refuted

        def rate(value: int, denominator: int = total) -> float:
            return round(value / denominator, 6) if denominator else 0.0

        return VerificationMetrics(
            total_findings=total,
            supported_count=supported,
            not_reproduced_count=not_reproduced,
            not_testable_count=not_testable,
            refuted_count=refuted,
            inconclusive_count=inconclusive,
            erroneous_refutation_count=erroneous_refutations,
            support_rate=rate(supported),
            not_reproduced_rate=rate(not_reproduced),
            not_testable_rate=rate(not_testable),
            refutation_rate=rate(refuted),
            inconclusive_rate=rate(inconclusive),
            erroneous_refutation_rate=rate(erroneous_refutations, refuted),
        )

    layer_scores: dict[str, list[EvaluationScores]] = {
        "raw": [], "deduplicated": [], "verified": []
    }
    case_by_id = {case.case_id: case for case in cases}
    per_language_scores: dict[str, list[EvaluationScores]] = {}
    language_totals: dict[str, int] = {}
    language_valid: dict[str, int] = {}
    for result in results:
        case = case_by_id[result.case_id]
        language = str(
            case.metadata.get("language")
            or result.output.project_profile.get("language")
            or "unknown"
        ).lower()
        language_totals[language] = language_totals.get(language, 0) + 1
        if result in valid_results:
            language_valid[language] = language_valid.get(language, 0) + 1
    for result in valid_results:
        for layer, findings in (
            ("raw", result.output.raw_findings),
            ("deduplicated", result.output.deduplicated_findings or result.output.findings),
            ("verified", result.output.verified_findings),
        ):
            layer_output = result.output.model_copy(update={"findings": findings})
            # 根据 case_id 找回 reference；results 与输入顺序一致，但显式 map 更稳。
            case = case_by_id[result.case_id]
            layer_score = score_output(case, layer_output)
            layer_scores[layer].append(layer_score)
            if layer == "deduplicated":
                language = str(
                    case.metadata.get("language")
                    or result.output.project_profile.get("language")
                    or "unknown"
                ).lower()
                per_language_scores.setdefault(language, []).append(layer_score)

    layer_metrics = {name: aggregate(values) for name, values in layer_scores.items()}
    primary = layer_metrics["deduplicated"]
    risk_scores = [result.scores.risk_accuracy for result in valid_results if result.scores.risk_accuracy is not None]
    durations = [int(result.output.run_metadata.get("duration_ms") or 0) for result in results]
    model_call_count = sum(
        int(result.output.run_metadata.get("model_call_count") or 0) for result in results
    )
    model_call_failure_count = sum(
        int(result.output.run_metadata.get("model_call_failure_count") or 0)
        for result in results
    )
    reviewer_call_count = sum(
        int(result.output.board_metrics.get("reviewer_count") or 0) for result in results
    )
    reviewer_error_count = sum(
        int(result.output.board_metrics.get("reviewer_error_count") or 0) for result in results
    )
    return SuiteReport(
        case_count=len(results),
        valid_case_count=len(valid_results),
        invalid_case_count=len(invalid_results),
        valid_run_rate=round(len(valid_results) / len(results), 6),
        micro_precision=primary.micro_precision,
        micro_recall=primary.micro_recall,
        micro_f1=primary.micro_f1,
        macro_precision=primary.macro_precision,
        macro_recall=primary.macro_recall,
        macro_f1=primary.macro_f1,
        risk_accuracy=round(fmean(risk_scores), 6) if risk_scores else None,
        failed_case_ids=[result.case_id for result in valid_results if not result.scores.passed],
        invalid_case_ids=[result.case_id for result in invalid_results],
        layer_metrics=layer_metrics,
        per_language_metrics={
            language: aggregate(scores)
            for language, scores in sorted(per_language_scores.items())
        },
        language_valid_run_rate={
            language: round(language_valid.get(language, 0) / total, 6)
            for language, total in sorted(language_totals.items())
        },
        confidence_intervals=bootstrap_intervals(layer_scores["deduplicated"]),
        verification_metrics=aggregate_verification(),
        total_duration_ms=sum(durations),
        mean_duration_ms=round(fmean(durations), 3) if durations else 0.0,
        max_duration_ms=max(durations, default=0),
        model_call_count=model_call_count,
        model_call_failure_count=model_call_failure_count,
        mean_model_calls_per_case=round(model_call_count / len(results), 3),
        reviewer_call_count=reviewer_call_count,
        reviewer_error_count=reviewer_error_count,
        reviewer_error_rate=(
            round(reviewer_error_count / reviewer_call_count, 6)
            if reviewer_call_count
            else 0.0
        ),
        results=results,
    )


def write_report(report: SuiteReport, path: str | Path) -> None:
    """把完整明细写为 JSON，便于 CI artifact 或后续平台导入。"""

    Path(path).write_text(report.model_dump_json(indent=2), encoding="utf-8")
