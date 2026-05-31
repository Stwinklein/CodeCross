from __future__ import annotations

"""确定性测试执行器。

这个模块不调用 LLM，也不判断最终风险。它只负责把 TestCase 写成临时 pytest 文件，
真实执行 pytest，然后把 stdout/stderr/exit code 收集成 TestResult。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import ast
from pathlib import Path

from .config import RunnerConfig
from .schemas import TestCase, TestResult


# AST sanitizer 是 V1.1 的“轻量沙箱”：它不是 Docker 那样的系统隔离，
# 而是在运行 pytest 之前先审查生成测试的语法树。这样能拦住大多数明显危险行为，
# 同时保持 executor 仍然简单、确定、适合本地 V1 使用。
BLOCKED_IMPORT_ROOTS = {
    "os",
    "subprocess",
    "socket",
    "requests",
    "urllib",
    "http",
    "shutil",
}

# 这些内置函数会打开动态执行/动态导入能力。模型生成的测试不需要它们；
# 一旦允许，就很容易绕过静态检查，例如 __import__("os").system(...)。
BLOCKED_BUILTIN_CALLS = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "vars",
}

# 对象方法级别的文件系统写入/删除能力。这里不禁止 Path 本身，
# 因为后续更强的测试生成器可能需要读取 fixture；但写/删必须拦住。
BLOCKED_MUTATING_METHODS = {
    "mkdir",
    "remove",
    "rename",
    "replace",
    "rmdir",
    "rmtree",
    "unlink",
    "write_bytes",
    "write_text",
}

# AST 是主防线，文本标记只是兜底。这里故意不再放 "open(" 这种容易误杀的片段；
# 例如字符串字面量里出现 "open(" 不应该被当作危险代码。
SECONDARY_BLOCKED_TEXT_MARKERS = [
    "shell=true",
    "powershell",
    "cmd.exe",
    "bash -c",
]

# 这些短语通常意味着模型没有生成真正的验证代码，而是在写“占位测试”。
# 如果让这类测试进入 pytest，它们往往会因为 `assert True` 通过，从而把 finding
# 错误地标成 refuted。这里把它们降级为 skipped，让 Board 保持 inconclusive。
LOW_VALUE_TEST_MARKERS = (
    "placeholder",
    "replace with actual",
    "actual implementation would",
    "cannot import the actual",
    "we assume",
    "this test documents",
)


def _string_literal(node: ast.AST | None) -> str | None:
    """如果 AST 节点是字符串字面量则返回文本，否则返回 None。"""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str:
    """把 Call.func 渲染成便于匹配的名字。

    例子：
    - eval(...)          -> "eval"
    - os.system(...)     -> "os.system"
    - Path(...).unlink() -> "unlink" 无法拿到完整根对象，但方法名仍可拦截
    """

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_constant_true(node: ast.AST) -> bool:
    """判断一个 assert 表达式是否只是 `assert True` 这种无效证明。"""

    return isinstance(node, ast.Constant) and node.value is True


def _call_is_pytest_raises(node: ast.AST) -> bool:
    """识别 `pytest.raises(...)`，它也是一种有效断言形式。"""

    return _call_name(node).endswith("pytest.raises")


class TestValueVisitor(ast.NodeVisitor):
    """检查生成测试是否真的包含可用断言。

    这个 visitor 和安全 sanitizer 分开：安全检查回答“能不能跑”，价值检查回答
    “跑过以后能不能作为证据”。一个安全但只有 `assert True` 的测试不危险，但它
    对 finding 没有证明力，所以不能让 Board 用它反驳 reviewer。
    """

    def __init__(self) -> None:
        self.test_function_count = 0
        self.meaningful_assert_count = 0
        self.trivial_assert_count = 0
        self.pytest_raises_count = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name.startswith("test_"):
            self.test_function_count += 1
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name.startswith("test_"):
            self.test_function_count += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        if _is_constant_true(node.test):
            self.trivial_assert_count += 1
        else:
            self.meaningful_assert_count += 1
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if isinstance(item.context_expr, ast.Call) and _call_is_pytest_raises(item.context_expr):
                self.pytest_raises_count += 1
        self.generic_visit(node)


def low_value_test_reason(test_case: TestCase) -> str:
    """返回测试缺乏证据价值的原因；空字符串表示可以执行。

    这里故意不要求测试一定 import 目标模块，因为有些非 Python 项目可以通过读取
    源文件做静态验证。但至少要有真实断言、`pytest.raises`，且不能出现明显的
    placeholder 文案。
    """

    try:
        tree = ast.parse(test_case.test_code)
    except SyntaxError:
        # 语法错误交给安全检查处理，那里会返回 unsafe。
        return ""

    visitor = TestValueVisitor()
    visitor.visit(tree)
    lowered = test_case.test_code.lower()
    if visitor.test_function_count == 0:
        return "no_pytest_test_function"
    if visitor.meaningful_assert_count == 0 and visitor.pytest_raises_count == 0:
        for marker in LOW_VALUE_TEST_MARKERS:
            if marker in lowered:
                return f"low_value_test_marker:{marker}"
        if visitor.trivial_assert_count:
            return "only_trivial_assert_true"
        return "no_meaningful_assertion"
    return ""


def _contains_blocked_command_marker(text: str, command: str) -> bool:
    """用边界匹配命令黑名单，避免 `rm ` 误伤 `confirm `。

    早期实现直接做 substring 检查，WebAPI 的静态检查注释里出现 "confirm the"
    就会命中 `rm `。这里把命令当成独立 token 匹配，仍能拦截 `rm path`、
    `bash -c`，但不会误杀普通英文单词。
    """

    marker = command.strip().lower()
    if not marker:
        return False
    escaped = re.escape(marker)
    if re.search(r"^[a-z0-9_.-]+$", marker):
        return re.search(rf"(?<![\w.-]){escaped}(?![\w.-])", text) is not None
    return marker in text


def _open_call_writes(call: ast.Call) -> bool:
    """判断 open(...) 是否会写文件。

    Python 的 open 默认是只读，这里允许只读，主要拦截 w/a/x/+ 这些会写入或可写的模式。
    这比旧版直接按字符串拦截 open( 更少误杀，也更符合“读项目上下文、写临时测试”的长期方向。
    """

    mode_node: ast.AST | None = call.args[1] if len(call.args) >= 2 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
            break
    mode = _string_literal(mode_node)
    return bool(mode and any(flag in mode for flag in ("w", "a", "x", "+")))


class TestCodeSafetyVisitor(ast.NodeVisitor):
    """遍历测试代码语法树，收集不允许执行的危险行为。

    这个 visitor 只负责“发现风险”，不负责执行和业务判断。这样 executor 的安全策略
    可以单独测试，也方便以后把本地 executor 换成 Docker executor 时复用同一套预检。
    """

    def __init__(self) -> None:
        self.reasons: list[str] = []

    def _block(self, reason: str) -> None:
        """记录拦截原因，同一个原因只保留一次，避免错误信息刷屏。"""

        if reason not in self.reasons:
            self.reasons.append(reason)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in BLOCKED_IMPORT_ROOTS:
                self._block(f"blocked_import:{root}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root in BLOCKED_IMPORT_ROOTS:
            self._block(f"blocked_import:{root}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func)
        leaf_name = name.rsplit(".", 1)[-1] if name else ""

        if leaf_name in BLOCKED_BUILTIN_CALLS:
            self._block(f"blocked_call:{leaf_name}")
        if name in {"os.system", "os.popen"} or name.startswith("subprocess."):
            self._block(f"blocked_call:{name}")
        if name.startswith(("socket.", "requests.", "urllib.", "http.")):
            self._block(f"blocked_network_call:{name}")
        if leaf_name in BLOCKED_MUTATING_METHODS:
            self._block(f"blocked_mutating_method:{leaf_name}")
        if leaf_name == "open" and _open_call_writes(node):
            self._block("blocked_file_write:open")

        self.generic_visit(node)


def is_safe_test_code(test_case: TestCase, runner_config: RunnerConfig) -> tuple[bool, str]:
    """对生成测试做轻量安全过滤。

    返回 `(safe, reason)`，调用方据此决定是否进入 pytest。注意这里是“执行前安检”，
    它不能替代 Docker/容器隔离；但能显著降低 V1 本地执行模型生成代码的风险。
    """

    if test_case.safety_level == "unsafe":
        return False, "test_case_safety_level_is_unsafe"
    if test_case.requires_external_service and not runner_config.allow_network:
        return False, "external_service_required_but_network_disabled"

    try:
        tree = ast.parse(test_case.test_code)
    except SyntaxError as exc:
        # 语法无法解析就不能安全检查。这里选择拒绝执行，而不是把代码交给 pytest 报错；
        # 对安全边界来说，“无法检查”应该按不安全处理。
        return False, f"syntax_error_uninspectable:{exc.msg}"

    visitor = TestCodeSafetyVisitor()
    visitor.visit(tree)
    if visitor.reasons:
        return False, ";".join(visitor.reasons)

    lowered = test_case.test_code.lower()
    for blocked in SECONDARY_BLOCKED_TEXT_MARKERS:
        if blocked.lower() in lowered:
            return False, f"blocked_text_marker:{blocked}"
    for command in runner_config.blocked_commands:
        if _contains_blocked_command_marker(lowered, command):
            return False, f"blocked_command:{command}"
    return True, ""


def _summarize_failure(stdout: str, stderr: str) -> str:
    """从 pytest 输出里提取最关键的失败行，避免报告塞入过长日志。"""

    text = (stdout + "\n" + stderr).strip()
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    interesting = [
        line
        for line in lines
        if "FAILED" in line
        or "ERROR" in line
        or "AssertionError" in line
        or "Exception" in line
        or "Traceback" in line
        or "E   " in line
    ]
    selected = interesting[-12:] if interesting else lines[-12:]
    return "\n".join(selected)[-2000:]


def _unsafe_result(test_case: TestCase, round_index: int, reason: str) -> TestResult:
    """把被 sanitizer 拦截的测试转换成结构化结果。"""

    return TestResult(
        test_case_id=test_case.test_case_id,
        round_index=round_index,
        status="unsafe",
        failure_summary=reason,
        error_type="sanitizer_block",
        related_finding_ids=test_case.related_finding_ids,
    )


def _skipped_result(
    test_case: TestCase,
    round_index: int,
    reason: str,
    *,
    error_type: str = "skipped",
) -> TestResult:
    """把“不能形成有效证据”的测试显式写成 skipped 结果。

    skipped 不会支持或反驳 finding，只会让 Board 进入 inconclusive 路径。这样比
    静默丢弃测试更容易排查，也避免低价值测试通过后产生假阴性。
    """

    return TestResult(
        test_case_id=test_case.test_case_id,
        round_index=round_index,
        status="skipped",
        failure_summary=reason,
        error_type=error_type,
        related_finding_ids=test_case.related_finding_ids,
    )


def _merge_rerun_output(first: TestResult, second: TestResult) -> tuple[str, str, int]:
    """合并两次 pytest 的输出，方便后续人工排查 flaky 或复现失败。"""

    stdout = (
        "=== first run stdout ===\n"
        f"{first.stdout}\n"
        "=== rerun stdout ===\n"
        f"{second.stdout}"
    )
    stderr = (
        "=== first run stderr ===\n"
        f"{first.stderr}\n"
        "=== rerun stderr ===\n"
        f"{second.stderr}"
    )
    return stdout, stderr, first.duration_ms + second.duration_ms


def _flaky_result(test_case: TestCase, round_index: int, first: TestResult, second: TestResult) -> TestResult:
    """把前后两次不一致的 pytest 结果表达成现有 schema 能理解的 error。

    这里不新增 `status="flaky"`，是为了避免 V1.1 牵动 schema、Board、报告和前端。
    用 `status="error" + error_type="flaky_result"` 足够表达“不应作为强证据”。
    """

    stdout, stderr, duration_ms = _merge_rerun_output(first, second)
    return TestResult(
        test_case_id=test_case.test_case_id,
        round_index=round_index,
        status="error",
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        failure_summary=(
            "Test result was inconsistent across reruns: "
            f"{first.status} -> {second.status}."
        ),
        error_type="flaky_result",
        is_reproducible=False,
        related_finding_ids=test_case.related_finding_ids,
    )


def _run_pytest_once(
    test_case: TestCase,
    *,
    root: Path,
    round_index: int,
    runner_config: RunnerConfig,
) -> TestResult:
    """实际执行一次 pytest，并把结果标准化为 TestResult。

    这层函数故意不做 sanitizer：安全检查只在 execute_pytest_case 入口做一次。
    重跑时复用同一份已通过安检的 test_code，避免重复逻辑分散。
    """

    temp_parent = root / ".codecross-tmp"
    temp_parent.mkdir(exist_ok=True)
    start = time.perf_counter()

    try:
        # 临时测试文件放在项目根目录下，PYTHONPATH 指向项目根目录，
        # 这样生成测试可以 import 当前项目模块，但不会污染真实 tests/。
        with tempfile.TemporaryDirectory(prefix="pytest-", dir=temp_parent) as temp_dir:
            test_file = Path(temp_dir) / f"test_{test_case.test_case_id}.py"
            test_file.write_text(test_case.test_code, encoding="utf-8")

            env = os.environ.copy()
            current_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(root)
                if not current_pythonpath
                else str(root) + os.pathsep + current_pythonpath
            )
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_file), "-q"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=test_case.timeout_seconds or runner_config.test_timeout_seconds,
                env=env,
            )
    except subprocess.TimeoutExpired as exc:
        # 超时也要返回 TestResult，让 Board 能把它当作 executor_error 证据。
        duration_ms = int((time.perf_counter() - start) * 1000)
        return TestResult(
            test_case_id=test_case.test_case_id,
            round_index=round_index,
            status="timeout",
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_ms=duration_ms,
            failure_summary="pytest execution timed out",
            error_type="timeout",
            related_finding_ids=test_case.related_finding_ids,
        )
    finally:
        # TemporaryDirectory 会清理子目录；这里顺手删除空的父目录。
        if temp_parent.exists() and not any(temp_parent.iterdir()):
            shutil.rmtree(temp_parent, ignore_errors=True)

    duration_ms = int((time.perf_counter() - start) * 1000)
    if completed.returncode == 0:
        status = "passed"
        error_type = ""
    elif completed.returncode == 1:
        status = "failed"
        error_type = "pytest_failure"
    else:
        status = "error"
        error_type = f"pytest_exit_{completed.returncode}"

    return TestResult(
        test_case_id=test_case.test_case_id,
        round_index=round_index,
        status=status,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=duration_ms,
        failure_summary=_summarize_failure(completed.stdout, completed.stderr),
        error_type=error_type,
        is_reproducible=status == "failed",
        related_finding_ids=test_case.related_finding_ids,
    )


def execute_pytest_case(
    test_case: TestCase,
    *,
    project_root: str | Path,
    round_index: int,
    runner_config: RunnerConfig,
) -> TestResult:
    """执行单个 pytest 测试用例。

    V1.1 的关键变化：failed/error/timeout 会重跑一次。
    - 两次都 failed：说明可复现，Board 才能把它作为强证据。
    - 两次结果不一致：标记为 flaky_result，不支持也不反驳 finding。
    - error/timeout 即使重复出现，也仍然是 executor 问题，不直接证明业务缺陷。
    """

    safe, reason = is_safe_test_code(test_case, runner_config)
    if not safe:
        return _unsafe_result(test_case, round_index, reason)

    low_value_reason = low_value_test_reason(test_case)
    if low_value_reason:
        return _skipped_result(
            test_case,
            round_index,
            low_value_reason,
            error_type="low_value_test",
        )

    root = Path(project_root).resolve()
    first = _run_pytest_once(
        test_case,
        root=root,
        round_index=round_index,
        runner_config=runner_config,
    )
    if first.status not in {"failed", "error", "timeout"}:
        return first

    second = _run_pytest_once(
        test_case,
        root=root,
        round_index=round_index,
        runner_config=runner_config,
    )
    if first.status != second.status:
        return _flaky_result(test_case, round_index, first, second)

    stdout, stderr, duration_ms = _merge_rerun_output(first, second)
    second.stdout = stdout
    second.stderr = stderr
    second.duration_ms = duration_ms
    if second.status == "failed":
        second.is_reproducible = True
        second.failure_summary = "Reproduced on rerun.\n" + second.failure_summary
    else:
        second.is_reproducible = False
        second.failure_summary = (
            f"Executor status repeated on rerun: {second.status}.\n"
            + second.failure_summary
        )
    return second


def execute_test_cases(
    test_cases: list[TestCase],
    *,
    project_root: str | Path,
    round_index: int,
    runner_config: RunnerConfig,
) -> list[TestResult]:
    """按当前轮预算执行一批 TestCase。"""

    results: list[TestResult] = []
    # select_next_verification_targets 已经限制了“本轮最多验证多少 finding”。
    # Test Author 可能会再加正常路径对照用例，所以 executor 给实际测试数留出 2 倍余量；
    # 超出余量的测试会显式标记 skipped，而不是静默丢失。
    execution_limit = max(
        runner_config.max_tests_per_round,
        runner_config.max_tests_per_round * 2,
    )
    for test_case in test_cases[:execution_limit]:
        # static_check 的 test_code 仍然是 Python/pytest 代码，只是语义上表示
        # “读源码做静态断言”。WebAPI 这类 JS 项目短期就靠它提供可执行证据。
        if test_case.test_type not in {"pytest", "static_check"}:
            results.append(
                _skipped_result(
                    test_case,
                    round_index,
                    f"Unsupported test_type: {test_case.test_type}",
                    error_type="unsupported_test_type",
                )
            )
            continue
        results.append(
            execute_pytest_case(
                test_case,
                project_root=project_root,
                round_index=round_index,
                runner_config=runner_config,
            )
        )
    for test_case in test_cases[execution_limit:]:
        results.append(
            _skipped_result(
                test_case,
                round_index,
                f"Skipped because generated test count exceeded execution limit {execution_limit}.",
                error_type="max_tests_per_round_exceeded",
            )
        )
    return results
