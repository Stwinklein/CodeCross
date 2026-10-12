from __future__ import annotations

"""``codecross-eval`` 命令行入口。

推荐顺序：先执行 local；通过后用 public-prepare 检查公开样例；最后配置
LANGSMITH_API_KEY，再执行 langsmith-public。命令不会把任何 API key 写入输出文件。
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
from langsmith.utils import LangSmithError
from .langsmith_adapter import create_langsmith_client, run_langsmith_experiment, upload_cases
from .public_benchmarks import load_code_review_bench
from .models import CaseEvaluationResult, EvaluationOutput, PredictedFinding
from .judges import (
    build_multi_judge_settings,
    calibrate_judges,
    load_judge_calibration_jsonl,
)
from .runner import load_cases_jsonl, run_case, run_local_suite, write_cases_jsonl, write_report
from .swe_prbench import load_swe_prbench_cases
from .swe_prbench_export import official_agent_output
from .swe_prbench_judge import run_swe_prbench_judge, score_swe_prbench_core


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_CASES = PROJECT_ROOT / "evals" / "local_cases.jsonl"
DEFAULT_BENCHMARK_ROOT = PROJECT_ROOT / ".codecross" / "benchmarks" / "code-review-benchmark"
DEFAULT_SPLIT_MANIFEST = PROJECT_ROOT / "evals" / "code_review_bench_split.json"
DEFAULT_SWE_CACHE = PROJECT_ROOT / ".codecross" / "benchmarks" / "swe-prbench"
DEFAULT_SWE_MANIFEST = PROJECT_ROOT / "evals" / "swe_prbench_holdout_v3.json"


def _add_benchmark_args(parser: argparse.ArgumentParser, *, default_full_repo: bool = False) -> None:
    parser.add_argument("--benchmark-root", type=Path, default=DEFAULT_BENCHMARK_ROOT)
    parser.add_argument("--limit", type=int, default=3, help="先用少量 PR 验证成本和链路。")
    parser.add_argument("--split-manifest", type=Path, default=DEFAULT_SPLIT_MANIFEST)
    parser.add_argument(
        "--phase",
        choices=["dev", "pilot", "holdout", "all"],
        default="pilot",
        help="默认只跑 pilot，holdout 必须在版本冻结后显式选择。",
    )
    context = parser.add_mutually_exclusive_group()
    context.add_argument("--full-repo", dest="full_repo", action="store_true", help="检出 PR head 完整仓库。")
    context.add_argument("--diff-only", dest="full_repo", action="store_false", help="只使用 diff（仅用于链路预览）。")
    parser.set_defaults(full_repo=default_full_repo)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CodeCross 本地与 LangSmith 评测工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    local = subparsers.add_parser("local", help="运行不访问网络的本地评测")
    local.add_argument("--cases", type=Path, default=DEFAULT_LOCAL_CASES)
    local.add_argument("--output", type=Path, default=PROJECT_ROOT / ".codecross" / "local-eval.json")
    local.add_argument("--real-models", action="store_true", help="默认使用稳定 mock；显式开启才调用模型。")
    local.add_argument(
        "--mode",
        choices=["review_only", "evidence_verification"],
        default="evidence_verification",
    )

    prepare = subparsers.add_parser("public-prepare", help="下载并转换公开 Code Review Bench")
    _add_benchmark_args(prepare)
    prepare.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / ".codecross" / "code-review-bench-preview.jsonl",
    )

    upload = subparsers.add_parser("langsmith-upload", help="把已有 JSONL 上传为 LangSmith dataset")
    upload.add_argument("--cases", type=Path, default=DEFAULT_LOCAL_CASES)
    upload.add_argument("--dataset", default="codecross-local-evals")

    public = subparsers.add_parser("langsmith-public", help="上传公开集并运行 LangSmith 实验")
    _add_benchmark_args(public, default_full_repo=True)
    public.add_argument("--dataset", default="codecross-code-review-bench")
    public.add_argument("--experiment-prefix", default="codecross-public")
    public.add_argument("--mock-llm", action="store_true", help="只验证平台链路，不代表真实模型成绩。")
    public.add_argument(
        "--multi-judge",
        action="store_true",
        help="使用两个显式配置的独立 Judge，并额外报告一致率。",
    )

    calibration = subparsers.add_parser(
        "judge-calibrate",
        help="在人工 match/non-match JSONL 上校准两个 Judge 的准确率和一致率。",
    )
    calibration.add_argument("--cases", type=Path, required=True)
    calibration.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / ".codecross" / "judge-calibration.json",
    )

    langsmith_swe = subparsers.add_parser(
        "langsmith-swe-prbench",
        help="上传 SWE-PRBench 公开集并用官方核心三分类指标运行 LangSmith 实验。",
    )
    langsmith_swe.add_argument("--manifest", type=Path, default=DEFAULT_SWE_MANIFEST)
    langsmith_swe.add_argument("--cache-dir", type=Path, default=DEFAULT_SWE_CACHE)
    langsmith_swe.add_argument(
        "--context-config",
        choices=["config_A", "config_B", "config_C"],
        default="config_A",
    )
    langsmith_swe.add_argument(
        "--partition",
        choices=["development", "sealed", "all"],
        default="sealed",
        help="默认只跑未参与调参的 sealed task。",
    )
    langsmith_swe.add_argument("--limit", type=int, default=None)
    langsmith_swe.add_argument("--dataset", default="codecross-swe-prbench-v3")
    langsmith_swe.add_argument("--experiment-prefix", default="codecross-swe-prbench")
    langsmith_swe.add_argument("--mock-llm", action="store_true")

    swe = subparsers.add_parser(
        "swe-prbench-run",
        help="逐题运行 SWE-PRBench；支持断点恢复，适合分钟级真实模型评测。",
    )
    swe.add_argument("--manifest", type=Path, default=DEFAULT_SWE_MANIFEST)
    swe.add_argument("--cache-dir", type=Path, default=DEFAULT_SWE_CACHE)
    swe.add_argument(
        "--context-config",
        choices=["config_A", "config_B", "config_C"],
        default="config_A",
        help="官方 baseline 在 config_A 上最好；B/C 用于上下文消融。",
    )
    swe.add_argument("--limit", type=int, default=None)
    swe.add_argument(
        "--task-id",
        action="append",
        default=[],
        help="只运行指定 task，可重复传入；为空时按 manifest 顺序运行。",
    )
    swe.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / ".codecross" / "swe-prbench-runs",
    )
    swe.add_argument("--run-label", default="manual", help="写入文件名的版本/实验标签。")
    swe.add_argument(
        "--skip-existing",
        action="store_true",
        help="结果文件已存在时跳过，避免恢复长任务时重复调用模型。",
    )
    swe.add_argument("--real-models", action="store_true", help="显式开启真实模型；默认 mock。")

    swe_score = subparsers.add_parser(
        "swe-prbench-score",
        help="用 CodeCross 自定义 Judge 评分已有逐题结果（内部诊断分，不是官方榜分）。",
    )
    swe_score.add_argument("--manifest", type=Path, default=DEFAULT_SWE_MANIFEST)
    swe_score.add_argument("--cache-dir", type=Path, default=DEFAULT_SWE_CACHE)
    swe_score.add_argument(
        "--context-config",
        choices=["config_A", "config_B", "config_C"],
        default="config_A",
    )
    swe_score.add_argument(
        "--result",
        type=Path,
        action="append",
        required=True,
        help="待评分的 CaseEvaluationResult JSON，可重复传入。",
    )
    swe_score.add_argument(
        "--finding-layer",
        choices=["raw", "deduplicated", "final", "verified"],
        default="final",
        help=(
            "选择交给官方 Judge 的 Finding 层。raw 用于诊断生成召回，"
            "deduplicated 用于诊断去重损失，final 是正式输出，verified 用于证据层。"
        ),
    )
    swe_score.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / ".codecross" / "swe-prbench-core-score.json",
    )
    swe_export = subparsers.add_parser(
        "swe-prbench-export",
        help="将 CodeCross 逐题结果导出为官方 v0.4.1 AgentOutput JSON；不运行付费 Judge。",
    )
    swe_export.add_argument("--manifest", type=Path, default=DEFAULT_SWE_MANIFEST)
    swe_export.add_argument("--cache-dir", type=Path, default=DEFAULT_SWE_CACHE)
    swe_export.add_argument("--context-config", choices=["config_A", "config_B", "config_C"], default="config_A")
    swe_export.add_argument("--result", type=Path, action="append", required=True)
    swe_export.add_argument("--finding-layer", choices=["raw", "deduplicated", "final", "verified"], default="final")
    swe_export.add_argument("--model-label", default="codecross", help="官方 AgentOutput 的产品标识，不冒充单一底层模型。")
    swe_export.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / ".codecross" / "swe-prbench-agent-outputs")
    return parser.parse_args(argv)


def _safe_task_name(task_id: str) -> str:
    """把公开 task id 转成稳定文件名，仅允许常见安全字符。"""

    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in task_id)


def _write_case_result_atomic(result, target: Path) -> None:
    """先写临时文件再原子替换，进程中断时不会留下半截 JSON。

    长评测最常见的失败不是业务异常，而是终端关闭、网络断开或用户停止任务。
    每完成一题立即落盘，并用 replace 提交，可让下一次 `--skip-existing` 从断点续跑。
    """

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(target)


def _write_json_atomic(payload: object, target: Path) -> None:
    """评测摘要也采用原子写入，避免 Judge 批处理中断后留下无效 JSON。"""

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _select_swe_score_findings(
    output: EvaluationOutput,
    layer: str,
) -> list[PredictedFinding]:
    """返回指定审查层，保证诊断评分不会悄悄改变正式输出口径。

    四层的用途不同：

    - ``raw``：两个 Reviewer 的原始合集，用来判断问题是否在生成阶段就漏掉；
    - ``deduplicated``：确定性/语义归一化结果，用来定位去重是否误合并；
    - ``final``：排序、限额后的产品输出，也是正式 benchmark 默认层；
    - ``verified``：证据执行后的结果，仅用于 evidence-verification 实验。

    这里不对空层做 fallback。比如 review-only 的 verified 通常为空，若自动回退到
    final，会让调用方误以为自己测到了验证层，破坏实验可复现性。
    """

    layers = {
        "raw": output.raw_findings,
        "deduplicated": output.deduplicated_findings,
        "final": output.findings,
        "verified": output.verified_findings,
    }
    try:
        return list(layers[layer])
    except KeyError as exc:  # pragma: no cover - argparse 已限制，保留给直接函数调用者。
        raise ValueError(f"未知 Finding 层: {layer}") from exc


def _valid_swe_run(result: CaseEvaluationResult) -> bool:
    board = result.output.board_metrics
    reviewer_count = int(board.get("reviewer_count") or 0)
    reviewer_success = int(board.get("reviewer_success_count") or 0)
    return (
        reviewer_count > 0
        and reviewer_count == reviewer_success
        and not board.get("critic_error")
        and not board.get("semantic_dedup_error")
    )


def _print_local_summary(report) -> None:
    summary = {
        "case_count": report.case_count,
        "valid_case_count": report.valid_case_count,
        "invalid_case_count": report.invalid_case_count,
        "valid_run_rate": report.valid_run_rate,
        "micro_precision": report.micro_precision,
        "micro_recall": report.micro_recall,
        "micro_f1": report.micro_f1,
        "macro_precision": report.macro_precision,
        "macro_recall": report.macro_recall,
        "macro_f1": report.macro_f1,
        "risk_accuracy": report.risk_accuracy,
        "failed_case_ids": report.failed_case_ids,
        "invalid_case_ids": report.invalid_case_ids,
        "layer_metrics": {
            name: metrics.model_dump(mode="json")
            for name, metrics in report.layer_metrics.items()
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _raise_friendly_langsmith_error(exc: Exception) -> None:
    """把常见平台鉴权问题翻译成可操作提示，同时保留其它错误的原始信息。"""

    message = str(exc)
    if "403" in message or "Forbidden" in message:
        raise SystemExit(
            "LangSmith 返回 403 Forbidden。当前 Key 可能关联多个工作区，请在 .env "
            "增加 LANGSMITH_WORKSPACE_ID；也请确认账号区域对应的 LANGSMITH_ENDPOINT。"
        ) from None
    raise SystemExit(f"LangSmith 请求失败: {message}") from None


def main(argv: list[str] | None = None) -> None:
    # 与主应用显式传入 project_root 时的行为一致：项目自己的 .env 是本次评测的
    # 权威配置。Codex/IDE 宿主进程可能预先注入同名 ANTHROPIC_MODEL；若不 override，
    # Judge 会意外使用宿主模型名，而不是用户刚在项目 .env 中验证过的模型。
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    args = parse_args(argv)

    if args.command == "local":
        report = run_local_suite(
            load_cases_jsonl(args.cases),
            mock_llm=not args.real_models,
            default_project_root=PROJECT_ROOT,
            evaluation_mode=args.mode,
        )
        write_report(report, args.output)
        _print_local_summary(report)
        if report.failed_case_ids:
            raise SystemExit(1)
        return

    if args.command == "public-prepare":
        cases = load_code_review_bench(
            args.benchmark_root,
            limit=args.limit,
            materialize_repositories=args.full_repo,
            repository_cache_dir=PROJECT_ROOT / ".codecross" / "benchmark_repositories",
            require_repository=args.full_repo,
            split_manifest=args.split_manifest,
            phase=args.phase,
        )
        write_cases_jsonl(cases, args.output)
        print(f"已准备 {len(cases)} 条公开评测，输出: {args.output}")
        return

    if args.command == "swe-prbench-run":
        cases = load_swe_prbench_cases(
            cache_dir=args.cache_dir,
            manifest_path=args.manifest,
            context_config=args.context_config,
            limit=args.limit,
        )
        requested = set(args.task_id)
        if requested:
            known = {str(case.metadata.get("task_id") or "") for case in cases}
            unknown = sorted(requested - known)
            if unknown:
                raise SystemExit(f"manifest/limit 中不存在 task: {', '.join(unknown)}")
            cases = [case for case in cases if case.metadata.get("task_id") in requested]

        summaries = []
        for case in cases:
            task_id = str(case.metadata.get("task_id") or case.case_id)
            filename = (
                f"{_safe_task_name(task_id)}__{args.context_config}__"
                f"{_safe_task_name(args.run_label)}.json"
            )
            target = args.output_dir / filename
            if args.skip_existing and target.exists():
                summaries.append({"task_id": task_id, "status": "skipped_existing", "output": str(target)})
                print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
                continue

            result = run_case(
                case,
                mock_llm=not args.real_models,
                default_project_root=PROJECT_ROOT,
                evaluation_mode="review_only",
            )
            _write_case_result_atomic(result, target)
            board = result.output.board_metrics
            summary = {
                "task_id": task_id,
                "status": "completed",
                "output": str(target),
                "raw_findings": len(result.output.raw_findings),
                "final_findings": len(result.output.findings),
                "reviewer_success_count": board.get("reviewer_success_count", 0),
                "reviewer_count": board.get("reviewer_count", 0),
                "critic_error": board.get("critic_error", ""),
                "semantic_dedup_error": board.get("semantic_dedup_error", ""),
            }
            summaries.append(summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)

        # 最后一行是整批机器可读摘要；即使调用方只保留 stdout，也能追踪完成范围。
        print(json.dumps({"case_count": len(cases), "results": summaries}, ensure_ascii=False), flush=True)
        return

    if args.command == "swe-prbench-export":
        cases = load_swe_prbench_cases(
            cache_dir=args.cache_dir, manifest_path=args.manifest,
            context_config=args.context_config,
        )
        cases_by_id = {case.case_id: case for case in cases}
        results: dict[str, CaseEvaluationResult] = {}
        for path in args.result:
            result = CaseEvaluationResult.model_validate_json(path.read_text(encoding="utf-8"))
            if result.case_id not in cases_by_id:
                raise SystemExit(f"结果不属于当前 manifest: {result.case_id}")
            if result.case_id in results:
                raise SystemExit(f"重复结果 case_id: {result.case_id}")
            results[result.case_id] = result
        statuses = []
        config_short = args.context_config[-1]
        for case in cases:
            result = results.get(case.case_id)
            if result is None:
                error = "missing_codecross_result"
            elif not _valid_swe_run(result):
                error = "invalid_codecross_run"
            else:
                error = None
            predictions = (
                _select_swe_score_findings(result.output, args.finding_layer)
                if result is not None and error is None else []
            )
            output = official_agent_output(
                case, predictions, context_config=args.context_config,
                model_label=args.model_label, parse_error=error,
            )
            task_id = str(case.metadata["task_id"])
            target = args.output_dir / f"{_safe_task_name(task_id)}_{config_short}_agent.json"
            _write_json_atomic(output, target)
            statuses.append({"task_id": task_id, "status": "exported" if error is None else error, "output": str(target)})
        report = {
            "protocol": "swe-prbench-v0.4.1-agent-output-only",
            "manifest_sha256": cases[0].metadata.get("manifest_sha256") if cases else "",
            "context_config": args.context_config,
            "finding_layer": args.finding_layer,
            "case_count": len(cases),
            "valid_export_count": sum(item["status"] == "exported" for item in statuses),
            "results": statuses,
            "note": "仅导出官方输入结构；须由官方 harness/Judge 评分后才能与公开榜单对比。",
        }
        _write_json_atomic(report, args.output_dir / "export_manifest.json")
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False), flush=True)
        return

    if args.command == "swe-prbench-score":
        cases = load_swe_prbench_cases(
            cache_dir=args.cache_dir,
            manifest_path=args.manifest,
            context_config=args.context_config,
        )
        cases_by_id = {case.case_id: case for case in cases}
        scored = []
        invalid = []
        judge_errors = []
        parse_errors = []
        no_output = []
        seen_case_ids: set[str] = set()
        for result_path in args.result:
            try:
                result = CaseEvaluationResult.model_validate_json(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                parse_errors.append({"path": str(result_path), "error": str(exc)})
                print(json.dumps({"path": str(result_path), "status": "parse_error"}, ensure_ascii=False), flush=True)
                continue
            if result.case_id in seen_case_ids:
                raise SystemExit(f"重复结果 case_id: {result.case_id}")
            seen_case_ids.add(result.case_id)
            case = cases_by_id.get(result.case_id)
            if case is None:
                raise SystemExit(f"结果不属于当前 manifest: {result.case_id}")
            valid = _valid_swe_run(result)
            if not valid:
                invalid.append(result.case_id)
                print(
                    json.dumps({"case_id": result.case_id, "status": "invalid_run"}, ensure_ascii=False),
                    flush=True,
                )
                continue
            predictions = _select_swe_score_findings(result.output, args.finding_layer)
            if not predictions:
                no_output.append(result.case_id)
            try:
                decision = run_swe_prbench_judge(
                    diff=case.diff,
                    references=case.reference_findings,
                    predictions=predictions,
                )
                metrics = score_swe_prbench_core(
                    case.reference_findings,
                    predictions,
                    decision,
                )
            except ValueError as exc:
                judge_errors.append({"case_id": result.case_id, "error": str(exc)})
                print(json.dumps({"case_id": result.case_id, "status": "judge_error"}, ensure_ascii=False), flush=True)
                continue
            item = {
                "case_id": result.case_id,
                "task_id": case.metadata.get("task_id"),
                "metrics": metrics.model_dump(mode="json"),
            }
            scored.append(item)
            print(
                json.dumps(
                    {
                        "case_id": result.case_id,
                        "status": "scored",
                        "detection_rate": metrics.detection_rate,
                        "false_positive_rate": metrics.false_positive_rate,
                        "confirmed": metrics.confirmed_count,
                        "plausible": metrics.plausible_count,
                        "fabricated": metrics.fabricated_count,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        def mean(field: str) -> float:
            values = [float(item["metrics"][field]) for item in scored]
            return round(sum(values) / len(values), 6) if values else 0.0

        missing_case_ids = sorted(set(cases_by_id) - seen_case_ids)
        report = {
            "protocol": "codecross-swe-prbench-core-v1-custom-judge",
            "context_config": args.context_config,
            "manifest_sha256": cases[0].metadata.get("manifest_sha256") if cases else "",
            "source_split": cases[0].metadata.get("source_split") if cases else "",
            # 报告必须带层名，否则 raw 诊断结果很容易被误读成正式 final 成绩。
            "finding_layer": args.finding_layer,
            "case_count": len(cases),
            "result_file_count": len(args.result),
            "valid_case_count": len(scored),
            "invalid_case_count": len(invalid),
            "missing_case_count": len(missing_case_ids),
            "judge_error_count": len(judge_errors),
            "parse_error_count": len(parse_errors),
            "no_output_case_count": len(no_output),
            "valid_run_rate": round(len(scored) / len(cases), 6) if cases else 0.0,
            # 官方 leaderboard 的聚合是先算每个 PR，再取均值；不能用全局 TP/FN
            # 替代，否则 human comment 较多的 PR 会获得额外权重。
            "mean_detection_rate": mean("detection_rate"),
            "mean_false_positive_rate": mean("false_positive_rate"),
            "mean_precision": mean("precision"),
            "mean_recall": mean("recall"),
            "mean_f1": mean("f1_score"),
            "overall_score": None,
            "overall_note": "使用 CodeCross 自定义 Judge，且缺少官方 embedding；这是内部诊断分，不可与官方榜单直接比较。",
            "invalid_case_ids": invalid,
            "missing_case_ids": missing_case_ids,
            "judge_errors": judge_errors,
            "parse_errors": parse_errors,
            "no_output_case_ids": no_output,
            "results": scored,
        }
        _write_json_atomic(report, args.output)
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False), flush=True)
        return

    if args.command == "langsmith-upload":
        cases = load_cases_jsonl(args.cases)
        try:
            client = create_langsmith_client()
        except RuntimeError as exc:
            # CLI 用户只需要知道缺哪个配置，不需要看到一整段 Python traceback。
            raise SystemExit(str(exc)) from None
        try:
            upload_cases(client, args.dataset, cases)
        except LangSmithError as exc:
            _raise_friendly_langsmith_error(exc)
        print(f"已上传 {len(cases)} 条样例到 LangSmith dataset: {args.dataset}")
        return

    if args.command == "judge-calibrate":
        examples = load_judge_calibration_jsonl(args.cases)
        report = calibrate_judges(examples, settings=build_multi_judge_settings())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        print(report.model_dump_json(indent=2))
        return

    if args.command == "langsmith-public":
        cases = load_code_review_bench(
            args.benchmark_root,
            limit=args.limit,
            materialize_repositories=args.full_repo,
            repository_cache_dir=PROJECT_ROOT / ".codecross" / "benchmark_repositories",
            require_repository=args.full_repo,
            split_manifest=args.split_manifest,
            phase=args.phase,
        )
        try:
            client = create_langsmith_client()
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from None
        try:
            upload_cases(
                client,
                args.dataset,
                cases,
                description=(
                    "CodeCross import of Martian Code Review Bench (MIT, full repository)"
                    if args.full_repo
                    else "CodeCross import of Martian Code Review Bench (MIT, diff-only preview)"
                ),
            )
            run_langsmith_experiment(
                dataset_name=args.dataset,
                experiment_prefix=args.experiment_prefix,
                mock_llm=args.mock_llm,
                default_project_root=PROJECT_ROOT,
                metadata={
                    "benchmark": "code-review-bench",
                    "context_mode": "full_repository" if args.full_repo else "diff_only",
                    "case_count": len(cases),
                    "benchmark_phase": args.phase,
                },
                semantic_judge=True,
                multi_judge=args.multi_judge,
                evaluation_mode="review_only",
            )
        except LangSmithError as exc:
            _raise_friendly_langsmith_error(exc)
        return

    if args.command == "langsmith-swe-prbench":
        cases = load_swe_prbench_cases(
            cache_dir=args.cache_dir,
            manifest_path=args.manifest,
            context_config=args.context_config,
        )
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.partition == "development":
            selected_ids = set(manifest.get("development_task_ids") or [])
        elif args.partition == "sealed":
            selected_ids = set(manifest.get("sealed_task_ids") or [])
        else:
            selected_ids = {str(case.metadata.get("task_id")) for case in cases}
        cases = [case for case in cases if str(case.metadata.get("task_id")) in selected_ids]
        if args.limit is not None:
            cases = cases[: args.limit]
        if not cases:
            raise SystemExit(f"partition={args.partition} 没有可运行 case。")
        try:
            client = create_langsmith_client()
            upload_cases(
                client,
                args.dataset,
                cases,
                description=(
                    "CodeCross import of foundry-ai/SWE-PRBench eval_100 "
                    f"({args.context_config}, {args.partition}, CC BY 4.0)"
                ),
            )
            run_langsmith_experiment(
                dataset_name=args.dataset,
                experiment_prefix=args.experiment_prefix,
                mock_llm=args.mock_llm,
                default_project_root=PROJECT_ROOT,
                metadata={
                    "benchmark": "swe-prbench",
                    "pipeline_version": "v0.4.1",
                    "context_config": args.context_config,
                    "partition": args.partition,
                    "case_count": len(cases),
                    "official_metric_scope": "core_without_embedding",
                },
                swe_prbench_core=True,
                evaluation_mode="review_only",
            )
        except (LangSmithError, RuntimeError) as exc:
            _raise_friendly_langsmith_error(exc)
        return


if __name__ == "__main__":
    main()
