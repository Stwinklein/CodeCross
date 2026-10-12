# CodeCross 评测指南

这套评测分成两层：仓库内代码负责稳定的数据契约和评分口径；LangSmith 负责
数据集版本、实验运行、调用链、成本与结果看板。没有 LangSmith Key 仍可运行本地
评审回归；证据验证若缺少 Docker/镜像会明确返回 `not_testable`，不会在宿主机执行。

## 1. 先运行本地评测

```powershell
.\.venv\Scripts\python.exe -m codecross.evaluation.cli local
```

默认使用 `mock_llm=True`，因此不会产生模型费用。评测集位于
`evals/local_cases.jsonl`，一行一个 JSON。新增 case 时建议至少填写：

- `diff`：被审查的 unified diff；
- `reference_findings`：人工确认的问题；
- `expected_risk_level`：希望规则 Evaluator 输出的风险等级；
- `metadata`：语言、缺陷类型、数据来源等分组信息。

## 2. 准备公开 Code Review Bench

项目默认从 `.codecross/benchmarks/code-review-benchmark` 读取 Martian 的 MIT
开源 benchmark。先从少量 PR 开始，确认下载格式和成本：

```powershell
git clone --depth 1 https://github.com/withmartian/code-review-benchmark.git `
  .codecross/benchmarks/code-review-benchmark

.\.venv\Scripts\python.exe -m codecross.evaluation.cli public-prepare --limit 3
```

`langsmith-public` 默认物化完整 PR 仓库；只有显式传入 `--diff-only` 才降级为纯 diff。
无论使用哪种上下文，都不应把 Board 的 pytest/原生测试执行率解释成 Reviewer
正确率；质量指标与 Evidence Verification 状态会分别报告。

## 3. 上传并运行 LangSmith

把 LangSmith Key 只写进本地 `.env`：

```text
LANGSMITH_API_KEY=你的 key
# 如果账号下有多个 workspace，请从 Settings > General 复制 Workspace ID。
LANGSMITH_WORKSPACE_ID=你的 workspace UUID
# 欧洲区账号必须显式使用欧洲 API，否则会在 dataset 请求时得到 403。
LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
LANGSMITH_PROJECT=codecross-evaluation
LANGSMITH_TRACING=false
```

先上传本地数据验证 dataset 权限：

```powershell
.\.venv\Scripts\python.exe -m codecross.evaluation.cli langsmith-upload
```

再用 3 条公开 PR 跑真实模型与语义 judge：

```powershell
.\.venv\Scripts\python.exe -m codecross.evaluation.cli langsmith-public --limit 3
```

公开 benchmark 的语义指标采用与 Code Review Bench 相同的核心原则：判断
candidate 和 golden 是否描述同一个底层问题。每个 golden/candidate pair 都可能
触发一次 judge 调用，因此扩大到 50 条前应先观察 3 条实验的调用量和费用。

## 4. 指标解释

- `finding_precision`：CodeCross 评论中有多少能命中 golden，主要反映噪声；
- `finding_recall`：golden 问题中有多少被找到，主要反映漏报；
- `finding_f1`：precision 与 recall 的调和平均；
- `risk_accuracy`：规则 Evaluator 风险等级是否符合本地 reference；
- `semantic_finding_*`：使用 LLM judge 的语义匹配指标，适合公开自然语言评论；
- latency/token/cost：由 LangSmith trace/experiment 提供，不属于质量分数本身。

## 5. SWE-PRBench：可恢复运行、内部诊断与官方导出

SWE-PRBench 的 frozen context 已经包含 diff，因此适配器会设置
`prebuilt_context_owns_diff=true`，Reviewer/Critic 不会再拼接第二份 patch。真实模型
运行时间较长，使用正式 CLI 逐题原子保存：

```powershell
python -m codecross.evaluation.cli swe-prbench-run `
  --manifest evals/swe_prbench_holdout_v3.json `
  --context-config config_A `
  --output-dir .codecross/swe-prbench-runs/v025-development `
  --run-label v025-development --skip-existing --real-models
```

只补跑指定题可重复传 `--task-id`。已有结果用 CodeCross 自定义三分类 Judge 作内部诊断时，逐个传入
`--result`：

```powershell
python -m codecross.evaluation.cli swe-prbench-score `
  --manifest evals/swe_prbench_holdout_v3.json `
  --context-config config_A `
  --finding-layer final `
  --result .codecross/swe-prbench-runs/v025-development/task__config_A__v025-development.json `
  --output .codecross/swe-prbench-core-v025-development.json
```

`--finding-layer` 支持 `raw / deduplicated / final / verified`。它只用于定位召回在哪一层
丢失，对外 Reviewer 结果使用 `final`；报告会保存层名。报告分母现在固定为 manifest
全部任务，缺文件、空输出、无效运行和 Judge 错误分别列出。Judge 缺少 comment ID 时
会重试并拒绝不完整评分，而不是默认为 `PLAUSIBLE`。

SWE 的 `false_positive_rate` 只统计事实错误的 `FABRICATED`；正确但未命中 human
golden 的意见是 `PLAUSIBLE`，不能按普通二元匹配算 FP。当前实现严格提供不依赖
embedding 的 Detection Rate、FPR、P/R/F1 与 actionability 的**内部近似诊断**；
自定义 Judge 与官方 GPT-5.2 Judge 不同，`overall_score` 明确为 `null`，不能拿这些
数字直接与官方排行榜相减。

若要准备同协议评测，先将完整 manifest 的结果（缺失任务也会导出失败占位）转换
为官方 v0.4.1 `AgentOutput` 结构，再使用官方 harness/Judge 评分；转换本身不会产生
榜单成绩，也不会调用模型：

```powershell
python -m codecross.evaluation.cli swe-prbench-export `
  --manifest evals/swe_prbench_holdout_v3.json `
  --context-config config_A `
  --finding-layer final `
  --result .codecross/swe-prbench-runs/v025-development/task__config_A__v025-development.json `
  --output-dir .codecross/swe-prbench-agent-outputs
```

适配器还支持 `source_split=all_prs` 的 manifest，从官方 `prs.jsonl` 读取 350 PR；
`eval_100` 与全量任务不得混成一个分母。SWE-bench 原版的 resolved rate 是 issue
修复指标，不是 PR review 的 DR/FPR。

当前 v3 的 15 题已经全部用于 development，不能再称为盲测。新的
`evals/swe_prbench_holdout_v4.json` 含 10 个未读取 annotation 的 sealed task，状态为
`sealed_not_run`。只有 development 同时达到 valid≥95%、DR≥0.30、FPR≤0.35 后，才允许
一次性运行 v4 并上传欧洲区 LangSmith：

```powershell
python -m codecross.evaluation.cli langsmith-swe-prbench `
  --manifest evals/swe_prbench_holdout_v4.json `
  --partition sealed --context-config config_A
```

LangSmith 里主要查看 `valid_review_run`、`swe_detection_rate`、
`swe_false_positive_rate`、`swe_precision` 和 `swe_plausible_rate`。开发集与 sealed
集必须分开实验，不能在看过 sealed 逐题结果后继续调 Prompt 并仍称其为 holdout。

截至 v0.25，旧自定义 Judge 对 15 题 development 的历史报告是 valid=100%、
DR=0.152088、FPR=0.077778；这些不是官方成绩。新版人工评论筛选规则会改变口径，
必须重跑评分，不能直接沿用旧数。DR 仍低于预设的 0.30 目标，v4 未作为正式封存集
发布成绩。离线问题分布见 [15 PR 诊断](../docs/evaluation-diagnosis-v025.md)。
