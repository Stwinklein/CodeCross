# CodeCross V1.1 修订版修复计划

本计划在原 `codecross_v1_1_fix_plan.md` 基础上做了三处补强：
- 把 abort 能力纳入 V1.1（前端 V1 强依赖）。
- 把事件回调接口纳入 V1.1（为 V1.2 SSE 与前端铺路）。
- 每一项都给到具体的模块、函数签名、改动行号、反向用例和验收方式。

## 版本目标

V1.1 不扩展能力，只让 V1 主链路达到"敢上生产"的最低门槛：

1. **安全底线**：模型生成的测试不能伤到宿主。
2. **结论可信度**：单次测试结果不再过度决定 finding 结论。
3. **真实链路可运行**：真实模型调用不能因为参数或异常处理静默失败。
4. **可终止**：前端 V1 的终止按钮要有后端能力支撑。
5. **可观测**：工作流要能向外发结构化事件。

不做架构重写。

## 范围一览

| 优先级 | 项目 | 一句话说明 |
|---|---|---|
| P0-A | Executor AST + Import 白名单 | 替换字符串黑名单，叠加多层防御 |
| P0-B | 异常处理收紧 | 列白名单异常，其它向上抛 |
| P0-C | Provider Kwargs 集中化 | 修 Anthropic 参数 + 启动期能力探测 |
| P0-D | 协作式 abort | RunController + 节点入口检查 + keep_partial |
| P1-A | Diff parser 替换 | unidiff + hunk 行号下传 reviewer |
| P1-B | Verification 规则升级 | 重跑 + 早停 + 默认 confidence 下调 |
| P1-C | Reviewer 分歧标记 | FindingCluster 增加 disagreement 字段 |
| P1-D | 事件回调接口 | on_event 钩子，给 V1.2/前端铺路 |
| P1-E | 关键单测补齐 | 9 个文件的最小回归集 |

不进入 V1.1 的 13 项见末尾"显式延后"。

## P0-A. Executor AST + Import 白名单

**修复目标**：替换 `executor.py:23` 的 `BLOCKED_TEST_SNIPPETS` 字符串黑名单，改成"AST 检查 + Import 白名单 + cwd/超时/资源限制"四道叠加。

**为什么不上 Docker**：V1.1 不引入容器依赖。AST 检查 + 严格白名单足够把"测试代码意外炸到宿主"的概率压到很低；真正的强隔离放 V2。

### 落地方案

新增模块 `codecross/sanitizer.py`，对外暴露：

```python
def check_test_code(code: str, runner_config: RunnerConfig) -> SanitizerResult: ...

@dataclass
class SanitizerResult:
    safe: bool
    reason: str          # 不安全时的简短原因，例如 "import_not_allowed:os"
    details: str = ""    # 可选的补充上下文，写入 TestResult.failure_summary
```

`executor.is_safe_test_code` 整段替换为对 `check_test_code` 的调用。`BLOCKED_TEST_SNIPPETS` 删除。

**AST 检查规则**（对每一棵 AST，walk 一遍，命中任一即判 unsafe）：

1. `Import` / `ImportFrom`：模块名必须在 `RunnerConfig.allowed_imports` 白名单内。
2. `Call`：被调用对象的"完整名"匹配下列模式即拦：
   - `eval` / `exec` / `compile` / `__import__` / `globals` / `locals` / `vars` / `getattr` / `setattr` / `delattr`
   - 任何 `subprocess.*`、`os.system`、`os.popen`、`os.exec*`、`os.spawn*`、`os.fork`
   - 任何以 `socket.` 开头的调用
   - `open(..., mode=...)` 中 mode 含 `w` / `a` / `x`（用 AST 判 keyword/positional 第二参数）
3. `Attribute`：链路上含 `__class__` / `__bases__` / `__subclasses__` / `__globals__` / `__builtins__` 直接拦。
4. `With` / `AsyncWith`：上下文管理器表达式同样走 Call 检查。

**Import 白名单**（写到 `RunnerConfig.allowed_imports`，可被 `codecross.yaml` 覆盖）：

```
pytest, unittest, unittest.mock, typing, dataclasses,
math, decimal, fractions, statistics, re, json,
itertools, functools, collections, collections.abc,
copy, enum, datetime, uuid, contextlib, io
```

特别说明：`io` 允许是因为 `StringIO` / `BytesIO` 经常用于 mock；`unittest.mock` 允许是为了让生成测试能 patch 出依赖。

**RunnerConfig 新增字段**：

```python
allowed_imports: list[str]
sanitizer_extra_blocked_calls: list[str]   # 项目级追加，例如 "ssl.create_default_context"
```

旧的 `blocked_commands` 保留（作为字符串层兜底，第二道）。

**子进程层面叠加**：
- `subprocess.run` 已传 `cwd=root`，保留。
- 新增 `env` 限制：复制后 `pop("HTTP_PROXY", None)`、`pop("HTTPS_PROXY", None)`、`pop("ALL_PROXY", None)`，避免测试无意走代理外联。
- 新增 wall-clock 双限：`timeout = min(test_case.timeout_seconds, runner_config.test_timeout_seconds)`，避免单 case 写 1000 秒绕开预算。
- POSIX 平台叠加 `preexec_fn=os.setsid`（便于超时时用 `os.killpg` 清理子孙进程）；Windows 用 `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`。

### 反向用例（必须全部判 unsafe）

新建 `tests/test_sanitizer.py`，至少覆盖以下 20+ 例：

```
1.  os.system("rm -rf /")
2.  __import__("os").system("...")
3.  from os import system as s; s("...")
4.  getattr(__import__("os"), "system")("...")
5.  globals()["__builtins__"]["eval"]("1+1")
6.  exec("print(1)")
7.  compile("1", "<s>", "eval")
8.  subprocess.Popen(["sh"])
9.  subprocess.run("sh", shell=True)
10. import socket; socket.socket()
11. import urllib.request; urllib.request.urlopen("...")
12. import requests; requests.get("...")
13. open("/etc/passwd", "w").write("x")
14. pathlib.Path("/etc/passwd").write_text("x")
15. shutil.rmtree("/")
16. os.unlink("/tmp/x")
17. ().__class__.__bases__[0].__subclasses__()
18. type("X", (), {}).__init_subclass__
19. async def t(): import socket
20. with open("/tmp/x", "w") as f: f.write("x")
21. import os; os.popen("ls")
22. ctypes.CDLL("libc.so.6").system(b"ls")
```

### 正向用例（必须判 safe）

```
1.  from examples.sample_project.math_utils import safe_divide; assert safe_divide(10, 0) is None
2.  使用 unittest.mock.patch 装饰器 patch 一个函数
3.  使用 io.StringIO 捕获输出
4.  pytest.raises 上下文
5.  pytest.mark.parametrize
6.  使用 dataclasses 构造测试数据
```

### 验收标准

- 反向用例集全部返回 `safe=False`，`reason` 字段必须可读。
- 正向用例集全部返回 `safe=True`。
- `is_safe_test_code` 旧签名被 `check_test_code` 完全替代；`executor.execute_pytest_case` 用新接口。
- 沙箱拦截时返回的 `TestResult.status == "unsafe"`，`failure_summary` 含 reason。

## P0-B. 异常处理收紧

**修复目标**：`agents.py:198`、`agents.py:422` 的 `except Exception` 把所有异常都吞成"reviewer/test-case failed"，导致 schema 字段名错、AttributeError、KeyError 这类真实代码 bug 被掩盖成"模型失败"。

### 落地方案

**第一步：在 `llm.py` 定义异常白名单**：

```python
RECOVERABLE_LLM_ERRORS: tuple[type[BaseException], ...] = (
    ModelCallError,
    ValidationError,
    TimeoutError,
    ConnectionError,
    json.JSONDecodeError,
)
```

如果工程实际依赖 `httpx` / `anthropic` / `openai` SDK 抛出独有异常，按需追加：

```python
try:
    import httpx
    RECOVERABLE_LLM_ERRORS = (*RECOVERABLE_LLM_ERRORS, httpx.HTTPError)
except ImportError:
    pass
```

**第二步：替换所有 `except Exception` 兜底**

| 位置 | 原写法 | 修订写法 |
|---|---|---|
| `agents.py:198` reviewer_agent | `except Exception as exc:` | `except RECOVERABLE_LLM_ERRORS as exc:` |
| `agents.py:422` test_case_agent | `except Exception:` | `except RECOVERABLE_LLM_ERRORS:` |

`call_model_json` 内层（`llm.py:182`）已经显式列出异常类型，保留即可。

**第三步：日志保留堆栈**

收紧不等于"丢失诊断信息"。在 `except` 块里：

```python
except RECOVERABLE_LLM_ERRORS as exc:
    logger.exception(
        "reviewer %s failed with recoverable error", reviewer.display_name
    )
    return ReviewerResult(
        reviewer_name=reviewer.display_name,
        model=f"{reviewer.provider}/{reviewer.model}",
        reviewer_notes="Reviewer failed; workflow continues with other reviewers.",
        errors=[f"{type(exc).__name__}: {exc}"],
    )
```

`logger.exception` 会带上完整堆栈到 stderr/日志文件，不影响 ReviewerResult 输出。

**第四步：mock 路径不受影响**

mock_llm 不会触发模型异常路径，但 `_mock_test_cases` 内部如果将来出错也走同样规则。V1.1 不动 mock。

### 验收标准

- 把 reviewer prompt 故意改成 schema 不一致 → ValidationError，被白名单捕获，reviewer 输出 errors 字段。
- 在 reviewer_agent 内人为 `raise AttributeError("debug")` → 直接向上传播，不会被静默吞掉。
- `--mock-llm` 端到端跑通不变化。

## P0-C. Provider Kwargs 集中化

**修复目标**：`llm.py:60` 给 Anthropic 传 `max_tokens_to_sample` 是旧 SDK 字段，现行 `langchain-anthropic` 用 `max_tokens`。一旦真实运行直接报 `TypeError: unexpected keyword argument`。同时把 provider 差异集中维护，避免后续加 OpenAI/Qwen 时散落 if/else。

### 落地方案

**新增 `llm.py:build_provider_kwargs`**：

```python
def build_provider_kwargs(settings: ModelSettings) -> dict[str, Any]:
    base: dict[str, Any] = {
        "temperature": settings.temperature,
        "timeout": settings.timeout_seconds,
    }
    if settings.api_key:
        base["api_key"] = settings.api_key
    if settings.base_url:
        base["base_url"] = settings.base_url

    provider_specific: dict[str, dict[str, Any]] = {
        "anthropic": {"max_tokens": settings.max_tokens},
        "deepseek":  {"max_tokens": settings.max_tokens},
        "openai":    {"max_tokens": settings.max_tokens},
    }
    base.update(provider_specific.get(settings.provider, {"max_tokens": settings.max_tokens}))
    return base
```

`create_chat_model` 改为：

```python
def create_chat_model(settings: ModelSettings):
    if settings.api_key_env and not settings.api_key:
        raise ModelCallError(...)
    return init_chat_model(
        model=settings.model,
        model_provider=settings.provider,
        **build_provider_kwargs(settings),
    )
```

**启动期能力探测**（可选但推荐）：

`load_project_config` 末尾或 `build_graph` 入口处加一次：

```python
def probe_provider_kwargs() -> None:
    """启动时打印一次每个 provider 期望的字段，帮助升级时定位问题。"""
    for settings in iter_all_model_settings(config):
        try:
            model = create_chat_model(settings)
            logger.debug("provider %s ok: %s", settings.provider, type(model).__name__)
        except ModelCallError:
            continue
        except TypeError as exc:
            logger.warning("provider %s kwargs mismatch: %s", settings.provider, exc)
```

只在 `--debug` 或 env `CODECROSS_PROBE=1` 下跑，避免每次正常运行都额外初始化。

### 验收标准

- 真实 Anthropic 配置（`ANTHROPIC_API_KEY` 已设）下 `python -m codecross.main --demo` 不再因 `max_tokens_to_sample` 报错。
- DeepSeek 路径行为不变。
- 在 `tests/test_provider_kwargs.py` 中验证：
  - `build_provider_kwargs(anthropic_settings)["max_tokens"]` 存在且不存在 `max_tokens_to_sample`。
  - `build_provider_kwargs(deepseek_settings)["base_url"]` 在 `DEEPSEEK_BASE_URL` 设置时被填入。

## P0-D. 协作式 abort

**修复目标**：前端 V1 的核心交互是终止按钮，但当前 `app.invoke()` 不可中断。V1.1 必须提供"协作式 abort"——已经在跑的节点不强杀，但后续节点能感知并跳过，可选生成简报。

### 落地方案

**新增 `codecross/runtime.py`**：

```python
import threading
from dataclasses import dataclass

@dataclass
class RunController:
    """单次 Run 的运行时控制对象，跨节点共享。"""
    run_id: str
    abort_event: threading.Event = field(default_factory=threading.Event)
    keep_partial: bool = True

    def request_abort(self, *, keep_partial: bool = True) -> None:
        self.keep_partial = keep_partial
        self.abort_event.set()

    @property
    def aborted(self) -> bool:
        return self.abort_event.is_set()
```

**State 与 ProjectConfig 的关系**：`RunController` 不放 state（state 跨节点序列化语义不适合放 Event 对象），改放 `ProjectConfig.runtime` 字段（dataclass field, default=None），由调用方在 `run_workflow` 时注入。

```python
@dataclass(slots=True)
class ProjectConfig:
    ...
    runtime: RunController | None = None
```

**节点入口装饰器**：

```python
def abortable(node_func):
    """节点装饰器：进入节点前检查 abort 状态。"""
    @functools.wraps(node_func)
    def wrapper(state: CodeCrossState):
        controller: RunController | None = state["config"].runtime
        if controller and controller.aborted:
            return {"stop_reason": "aborted"}  # 短路返回，不更新业务字段
        return node_func(state)
    return wrapper
```

把所有"业务节点"（planner_node、reviewer 节点、test_strategy_node、test_case_node、executor_node、test_result_analyzer_node、coverage_gap_node、targeted_review_node、update_board_node、evaluator_node、high_risk_review_node、final_report_node）用 `@abortable` 包一层。`select_verification_targets_node`、`initial_board_node` 这种秒级节点可以不包，影响极小。

**条件路由的 abort 短路**：

`route_after_select` / `route_after_coverage_gap` / `route_after_update_board` / `route_after_evaluator` 内部增加：

```python
def route_after_update_board(state: CodeCrossState) -> str:
    controller = state["config"].runtime
    if controller and controller.aborted:
        return "evaluator" if controller.keep_partial else "final_report"
    if state.get("continue_verification"):
        return "select_verification_targets"
    return "evaluator"
```

`keep_partial=True` → 仍然进 evaluator + final_report，让用户拿到部分简报。
`keep_partial=False` → 直接跳 final_report 输出"aborted"信息，跳过 evaluator。

**Executor 内部的 abort 检查**：

`executor.py:execute_test_cases` 是 V1.1 唯一带循环的节点：

```python
def execute_test_cases(test_cases, *, project_root, round_index, runner_config, controller=None):
    results: list[TestResult] = []
    for test_case in test_cases[:runner_config.max_tests_per_round]:
        if controller and controller.aborted:
            break
        ...
```

`executor_node` 把 `state["config"].runtime` 传进去。

**已运行 subprocess 的处理**：当前 `subprocess.run` 同步阻塞，不强杀。pytest 用例本身有 timeout，最差等一个 timeout（默认 20 秒）。这个延迟可接受，V1.1 不引入异步执行。

**对外 API**：

```python
def run_workflow(diff, *, project_root=None, config=None, controller=None) -> CodeCrossState: ...
```

CLI 暂不暴露 abort（CLI 用户没必要）；前端通过 `/api/runs/:id/abort` 调到这个 controller。

### 验收标准

- 单元测试：在 reviewer 节点完成后立即调用 `controller.request_abort()`，graph 跳过 verification loop，直接到 evaluator/final_report，`final_report` 中包含"aborted"标记。
- `keep_partial=True` 时输出含已收集证据的简报；`keep_partial=False` 时输出最简短的"aborted"页。
- 多次重复 `request_abort` 幂等。
- abort 之后再次 invoke 同一 controller 行为符合：第二次也立刻短路（除非用户重新构造 controller）。

## P1-A. Diff parser 替换

**修复目标**：`agents.py:46` 的 `parse_changed_files` 是手写正则，对 rename / binary / mode change 不稳，hunk 行号信息完全丢弃。换成 `unidiff` 库，并把 hunk 行号作为 `Finding.line_range` 的兜底来源。

### 落地方案

**新增依赖**：`unidiff>=0.7`（纯 Python，无副作用）。加到 `pyproject.toml`。

**新增模块 `codecross/diff_parser.py`**：

```python
from dataclasses import dataclass
from unidiff import PatchSet, PatchedFile

@dataclass
class HunkRange:
    file_path: str
    old_start: int
    old_length: int
    new_start: int
    new_length: int
    is_binary: bool
    is_rename: bool
    is_deletion: bool
    is_addition: bool

@dataclass
class ParsedDiff:
    changed_files: list[str]
    changed_symbols: list[str]
    hunks: list[HunkRange]
    has_binary: bool

def parse_diff(text: str) -> ParsedDiff: ...
```

`parse_diff` 的实现：

```python
def parse_diff(text: str) -> ParsedDiff:
    patch = PatchSet(text)
    files: list[str] = []
    hunks: list[HunkRange] = []
    has_binary = False
    for f in patch:
        target = f.target_file or f.source_file
        path = target.removeprefix("b/").removeprefix("a/")
        files.append(path)
        if f.is_binary_file:
            has_binary = True
        for h in f:
            hunks.append(HunkRange(
                file_path=path,
                old_start=h.source_start, old_length=h.source_length,
                new_start=h.target_start, new_length=h.target_length,
                is_binary=f.is_binary_file,
                is_rename=f.is_rename,
                is_deletion=f.is_removed_file,
                is_addition=f.is_added_file,
            ))
    return ParsedDiff(
        changed_files=sorted(dict.fromkeys(files)),
        changed_symbols=_extract_symbols_from_hunks(text),
        hunks=hunks,
        has_binary=has_binary,
    )
```

`_extract_symbols_from_hunks` 复用现有正则，但只在 hunk 的新增行内匹配，避免误报。

**与 reviewer 的对接**：

`planner_agent` 把 `ParsedDiff` 输出注入 `ReviewPlan.context_notes`：

```
File examples/sample_project/math_utils.py hunk new_start=1 new_length=2
```

`reviewer_agent` prompt 中追加一段：

```
Hunk ranges available:
- math_utils.py: new lines 1-2 (added)
```

模型如果不填 `Finding.line_range`，由 `_normalize_reviewer_result` 兜底：

```python
def _normalize_reviewer_result(result, reviewer, parsed: ParsedDiff):
    for finding in result.findings:
        if finding.line_range.start is None and finding.line_range.end is None:
            for hunk in parsed.hunks:
                if hunk.file_path == finding.file_path:
                    finding.line_range = LineRange(
                        start=hunk.new_start,
                        end=hunk.new_start + hunk.new_length - 1,
                    )
                    break
        ...
```

`agents.py:parse_changed_files` 和 `parse_changed_symbols` 删除，全部改用 `parse_diff`。`_mock_planner` 同步替换实现。

### 反向/正向用例

新建 `tests/test_diff_parser.py`：

| 用例 | 期望 |
|---|---|
| 普通新增文件 | `changed_files` 含新文件，`hunks` 至少一个 |
| 普通修改 | `hunks.new_start` 与 git 一致 |
| Rename（`rename from / rename to`） | `changed_files` 含新名，`is_rename=True` |
| 删除文件（`+++ /dev/null`） | `is_deletion=True` |
| Mode change 无内容 | 不报错，`hunks=[]` |
| Binary | `has_binary=True`，不抛异常 |
| 同文件多 hunk | hunks 数量正确 |
| 多文件 | `changed_files` 去重保序 |

### 验收标准

- 8 个用例全过。
- `--mock-llm --demo` 输出的 ReviewPlan 中 `changed_files` 与之前一致，没有回归。
- 真实 reviewer 调用时 prompt 含 hunk 行号字符串。
- Finding 没填 line_range 时被兜底填入。

## P1-B. Verification 规则升级

**修复目标**：`board.py:243` 的"failed → supported high / passed → refuted medium"过于激进，flaky 测试一次失败就 supported；同时 `should_continue_verification` 只看队列空和轮次上限，没"无进展早停"。

### 落地方案

**改动一：默认 confidence 下调 + 一次确认重跑**

修改 `update_finding_verifications_from_results` 的状态转换（`board.py:275-300`）：

| TestResult.status | 旧规则 | 新规则 |
|---|---|---|
| `failed` (首次) | supported, high | supported, **medium**（要求重跑确认） |
| `failed` (重跑同结果) | - | supported, **high** |
| `passed` | refuted, medium | refuted, **medium**（不变，但显式标注是单一证据） |
| `unsafe` | not_testable, medium | not_testable, medium（不变） |
| `error/timeout` | inconclusive, low | inconclusive, low（**前置一次重跑**） |

**重跑实现位置**：放在 `executor.py`，不污染 board 状态机。

新增 `executor.py:execute_with_rerun`：

```python
def execute_with_rerun(test_case, *, project_root, round_index, runner_config, controller):
    first = execute_pytest_case(test_case, project_root=project_root,
                                round_index=round_index, runner_config=runner_config)
    needs_rerun = first.status in {"failed", "error", "timeout"}
    if not needs_rerun or runner_config.rerun_uncertain is False:
        return [first]
    if controller and controller.aborted:
        return [first]
    second = execute_pytest_case(test_case, project_root=project_root,
                                 round_index=round_index, runner_config=runner_config)
    second.error_type = second.error_type or "rerun"
    return [first, second]
```

`execute_test_cases` 改为聚合两次结果。`update_finding_verifications_from_results` 看到同一 `test_case_id` 的两条结果时，用合并规则：

```
两次都 failed   → supported, high
两次都 passed   → refuted, medium
不一致          → inconclusive, low, 标注 "flaky"
两次 error/timeout → inconclusive, low, 标注 "executor_unstable"
```

`RunnerConfig` 新增字段 `rerun_uncertain: bool = True`，可在配置或测试中关闭以加速。

**改动二：无进展早停**

`board.py:should_continue_verification`：

```python
def should_continue_verification(board_summary, round_index, runner_config, last_round_changes):
    if not board_summary.verification_queue:
        return False, "verification_queue_empty"
    if round_index >= runner_config.max_verification_rounds:
        return False, "max_verification_rounds_reached"
    if round_index >= 2 and not last_round_changes:
        return False, "no_progress_after_round_2"
    return True, "verification_queue_has_unresolved_targets"
```

`workflow.py:update_board_node` 把 `status_changes` 传入：

```python
continue_verification, stop_reason = should_continue_verification(
    provisional_summary, state["round_index"], state["config"].runner_config,
    last_round_changes=status_changes,
)
```

**改动三：BoardSummary 增加 stop_reason 维度**

报告中"Stop reason"已经渲染（`render_board_markdown`），不需要改 schema。

### 验收标准

- 单元测试：构造一个会抛异常的 finding，单次失败 → confidence=medium；两次失败 → confidence=high。
- 单元测试：构造 flaky 用例（第一次 fail，第二次 pass），最终状态 `inconclusive`，标记 flaky。
- 单元测试：第三轮无变化 → 早停，stop_reason=`no_progress_after_round_2`。
- 集成测试：`--mock-llm --demo` 报告中 `Stop reason` 字段非空。

## P1-C. Reviewer 分歧标记

**修复目标**：`board.py:91` 的 `cluster_findings` 已经能聚出"多个 reviewer 命中同类问题"，但没区分"共识"和"分歧"。V1.1 加一个最小标记，让验证队列优先验证"分歧 cluster"。

### 落地方案

**Schema 改动**：`FindingCluster` 增加字段：

```python
class FindingCluster(CodeCrossModel):
    ...
    disagreement: bool = False
    expected_reviewer_count: int = 0   # 启用的 reviewer 总数，便于判断
```

**聚类逻辑改动**：`board.py:cluster_findings` 在 build cluster 时：

```python
def cluster_findings(findings, *, expected_reviewers):
    ...
    clusters.append(FindingCluster(
        finding_ids=[item.finding_id for item in items],
        representative_problem=top.problem,
        reviewers=sorted({item.source_reviewer for item in items}),
        severity=top.severity,
        expected_reviewer_count=expected_reviewers,
        disagreement=(len({item.source_reviewer for item in items}) < expected_reviewers),
    ))
```

调用方 `build_board_summary` 把 `len(reviewer_results)` 传入。

**验证队列加权**：`build_verification_queue` 排序键增加分歧优先：

```python
candidates.sort(key=lambda item: (
    -SEVERITY_ORDER.get(item.severity, 0),
    0 if item.finding_id in disagreement_ids else 1,
    item.file_path,
    item.finding_id,
))
```

`disagreement_ids` 由 cluster 中 `disagreement=True` 的所有 finding_id 构成。

**报告渲染**：`render_board_markdown` 在 cluster 段落输出 "(disagreement)" 标记。

### 验收标准

- 单元测试：两个 reviewer 输出，A 命中 finding X，B 不命中 → cluster `disagreement=True`，X 在验证队列中比同 severity 的共识 finding 靠前。
- 单元测试：两个 reviewer 都命中 → `disagreement=False`。
- 报告渲染包含 disagreement 标记。

## P1-D. 事件回调接口

**修复目标**：当前节点完成只在 state 留痕，无法向外推进度。前端 V1 的 SSE、CLI 的进度提示、未来的可观测系统都要这层。V1.1 不实现 SSE，但**接口要先固化**，否则 V1.2 再加要改 workflow 内部一遍。

### 落地方案

**事件 Schema**：新增 `codecross/events.py`：

```python
from dataclasses import dataclass, field
from typing import Any, Literal

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
```

**回调签名**：

```python
EventCallback = Callable[[Event], None]
```

线程安全：调用方负责。如果前端用 `queue.Queue` 桥接到 SSE，由消费端处理。

**注入位置**：`RunController` 增加字段：

```python
@dataclass
class RunController:
    ...
    on_event: EventCallback | None = None

    def emit(self, event: Event) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                logger.exception("event callback failed")
```

`emit` 永远不抛，避免观测层影响主流程。

**节点装饰器升级**（与 P0-D 共享）：

```python
def abortable_and_observed(node_name: str, friendly: str):
    def wrap(node_func):
        @functools.wraps(node_func)
        def inner(state):
            controller = state["config"].runtime
            if controller and controller.aborted:
                return {"stop_reason": "aborted"}
            if controller:
                controller.emit(Event(event_type="node_started",
                                      node_name=node_name, friendly_name=friendly,
                                      round_index=state.get("round_index", 0),
                                      run_id=controller.run_id))
            try:
                result = node_func(state)
            except Exception as exc:
                if controller:
                    controller.emit(Event(event_type="node_failed",
                                          node_name=node_name, friendly_name=friendly,
                                          payload={"error": f"{type(exc).__name__}: {exc}"},
                                          run_id=controller.run_id))
                raise
            if controller:
                controller.emit(Event(event_type="node_finished",
                                      node_name=node_name, friendly_name=friendly,
                                      run_id=controller.run_id))
            return result
        return inner
    return wrap
```

**节点名 → friendly_name 映射表**：写入 `codecross/events.py`：

```python
NODE_FRIENDLY_NAMES = {
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

**executor 内部子事件**：循环内 emit `test_started` / `test_finished`，方便前端展示"3/8"。

**对外 API**：

```python
def run_workflow(diff, *, project_root=None, config=None,
                 controller: RunController | None = None) -> CodeCrossState: ...
```

`controller` 同时承担 abort + on_event 两职。CLI 路径不传 controller，行为不变。

### 验收标准

- 单元测试：注册 callback，跑 `--mock-llm --demo`，至少收到 `run_started`、N 个 `node_started/finished`、`run_finished` 各一次。
- 节点失败时收到 `node_failed`，payload 含异常类型。
- abort 后收到 `run_aborted`。
- callback 抛异常不影响主流程。

## P1-E. 关键单测补齐

V1.1 必须新增的测试文件，作为防回归基线：

| 文件 | 覆盖目标 | 最少用例数 |
|---|---|---|
| `tests/test_sanitizer.py` | P0-A 反向/正向用例集 | 22 反 + 6 正 |
| `tests/test_provider_kwargs.py` | P0-C build_provider_kwargs | 4 |
| `tests/test_runtime_abort.py` | P0-D RunController + 节点短路 | 6 |
| `tests/test_diff_parser.py` | P1-A 8 类 diff 形态 | 8 |
| `tests/test_verification_rules.py` | P1-B 重跑 + 早停 + 状态合并 | 6 |
| `tests/test_disagreement_cluster.py` | P1-C 共识/分歧 + 队列加权 | 4 |
| `tests/test_events.py` | P1-D 事件回调最小流 | 5 |
| `tests/test_workflow_smoke.py` | `--mock-llm` 端到端 | 1 |
| `tests/test_exception_boundary.py` | P0-B 白名单异常 vs 真实 bug | 4 |

测试运行入口：`pytest tests/`，CI（如有）应在 `--mock-llm` 模式跑。

### 验收标准

- 9 个文件全部就位，`pytest tests/` 通过。
- 总用例数 ≥ 60。
- 不依赖网络、不依赖真实模型 API。

## 显式延后（不进入 V1.1）

| 编号 | 项目 | 推迟到 |
|---|---|---|
| #3 | Diff 上下文检索（符号索引/grep/LSP） | V1.2 |
| #4 | Reviewer 独立性重构（plan 不贴标签） | V1.2 |
| #5 | Test Case 生成能力（fixture/mock/Hypothesis） | V1.2+ |
| #6 | 多语言 executor（Strategy 抽象） | V2 |
| #8 | RiskRules 配置化 | V1.2 |
| #9 | 语义聚类（embedding） | V1.2+（V1.1 已做最小分歧标记） |
| #10 | 配置外部化（codecross.yaml） | V1.2 |
| #11 | 观测性（LangSmith/OTel/成本统计） | V2 |
| #12 | CI 集成 / SARIF / GitHub Action | V2 |
| #13 | 缓存（diff hash + prompt version） | V2 |
| #16 | State 拆分（sub-graph） | V2 |
| #17 | Prompt 工程系统化（few-shot/severity 校准） | 渐进 |
| #18 | HTML/SARIF 报告 | V2 |

## V1.1 不做的边界

- 不引入 Docker / 容器沙箱。
- 不重写 LangGraph 拓扑。
- 不做多语言支持。
- 不做项目/账号/CI 平台集成。
- 不重构整个 state schema。
- 不做向量检索 / embedding。
- 不做 SSE 服务器实现（接口预留即可）。

## 实施顺序

按"先地基、再链路、再观测"分三批：

**批次 1：地基（不可见但必做）**
1. P0-C Provider Kwargs（半天）
2. P0-B 异常处理收紧（半天）
3. P0-A Executor sanitizer + 反向用例集（1-2 天）
4. P1-A Diff parser 替换（1 天）

**批次 2：链路（用户可见行为变化）**
5. P0-D RunController + abort 装饰器（1-2 天）
6. P1-D 事件回调接口（与 P0-D 同期，半天）
7. P1-B Verification 重跑 + 早停（1 天）
8. P1-C 分歧标记（半天）

**批次 3：回归**
9. P1-E 9 个测试文件（1-2 天）
10. `--mock-llm --demo` + 真实模型抽测各跑一次

整体节奏建议两个工作周。

## 与前端 V1 的耦合点

V1.1 与前端 V1 共用三处接口，需双方对齐：

1. **`POST /api/runs/:id/abort`** 调用的是 `RunController.request_abort(keep_partial=...)`。
2. **`GET /api/runs/:id/events`** SSE 由前端 V1 的 FastAPI 层把 `Event` 对象序列化推流。
3. **节点 friendly_name** 表前后端共享，前端不要再维护一份。

V1.1 只保证后端能力，FastAPI 暴露由前端工作流并行推进。



