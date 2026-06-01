# `codecross/executor.py` 解析

## 文件作用
确定性测试执行器，负责测试价值检查、安全过滤、pytest 执行、重跑/flake 处理以及交叉验证接入。

- 源码行数：773
- 主要依赖：`os`、`re`、`shutil`、`subprocess`、`sys`、`tempfile`、`time`、`ast`、`pathlib`、`config`、`schemas`

## 函数与类

### `_string_literal`（函数，第 93 行）
- 签名：`def _string_literal(node: ast.AST | None) -> str | None`
- 作用：如果 AST 节点是字符串字面量则返回文本，否则返回 None。

### `_call_name`（函数，第 101 行）
- 签名：`def _call_name(node: ast.AST) -> str`
- 作用：把 Call.func 渲染成便于匹配的名字。

### `_is_constant_true`（函数，第 118 行）
- 签名：`def _is_constant_true(node: ast.AST) -> bool`
- 作用：判断一个 assert 表达式是否只是 `assert True` 这种无效证明。

### `_call_is_pytest_raises`（函数，第 124 行）
- 签名：`def _call_is_pytest_raises(node: ast.AST) -> bool`
- 作用：识别 `pytest.raises(...)`，它也是一种有效断言形式。

### `TestValueVisitor`（类，第 130 行）
- 签名：`class TestValueVisitor(ast.NodeVisitor)`
- 作用：检查生成测试是否真的包含可用断言。
- 方法：
  - `__init__`（第 138 行）：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。
  - `visit_FunctionDef`（第 144 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。
  - `visit_AsyncFunctionDef`（第 149 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。
  - `visit_Assert`（第 154 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。
  - `visit_With`（第 161 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。

### `low_value_test_reason`（函数，第 168 行）
- 签名：`def low_value_test_reason(test_case: TestCase) -> str`
- 作用：返回测试缺乏证据价值的原因；空字符串表示可以执行。

### `_contains_blocked_command_marker`（函数，第 197 行）
- 签名：`def _contains_blocked_command_marker(text: str, command: str) -> bool`
- 作用：用边界匹配命令黑名单，避免 `rm ` 误伤 `confirm `。

### `_open_call_writes`（函数，第 214 行）
- 签名：`def _open_call_writes(call: ast.Call) -> bool`
- 作用：判断 open(...) 是否会写文件。

### `TestCodeSafetyVisitor`（类，第 230 行）
- 签名：`class TestCodeSafetyVisitor(ast.NodeVisitor)`
- 作用：遍历测试代码语法树，收集不允许执行的危险行为。
- 方法：
  - `__init__`（第 237 行）：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。
  - `_block`（第 240 行）：记录拦截原因，同一个原因只保留一次，避免错误信息刷屏。
  - `visit_Import`（第 246 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。
  - `visit_ImportFrom`（第 253 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。
  - `visit_Call`（第 259 行）：AST Visitor 回调，用于检查对应语法节点并记录结果。

### `is_safe_test_code`（函数，第 277 行）
- 签名：`def is_safe_test_code(test_case: TestCase, runner_config: RunnerConfig) -> tuple[bool, str]`
- 作用：对生成测试做轻量安全过滤。

### `_summarize_failure`（函数，第 311 行）
- 签名：`def _summarize_failure(stdout: str, stderr: str) -> str`
- 作用：从 pytest 输出里提取最关键的失败行，避免报告塞入过长日志。

### `_unsafe_result`（函数，第 332 行）
- 签名：`def _unsafe_result(test_case: TestCase, round_index: int, reason: str) -> TestResult`
- 作用：把被 sanitizer 拦截的测试转换成结构化结果。

### `_skipped_result`（函数，第 345 行）
- 签名：`def _skipped_result(test_case: TestCase, round_index: int, reason: str, *, error_type: str='skipped') -> TestResult`
- 作用：把“不能形成有效证据”的测试显式写成 skipped 结果。

### `_merge_rerun_output`（函数，第 368 行）
- 签名：`def _merge_rerun_output(first: TestResult, second: TestResult) -> tuple[str, str, int]`
- 作用：合并两次 pytest 的输出，方便后续人工排查 flaky 或复现失败。

### `_flaky_result`（函数，第 386 行）
- 签名：`def _flaky_result(test_case: TestCase, round_index: int, first: TestResult, second: TestResult) -> TestResult`
- 作用：把前后两次不一致的 pytest 结果表达成现有 schema 能理解的 error。

### `_run_pytest_once`（函数，第 411 行）
- 签名：`def _run_pytest_once(test_case: TestCase, *, root: Path, round_index: int, runner_config: RunnerConfig) -> TestResult`
- 作用：实际执行一次 pytest，并把结果标准化为 TestResult。

### `execute_pytest_case`（函数，第 495 行）
- 签名：`def execute_pytest_case(test_case: TestCase, *, project_root: str | Path, round_index: int, runner_config: RunnerConfig) -> TestResult`
- 作用：执行单个 pytest 测试用例。

### `execute_test_cases`（函数，第 558 行）
- 签名：`def execute_test_cases(test_cases: list[TestCase], *, project_root: str | Path, round_index: int, runner_config: RunnerConfig, findings: list[Finding] | None=None, code_context: CodeContext | None=None, project_config: 'ProjectConfig | None'=None) -> list[TestResult]`
- 作用：按当前轮预算执行一批 TestCase。普通 pytest 用例会进入 `execute_pytest_case`；`llm_cross_verification` 用例，或 unsupported language / environment error / executor_blocked 触发的 fallback，会进入 ReproAgent 交叉验证分支。

### `_cross_status_to_test_status`（函数，第 654 行）
- 签名：`def _cross_status_to_test_status(status: str) -> str`
- 作用：把 CrossVerificationPayload.status 翻译成 TestResult.status。

### `_run_cross_verification`（函数，第 670 行）
- 签名：`def _run_cross_verification(test_case: TestCase, *, round_index: int, finding_map: dict[str, Finding], code_context: CodeContext | None, project_config: 'ProjectConfig | None') -> TestResult`
- 作用：LLM 交叉验证执行入口。它延迟导入 `run_repro_agent`，找到关联 finding 后调用 ReproAgent，并把 `CrossVerificationPayload` 包装成普通 `TestResult`，这样 Board 后续仍然只消费统一的测试结果对象。

### `_build_cross_result`（函数，第 744 行）
- 签名：`def _build_cross_result(*, test_case: TestCase, round_index: int, payload: CrossVerificationPayload) -> TestResult`
- 作用：把 CrossVerificationPayload 包成 TestResult。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
