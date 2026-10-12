"""多语言原生执行器的离线契约测试。

这些测试不要求开发机安装 Node/Go/Java/Rust/Ruby：命令路由使用受控的 subprocess
替身验证，只有 Python unittest 路径做一次真实执行。这样 CI 可以稳定覆盖所有语言，
同时又能证明至少一条非 pytest 原生链路真的运行了测试代码。
"""

from pathlib import Path
from functools import partial
from types import SimpleNamespace

import pytest

import codecross.native_executor as native
from codecross.board import init_finding_verifications, update_finding_verifications_from_results
from codecross.config import ProjectConfig, RunnerConfig as _RunnerConfig
from codecross.schemas import Finding, LineRange, TestCase


RunnerConfig = partial(_RunnerConfig, execution_mode="trusted_local")


def _case(test_type: str, *, target_file: str, test_name: str, code: str) -> TestCase:
    """构造带 finding 关联的最小原生测试，减少各测试重复样板。"""

    return TestCase(
        source_agent="native-test",
        related_finding_ids=["finding_native"],
        test_type=test_type,  # type: ignore[arg-type]
        target_file=target_file,
        test_name=test_name,
        test_code=code,
        expected_behavior="Native framework executes the generated regression test.",
        target_language=target_file.rsplit(".", 1)[-1],
    )


def test_unittest_adapter_executes_real_test_without_pytest(tmp_path: Path):
    """unittest 适配器应通过 discover 真正运行临时测试并在结束后清理。"""

    case = _case(
        "unittest",
        target_file="pkg/service.py",
        test_name="test_value",
        code=(
            "import unittest\n\n"
            "class ValueTest(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(2 + 2, 4)\n"
        ),
    )
    config = ProjectConfig(
        language="python",
        test_framework="unittest",
        test_command=["python", "-m", "unittest", "discover"],
    )

    result = native.execute_native_test_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        project_config=config,
    )

    assert result.status == "passed"
    assert result.evidence_kind == "native_test"
    assert result.executor_name == "unittest"
    assert not (tmp_path / ".codecross-tmp").exists()


def test_vitest_uses_detector_command_and_shell_false(monkeypatch, tmp_path: Path):
    """Vitest 应消费结构化 detector argv，并把唯一临时文件作为 selector。"""

    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["shell"] = kwargs["shell"]
        test_file = tmp_path / command[-1]
        assert test_file.exists()
        assert test_file.suffix == ".ts"
        assert "expect(add(1, 2)).toBe(3)" in test_file.read_text(encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    monkeypatch.setattr(native, "_executable_exists", lambda command, root: True)
    monkeypatch.setattr(native.subprocess, "run", fake_run)
    case = _case(
        "vitest",
        target_file="src/add.ts",
        test_name="adds numbers",
        code=(
            "import { test, expect } from 'vitest';\n"
            "import { add } from '../src/add';\n"
            "test('adds numbers', () => { expect(add(1, 2)).toBe(3); });\n"
        ),
    )
    config = ProjectConfig(
        language="typescript",
        test_framework="vitest",
        test_command=["pnpm", "exec", "vitest", "run"],
    )

    result = native.execute_native_test_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        project_config=config,
    )

    assert result.status == "passed"
    assert observed["command"][:4] == ["pnpm", "exec", "vitest", "run"]
    assert observed["shell"] is False
    assert not (tmp_path / ".codecross-tmp").exists()


def test_go_failure_reruns_in_target_package_and_cleans_file(monkeypatch, tmp_path: Path):
    """Go 测试必须放进目标 package；连续两次失败才是可复现强证据。"""

    package = tmp_path / "service"
    package.mkdir()
    (package / "handler.go").write_text("package service\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(command)
        generated = list(package.glob("zz_codecross_*_test.go"))
        assert len(generated) == 1
        assert "func TestHandleRequest" in generated[0].read_text(encoding="utf-8")
        return SimpleNamespace(returncode=1, stdout="--- FAIL: TestHandleRequest", stderr="")

    monkeypatch.setattr(native, "_executable_exists", lambda command, root: True)
    monkeypatch.setattr(native.subprocess, "run", fake_run)
    case = _case(
        "go_test",
        target_file="service/handler.go",
        test_name="TestHandleRequest",
        code=(
            "package service\n\n"
            "import \"testing\"\n\n"
            "func TestHandleRequest(t *testing.T) {\n"
            "    if got := 1; got != 2 { t.Errorf(\"got %d\", got) }\n"
            "}\n"
        ),
    )
    config = ProjectConfig(language="go", test_framework="go test", test_command=["go", "test", "./..."])

    result = native.execute_native_test_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        project_config=config,
    )

    assert result.status == "failed"
    assert result.is_reproducible is True
    assert len(commands) == 2
    assert commands[0] == ["go", "test", "./service", "-run", "^TestHandleRequest$"]
    assert list(package.glob("zz_codecross_*_test.go")) == []


@pytest.mark.parametrize(
    ("test_type", "framework", "test_file", "test_name", "expected"),
    [
        ("jest", "jest", ".codecross-tmp/demo.test.js", "works", ["npm", "test", "--", ".codecross-tmp/demo.test.js", "--runInBand"]),
        ("maven", "maven", "src/test/java/DemoTest.java", "testDemo", ["mvn", "-q", "-Dtest=DemoTest", "test"]),
        ("gradle", "gradle", "src/test/java/DemoTest.java", "testDemo", ["gradle", "test", "--tests", "DemoTest"]),
        ("cargo_test", "cargo test", "tests/demo.rs", "works", ["cargo", "test", "--test", "demo"]),
        ("rspec", "rspec", ".codecross-tmp/demo_spec.rb", "works", ["bundle", "exec", "rspec", ".codecross-tmp/demo_spec.rb"]),
    ],
)
def test_command_builders_are_framework_specific(
    tmp_path: Path,
    test_type: str,
    framework: str,
    test_file: str,
    test_name: str,
    expected: list[str],
):
    """五种框架必须生成参数列表，而不是拼成一条 shell 命令。"""

    path = tmp_path / test_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("public class DemoTest {}", encoding="utf-8")
    case = _case(test_type, target_file="src/demo.txt", test_name=test_name, code="public class DemoTest {}")
    default_commands = {
        "jest": ["npm", "test"],
        "maven": ["mvn", "test"],
        "gradle": ["gradle", "test"],
        "cargo_test": ["cargo", "test"],
        "rspec": ["bundle", "exec", "rspec"],
    }
    config = ProjectConfig(test_framework=framework, test_command=default_commands[test_type])

    command = native.build_native_command(
        case,
        test_file=path,
        root=tmp_path,
        project_config=config,
        runner_config=RunnerConfig(),
    )

    assert command == expected


def test_missing_executable_becomes_not_testable_and_board_preserves_it(monkeypatch, tmp_path: Path):
    """开发机没装工具时既不能报 failed，也不能被 Analyzer/Board 当成反证。"""

    monkeypatch.setattr(native, "_executable_exists", lambda command, root: False)
    case = _case(
        "cargo_test",
        target_file="src/lib.rs",
        test_name="checks_value",
        code="#[test]\nfn checks_value() { assert_eq!(1, 1); }\n",
    )
    config = ProjectConfig(language="rust", test_framework="cargo test", test_command=["cargo", "test"])
    result = native.execute_native_test_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        project_config=config,
    )

    finding = Finding(
        finding_id="finding_native",
        source_reviewer="reviewer",
        model="fixture/model",
        severity="high",
        category="correctness",
        file_path="src/lib.rs",
        line_range=LineRange(start=1, end=1),
        symbol="checks_value",
        problem="Value handling is incorrect.",
        evidence="The changed branch returns the wrong value.",
        impact="Callers receive an invalid result.",
        suggestion="Return the validated value.",
    )
    verifications = init_finding_verifications([finding])
    verifications, _, _ = update_finding_verifications_from_results(verifications, {}, [result])

    assert result.status == "skipped"
    assert result.error_type == "not_testable_missing_executable"
    assert verifications["finding_native"].status == "not_testable"


def test_dependency_failure_is_not_reported_as_finding_failure(monkeypatch, tmp_path: Path):
    """模块缺失导致的 exit 1 必须与断言失败分开归因。"""

    monkeypatch.setattr(native, "_executable_exists", lambda command, root: True)
    monkeypatch.setattr(
        native.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="Error: Cannot find module 'vitest'",
        ),
    )
    case = _case(
        "vitest",
        target_file="src/add.ts",
        test_name="adds",
        code="import { test, expect } from 'vitest'; test('adds', () => expect(2).toBe(2));",
    )
    config = ProjectConfig(language="typescript", test_framework="vitest", test_command=["npx", "vitest", "run"])

    result = native.execute_native_test_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        project_config=config,
    )

    assert result.status == "skipped"
    assert result.error_type == "not_testable_build_or_dependency"


def test_native_sanitizer_blocks_process_execution(tmp_path: Path):
    """非 Python 测试也必须在启动构建工具前拦截进程执行能力。"""

    case = _case(
        "jest",
        target_file="src/add.js",
        test_name="unsafe",
        code=(
            "const { exec } = require('child_process');\n"
            "test('unsafe', () => { exec('whoami'); expect(true).toBe(true); });\n"
        ),
    )
    result = native.execute_native_test_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
        project_config=ProjectConfig(test_framework="jest", test_command=["npm", "test"]),
    )

    assert result.status == "unsafe"
    assert result.error_type == "sanitizer_block"
