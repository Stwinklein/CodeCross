"""多 Judge 一致性与人工校准集的离线测试。"""

from pathlib import Path

import pytest

import codecross.evaluation.judges as judges
from codecross.config import ModelSettings
from codecross.evaluation.judges import (
    JudgeCalibrationExample,
    SemanticMatchDecision,
    calibrate_judges,
    judge_same_issue_multi,
    load_judge_calibration_jsonl,
)
from codecross.evaluation.models import PredictedFinding, ReferenceFinding


def _settings(model: str) -> ModelSettings:
    return ModelSettings(role=f"judge_{model}", provider="fixture", model=model)


def _reference(description: str) -> ReferenceFinding:
    return ReferenceFinding(description=description, file_path="a.py")


def _prediction(description: str) -> PredictedFinding:
    return PredictedFinding(description=description, severity="high", file_path="a.py")


def test_multi_judge_tie_is_conservative_and_reports_half_agreement(monkeypatch):
    """两个 Judge 一正一反时不能强行命中，应交给一致率暴露分歧。"""

    def fake_judge(reference, prediction, *, settings):
        return SemanticMatchDecision(
            reasoning=settings.model,
            match=settings.model == "positive",
            confidence=0.9,
        )

    monkeypatch.setattr(judges, "judge_same_issue", fake_judge)
    decision = judge_same_issue_multi(
        _reference("same issue"),
        _prediction("same issue"),
        settings=[_settings("positive"), _settings("negative")],
    )

    assert decision.match is False
    assert decision.agreement_rate == 0.5
    assert decision.judge_models == ["fixture/positive", "fixture/negative"]


def test_calibration_reports_per_judge_ensemble_accuracy_and_agreement(monkeypatch):
    """人工校准报告应能区分单 Judge 偏差和多数决结果。"""

    def fake_judge(reference, prediction, *, settings):
        if reference.description == "positive":
            match = True
        else:
            match = settings.model == "judge-b"
        return SemanticMatchDecision(reasoning="fixture", match=match, confidence=0.8)

    monkeypatch.setattr(judges, "judge_same_issue", fake_judge)
    examples = [
        JudgeCalibrationExample(
            calibration_id="positive",
            reference=_reference("positive"),
            prediction=_prediction("candidate"),
            expected_match=True,
        ),
        JudgeCalibrationExample(
            calibration_id="negative",
            reference=_reference("negative"),
            prediction=_prediction("candidate"),
            expected_match=False,
        ),
    ]

    report = calibrate_judges(
        examples,
        settings=[_settings("judge-a"), _settings("judge-b")],
    )

    assert report.example_count == 2
    assert report.per_judge_accuracy == {
        "fixture/judge-a": 1.0,
        "fixture/judge-b": 0.5,
    }
    assert report.ensemble_accuracy == 1.0
    assert report.mean_agreement_rate == 0.75


def test_load_calibration_jsonl_reports_bad_line(tmp_path: Path):
    """人工维护校准集时，坏 JSON 必须能定位到具体行。"""

    path = tmp_path / "calibration.jsonl"
    path.write_text("# comment\n{bad json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="第 2 行"):
        load_judge_calibration_jsonl(path)


def test_multi_judge_settings_require_explicit_secondary_model(monkeypatch):
    """没有第二模型时必须失败，不能把同一个 Judge 调两次伪装成一致性。"""

    monkeypatch.delenv("CODECROSS_EVAL_SECONDARY_JUDGE_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="SECONDARY_JUDGE_MODEL"):
        judges.build_multi_judge_settings()

