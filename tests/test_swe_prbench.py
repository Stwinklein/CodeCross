"""SWE-PRBench adapter 使用离线 fixture，单测不访问 Hugging Face。"""

import json
from pathlib import Path

import pytest

import codecross.evaluation.swe_prbench as adapter


def test_repository_diverse_selection_is_stable_and_excludes_schema_dev():
    records = [
        {"task_id": "prowler__9865", "repo": "schema/dev"},
        {"task_id": "a__1", "repo": "org/a"},
        {"task_id": "a__2", "repo": "org/a"},
        {"task_id": "b__1", "repo": "org/b"},
        {"task_id": "c__1", "repo": "org/c"},
    ]

    first = adapter.select_repository_diverse_tasks(records, count=3, seed="fixed")
    second = adapter.select_repository_diverse_tasks(records, count=3, seed="fixed")

    assert first == second
    assert "prowler__9865" not in first
    assert len({next(item["repo"] for item in records if item["task_id"] == task) for task in first}) == 3


def test_load_cases_keeps_ground_truth_out_of_prebuilt_context(monkeypatch, tmp_path: Path):
    record = {
        "task_id": "demo__1",
        "repo": "org/demo",
        "title": "Demo PR",
        "diff_patch": "diff --git a/a.py b/a.py\n",
        "human_review_comments": [
            {"body": "A real human issue.", "path": "a.py", "line": 12, "replyTo": None},
            {"body": "reply", "path": "a.py", "line": 12, "replyTo": "parent"},
        ],
        "changed_files": ["a.py"],
    }
    monkeypatch.setattr(adapter, "load_eval_100", lambda cache: [record])
    monkeypatch.setattr(
        adapter,
        "_download_json",
        lambda url, target: (
            {
                "comments": [
                    {
                        "comment_id": "c_1",
                        "body": "The final annotated human issue.",
                        "file": "a.py",
                        "line": 12,
                        "is_reply": False,
                        "is_initiating_comment": True,
                    },
                    {
                        "comment_id": "c_reply",
                        "body": "reply",
                        "file": "a.py",
                        "is_reply": True,
                    },
                ]
            }
            if "/annotations/" in url
            else {"rendered": "Frozen context without the golden text."}
        ),
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "task_ids": ["demo__1"],
                "development_task_ids": ["demo__1"],
                "sealed_task_ids": [],
                "dataset_version": "fixture-v1",
            }
        ),
        encoding="utf-8",
    )

    [case] = adapter.load_swe_prbench_cases(
        cache_dir=tmp_path / "cache", manifest_path=manifest
    )

    assert case.reference_findings[0].description == "The final annotated human issue."
    assert len(case.reference_findings) == 1
    assert "annotated human issue" not in case.prebuilt_context
    assert case.metadata["context_mode"] == "frozen_config_C"
    assert case.metadata["annotation_comment_ids"] == ["c_1"]
    assert case.metadata["benchmark_partition"] == "development"
    assert case.metadata["dataset_version"] == "fixture-v1"
    assert case.metadata["split"] == ["public", "swe-prbench", "development"]
    assert case.prebuilt_context_owns_diff is True


def test_written_holdout_manifest_is_sealed_and_uses_config_a(tmp_path: Path):
    """新建 holdout 时必须在运行前冻结 partition、上下文配置和排除集。"""

    records = [
        {"task_id": "a__1", "repo": "org/a"},
        {"task_id": "b__1", "repo": "org/b"},
    ]
    path = tmp_path / "holdout.json"

    selected = adapter.write_holdout_manifest(
        records,
        path,
        count=1,
        seed="fixture",
        excluded_task_ids={"old__1"},
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))

    assert manifest["context_config"] == "config_A"
    assert manifest["status"] == "sealed_not_run"
    assert manifest["development_task_ids"] == []
    assert manifest["sealed_task_ids"] == selected
    assert manifest["additional_exclusions"] == ["old__1"]


def test_official_substantive_ids_override_initiating_flag():
    annotation = {
        "substantive_comment_ids": ["selected"],
        "comments": [
            {"comment_id": "selected", "is_initiating_comment": False},
            {"comment_id": "other", "is_initiating_comment": True},
        ],
    }

    assert [item["comment_id"] for item in adapter._eligible_comments(annotation)] == ["selected"]


def test_all_prs_loader_reads_jsonl_and_rejects_duplicate_ids(tmp_path: Path):
    path = tmp_path / "prs.jsonl"
    path.write_text('{"task_id":"a__1"}\n{"task_id":"b__2"}\n', encoding="utf-8")
    assert [row["task_id"] for row in adapter.load_all_prs(tmp_path)] == ["a__1", "b__2"]
    path.write_text('{"task_id":"a__1"}\n{"task_id":"a__1"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        adapter.load_all_prs(tmp_path)
