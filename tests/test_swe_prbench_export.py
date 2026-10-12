"""Official AgentOutput compatibility fixtures, without judge API calls."""

from codecross.evaluation.models import EvaluationCase, PredictedFinding
from codecross.evaluation.swe_prbench_export import official_agent_output


def test_export_matches_v041_agent_output_shape():
    case = EvaluationCase(
        case_id="swe-prbench:demo__1", name="demo",
        diff="diff --git a/a.py b/a.py\n@@ -4,1 +4,1 @@\n-old\n+new\n",
        metadata={"task_id": "demo__1"},
    )
    finding = PredictedFinding(
        description="New branch drops a required check", severity="high",
        file_path="a.py", line_start=4,
    )

    output = official_agent_output(
        case, [finding], context_config="config_A", model_label="codecross",
    )

    assert set(output) == {"task_id", "config_name", "model", "raw_response", "comments", "parse_success", "parse_error"}
    assert output["comments"] == [{
        "comment_id": "demo__1_config_A_0",
        "body": "New branch drops a required check",
        "file_reference": "a.py", "line_reference": 4,
        "severity_claim": "P1", "is_outside_diff": False,
    }]
    assert output["parse_success"] is True


def test_missing_result_exports_hard_failure_not_empty_success():
    case = EvaluationCase(case_id="swe-prbench:demo__2", name="demo", diff="", metadata={"task_id": "demo__2"})

    output = official_agent_output(
        case, [], context_config="config_A", model_label="codecross",
        parse_error="missing_codecross_result",
    )

    assert output["comments"] == []
    assert output["parse_success"] is False
    assert output["parse_error"] == "missing_codecross_result"
