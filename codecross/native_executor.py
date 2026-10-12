from __future__ import annotations

"""多语言原生测试执行器。

本模块只负责 Jest/Vitest、Go、Java、Rust、Ruby 和 unittest 的确定性执行。
它与 ``executor.py`` 中历史 pytest 执行器分开，原因有三点：

1. 每种工具的临时文件位置和命令行参数不同，集中塞进 pytest 分支会很难审计；
2. 原生测试代码无法复用 Python AST sanitizer，必须使用更保守的跨语言预检；
3. “缺少工具/依赖/无法构建”不是 finding 的反证，必须稳定映射为 not_testable。

安全边界：这里仍然只是本地轻量执行器，不等价于容器沙箱。生产环境执行未知仓库
时仍应放进禁网、限时、限 CPU/内存的容器。本模块保证不使用 shell、不会自动安装
依赖，并且只清理自己创建的唯一临时测试文件。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config import ProjectConfig, RunnerConfig
from .schemas import TestCase, TestResult


NATIVE_TEST_TYPES = frozenset(
    {"unittest", "jest", "vitest", "go_test", "maven", "gradle", "cargo_test", "rspec"}
)


@dataclass(frozen=True, slots=True)
class NativeTestAdapter:
    """描述一种测试框架的稳定执行规则。

    ``placement`` 决定临时文件应放在哪里：JS/Ruby/unittest 可以放 CodeCross 临时
    目录；Go/Rust/Java 的构建工具只会扫描约定目录，因此要短暂写入项目内的测试目录。
    文件名带随机 TestCase ID，且上下文退出时只删除该文件，不触碰用户已有测试。
    """

    test_type: str
    executor_name: str
    suffix: str
    placement: str
    default_command: tuple[str, ...]


ADAPTERS: dict[str, NativeTestAdapter] = {
    "unittest": NativeTestAdapter("unittest", "unittest", ".py", "temp", (sys.executable, "-m", "unittest")),
    "jest": NativeTestAdapter("jest", "jest", ".test.js", "temp", ("npm", "test")),
    "vitest": NativeTestAdapter("vitest", "vitest", ".test.ts", "temp", ("npx", "vitest", "run")),
    "go_test": NativeTestAdapter("go_test", "go test", "_test.go", "target_package", ("go", "test", "./...")),
    "maven": NativeTestAdapter("maven", "Maven", ".java", "java_test", ("mvn", "test")),
    "gradle": NativeTestAdapter("gradle", "Gradle", ".java", "java_test", ("gradle", "test")),
    "cargo_test": NativeTestAdapter("cargo_test", "cargo test", ".rs", "rust_tests", ("cargo", "test")),
    "rspec": NativeTestAdapter("rspec", "RSpec", "_spec.rb", "temp", ("bundle", "exec", "rspec")),
}


def native_test_type_for_project(config: ProjectConfig) -> str | None:
    """把 ProjectDetector 的 framework 映射为 TestCase.test_type。

    该函数同时供 Test Case Agent Prompt 和 executor 使用，避免“生成器认为是 Jest、
    执行器却按 package-script”这种两套映射逐渐漂移。
    """

    framework = config.test_framework.strip().lower()
    mapping = {
        "unittest": "unittest",
        "jest": "jest",
        "vitest": "vitest",
        "go test": "go_test",
        "maven": "maven",
        "gradle": "gradle",
        "cargo test": "cargo_test",
        "rspec": "rspec",
    }
    return mapping.get(framework)


# 原生语言没有统一 AST，因此这里采用“高风险能力黑名单”。规则只覆盖执行命令、
# 网络和写/删文件等 CodeCross 测试不需要的能力；普通只读 API 不应被误杀。
_DANGEROUS_CODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shell_process", re.compile(r"\b(child_process|processbuilder|runtime\.getruntime|std::process::command|os/exec)\b", re.I)),
    ("network", re.compile(r"\b(fetch\s*\(|axios\.|http\.get\s*\(|net\.dial\s*\(|reqwest::|tcpstream|socket\.)", re.I)),
    ("file_write", re.compile(r"\b(writefile(?:sync)?|appendfile(?:sync)?|os\.writefile|os\.remove|file\.delete|remove_file|remove_dir_all)\b", re.I)),
    ("dynamic_eval", re.compile(r"\b(eval\s*\(|exec\s*\(|class\.forname\s*\()", re.I)),
)


def is_safe_native_test_code(test_case: TestCase, runner_config: RunnerConfig) -> tuple[bool, str]:
    """对非 Python 测试做保守预检，返回 ``(可执行, 原因)``。"""

    if test_case.safety_level == "unsafe":
        return False, "test_case_safety_level_is_unsafe"
    if test_case.requires_external_service and not runner_config.allow_network:
        return False, "external_service_required_but_network_disabled"
    for name, pattern in _DANGEROUS_CODE_PATTERNS:
        if pattern.search(test_case.test_code):
            return False, f"blocked_native_capability:{name}"
    return True, ""


_VALUE_PATTERNS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "unittest": (re.compile(r"\bdef\s+test_"), re.compile(r"\bassert|\.assert\w+\s*\(")),
    "jest": (re.compile(r"\b(?:test|it)\s*\("), re.compile(r"\bexpect\s*\(|\bassert\.")),
    "vitest": (re.compile(r"\b(?:test|it)\s*\("), re.compile(r"\bexpect\s*\(|\bassert\.")),
    "go_test": (re.compile(r"\bfunc\s+Test\w+\s*\("), re.compile(r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(")),
    "maven": (re.compile(r"@Test\b"), re.compile(r"\bassert\w*\s*\(")),
    "gradle": (re.compile(r"@Test\b"), re.compile(r"\bassert\w*\s*\(")),
    "cargo_test": (re.compile(r"#\s*\[\s*test\s*\]"), re.compile(r"\bassert(?:_eq|_ne)?!\s*\(")),
    "rspec": (re.compile(r"\b(?:it|specify)\s+[\"']"), re.compile(r"\bexpect\s*\(|\bassert\b")),
}


def low_value_native_test_reason(test_case: TestCase) -> str:
    """拒绝没有测试入口或断言的占位代码，防止“空测试通过”制造假反证。"""

    patterns = _VALUE_PATTERNS.get(test_case.test_type)
    if patterns is None:
        return "unsupported_native_test_type"
    test_pattern, assertion_pattern = patterns
    if not test_pattern.search(test_case.test_code):
        return "no_native_test_entrypoint"
    if not assertion_pattern.search(test_case.test_code):
        return "no_meaningful_native_assertion"
    return ""


def _safe_target_directory(root: Path, target_file: str) -> Path:
    """返回 target 所在目录，并保证最终路径仍在项目根目录中。"""

    candidate = (root / target_file).resolve().parent
    candidate.relative_to(root)
    if not candidate.exists():
        raise FileNotFoundError(f"目标包目录不存在: {candidate}")
    return candidate


def _java_class_name(test_case: TestCase) -> str:
    """提取 Java 顶层类名；public class 的文件名必须与类名相同。"""

    match = re.search(r"\b(?:public\s+)?class\s+([A-Za-z_$][\w$]*)", test_case.test_code)
    if match:
        return match.group(1)
    # 模型漏写 class 时后续构建必然失败，仍给一个稳定名字用于可解释错误输出。
    return f"CodeCross{re.sub(r'[^A-Za-z0-9]', '', test_case.test_case_id)}Test"


@contextmanager
def prepared_native_test_file(
    test_case: TestCase,
    *,
    root: Path,
    adapter: NativeTestAdapter,
) -> Iterator[Path]:
    """创建唯一测试文件并在退出时清理，仅删除本函数创建的路径。"""

    temp_parent = root / ".codecross-tmp"
    created_parent: Path | None = None
    test_file: Path | None = None
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    try:
        if adapter.placement == "temp":
            temp_parent.mkdir(exist_ok=True)
            temporary_directory = tempfile.TemporaryDirectory(
                prefix=f"native-{adapter.test_type}-", dir=temp_parent
            )
            test_file = Path(temporary_directory.name) / f"codecross_{test_case.test_case_id}{adapter.suffix}"
        elif adapter.placement == "target_package":
            target_dir = _safe_target_directory(root, test_case.target_file)
            test_file = target_dir / f"zz_codecross_{test_case.test_case_id}{adapter.suffix}"
        elif adapter.placement == "rust_tests":
            target_dir = root / "tests"
            if not target_dir.exists():
                target_dir.mkdir()
                created_parent = target_dir
            test_file = target_dir / f"codecross_{test_case.test_case_id}.rs"
        elif adapter.placement == "java_test":
            target_dir = root / "src" / "test" / "java"
            if not target_dir.exists():
                target_dir.mkdir(parents=True)
                created_parent = target_dir
            test_file = target_dir / f"{_java_class_name(test_case)}.java"
        else:  # pragma: no cover - ADAPTERS 是本模块内常量，防御未来错误扩展。
            raise ValueError(f"未知原生测试放置策略: {adapter.placement}")

        # 不覆盖用户文件。随机 ID 理论上不会冲突，但安全边界不能依赖概率。
        if test_file.exists():
            raise FileExistsError(f"临时测试文件已存在，拒绝覆盖: {test_file}")
        test_file.write_text(test_case.test_code, encoding="utf-8")
        yield test_file
    finally:
        if test_file is not None and test_file.exists():
            test_file.unlink()
        if temporary_directory is not None:
            temporary_directory.cleanup()
        if temp_parent.exists() and not any(temp_parent.iterdir()):
            temp_parent.rmdir()
        # 只删除本次创建且仍为空的最深目录；不递归删除 src/test 等用户目录。
        if created_parent is not None and created_parent.exists() and not any(created_parent.iterdir()):
            created_parent.rmdir()


def _base_command(
    adapter: NativeTestAdapter,
    *,
    project_config: ProjectConfig,
    runner_config: RunnerConfig,
) -> list[str]:
    """按“用户覆盖 > detector > adapter 默认值”选择结构化 argv。"""

    if runner_config.test_command_override:
        return list(runner_config.test_command_override)
    if project_config.test_command:
        return list(project_config.test_command)
    return list(adapter.default_command)


def _relative_argument(path: Path, root: Path) -> str:
    """命令行中使用 POSIX 风格相对路径，Node/Go/Cargo 在 Windows 也能识别。"""

    return path.resolve().relative_to(root).as_posix()


def build_native_command(
    test_case: TestCase,
    *,
    test_file: Path,
    root: Path,
    project_config: ProjectConfig,
    runner_config: RunnerConfig,
) -> list[str]:
    """为单个临时测试构造 argv；绝不生成 shell 字符串。"""

    adapter = ADAPTERS[test_case.test_type]
    command = _base_command(adapter, project_config=project_config, runner_config=runner_config)
    relative_file = _relative_argument(test_file, root)
    class_name = _java_class_name(test_case)

    # 高级用户可以在 override 中放占位符，精确控制参数位置；没有占位符时使用
    # 各框架的安全默认 selector，保证只运行这次生成的测试，而不是整个未知仓库。
    placeholders = {"{test_file}": relative_file, "{test_name}": test_case.test_name, "{class_name}": class_name}
    if any(token in placeholders for token in command):
        return [placeholders.get(token, token) for token in command]

    if test_case.test_type == "unittest":
        # unittest 的 discover 模式可以直接扫描任意临时目录；若把带连字符的
        # `.codecross-tmp` 路径当模块名传入，Python 会因非法包名而误报导入失败。
        relative_parent = test_file.parent.resolve().relative_to(root).as_posix()
        return [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            relative_parent,
            "-p",
            test_file.name,
        ]
    if test_case.test_type in {"jest", "vitest"}:
        if command[:2] in (["npm", "test"], ["pnpm", "test"], ["yarn", "test"]):
            command.append("--")
        command.append(relative_file)
        if test_case.test_type == "jest" and "--runInBand" not in command:
            command.append("--runInBand")
        return command
    if test_case.test_type == "go_test":
        package_dir = test_file.parent.resolve().relative_to(root).as_posix()
        package_arg = "." if package_dir == "." else f"./{package_dir}"
        executable = command[0] if command else "go"
        return [executable, "test", package_arg, "-run", f"^{test_case.test_name}$"]
    if test_case.test_type == "maven":
        executable = command[0] if command else "mvn"
        return [executable, "-q", f"-Dtest={class_name}", "test"]
    if test_case.test_type == "gradle":
        executable = command[0] if command else "gradle"
        return [executable, "test", "--tests", class_name]
    if test_case.test_type == "cargo_test":
        executable = command[0] if command else "cargo"
        return [executable, "test", "--test", test_file.stem]
    if test_case.test_type == "rspec":
        return [*command, relative_file]
    raise ValueError(f"不支持的原生 test_type: {test_case.test_type}")


def _executable_exists(command: list[str], root: Path) -> bool:
    """同时支持 PATH 工具和仓库内 ./mvnw、./gradlew wrapper。"""

    if not command:
        return False
    executable = command[0]
    if executable.startswith(("./", ".\\")) or "/" in executable or "\\" in executable:
        return (root / executable).resolve().exists()
    return shutil.which(executable) is not None


_ENVIRONMENT_ERROR = re.compile(
    r"(cannot find module|module not found|no module named|could not resolve dependencies|"
    r"failed to resolve|command not found|could not find .* in registry|unable to find package|"
    r"cannot find symbol|package .* does not exist|could not compile|build failed)",
    re.I,
)


def _summarize_native_output(stdout: str, stderr: str) -> str:
    """保留末尾关键日志，限制长度避免报告和 LangSmith trace 失控。"""

    lines = [line.rstrip() for line in f"{stdout}\n{stderr}".splitlines() if line.strip()]
    return "\n".join(lines[-16:])[-3000:]


def _result(
    test_case: TestCase,
    *,
    round_index: int,
    adapter: NativeTestAdapter,
    status: str,
    command: list[str] | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    duration_ms: int = 0,
    failure_summary: str = "",
    error_type: str = "",
    reproducible: bool = False,
) -> TestResult:
    """集中构造原生 TestResult，防止分支漏写证据来源。"""

    return TestResult(
        test_case_id=test_case.test_case_id,
        round_index=round_index,
        status=status,  # type: ignore[arg-type]
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        failure_summary=failure_summary,
        error_type=error_type,
        is_reproducible=reproducible,
        related_finding_ids=test_case.related_finding_ids,
        evidence_kind="native_test",
        executor_name=adapter.executor_name,
        executor_command=command or [],
    )


def run_native_test_once(
    test_case: TestCase,
    *,
    project_root: str | Path,
    round_index: int,
    runner_config: RunnerConfig,
    project_config: ProjectConfig,
) -> TestResult:
    """执行一次原生测试；环境问题统一返回结构化 not_testable skipped。"""

    root = Path(project_root).resolve()
    adapter = ADAPTERS[test_case.test_type]
    start = time.perf_counter()
    try:
        with prepared_native_test_file(test_case, root=root, adapter=adapter) as test_file:
            command = build_native_command(
                test_case,
                test_file=test_file,
                root=root,
                project_config=project_config,
                runner_config=runner_config,
            )
            if not _executable_exists(command, root):
                return _result(
                    test_case,
                    round_index=round_index,
                    adapter=adapter,
                    status="skipped",
                    command=command,
                    failure_summary=f"测试工具不可用: {command[0] if command else '<empty>'}",
                    error_type="not_testable_missing_executable",
                )
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=test_case.timeout_seconds or runner_config.test_timeout_seconds,
                env=os.environ.copy(),
                shell=False,
            )
    except subprocess.TimeoutExpired as exc:
        return _result(
            test_case,
            round_index=round_index,
            adapter=adapter,
            status="timeout",
            command=locals().get("command", []),
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            duration_ms=int((time.perf_counter() - start) * 1000),
            failure_summary=f"{adapter.executor_name} execution timed out",
            error_type="timeout",
        )
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        return _result(
            test_case,
            round_index=round_index,
            adapter=adapter,
            status="skipped",
            duration_ms=int((time.perf_counter() - start) * 1000),
            failure_summary=str(exc),
            error_type="not_testable_project_layout",
        )
    except OSError as exc:
        # wrapper 存在但不可执行、Windows 文件关联异常等都属于环境不可用。
        return _result(
            test_case,
            round_index=round_index,
            adapter=adapter,
            status="skipped",
            duration_ms=int((time.perf_counter() - start) * 1000),
            failure_summary=str(exc),
            error_type="not_testable_process_start",
        )

    duration_ms = int((time.perf_counter() - start) * 1000)
    combined = f"{completed.stdout}\n{completed.stderr}"
    if completed.returncode == 0:
        status, error_type = "passed", ""
    elif _ENVIRONMENT_ERROR.search(combined):
        # 编译/依赖/构建失败只说明当前环境无法验证，不能支持或反驳 finding。
        status, error_type = "skipped", "not_testable_build_or_dependency"
    elif completed.returncode == 1:
        status, error_type = "failed", f"{test_case.test_type}_failure"
    else:
        status, error_type = "error", f"{test_case.test_type}_exit_{completed.returncode}"
    return _result(
        test_case,
        round_index=round_index,
        adapter=adapter,
        status=status,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        duration_ms=duration_ms,
        failure_summary=_summarize_native_output(completed.stdout, completed.stderr),
        error_type=error_type,
        reproducible=status == "failed",
    )


def execute_native_test_case(
    test_case: TestCase,
    *,
    project_root: str | Path,
    round_index: int,
    runner_config: RunnerConfig,
    project_config: ProjectConfig,
) -> TestResult:
    """执行带安全/价值检查和失败重跑的单个原生测试。"""

    adapter = ADAPTERS.get(test_case.test_type)
    if adapter is None:
        # 该分支只用于直接调用本函数的防御；正常路由会在 executor.py 先过滤。
        return TestResult(
            test_case_id=test_case.test_case_id,
            round_index=round_index,
            status="skipped",
            failure_summary=f"不支持的原生 test_type: {test_case.test_type}",
            error_type="not_testable_unsupported_native_type",
            related_finding_ids=test_case.related_finding_ids,
            evidence_kind="native_test",
            executor_name="unknown",
        )
    safe, reason = is_safe_native_test_code(test_case, runner_config)
    if not safe:
        return _result(
            test_case,
            round_index=round_index,
            adapter=adapter,
            status="unsafe",
            failure_summary=reason,
            error_type="sanitizer_block",
        )
    low_value_reason = low_value_native_test_reason(test_case)
    if low_value_reason:
        return _result(
            test_case,
            round_index=round_index,
            adapter=adapter,
            status="skipped",
            failure_summary=low_value_reason,
            error_type="low_value_test",
        )

    first = run_native_test_once(
        test_case,
        project_root=project_root,
        round_index=round_index,
        runner_config=runner_config,
        project_config=project_config,
    )
    if first.status != "failed":
        return first
    second = run_native_test_once(
        test_case,
        project_root=project_root,
        round_index=round_index,
        runner_config=runner_config,
        project_config=project_config,
    )
    if second.status != "failed":
        rerun_status = second.status
        second.status = "error"
        second.error_type = "flaky_result"
        second.is_reproducible = False
        second.failure_summary = f"原生测试重跑结果不一致: failed -> {rerun_status}"
        return second
    second.is_reproducible = True
    second.duration_ms += first.duration_ms
    second.stdout = f"=== first run ===\n{first.stdout}\n=== rerun ===\n{second.stdout}"
    second.stderr = f"=== first run ===\n{first.stderr}\n=== rerun ===\n{second.stderr}"
    second.failure_summary = "原生测试连续两次失败，已确认可复现。\n" + second.failure_summary
    return second
