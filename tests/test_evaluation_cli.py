"""评测 CLI 的参数和断点文件契约测试；不访问模型或公开网络。"""

from pathlib import Path
import json

import codecross.evaluation.cli as eval_cli

from codecross.evaluation.cli import (
    _safe_task_name,
    _select_swe_score_findings,
    _write_case_result_atomic,
    parse_args,
)
from codecross.evaluation.models import (
    CaseEvaluationResult,
    EvaluationCase,
    EvaluationOutput,
    EvaluationScores,
    PredictedFinding,
    ReferenceFinding,
)


def test_swe_prbench_run_parses_recoverable_options(tmp_path: Path):
    args = parse_args(
        [
            "swe-prbench-run",
            "--context-config",
            "config_A",
            "--task-id",
            "repo__1",
            "--task-id",
            "repo__2",
            "--output-dir",
            str(tmp_path),
            "--skip-existing",
            "--real-models",
        ]
    )

    assert args.task_id == ["repo__1", "repo__2"]
    assert args.context_config == "config_A"
    assert args.skip_existing is True
    assert args.real_models is True


def test_atomic_case_writer_leaves_complete_json(tmp_path: Path):
    result = CaseEvaluationResult(
        case_id="demo",
        name="demo",
        output=EvaluationOutput(),
        scores=EvaluationScores(),
    )
    target = tmp_path / "result.json"

    _write_case_result_atomic(result, target)

    assert target.exists()
    assert '"case_id": "demo"' in target.read_text(encoding="utf-8")
    assert not target.with_suffix(".json.tmp").exists()


def test_safe_task_name_removes_path_separators():
    assert _safe_task_name("org/repo:1") == "org_repo_1"


def test_swe_score_requires_explicit_result_files(tmp_path: Path):
    result = tmp_path / "case.json"
    args = parse_args(["swe-prbench-score", "--result", str(result)])

    assert args.result == [result]
    assert args.context_config == "config_A"
    assert args.finding_layer == "final"


def test_swe_score_can_select_raw_layer(tmp_path: Path):
    result = tmp_path / "case.json"
    args = parse_args(
        ["swe-prbench-score", "--result", str(result), "--finding-layer", "raw"]
    )

    assert args.finding_layer == "raw"


def test_swe_score_layer_selection_does_not_fallback_when_layer_is_empty():
    """verified 为空时必须返回空列表，不能暗中拿 final 冒充验证层。"""

    final = PredictedFinding(description="final finding", severity="medium")
    raw = PredictedFinding(description="raw finding", severity="medium")
    output = EvaluationOutput(findings=[final], raw_findings=[raw])

    assert _select_swe_score_findings(output, "raw") == [raw]
    assert _select_swe_score_findings(output, "final") == [final]
    assert _select_swe_score_findings(output, "verified") == []


def test_langsmith_swe_defaults_to_sealed_partition():
    args = parse_args(["langsmith-swe-prbench"])

    assert args.partition == "sealed"
    assert args.context_config == "config_A"


def test_langsmith_public_can_enable_multi_judge():
    args = parse_args(["langsmith-public", "--multi-judge"])

    assert args.multi_judge is True


def test_judge_calibration_requires_input_and_accepts_output(tmp_path: Path):
    cases = tmp_path / "calibration.jsonl"
    output = tmp_path / "report.json"
    args = parse_args(
        ["judge-calibrate", "--cases", str(cases), "--output", str(output)]
    )

    assert args.cases == cases
    assert args.output == output


def test_swe_score_uses_manifest_denominator_and_reports_missing(monkeypatch, tmp_path: Path):
    cases = [
        EvaluationCase(
            case_id=f"swe-prbench:demo__{index}", name="demo", diff="",
            reference_findings=[ReferenceFinding(description="issue")],
            metadata={"task_id": f"demo__{index}", "manifest_sha256": "fixture"},
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(eval_cli, "load_swe_prbench_cases", lambda **kwargs: cases)
    result = CaseEvaluationResult(
        case_id=cases[0].case_id, name="demo",
        output=EvaluationOutput(board_metrics={"reviewer_count": 1, "reviewer_success_count": 1}),
        scores=EvaluationScores(),
    )
    result_path = tmp_path / "case.json"
    result_path.write_text(result.model_dump_json(), encoding="utf-8")
    report_path = tmp_path / "report.json"

    eval_cli.main([
        "swe-prbench-score", "--result", str(result_path), "--output", str(report_path),
    ])
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["case_count"] == 2
    assert report["valid_case_count"] == 1
    assert report["valid_run_rate"] == 0.5
    assert report["missing_case_ids"] == [cases[1].case_id]
    assert report["no_output_case_ids"] == [cases[0].case_id]
    assert report["overall_score"] is None
