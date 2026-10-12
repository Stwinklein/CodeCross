"""评测 CLI 的参数和断点文件契约测试；不访问模型或公开网络。"""

from pathlib import Path

from codecross.evaluation.cli import (
    _safe_task_name,
    _select_swe_score_findings,
    _write_case_result_atomic,
    parse_args,
)
from codecross.evaluation.models import (
    CaseEvaluationResult,
    EvaluationOutput,
    EvaluationScores,
    PredictedFinding,
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
