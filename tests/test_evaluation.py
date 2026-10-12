"""CodeCross 离线评测与 LangSmith 适配层测试。

这些测试刻意不访问网络：本地 scorer、公开 Benchmark 解析器和 LangSmith
上传逻辑都通过固定输入或 FakeClient 验证。这样 PR CI 不需要任何平台密钥，
也不会因为 LangSmith 或模型服务暂时不可用而失去基础回归保护。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codecross.evaluation.langsmith_adapter import (
    build_experiment_metadata,
    build_langsmith_examples,
    create_langsmith_client,
    finding_metrics_evaluator,
    finding_layer_metrics_evaluator,
    risk_accuracy_evaluator,
    run_validity_evaluator,
    swe_prbench_core_evaluator,
    upload_cases,
    verification_metrics_evaluator,
)
from codecross.evaluation.judges import SemanticMatchDecision, score_semantic_findings
from codecross.evaluation.models import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationOutput,
    EvaluationScores,
    PredictedFinding,
    ReferenceFinding,
)
from codecross.evaluation.public_benchmarks import load_code_review_bench
from codecross.evaluation.runner import load_cases_jsonl, run_local_suite, state_to_evaluation_output
from codecross.evaluation.scorers import score_output
from codecross.schemas import Finding, LineRange


def _reference(problem: str, *, severity: str = "high") -> ReferenceFinding:
    return ReferenceFinding(
        description=problem,
        severity=severity,
        category="runtime",
        file_path="pkg/math_utils.py",
        keywords=["zero denominator"],
    )


def _prediction(problem: str, *, severity: str = "high") -> PredictedFinding:
    return PredictedFinding(
        description=problem,
        severity=severity,
        category="runtime",
        file_path="pkg/math_utils.py",
    )


def test_score_output_counts_true_false_positive_and_false_negative():
    """一对一匹配后应同时正确统计 TP、FP 和 FN。"""

    case = EvaluationCase(
        case_id="mixed",
        name="mixed findings",
        diff="diff --git a/a.py b/a.py\n",
        expected_risk_level="high",
        reference_findings=[
            _reference("Zero denominator raises an exception."),
            _reference("A second independent runtime bug.", severity="medium"),
        ],
    )
    output = EvaluationOutput(
        risk_level="high",
        findings=[
            _prediction("The helper does not handle a zero denominator."),
            PredictedFinding(
                description="Unrelated style suggestion.",
                severity="low",
                category="style",
                file_path="pkg/other.py",
            ),
        ],
    )

    scores = score_output(case, output)

    assert (scores.true_positives, scores.false_positives, scores.false_negatives) == (1, 1, 1)
    assert scores.precision == pytest.approx(0.5)
    assert scores.recall == pytest.approx(0.5)
    assert scores.f1 == pytest.approx(0.5)
    assert scores.risk_accuracy == 1.0


def test_score_output_treats_clean_case_as_perfect_when_no_findings():
    """干净 PR 没有预期问题且模型保持安静时，三项 finding 指标都应为 1。"""

    case = EvaluationCase(case_id="clean", name="clean", diff="diff\n", expected_risk_level="low")
    scores = score_output(case, EvaluationOutput(risk_level="low"))

    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert scores.f1 == 1.0


def test_evaluation_output_preserves_reviewer_and_model_provenance():
    """双 Reviewer 的精简评测输出仍需保留来源，才能做模型贡献消融。"""

    finding = Finding(
        source_reviewer="logic_reviewer",
        model="deepseek/deepseek-chat",
        category="runtime",
        severity="high",
        confidence="high",
        file_path="pkg/example.py",
        line_range=LineRange(start=7, end=7),
        problem="A concrete changed-line failure.",
        evidence="pkg/example.py:7",
        impact="The request fails.",
        suggestion="Handle the failing branch.",
    )

    output = state_to_evaluation_output(
        {
            "raw_findings": [finding],
            "deduplicated_findings": [finding],
            "findings": [finding],
        }
    )

    assert output.findings[0].source_reviewer == "logic_reviewer"
    assert output.findings[0].model == "deepseek/deepseek-chat"


def test_load_cases_jsonl_reports_line_number_for_invalid_case(tmp_path: Path):
    """坏数据要报告 JSONL 行号，避免公开数据集转换失败时难以定位。"""

    path = tmp_path / "cases.jsonl"
    path.write_text('{"case_id":"ok","name":"ok","diff":"x"}\n{bad json}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="第 2 行"):
        load_cases_jsonl(path)


def test_langsmith_example_and_evaluators_use_platform_independent_scores():
    """LangSmith 只消费本地标准结构，不应复制一份不同的评分算法。"""

    case = EvaluationCase(
        case_id="case-1",
        name="division",
        diff="diff --git a/a.py b/a.py\n",
        expected_risk_level="high",
        reference_findings=[_reference("Zero denominator is not handled.")],
        prebuilt_context="official frozen context with diff",
        prebuilt_context_owns_diff=True,
        metadata={"split": "local"},
    )
    [example] = build_langsmith_examples([case], dataset_name="codecross-local")
    output = {
        "risk_level": "high",
        "findings": [_prediction("Missing zero denominator handling.").model_dump(mode="json")],
    }

    assert example["inputs"]["case_id"] == "case-1"
    assert example["inputs"]["prebuilt_context_owns_diff"] is True
    assert example["inputs"]["case_metadata"] == {"split": "local"}
    assert example["outputs"]["expected_risk_level"] == "high"
    assert example["metadata"]["split"] == "local"
    assert risk_accuracy_evaluator(example["inputs"], output, example["outputs"])["score"] == 1.0
    feedback = finding_metrics_evaluator(example["inputs"], output, example["outputs"])
    assert {item["key"] for item in feedback} == {
        "finding_precision",
        "finding_recall",
        "finding_f1",
    }
    assert all(item["score"] == 1.0 for item in feedback)

    validity = run_validity_evaluator(
        example["inputs"],
        {**output, "board_metrics": {"reviewer_count": 2, "reviewer_success_count": 1}},
        example["outputs"],
    )
    assert validity == [
        {"key": "reviewer_success_rate", "score": 0.5},
        {"key": "valid_review_run", "score": 0.0},
    ]


def test_verification_evaluator_separates_not_reproduced_and_erroneous_refutation():
    """LangSmith 必须显示证据状态分布，并识别被错误否决的 golden finding。"""

    matching_refuted = _prediction("Zero denominator is not handled.").model_copy(
        update={"verification_status": "refuted"}
    )
    not_reproduced = _prediction("A separate unverified concern.").model_copy(
        update={"verification_status": "not_reproduced"}
    )
    feedback = verification_metrics_evaluator(
        {"case_id": "verify", "name": "verify", "diff": "diff"},
        EvaluationOutput(findings=[matching_refuted, not_reproduced]).model_dump(mode="json"),
        {"reference_findings": [_reference("Zero denominator is not handled.").model_dump(mode="json")]},
    )
    scores = {item["key"]: item["score"] for item in feedback}

    assert scores["verification_refutation_rate"] == 0.5
    assert scores["verification_not_reproduced_rate"] == 0.5
    assert scores["verification_erroneous_refutation_rate"] == 1.0


def test_suite_aggregates_verification_latency_and_model_calls(monkeypatch):
    """suite 报告应同时聚合质量、证据和运维指标，且错误否决只看有效运行。"""

    case = EvaluationCase(
        case_id="aggregate",
        name="aggregate",
        diff="diff",
        reference_findings=[_reference("Zero denominator is not handled.")],
        metadata={"language": "python"},
    )
    refuted = _prediction("Zero denominator is not handled.").model_copy(
        update={"verification_status": "refuted"}
    )
    output = EvaluationOutput(
        findings=[refuted],
        raw_findings=[refuted],
        deduplicated_findings=[refuted],
        verified_findings=[],
        board_metrics={
            "reviewer_count": 2,
            "reviewer_success_count": 2,
            "reviewer_error_count": 0,
        },
        run_metadata={
            "duration_ms": 120,
            "model_call_count": 3,
            "model_call_failure_count": 1,
        },
    )
    result = CaseEvaluationResult(
        case_id=case.case_id,
        name=case.name,
        output=output,
        scores=score_output(case, output),
    )

    import codecross.evaluation.runner as runner_module

    monkeypatch.setattr(runner_module, "run_case", lambda *args, **kwargs: result)
    report = runner_module.run_local_suite([case], evaluation_mode="evidence_verification")

    assert report.verification_metrics.refuted_count == 1
    assert report.verification_metrics.erroneous_refutation_count == 1
    assert report.verification_metrics.erroneous_refutation_rate == 1.0
    assert report.total_duration_ms == 120
    assert report.mean_duration_ms == 120.0
    assert report.model_call_count == 3
    assert report.model_call_failure_count == 1
    assert report.reviewer_error_rate == 0.0
    assert report.per_language_metrics["python"].micro_f1 == 1.0
    assert report.language_valid_run_rate == {"python": 1.0}
    assert report.confidence_intervals["micro_f1"].estimate == 1.0
    assert report.confidence_intervals["micro_f1"].lower == 1.0
    assert report.confidence_intervals["micro_f1"].upper == 1.0
    assert report.confidence_intervals["micro_f1"].resamples == 1000


def test_language_valid_rate_keeps_invalid_language_visible(monkeypatch):
    """某语言全部 Reviewer 失败时仍应出现在有效率切片，而不是从报告消失。"""

    python_case = EvaluationCase(
        case_id="python",
        name="python",
        diff="diff",
        metadata={"language": "python"},
    )
    go_case = EvaluationCase(
        case_id="go",
        name="go",
        diff="diff",
        metadata={"language": "go"},
    )

    def result_for(case: EvaluationCase) -> CaseEvaluationResult:
        valid = case.case_id == "python"
        output = EvaluationOutput(
            board_metrics={
                "reviewer_count": 2,
                "reviewer_success_count": 2 if valid else 1,
                "reviewer_error_count": 0 if valid else 1,
            }
        )
        return CaseEvaluationResult(
            case_id=case.case_id,
            name=case.name,
            output=output,
            scores=EvaluationScores(passed=valid),
        )

    import codecross.evaluation.runner as runner_module

    monkeypatch.setattr(
        runner_module,
        "run_case",
        lambda case, **kwargs: result_for(case),
    )
    report = runner_module.run_local_suite([python_case, go_case])

    assert report.language_valid_run_rate == {"go": 0.0, "python": 1.0}
    assert "go" not in report.per_language_metrics
    assert report.invalid_case_ids == ["go"]


def test_experiment_metadata_auto_includes_versions_and_models(monkeypatch, tmp_path: Path):
    """调用方不手填时，LangSmith 实验仍必须拥有可复现版本字段。"""

    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-versioned")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-versioned")
    monkeypatch.setenv("CODECROSS_PROMPT_VERSION", "v-test")
    metadata = build_experiment_metadata(
        dataset_name="dataset-v3",
        evaluation_mode="review_only",
        default_project_root=tmp_path,
        mock_llm=True,
        metadata={"benchmark": "fixture"},
    )

    assert metadata["dataset_name"] == "dataset-v3"
    assert metadata["dataset_version"] == "dataset-v3"
    assert metadata["prompt_version"] == "v-test"
    assert metadata["reviewer_models"] == [
        "deepseek/deepseek-versioned",
        "anthropic/claude-versioned",
    ]
    assert metadata["judge_model"] == "anthropic/claude-versioned"
    assert metadata["codecross_version"]
    assert metadata["git_commit"]


def test_invalid_reviewer_run_has_no_quality_score():
    """工程失败只计入 valid-run，不能混入质量 P/R/F1 聚合。"""

    feedback = finding_metrics_evaluator(
        {"case_id": "invalid", "name": "invalid", "diff": "diff"},
        {
            "findings": [],
            "board_metrics": {"reviewer_count": 2, "reviewer_success_count": 0},
        },
        {"reference_findings": [_reference("A real issue exists.").model_dump(mode="json")]},
    )

    assert all(item["score"] is None for item in feedback)
    assert all("valid_review_run" in item["comment"] for item in feedback)


def test_invalid_reviewer_run_skips_swe_judge_too():
    """SWE 三分类 Judge 成本较高，无效运行必须在调用 Judge 前直接跳过。"""

    feedback = swe_prbench_core_evaluator(
        {"case_id": "invalid", "name": "invalid", "diff": "diff"},
        {
            "findings": [],
            "board_metrics": {"reviewer_count": 2, "reviewer_success_count": 1},
        },
        {"reference_findings": []},
    )

    assert {item["key"] for item in feedback} == {
        "swe_detection_rate",
        "swe_false_positive_rate",
        "swe_precision",
        "swe_f1",
    }
    assert all(item["score"] is None for item in feedback)
    assert all(item["comment"] == "invalid reviewer run" for item in feedback)


def test_critic_failure_invalidates_review_run():
    output = {
        "findings": [],
        "board_metrics": {
            "reviewer_count": 2,
            "reviewer_success_count": 2,
            "critic_error": "ModelCallError: timeout",
        },
    }
    validity = run_validity_evaluator({}, output, {})
    quality = finding_metrics_evaluator(
        {"case_id": "critic-failed", "name": "critic-failed", "diff": "diff"},
        output,
        {"reference_findings": []},
    )

    assert validity[-1] == {"key": "valid_review_run", "score": 0.0}
    assert all(item["score"] is None for item in quality)


def test_layer_evaluator_reports_raw_deduplicated_and_verified():
    prediction = _prediction("Zero denominator is not handled.").model_dump(mode="json")
    feedback = finding_layer_metrics_evaluator(
        {"case_id": "layers", "name": "layers", "diff": "diff"},
        {
            "findings": [prediction],
            "raw_findings": [prediction, prediction],
            "deduplicated_findings": [prediction],
            "verified_findings": [prediction],
        },
        {"reference_findings": [_reference("Zero denominator is not handled.").model_dump(mode="json")]},
    )

    assert {item["key"] for item in feedback} == {
        "raw_finding_f1", "deduplicated_finding_f1", "verified_finding_f1"
    }
    assert next(item["score"] for item in feedback if item["key"] == "raw_finding_f1") < 1.0
    assert next(item["score"] for item in feedback if item["key"] == "deduplicated_finding_f1") == 1.0


def test_upload_cases_is_idempotent_with_deterministic_example_ids():
    """重复上传同一 case 时使用同一个 UUID，便于 LangSmith 做 upsert。"""

    class FakeClient:
        def __init__(self) -> None:
            self.created_dataset = None
            self.examples = None
            self.updates = None
            self.existing_ids = set()

        def has_dataset(self, *, dataset_name: str) -> bool:
            return False

        def create_dataset(self, dataset_name: str, **kwargs):
            self.created_dataset = (dataset_name, kwargs)
            return object()

        def create_examples(self, **kwargs):
            self.examples = kwargs
            self.existing_ids.update(example["id"] for example in kwargs["examples"])
            return {"count": len(kwargs["examples"])}

        def list_examples(self, **kwargs):
            requested = set(kwargs["example_ids"])
            return [SimpleNamespace(id=value) for value in self.existing_ids & requested]

        def update_examples(self, **kwargs):
            self.updates = kwargs
            return {"count": len(kwargs["updates"])}

    case = EvaluationCase(case_id="stable", name="stable", diff="diff\n")
    client = FakeClient()

    upload_cases(client, "codecross-local", [case])
    first_id = client.examples["examples"][0]["id"]
    result = upload_cases(client, "codecross-local", [case])
    second_id = client.updates["updates"][0]["id"]

    assert first_id == second_id
    assert client.created_dataset[0] == "codecross-local"
    assert result == {"created": 0, "updated": 1, "total": 1}


def test_semantic_score_keeps_matches_one_to_one():
    """即使 matcher 认为一条宽泛评论能命中两个 golden，也只能计一个 TP。"""

    references = [
        _reference("Zero denominator is not handled."),
        _reference("Division can overflow.", severity="medium"),
    ]
    predictions = [_prediction("The division helper has unsafe edge cases.")]

    metrics = score_semantic_findings(
        references,
        predictions,
        matcher=lambda reference, prediction: SemanticMatchDecision(
            reasoning="test decision",
            match=True,
            confidence=0.9,
        ),
    )

    assert metrics.true_positives == 1
    assert metrics.false_positives == 0
    assert metrics.false_negatives == 1
    assert metrics.precision == 1.0
    assert metrics.recall == 0.5


def test_create_langsmith_client_requires_explicit_key(monkeypatch):
    """没有平台凭据时应在任何网络请求之前给出清晰错误。"""

    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="LANGSMITH_API_KEY"):
        create_langsmith_client()


def test_load_code_review_bench_converts_public_goldens(tmp_path: Path, monkeypatch):
    """Martian Code Review Bench 的 golden comments 应转换为统一 EvaluationCase。"""

    benchmark = tmp_path / "benchmark"
    golden_dir = benchmark / "offline" / "golden_comments"
    golden_dir.mkdir(parents=True)
    (golden_dir / "demo.json").write_text(
        json.dumps(
            [
                {
                    "pr_title": "Fix worker",
                    "url": "https://github.com/acme/demo/pull/12",
                    "comments": [{"comment": "The lock can deadlock.", "severity": "High"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    results_dir = benchmark / "offline" / "results"
    results_dir.mkdir()
    (results_dir / "benchmark_data.json").write_text(
        json.dumps(
            {
                "https://github.com/acme/demo/pull/12": {
                    "golden_comments": [
                        {"comment": "The lock can deadlock.", "severity": "High"},
                        {"comment": "A later human audit found a second issue.", "severity": "Medium"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "codecross.evaluation.public_benchmarks.fetch_github_pr_diff",
        lambda url, cache_dir=None: "diff --git a/worker.py b/worker.py\n",
    )

    [case] = load_code_review_bench(benchmark, limit=1)

    assert case.case_id == "code-review-bench:acme/demo#12"
    assert case.reference_findings[0].description == "The lock can deadlock."
    assert case.reference_findings[0].severity == "high"
    assert len(case.reference_findings) == 2
    assert case.metadata["source"] == "code-review-bench"


def test_public_benchmark_manifest_filters_repository_phase(tmp_path: Path, monkeypatch):
    benchmark = tmp_path / "benchmark"
    golden = benchmark / "offline" / "golden_comments"
    golden.mkdir(parents=True)
    record = lambda url: [{"url": url, "comments": []}]
    (golden / "dev.json").write_text(
        json.dumps(record("https://github.com/acme/dev/pull/1")), encoding="utf-8"
    )
    (golden / "holdout.json").write_text(
        json.dumps(record("https://github.com/acme/holdout/pull/2")), encoding="utf-8"
    )
    manifest = tmp_path / "split.json"
    manifest.write_text(
        json.dumps({"source_file_phases": {"dev.json": "dev", "holdout.json": "holdout"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "codecross.evaluation.public_benchmarks.fetch_github_pr_diff",
        lambda url, cache_dir=None: "diff --git a/a.py b/a.py\n",
    )

    cases = load_code_review_bench(
        benchmark, split_manifest=manifest, phase="holdout"
    )

    assert [case.case_id for case in cases] == ["code-review-bench:acme/holdout#2"]
    assert cases[0].metadata["benchmark_phase"] == "holdout"


def test_state_output_keeps_precritic_deduplicated_layer():
    """Critic 删除 final finding 时，canonical 层仍需保留以便审计 recall 损失。"""

    from codecross.evaluation.runner import state_to_evaluation_output
    from codecross.schemas import Finding, LineRange

    canonical = Finding(
        source_reviewer="reviewer",
        model="model",
        category="logic",
        severity="medium",
        confidence="high",
        file_path="a.py",
        line_range=LineRange(start=1, end=1),
        problem="A grounded concern",
        evidence="a.py:1",
        impact="failure",
        suggestion="fix it",
    )
    output = state_to_evaluation_output(
        {
            "raw_findings": [canonical],
            "deduplicated_findings": [canonical],
            "findings": [],
        }
    )

    assert len(output.raw_findings) == 1
    assert len(output.deduplicated_findings) == 1
    assert output.findings == []


def test_bundled_local_suite_passes_without_network():
    """仓库自带的几个小评测必须能在 mock 模式下稳定通过。"""

    case_path = Path(__file__).resolve().parents[1] / "evals" / "local_cases.jsonl"
    report = run_local_suite(load_cases_jsonl(case_path), mock_llm=True)

    assert report.case_count >= 3
    assert report.failed_case_ids == []
    assert report.macro_f1 == 1.0
    assert report.risk_accuracy == 1.0
