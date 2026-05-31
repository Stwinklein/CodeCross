# CodeCross V1.2 修复与扩展计划

V1.1 已经把"安全底线"和"误判防护"两条主线落地：AST sanitizer、provider kwargs 集中化、异常白名单、diff 元数据上下文、failed/reproducible 双轨证据、flaky 重跑识别、无进展早停。这一版让主链路敢跑，但还**没解决**两件事：

1. **可终止 + 可观测**：原 V1.1 修订版里的 P0-D（协作式 abort）和 P1-D（事件回调接口）被推迟。前端 V1 强依赖这两件事，不能再延。
2. **reviewer 输入质量**：V1.1 让 reviewer 拿到 hunk 元数据，但 reviewer 仍然只看 diff 片段，看不到调用方/被调用方/类型签名。这是当前 finding 准确率的最大瓶颈。

V1.2 围绕"前端能用 + reviewer 看得见上下文 + 配置可外化"三件事展开，不做容器沙箱、不换 LangGraph 拓扑、不做多语言。

## V1.1 已落地内容（基线）

| 模块 | 改动 | V1.2 复用点 |
|---|---|---|
| `executor.py` | AST sanitizer（4 类规则）+ rerun + flaky 识别 | sanitizer 接口稳定，V1.2 不动 |
| `executor.py` | `_run_pytest_once` / `_merge_rerun_output` / `_flaky_result` | rerun 控制开关已就位 |
| `board.py` | `is_reproducible` 双轨证据 + `apply_analysis_updates` 保留 `not_testable` | 状态机基本稳定 |
| `board.py` | `should_continue_verification(status_changes=...)` 无进展早停 | V1.2 在此基础上加配置化阈值 |
| `llm.py` | `_provider_kwargs` 集中差异 + `_is_recoverable_model_exception` 模块前缀判断 | V1.2 加 OpenAI/兼容网关时复用 |
| `agents.py` | `DiffFileMeta` + `parse_diff_files` + `parse_diff_context_notes` | V1.2 上下文检索的接入点 |
| `agents.py` | reviewer 改为只 catch `ModelCallError` | 异常边界稳定 |

## V1.2 范围一览

| 优先级 | 项目 | 一句话说明 |
|---|---|---|
| P0-A | RunController + 协作式 abort | 前端 V1 强依赖，原 V1.1 P0-D 落地 |
| P0-B | 事件回调接口 + friendly_name 表 | 前端 V1 强依赖，原 V1.1 P1-D 落地 |
| P0-C | Diff 上下文检索 | reviewer 看到调用点和符号定义，原问题清单 #3 |
| P1-A | Reviewer 独立性重构 | Planner 不再贴 risk_areas 标签，原问题清单 #4 |
| P1-B | Reviewer 分歧标记 | FindingCluster 加 `disagreement`，原 V1.1 P1-C 落地 |
| P1-C | RiskRules 配置化 | Evaluator 规则抽出 dataclass，原问题清单 #8 |
| P1-D | 配置外部化 (`codecross.yaml`) | reviewer/runner/risk_rules 可外部覆盖，原问题清单 #10 |
| P2-A | Test Case 能力扩展 | fixture / parametrize / monkeypatch 模板，原问题清单 #5 |
| P2-B | Prompt 工程系统化 | 抽到 `prompts/*.md`，加 severity 校准 + few-shot，原问题清单 #17 |
| P2-C | 关键单测补齐 | V1.2 新增能力的回归集 |

显式延后到 V2：多语言 executor (#6)、语义聚类 (#9 完整版)、观测性平台 (#11)、CI/SARIF (#12)、缓存 (#13)、State 拆分 (#16)、HTML 报告 (#18)。

## P0-A. RunController + 协作式 abort

**修复目标**：当前 `app.invoke()` 不可中断，前端 V1 的终止按钮无后端能力支撑。V1.2 提供"协作式 abort"——已运行节点不强杀，后续节点感知后跳过，可选生成 partial 简报。

### 落地方案

**新增 `codecross/runtime.py`**：

```python
import threading
from dataclasses import dataclass, field
from .events import EventCallback  # 见 P0-B

@dataclass
class RunController:
    """单次 Run 的运行时控制对象，跨节点共享。

    不放进 CodeCrossState：state 跨节点序列化，threading.Event 不适合。
    通过 ProjectConfig.runtime 字段注入，由调用方持有。
    """
    run_id: str
    abort_event: threading.Event = field(default_factory=threading.Event)
    keep_partial: bool = True
    on_event: EventCallback | None = None

    def request_abort(self, *, keep_partial: bool = True) -> None:
        self.keep_partial = keep_partial
        self.abort_event.set()

    @property
    def aborted(self) -> bool:
        return self.abort_event.is_set()

    def emit(self, event) -> None:
        """事件回调永远不抛，避免观测层影响主流程。"""
        if self.on_event is None:
            return
        try:
            self.on_event(event)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("event callback failed")
```

**ProjectConfig 增加 `runtime` 字段**：

```python
@dataclass(slots=True)
class ProjectConfig:
    ...
    runtime: "RunController | None" = None
```

由调用方在构造 ProjectConfig 后赋值。CLI 路径不赋值，行为完全不变。

**节点装饰器**（同时承担 abort 和事件 emit，避免两层装饰器叠加）：

放在 `codecross/workflow.py` 顶部：

```python
def _instrumented(node_name: str):
    """节点装饰器：abort 短路 + emit node_started/node_finished/node_failed。"""
    friendly = NODE_FRIENDLY_NAMES.get(node_name, node_name)

    def wrap(node_func):
        @functools.wraps(node_func)
        def inner(state):
            controller = state["config"].runtime
            if controller and controller.aborted:
                # 短路返回：不更新业务字段，让条件路由把控制流引向 evaluator/final_report
                return {"stop_reason": "aborted"}
            if controller:
                controller.emit(Event(
                    event_type="node_started",
                    node_name=node_name, friendly_name=friendly,
                    round_index=state.get("round_index", 0),
                    run_id=controller.run_id,
                ))
            try:
                result = node_func(state)
            except Exception as exc:
                if controller:
                    controller.emit(Event(
                        event_type="node_failed",
                        node_name=node_name, friendly_name=friendly,
                        payload={"error": f"{type(exc).__name__}: {exc}"},
                        run_id=controller.run_id,
                    ))
                raise
            if controller:
                controller.emit(Event(
                    event_type="node_finished",
                    node_name=node_name, friendly_name=friendly,
                    round_index=state.get("round_index", 0),
                    run_id=controller.run_id,
                ))
            return result
        return inner
    return wrap
```

**`build_graph` 改造**：每个 `add_node` 调用前用装饰器包裹。reviewer 节点的 node_name 用 `f"reviewer_{index}_{reviewer.display_name}"`，friendly_name 通过 `reviewer_friendly_name(index, display_name)` 生成。

**条件路由的 abort 短路**：四个 `route_after_*` 函数加入 abort 判断：

```python
def route_after_update_board(state):
    controller = state["config"].runtime
    if controller and controller.aborted:
        return "evaluator" if controller.keep_partial else "final_report"
    if state.get("continue_verification"):
        return "select_verification_targets"
    return "evaluator"
```

`route_after_select` / `route_after_coverage_gap` / `route_after_evaluator` 同样处理。

**Executor 内部循环检查**：`executor.py:execute_test_cases` 增加可选参数：

```python
def execute_test_cases(test_cases, *, project_root, round_index, runner_config, controller=None):
    results: list[TestResult] = []
    for test_case in test_cases[:runner_config.max_tests_per_round]:
        if controller and controller.aborted:
            break
        ...
```

`executor_node` 把 `state["config"].runtime` 传入。这样 abort 不必等一整批测试跑完。

**对外 API**：

```python
def run_workflow(diff, *, project_root=None, config=None) -> CodeCrossState: ...
```

`config.runtime` 由调用方在传入前注入。这样 `run_workflow` 的签名兼容 V1.1。

**已运行 subprocess 的处理**：保持同步阻塞，最差等一个 `test_timeout_seconds`（默认 20s）。V1.2 不引入异步 executor。

### 验收标准

- 在 reviewer 节点完成后立即调用 `controller.request_abort()`，graph 跳过 verification loop，进入 evaluator/final_report，最终报告含 "aborted" 字样。
- `keep_partial=True` 输出含已收集 reviewer 输出和 round 记录的简报；`keep_partial=False` 直接走 final_report 输出最简短"aborted"。
- 多次重复 `request_abort` 幂等，不引发重复 emit。
- abort 之后已在跑的 pytest subprocess 不被强杀，但 executor 不再开始新 case。

## P0-B. 事件回调接口 + friendly_name 表

**修复目标**：当前节点完成只在 state 留痕，前端 V1 的 SSE、CLI 进度提示、未来的可观测系统都缺这层。V1.2 固化接口（不实现 SSE 服务器，那是前端工作流的事）。

### 落地方案

**新增 `codecross/events.py`**：

```python
from dataclasses import dataclass, field
from typing import Any, Callable, Literal
from .schemas import utc_now

EventType = Literal[
    "run_started",
    "node_started",
    "node_finished",
    "node_failed",
    "round_started",
    "round_finished",
    "test_started",
    "test_finished",
    "run_finished",
    "run_aborted",
]

@dataclass
class Event:
    event_type: EventType
    node_name: str = ""
    friendly_name: str = ""
    round_index: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)
    run_id: str = ""

EventCallback = Callable[[Event], None]

NODE_FRIENDLY_NAMES: dict[str, str] = {
    "planner": "制定审查计划",
    "initial_board": "初始化 Review Board",
    "select_verification_targets": "挑选下轮验证目标",
    "test_strategy": "生成测试策略",
    "test_case": "生成测试用例",
    "executor": "执行 pytest",
    "test_result_analyzer": "分析测试结果",
    "coverage_gap": "汇总覆盖缺口",
    "targeted_review": "定向复审",
    "update_board": "更新 Board 状态",
    "evaluator": "综合风险评估",
    "high_risk_review": "高风险二次确认",
    "final_report": "生成最终报告",
}

def reviewer_friendly_name(index: int, display_name: str) -> str:
    return f"Reviewer {index} · {display_name}"
```

**事件触发点清单**：

| 事件 | 触发位置 | payload 内容 |
|---|---|---|
| `run_started` | `run_workflow` 入口 | `{"changed_files": [...], "reviewer_count": N}` |
| `node_started` | `_instrumented` 进入 | `{}` |
| `node_finished` | `_instrumented` 退出 | 节点 output 关键字段（如 reviewer 完成时的 finding_count）|
| `node_failed` | `_instrumented` except 分支 | `{"error": "..."}` |
| `round_started` | `select_verification_targets_node` | `{"target_finding_ids": [...]}` |
| `round_finished` | `update_board_node` | `{"status_changes": {...}, "stop_reason": "..."}` |
| `test_started` | `executor.execute_test_cases` 循环内 | `{"test_case_id": "...", "test_name": "..."}` |
| `test_finished` | 同上，循环内 | `{"test_case_id": "...", "status": "passed/failed/...", "is_reproducible": bool}` |
| `run_finished` | `run_workflow` 末尾 | `{"risk_level": "...", "finding_count": N}` |
| `run_aborted` | controller 检测 abort 后第一次 emit | `{"stop_reason": "aborted", "keep_partial": bool}` |

**线程安全约定**：callback 由调用方负责。如果前端用 `queue.Queue` 桥接 SSE，由消费端处理。`controller.emit` 内部 try/except 已经做了"callback 失败不影响主流程"。

### 验收标准

- 注册 callback 跑 `--mock-llm --demo`，至少收到 1 次 `run_started`、≥10 次 `node_started/finished` 配对、1 次 `run_finished`。
- callback 抛异常不影响主流程，错误进 `logger.exception`。
- abort 后收到 `run_aborted`，且 `payload.keep_partial` 与请求一致。
- `Event` 可 `dataclasses.asdict()` 转 JSON，前端可直接消费。

## P0-C. Diff 上下文检索

**修复目标**：当前 reviewer 只看 diff 片段。`safe_divide` 这种新增函数 reviewer 永远只能猜"调用方有没有兜底处理"，跨文件影响完全看不到。V1.2 加一个"轻量上下文检索"节点，让 reviewer 看到变更符号的定义和调用点。

不引入 LSP / tree-sitter / 向量库，用现成 grep + AST 即可，保持 V1.2 的"轻量增强"定位。

### 落地方案

**新增 `codecross/context_retrieval.py`**：

```python
from dataclasses import dataclass, field
from pathlib import Path
import ast
import re

@dataclass
class SymbolContext:
    symbol: str
    kind: str            # "definition" | "call_site"
    file_path: str       # 相对 project_root
    line: int
    snippet: str         # 该位置 ±3 行的代码片段
    in_changed_file: bool

@dataclass
class DiffContext:
    """Planner 之后、Reviewer 之前的上下文检索结果。"""
    changed_files: list[str]
    changed_symbols: list[str]
    symbol_contexts: list[SymbolContext] = field(default_factory=list)
    truncated: bool = False    # 命中过多被截断的标记
    skipped_reason: str = ""   # 项目过大或检索关闭时填

def retrieve_diff_context(
    *, diff: str, project_root: Path, max_files: int = 200, max_hits_per_symbol: int = 5
) -> DiffContext: ...
```

**实现策略**（按成本从低到高，命中即返回）：

1. **符号定义查找**：在 `project_root` 下用 `glob("**/*.py")` 收集 Python 文件，AST 解析每个文件，找 `FunctionDef` / `AsyncFunctionDef` / `ClassDef` 名字匹配 `changed_symbols` 的定义位置。
2. **调用点查找**：用 `ripgrep` 命令（如可用）`rg -n -F "<symbol>("`；`rg` 不可用则降级为纯 Python `re.finditer` over 文件内容。每个符号最多收集 `max_hits_per_symbol` 个调用点。
3. **变更文件本身的 import 链**：解析变更文件的 `Import/ImportFrom`，作为下游影响范围的提示。

**预算与降级**：
- 单次检索硬上限：扫描文件数 ≤ `max_files`（默认 200）。超过则只扫描 `changed_files` 同级目录 + 项目根三层内的文件，并设置 `truncated=True`。
- 项目里 `.gitignore` 规则尊重：直接跳过 `.git` / `node_modules` / `__pycache__` / `.venv` / `dist` / `build`。
- 检索失败（IO 错误、无 Python 文件等）不阻塞流程，返回空 `DiffContext` 并填 `skipped_reason`。
- `RunnerConfig` 新增 `enable_context_retrieval: bool = True`，可关闭。

**Workflow 接入点**：在 planner 和 reviewer 之间插一个新节点 `context_retrieval`：

```
START -> planner -> context_retrieval -> reviewer_0/1 (fan-out) -> initial_board -> ...
```

State 增加字段 `diff_context: DiffContext`。Reviewer prompt 中追加：

```
Retrieved code context (read-only, do not assume completeness):

[Definition] examples/sample_project/math_utils.py:1
  def safe_divide(a, b):
      return a / b

[Call sites]
  examples/sample_project/runner.py:12  result = safe_divide(x, y)
  ...
```

**保持独立性**：`DiffContext` 是客观信息（哪里定义、哪里调用），不像 `risk_areas` 那样贴标签。两个 reviewer 看同一份上下文不构成"被定调"。

**与 V1.1 `parse_diff_context_notes` 的协作**：`DiffContext` 不替换 `parse_diff_context_notes`，前者负责"代码长什么样"，后者负责"diff 形态是什么"，两者一起塞进 prompt。

### 验收标准

- 单元测试：构造一个有 `safe_divide` 定义和两处调用的小项目，`retrieve_diff_context` 返回 1 个 definition + 2 个 call_site。
- 单元测试：项目超过 `max_files`，`truncated=True` 但不抛异常。
- 单元测试：项目无 Python 文件，返回空 context 且 `skipped_reason` 非空。
- 集成测试：`--mock-llm --demo` 跑通，reviewer prompt 能看到 retrieved context 段落（mock_reviewer 不读 prompt 内容，但 workflow 不应报错）。
- 性能：100 文件项目检索 ≤ 1 秒（pytest 跑一遍验证）。

## P1-A. Reviewer 独立性重构

**修复目标**：`agents.py:_infer_risk_areas` 的 mock planner 把"command injection / boundary conditions / exception flow"等标签推给两个 reviewer，违背"reviewer 互不影响"的设计意图。V1.2 把 risk_areas 从 ReviewPlan 中移除（或仅 planner 自留），reviewer 只看客观元数据。

### 落地方案

**Schema 改动**：`ReviewPlan` 不动字段（向后兼容），但语义重新定义：

- `risk_areas`：只供报告渲染，不进入 reviewer prompt。
- `review_focus` / `testing_focus` / `context_notes`：保持，进入 reviewer prompt（这些是工作范围而非定性结论）。

**Reviewer prompt 改造**：删除 prompt 中包含 `review_plan.risk_areas` 的字段。具体做法：

```python
# 旧：把整份 review_plan 塞进 prompt
f"Review plan:\n{review_plan.model_dump_json(indent=2)}\n\n"

# 新：只塞中性字段
f"Review focus:\n{_json(review_plan.review_focus)}\n\n"
f"Testing focus:\n{_json(review_plan.testing_focus)}\n\n"
f"Context notes:\n{_json(review_plan.context_notes)}\n\n"
```

**给两个 reviewer 不同视角的 plan**：在 `make_reviewer_node` 里按 reviewer 索引返回不同 `review_focus`：

```python
PERSPECTIVE_FOCUS = {
    "logic":     ["correctness", "edge cases", "runtime behavior"],
    "security":  ["security boundary", "input validation", "side effects"],
}

def make_reviewer_node(index: int):
    def _node(state):
        reviewer = state["config"].enabled_reviewers()[index]
        perspective = "security" if "security" in reviewer.focus else "logic"
        plan = state["review_plan"].model_copy(update={
            "review_focus": PERSPECTIVE_FOCUS[perspective],
        })
        result = reviewer_agent(reviewer=reviewer, diff=state["diff"],
                                review_plan=plan, config=state["config"])
        return {"reviewer_results": [result]}
    return _node
```

**`_infer_risk_areas` 处理**：保留，但只供 mock_planner 自己内部使用，不再写入 `ReviewPlan.risk_areas`（mock planner 把它写到 `context_notes` 里作为客观提示，不作为定性标签）。或者直接删掉 `_infer_risk_areas` —— 它是 V1 demo 的产物，没有真实价值。

### 验收标准

- 单元测试：mock_reviewer 收到的 prompt 不包含 `risk_areas` 字段。
- 单元测试：两个 reviewer 收到的 `review_focus` 不同。
- 集成测试：`--mock-llm --demo` 输出报告中"Risk areas"段落仍然存在（来自 ReviewPlan.risk_areas，由 planner 自留）。

## P1-B. Reviewer 分歧标记

原 V1.1 P1-C 落地版。V1.2 顺手加上。

### 落地方案

**Schema 改动**：

```python
class FindingCluster(CodeCrossModel):
    ...
    disagreement: bool = False
    expected_reviewer_count: int = 0
```

**`board.cluster_findings` 改造**：

```python
def cluster_findings(findings, *, expected_reviewers: int) -> list[FindingCluster]:
    grouped = defaultdict(list)
    for finding in findings:
        grouped[_cluster_key(finding)].append(finding)
    clusters: list[FindingCluster] = []
    for items in grouped.values():
        top = max(items, key=severity_rank)
        reviewers = sorted({item.source_reviewer for item in items})
        clusters.append(FindingCluster(
            finding_ids=[item.finding_id for item in items],
            representative_problem=top.problem,
            reviewers=reviewers,
            severity=top.severity,
            expected_reviewer_count=expected_reviewers,
            disagreement=(len(reviewers) < expected_reviewers and expected_reviewers > 1),
        ))
    return clusters
```

`build_board_summary` 把 `len(reviewer_results)` 传入。

**验证队列加权**：`build_verification_queue` 排序键增加 disagreement 优先：

```python
disagreement_ids = {
    fid for cluster in clusters if cluster.disagreement
    for fid in cluster.finding_ids
}
candidates.sort(key=lambda item: (
    -SEVERITY_ORDER.get(item.severity, 0),
    0 if item.finding_id in disagreement_ids else 1,
    item.file_path,
    item.finding_id,
))
```

**报告渲染**：`render_board_markdown` 在 cluster 段落输出 `(disagreement)` 标记。

### 验收标准

- 单元测试：reviewer A 命中 finding X 但 B 未命中（expected=2）→ cluster `disagreement=True`，X 在队列中比同 severity 共识 finding 靠前。
- 单元测试：单 reviewer 场景（expected=1）→ `disagreement=False`，避免误判。

## P1-C. RiskRules 配置化

**修复目标**：`agents.py:evaluator_agent` 当前用 if/else 链硬编码风险等级。不同项目可能有不同策略（有些项目"high 即阻塞"，有些项目"只阻塞 critical"）。V1.2 把规则抽出。

### 落地方案

**新增 `codecross/risk_rules.py`**：

```python
from dataclasses import dataclass
from typing import Literal
from .schemas import BoardSummary, EvaluatorReport, Finding, RiskLevel

@dataclass(slots=True)
class RiskRules:
    block_on_supported: list[Literal["low", "medium", "high", "critical"]] = field(
        default_factory=lambda: ["high", "critical"]
    )
    medium_when_unresolved: list[str] = field(
        default_factory=lambda: ["high", "critical"]
    )
    reviewer_failure_threshold: float = 0.5  # 失败 reviewer 比例 > 此值则 unknown
    needs_high_risk_review_levels: list[str] = field(
        default_factory=lambda: ["high", "critical"]
    )

def evaluate_risk(
    *, rules: RiskRules,
    reviewer_results, findings, board_summary: BoardSummary
) -> tuple[RiskLevel, str, str, bool]:
    """返回 (risk_level, reasoning, recommended_action, needs_high_risk_review)。

    决策表（可被 rules 调整）：
    - reviewer 失败超阈值 → unknown
    - supported critical → critical
    - supported in block_on_supported → 对应等级
    - unresolved high/critical → medium
    - error_count > 0 → medium
    - else → low
    """
    ...
```

**Evaluator 改造**：`evaluator_agent` 删除 if/else 链，改为：

```python
def evaluator_agent(*, reviewer_results, findings, board_summary, config):
    rules = config.risk_rules
    risk_level, reasoning, action, needs_review = evaluate_risk(
        rules=rules, reviewer_results=reviewer_results,
        findings=findings, board_summary=board_summary,
    )
    return EvaluatorReport(...)
```

**ProjectConfig 改动**：

```python
@dataclass(slots=True)
class ProjectConfig:
    ...
    risk_rules: RiskRules = field(default_factory=RiskRules)
```

`load_project_config` 默认行为与 V1.1 一致（block_on_supported=["high", "critical"]）。

### 验收标准

- 单元测试：配置 `block_on_supported=["critical"]` 时，supported high finding 不再触发 high risk_level。
- 单元测试：`reviewer_failure_threshold=0.0` 时一个 reviewer 失败即 unknown。
- 集成测试：`--mock-llm --demo` 输出与 V1.1 相同（默认规则一致）。

## P1-D. 配置外部化（codecross.yaml）

**修复目标**：当前 `load_project_config` 全部硬编码。模型选择、reviewer 启用、risk_rules、runner_config 都应该可外部覆盖。

### 落地方案

**配置文件路径解析顺序**（优先级从高到低）：
1. CLI `--config <path>`（V1.2 新增 CLI 参数）
2. `${project_root}/codecross.yaml`
3. `${project_root}/.codecross/config.yaml`
4. 内置默认（即 V1.1 行为）

**Schema**（用 PyYAML 解析，pydantic 校验）：

```yaml
language: python
test_framework: pytest

reviewers:
  - name: logic_reviewer
    role: deepseek_reviewer
    provider: deepseek
    model: deepseek-chat
    focus: "logic, boundary conditions, runtime exceptions, maintainability"
    temperature: 0.2
    enabled: true
    api_key_env: DEEPSEEK_API_KEY
    base_url_env: DEEPSEEK_BASE_URL
  - name: security_maintainability_reviewer
    role: second_reviewer
    provider: anthropic
    model: claude-3-5-sonnet-latest
    focus: "security, API design, failure modes, missing tests"
    enabled: true
    api_key_env: ANTHROPIC_API_KEY

models:
  planner: { provider: deepseek, model: deepseek-chat }
  test_case_agent: { provider: deepseek, model: deepseek-chat, temperature: 0.1 }
  evaluator: { provider: deepseek, model: deepseek-chat }
  high_risk_reviewer: { provider: anthropic, model: claude-3-5-sonnet-latest }
  # 未指定的角色用 planner 的默认值

runner:
  max_verification_rounds: 3
  max_tests_per_round: 8
  test_timeout_seconds: 20
  rerun_uncertain: true
  enable_context_retrieval: true
  allow_network: false

risk_rules:
  block_on_supported: ["high", "critical"]
  reviewer_failure_threshold: 0.5
```

**新增 `codecross/config_loader.py`**：

```python
def load_config_file(path: Path) -> dict: ...
def merge_with_defaults(file_config: dict) -> ProjectConfig: ...
def resolve_config(project_root: Path, *,
                   explicit_path: Path | None = None,
                   mock_llm: bool = False) -> ProjectConfig: ...
```

`config.py:load_project_config` 改为调用 `resolve_config`。

**校验**：
- 至少一个 `enabled` reviewer，否则报错。
- `provider` 必须在已知列表（deepseek / anthropic / openai）。未知 provider 给 warning 但不阻塞。
- `risk_rules` 字段未知值（如 `block_on_supported: ["urgent"]`）报错。

**CLI 参数**：

```python
parser.add_argument("--config", help="Path to codecross.yaml override.")
```

### 验收标准

- 用一个最小 yaml（只配 reviewers）跑 `--mock-llm --demo`，行为与默认一致。
- 用 yaml 关闭一个 reviewer，graph 中该 reviewer 节点消失。
- yaml 缺字段使用默认值；多余字段（拼写错误）被 pydantic `extra="forbid"` 拦截并给清晰错误。
- 不存在 yaml 时退化到 V1.1 行为。

## P2-A. Test Case 能力扩展

**修复目标**：当前 `_mock_test_cases` 只能处理 safe_divide；真实 LLM 路径的 prompt 也只让模型生成单文件单 import 测试。V1.2 加 fixture / parametrize / monkeypatch / Hypothesis 模板，让 reviewer 拿到的 finding 能被覆盖到的范围扩大。

### 落地方案

**Prompt 模板扩展**：`test_case_agent` 的 system prompt 增加：

```
You may use these pytest features when appropriate:
- @pytest.mark.parametrize(...) for table-driven tests
- monkeypatch fixture for replacing module-level functions or attributes
- @pytest.fixture for shared setup
- pytest.raises(...) for expected exceptions
- hypothesis.strategies for property-based tests on numeric/boundary findings

Do NOT use: subprocess, os.system, network, file writes outside tmp_path.
```

**生成模板库**：新增 `codecross/test_templates.py`，提供 5 个模板：

| 模板 | 适用 finding 类型 | 示例 |
|---|---|---|
| `simple_assert` | runtime / logic | `assert f(1) == 2` |
| `pytest_raises` | exception flow | `with pytest.raises(...): f(0)` |
| `parametrize_table` | boundary conditions | `@pytest.mark.parametrize(...)` |
| `monkeypatch_dep` | side effect / IO | `monkeypatch.setattr(mod, "x", fake)` |
| `hypothesis_property` | numeric properties | `@given(st.integers())` |

模板是**生成提示**而非硬编码代码：根据 finding 的 `category` 选模板插入 prompt 的 example 段。

**Sanitizer 兼容**：所有模板内部使用的 import 必须在 V1.1 sanitizer 的允许列表中。`hypothesis` 是新依赖，需要：
- `BLOCKED_IMPORT_ROOTS` 不包含 `hypothesis`（已不在）。
- 但 V1.1 的白名单是隐式的（黑名单+AST 调用），所以 hypothesis 默认放行。
- `RunnerConfig.allowed_imports` 在 V1.2 引入显式白名单（见下方）。

**白名单转向**：V1.1 的 sanitizer 是黑名单优先。V1.2 加可选白名单层：

```python
@dataclass
class RunnerConfig:
    ...
    enforce_import_allowlist: bool = False  # 默认关，避免破坏 V1.1 行为
    allowed_imports: list[str] = field(default_factory=lambda: [
        "pytest", "unittest", "unittest.mock", "typing", "dataclasses",
        "math", "decimal", "fractions", "statistics", "re", "json",
        "itertools", "functools", "collections", "collections.abc",
        "copy", "enum", "datetime", "uuid", "contextlib", "io",
        "hypothesis", "hypothesis.strategies",
    ])
```

`TestCodeSafetyVisitor.visit_Import/ImportFrom` 增加：

```python
if runner_config.enforce_import_allowlist:
    if root not in runner_config.allowed_imports:
        self._block(f"import_not_allowed:{root}")
```

### 验收标准

- 单元测试：`hypothesis.given` 测试代码通过 sanitizer（白名单关闭和打开两种情况）。
- 单元测试：`@pytest.mark.parametrize` 测试代码不被误杀。
- 集成测试：mock_test_case_agent 增加一个针对 `boundary` 类 finding 的 parametrize 模板用例，`--mock-llm --demo` 输出包含该用例。

## P2-B. Prompt 工程系统化

**修复目标**：当前 prompt 散落在 `agents.py`，没有 severity 校准、没有 few-shot、版本不可追踪。V1.2 抽出。

### 落地方案

**目录结构**：

```
codecross/
  prompts/
    __init__.py           # load_prompt(name, version="v1") + 缓存
    planner.v1.md
    reviewer.v1.md
    test_strategy.v1.md
    test_case.v1.md
    targeted_reviewer.v1.md
    high_risk_reviewer.v1.md
    severity_calibration.md   # 共享段，被 reviewer.v1.md 引用
    examples/
      reviewer_input.json
      reviewer_output.json
```

**Prompt loader**：

```python
PROMPT_DIR = Path(__file__).parent / "prompts"

@functools.lru_cache(maxsize=None)
def load_prompt(name: str, version: str = "v1") -> str:
    path = PROMPT_DIR / f"{name}.{version}.md"
    return path.read_text(encoding="utf-8")

def render_prompt(name: str, *, version: str = "v1", **vars) -> str:
    template = load_prompt(name, version)
    return template.format(**vars)
```

**Severity 校准段**（severity_calibration.md）：

```
Severity guidance:
- critical: data loss, RCE, auth bypass, secret exposure
- high: incorrect business logic on common path, DoS, resource leak
- medium: incorrect logic on edge case, performance regression
- low: maintainability, style, missing test coverage

If unsure, prefer one level lower over higher.
```

**Few-shot 示例**：reviewer.v1.md 末尾追加 1 个示例 input → output JSON pair。

**版本号**：每次 prompt 修改时升一个版本（reviewer.v2.md）。`load_prompt` 默认 v1，调用方按需切换。版本号写入 `Event.payload`，便于追溯。

### 验收标准

- 所有 agent prompt 从 `agents.py` 迁出，源代码中只有 `render_prompt(...)` 调用。
- `tests/test_prompts.py` 验证每个 prompt 文件可加载、`{}` 占位符与渲染参数对得上。
- 报告 footer 增加 prompt 版本（用于排查"是不是 prompt 改坏了结果"）。

## P2-C. 关键单测补齐

| 文件 | 覆盖目标 | 最少用例数 |
|---|---|---|
| `tests/test_runtime_abort.py` | P0-A：abort 短路 + keep_partial 分支 | 6 |
| `tests/test_events.py` | P0-B：事件触发点 + callback 异常隔离 | 6 |
| `tests/test_context_retrieval.py` | P0-C：定义查找 / 调用点 / 截断 / 降级 | 6 |
| `tests/test_reviewer_independence.py` | P1-A：prompt 不含 risk_areas | 3 |
| `tests/test_disagreement_cluster.py` | P1-B：共识/分歧 + 队列加权 | 4 |
| `tests/test_risk_rules.py` | P1-C：可配置规则下的风险等级 | 5 |
| `tests/test_config_loader.py` | P1-D：yaml 解析 + 校验 + 默认 fallback | 6 |
| `tests/test_test_templates.py` | P2-A：模板 sanitizer 兼容 + 白名单开关 | 5 |
| `tests/test_prompts.py` | P2-B：prompt 加载 + 占位符校验 | 4 |
| `tests/test_workflow_smoke.py` | 端到端 mock_llm（V1.1 已有，V1.2 扩展 abort/event 用例） | +2 |

合计新增 ≥ 47 用例。

## 实施顺序

按"前端依赖优先、上下文质量次之、配置和 prompt 收尾"分三批：

**批次 1：前端依赖（必须先于前端 V1 收口）**
1. P0-A RunController + abort（2 天）
2. P0-B 事件回调接口 + friendly_name 表（1 天，与 P0-A 共用装饰器）

**批次 2：reviewer 输入质量（V1.2 的核心价值点）**
3. P0-C Diff 上下文检索（2-3 天）
4. P1-A Reviewer 独立性重构（1 天）
5. P1-B 分歧标记（半天）

**批次 3：可配置 + 收尾**
6. P1-C RiskRules 配置化（1 天）
7. P1-D 配置外部化（1-2 天）
8. P2-A Test Case 模板扩展（1-2 天）
9. P2-B Prompt 工程系统化（1-2 天）
10. P2-C 单测补齐（1-2 天，与上面并行）

整体节奏建议三个工作周。如果 P2 优先级临时压低，前两批两周可结。

## 与前端 V1 的接口契约（最终确认）

V1.2 完成后，前端 V1 通过 FastAPI 调用：

| 接口 | 后端依赖项 |
|---|---|
| `POST /api/runs` | `run_workflow(diff, config=...)`，config 含 RunController |
| `POST /api/runs/:id/abort` | `controller.request_abort(keep_partial=...)` |
| `GET /api/runs/:id/events` (SSE) | callback 写入 `queue.Queue`，FastAPI 端从 queue 读出 `Event` 序列化 |
| 节点 friendly_name | 读 `events.NODE_FRIENDLY_NAMES` 字典 |
| 进度计算 | 用 `round_started/round_finished` 对 + `runner_config.max_verification_rounds` |

前端**不需要**：维护节点名翻译、轮询状态、解析报告 Markdown。

## V1.2 不做的边界

- 不引入 Docker / 容器沙箱（保留 V2）。
- 不重写 LangGraph 拓扑（仅在 planner 后插一个 context_retrieval 节点）。
- 不做多语言（pytest only）。
- 不做向量检索 / embedding（context retrieval 用 grep + AST）。
- 不做 SSE 服务器实现（仅暴露事件流，FastAPI 由前端工作流落地）。
- 不做项目/账号系统。
- 不做 SARIF / HTML 报告。
- 不做缓存（按 diff hash 复用结果是 V2 范畴）。

## 破坏性变更与迁移

V1.2 对调用方有以下行为变化，需在 CHANGELOG 注明：

1. `ProjectConfig` 新增 `runtime` / `risk_rules` 字段（有默认值，构造方式不变）。
2. `should_continue_verification` 签名 V1.1 已加 `status_changes`，V1.2 不再变更。
3. `cluster_findings(findings)` 增加必传参数 `expected_reviewers`。直接调用方需更新（项目内只在 `build_board_summary` 中调用一次，迁移成本低）。
4. `evaluator_agent` 内部不再硬编码规则。如有外部 monkey-patch 该函数的代码（项目内无），需迁移到自定义 `RiskRules`。
5. CLI 新增 `--config` 参数，未传不影响行为。
6. Reviewer prompt 不再包含 `risk_areas`。如外部对 prompt 文本做正则匹配，需更新（项目内无此用法）。

## 与 V1.1 的对比表

| 能力 | V1.1 | V1.2 |
|---|---|---|
| 测试代码安全 | AST sanitizer | 同 + 可选 import 白名单 |
| 模型异常 | 模块前缀白名单 | 同 |
| Diff 元数据 | 文件级 + hunk 范围 | 同 + **符号定义/调用点** |
| 终止 | 不可中断 | **协作式 abort** |
| 观测 | 仅 state | **Event 回调 + friendly_name** |
| Reviewer 独立性 | planner 仍贴 risk_areas | **客观元数据 only + 视角分流** |
| 分歧识别 | 无 | **Cluster.disagreement + 队列加权** |
| 风险规则 | 硬编码 | **RiskRules 配置** |
| 配置外部化 | 无 | **codecross.yaml** |
| Test 模板 | 单一 simple_assert | **5 类模板 + Hypothesis** |
| Prompt | 内联字符串 | **`prompts/*.md` + 版本** |

V1.2 不为追新功能，而是把 V1.1 的"可信度地基"接到"用户可见 + 可控制 + 可配置"的体面态。
