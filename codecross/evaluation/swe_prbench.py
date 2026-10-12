from __future__ import annotations

"""SWE-PRBench（350 PR / 65 repositories）公开集适配器。

数据与 frozen contexts 来自官方 Hugging Face 仓库，license 为 CC BY 4.0。
adapter 只把 human review comments 转为 evaluator reference；Reviewer 输入只有 diff
和指定的 frozen context，ground truth 不会进入 Prompt。
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from .models import EvaluationCase, ReferenceFinding


DATASET_BASE = "https://huggingface.co/datasets/foundry-ai/swe-prbench/resolve/main/dataset"
SCHEMA_DEV_TASKS = {"prowler__9865"}


def _download_json(url: str, target: Path) -> Any:
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    response = httpx.get(url, follow_redirects=True, timeout=120.0)
    response.raise_for_status()
    target.write_bytes(response.content)
    return response.json()


def load_eval_100(cache_dir: str | Path) -> list[dict[str, Any]]:
    """下载官方论文使用的 100-PR stratified split，并做本地可删除缓存。"""

    cache = Path(cache_dir)
    data = _download_json(
        f"{DATASET_BASE}/evals/eval_100.json",
        cache / "eval_100.json",
    )
    if not isinstance(data, list):
        raise ValueError("SWE-PRBench eval_100.json 应为 JSON list。")
    return data


def load_all_prs(cache_dir: str | Path) -> list[dict[str, Any]]:
    """Read the official 350-PR JSONL, downloading it once if not cached."""

    target = Path(cache_dir) / "prs.jsonl"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", f"{DATASET_BASE}/prs.jsonl", follow_redirects=True, timeout=120.0) as response:
            response.raise_for_status()
            with target.open("wb") as stream:
                for chunk in response.iter_bytes():
                    stream.write(chunk)
    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records or len({str(item["task_id"]) for item in records}) != len(records):
        raise ValueError("SWE-PRBench prs.jsonl is empty or has duplicate task IDs")
    return records


def _eligible_comments(annotation: dict[str, Any]) -> list[dict[str, Any]]:
    """Match eval_harness.loader's ground-truth selection exactly."""

    comments = annotation.get("comments") or []
    substantive_ids = set(annotation.get("substantive_comment_ids") or [])
    if substantive_ids:
        return [item for item in comments if item.get("comment_id") in substantive_ids]
    return [item for item in comments if item.get("is_initiating_comment")]


def _source_hash(path: Path, parsed: Any) -> str:
    """Hash exact cached bytes; use canonical fixture data when downloads are mocked."""

    data = path.read_bytes() if path.exists() else json.dumps(parsed, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def select_repository_diverse_tasks(
    records: list[dict[str, Any]],
    *,
    count: int,
    seed: str = "codecross-v0.2-holdout",
    excluded_task_ids: set[str] | None = None,
) -> list[str]:
    """用稳定 hash 选择尽量不同 repository 的盲测任务，不读取 comments。"""

    excluded = SCHEMA_DEV_TASKS | (excluded_task_ids or set())
    eligible = [item for item in records if str(item["task_id"]) not in excluded]
    ranked = sorted(
        eligible,
        key=lambda item: hashlib.sha256(
            f"{seed}:{item['task_id']}".encode("utf-8")
        ).hexdigest(),
    )
    selected: list[str] = []
    seen_repositories: set[str] = set()
    for item in ranked:
        repo = str(item["repo"])
        if repo in seen_repositories:
            continue
        selected.append(str(item["task_id"]))
        seen_repositories.add(repo)
        if len(selected) >= count:
            return selected
    for item in ranked:
        task_id = str(item["task_id"])
        if task_id not in selected:
            selected.append(task_id)
        if len(selected) >= count:
            break
    return selected


def write_holdout_manifest(
    records: list[dict[str, Any]],
    path: str | Path,
    *,
    count: int,
    seed: str = "codecross-v0.2-holdout",
    excluded_task_ids: set[str] | None = None,
    context_config: str = "config_A",
) -> list[str]:
    """创建真正封存的 manifest；选择只使用 task_id/repository，不读取 golden。"""

    task_ids = select_repository_diverse_tasks(
        records,
        count=count,
        seed=seed,
        excluded_task_ids=excluded_task_ids,
    )
    payload = {
        "schema_version": 1,
        "benchmark": "foundry-ai/swe-prbench",
        "source_split": "eval_100",
        "context_config": context_config,
        "seed": seed,
        "status": "sealed_not_run",
        "dataset_version": "swe-prbench-v0.4.1-eval_100",
        "schema_dev_exclusions": sorted(SCHEMA_DEV_TASKS),
        "additional_exclusions": sorted(excluded_task_ids or set()),
        "task_ids": task_ids,
        "development_task_ids": [],
        "sealed_task_ids": task_ids,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return task_ids


def load_swe_prbench_cases(
    *,
    cache_dir: str | Path,
    manifest_path: str | Path,
    context_config: str = "config_C",
    limit: int | None = None,
) -> list[EvaluationCase]:
    """按封存 manifest 加载 case；只在 evaluator reference 中放人类评论。"""

    cache = Path(cache_dir)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    source_split = str(manifest.get("source_split") or "eval_100")
    if source_split == "eval_100":
        source_records = load_eval_100(cache)
    elif source_split == "all_prs":
        source_records = load_all_prs(cache)
    else:
        raise ValueError(f"unsupported SWE-PRBench source_split: {source_split}")
    records = {str(item["task_id"]): item for item in source_records}
    task_ids = [str(item) for item in manifest.get("task_ids") or []]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("SWE-PRBench manifest has duplicate task IDs")
    manifest_hash = hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
    development_ids = {
        str(item) for item in manifest.get("development_task_ids") or []
    }
    sealed_ids = {str(item) for item in manifest.get("sealed_task_ids") or []}
    dataset_version = str(
        manifest.get("dataset_version") or "swe-prbench-v0.4.1-eval_100"
    )
    if limit is not None:
        task_ids = task_ids[:limit]
    cases: list[EvaluationCase] = []
    for task_id in task_ids:
        if task_id in SCHEMA_DEV_TASKS:
            raise ValueError(f"schema-dev task 禁止进入 holdout: {task_id}")
        record = records.get(task_id)
        if record is None:
            raise KeyError(f"SWE-PRBench manifest 包含未知 task_id: {task_id}")
        if task_id in development_ids:
            partition = "development"
        elif task_id in sealed_ids:
            partition = "sealed"
        else:
            # 兼容早期只有 task_ids 的 manifest，但必须明确标成 unspecified，
            # 不能默认宣称 holdout/sealed。
            partition = "unspecified"
        context = _download_json(
            f"{DATASET_BASE}/contexts/{context_config}/{task_id}.json",
            cache / "contexts" / context_config / f"{task_id}.json",
        )
        # official harness 的权威 golden 是 annotations，而不是 eval row 中为展示方便
        # 冗余的 human_review_comments。当前版本两者多数相同，但 annotation 还包含
        # 稳定 comment_id、is_in_diff、requires_change 等协议字段；以后数据清洗更新时
        # 也只应跟随 annotation，避免评测分母静默漂移。
        annotation = _download_json(
            f"{DATASET_BASE}/annotations/{task_id}_human.json",
            cache / "annotations" / f"{task_id}_human.json",
        )
        eligible_comments = _eligible_comments(annotation)
        references = []
        for comment in eligible_comments:
            body = str(comment.get("body") or "").strip()
            references.append(
                ReferenceFinding(
                    description=body,
                    comment_id=str(comment.get("comment_id") or ""),
                    file_path=str(comment.get("file") or ""),
                    line_start=comment.get("line"),
                    line_end=comment.get("line"),
                )
            )
        cases.append(
            EvaluationCase(
                case_id=f"swe-prbench:{task_id}",
                name=str(record.get("title") or task_id),
                diff=str(record.get("diff_patch") or ""),
                reference_findings=references,
                prebuilt_context=str(context.get("rendered") or ""),
                prebuilt_context_name=f"swe-prbench/{context_config}@v0.4.1",
                # 官方 config_A/B/C 都以 diff 为基础逐层增加信息；rendered 文本已经
                # 包含 patch，再拼接 record.diff_patch 会把同一变更输入两次。
                prebuilt_context_owns_diff=True,
                review_profile="human_review_alignment",
                metadata={
                    "source": "swe-prbench",
                    "task_id": task_id,
                    "repository": str(record.get("repo") or ""),
                    "pr_url": str(record.get("pr_url") or ""),
                    "language": str(record.get("language") or ""),
                    "difficulty": str(record.get("difficulty") or ""),
                    "base_commit": str(record.get("base_commit") or ""),
                    "head_commit": str(record.get("head_commit") or ""),
                    "changed_files": list(record.get("changed_files") or []),
                    "context_mode": f"frozen_{context_config}",
                    "source_split": source_split,
                    "manifest_sha256": manifest_hash,
                    "annotation_sha256": _source_hash(cache / "annotations" / f"{task_id}_human.json", annotation),
                    "context_sha256": _source_hash(cache / "contexts" / context_config / f"{task_id}.json", context),
                    "annotation_source": f"annotations/{task_id}_human.json",
                    "annotation_comment_ids": [
                        str(comment.get("comment_id") or "")
                        for comment in eligible_comments
                    ],
                    "license": "CC BY 4.0",
                    "dataset_version": dataset_version,
                    "benchmark_partition": partition,
                    "split": ["public", "swe-prbench", partition],
                },
            )
        )
    return cases
