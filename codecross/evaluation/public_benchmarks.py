from __future__ import annotations

"""公开代码审查 Benchmark 的导入器。

当前支持 Martian 开源的 Code Review Bench。我们只读取其 MIT 许可的 golden
comments，并从原 GitHub PR 下载公开 diff；不复制它的 20MB 工具运行结果。
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..schemas import Severity
from .models import EvaluationCase, ReferenceFinding
from .repository import RepositoryMaterializationError, materialize_github_pr


PR_PATH = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)$")


def _parse_github_pr_url(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    match = PR_PATH.match(parsed.path.rstrip("/"))
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not match:
        raise ValueError(f"只允许标准 GitHub PR URL，收到: {url}")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def fetch_github_pr_diff(url: str, cache_dir: str | Path | None = None) -> str:
    """下载 GitHub 提供的 unified diff，并在 .codecross 下做可删除缓存。"""

    owner, repo, number = _parse_github_pr_url(url)
    cache_root = Path(cache_dir or Path(".codecross") / "benchmark_diffs")
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_path = cache_root / f"{owner}__{repo}__{number}.diff"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    response = httpx.get(
        f"https://github.com/{owner}/{repo}/pull/{number}.diff",
        headers={"User-Agent": "CodeCross-Evaluation/1.0"},
        follow_redirects=True,
        timeout=60.0,
    )
    response.raise_for_status()
    diff = response.text
    if not diff.startswith("diff --git"):
        raise ValueError(f"GitHub PR {url} 没有返回可识别的 unified diff")
    cache_path.write_text(diff, encoding="utf-8")
    return diff


def _severity(value: str) -> Severity:
    normalised = value.strip().lower()
    if normalised not in {"low", "medium", "high", "critical"}:
        raise ValueError(f"公开 benchmark 包含未知 severity: {value}")
    return normalised  # type: ignore[return-value]


def load_code_review_bench(
    benchmark_root: str | Path,
    *,
    limit: int | None = None,
    cache_dir: str | Path | None = None,
    materialize_repositories: bool = False,
    repository_cache_dir: str | Path | None = None,
    require_repository: bool = False,
    split_manifest: str | Path | None = None,
    phase: str = "all",
) -> list[EvaluationCase]:
    """把 golden_comments/*.json 转成 CodeCross 统一评测 case。"""

    root = Path(benchmark_root)
    golden_dir = root / "offline" / "golden_comments"
    if not golden_dir.is_dir():
        raise FileNotFoundError(f"找不到 Code Review Bench golden_comments: {golden_dir}")

    # 仓库的 results/benchmark_data.json 是后续流水线使用的最终汇总版本，偶尔会
    # 比最初的 golden_comments/*.json 多出人工补充的 golden。评测时应优先使用
    # 汇总版本，否则会少算 FN、虚高 Recall；没有 results 的最小测试夹具仍可回退。
    benchmark_data_path = root / "offline" / "results" / "benchmark_data.json"
    benchmark_data = (
        json.loads(benchmark_data_path.read_text(encoding="utf-8"))
        if benchmark_data_path.exists()
        else {}
    )
    source_file_phases: dict[str, str] = {}
    if split_manifest:
        manifest = json.loads(Path(split_manifest).read_text(encoding="utf-8"))
        source_file_phases = {
            str(key): str(value)
            for key, value in (manifest.get("source_file_phases") or {}).items()
        }
    if phase not in {"all", "dev", "pilot", "holdout"}:
        raise ValueError(f"未知 benchmark phase: {phase}")

    cases: list[EvaluationCase] = []
    for source_file in sorted(golden_dir.glob("*.json")):
        benchmark_phase = source_file_phases.get(source_file.name, "unassigned")
        if phase != "all" and benchmark_phase != phase:
            continue
        records = json.loads(source_file.read_text(encoding="utf-8"))
        for record in records:
            url = str(record["url"])
            owner, repo, number = _parse_github_pr_url(url)
            final_comments = benchmark_data.get(url, {}).get("golden_comments") or record.get(
                "comments", []
            )
            references = [
                ReferenceFinding(
                    description=str(comment["comment"]),
                    severity=_severity(str(comment["severity"])),
                )
                for comment in final_comments
            ]
            project_root: str | None = None
            context_metadata: dict[str, str | bool] = {"context_mode": "diff_only"}
            if materialize_repositories:
                try:
                    checkout = materialize_github_pr(
                        owner=owner,
                        repo=repo,
                        number=number,
                        cache_dir=repository_cache_dir or root / ".codecross_repositories",
                    )
                    project_root = str(checkout.project_root)
                    context_metadata = {
                        "context_mode": "full_repository",
                        "head_sha": checkout.head_sha,
                        "repository_cache_hit": checkout.cache_hit,
                    }
                except RepositoryMaterializationError as exc:
                    if require_repository:
                        raise
                    # 非正式预览可以降级，但必须把原因写进 metadata，不能让使用者
                    # 误以为这是完整仓库评测。
                    context_metadata = {
                        "context_mode": "diff_only",
                        "repository_materialization_error": str(exc),
                    }
            cases.append(
                EvaluationCase(
                    case_id=f"code-review-bench:{owner}/{repo}#{number}",
                    name=str(record.get("pr_title") or f"{owner}/{repo}#{number}"),
                    diff=fetch_github_pr_diff(url, cache_dir=cache_dir),
                    # Code Review Bench 的 ground truth 是 finding，不是 CodeCross Board
                    # 风险裁决，因此不伪造 expected_risk_level。
                    expected_risk_level=None,
                    reference_findings=references,
                    project_root=project_root,
                    metadata={
                        "source": "code-review-bench",
                        "source_file": source_file.name,
                        "pr_url": url,
                        "repository": f"{owner}/{repo}",
                        "split": ["public", "code-review-bench"],
                        "benchmark_phase": benchmark_phase,
                        **context_metadata,
                        "license": "MIT",
                    },
                )
            )
            if limit is not None and len(cases) >= limit:
                return cases
    return cases
