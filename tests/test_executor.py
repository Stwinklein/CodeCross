"""Executor 测试。

重点验证两件事：
1. 生成的 pytest 真的会被执行；
2. 不安全的测试代码会被 sanitizer 拦截。
"""

from pathlib import Path

import codecross.executor as executor
from codecross.config import RunnerConfig
from codecross.executor import execute_test_cases
from codecross.schemas import TestCase, TestResult


def test_executor_runs_pytest_and_reports_failure(tmp_path: Path):
    """真实执行一个会触发 ZeroDivisionError 的 pytest。"""

    # tmp_path 是 pytest 提供的临时目录，用来模拟一个最小 Python 项目。
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "math_utils.py").write_text(
        "def safe_divide(a, b):\n    return a / b\n",
        encoding="utf-8",
    )

    case = TestCase(
        source_agent="test",
        related_finding_ids=["finding_1"],
        target_file="pkg/math_utils.py",
        target_symbol="safe_divide",
        test_name="test_safe_divide_zero",
        test_code=(
            "from pkg.math_utils import safe_divide\n\n\n"
            "def test_safe_divide_zero():\n"
            "    assert safe_divide(10, 0) is None\n"
        ),
        expected_behavior="Zero denominator is handled.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert len(results) == 1
    assert results[0].status == "failed"
    assert results[0].is_reproducible is True
    assert results[0].related_finding_ids == ["finding_1"]
    assert "ZeroDivisionError" in results[0].failure_summary or "failed" in results[0].stdout.lower()


def test_executor_blocks_unsafe_test_code(tmp_path: Path):
    """包含 subprocess 的模型生成测试应该被拦截。"""

    case = TestCase(
        source_agent="test",
        related_finding_ids=["finding_1"],
        target_file="pkg/math_utils.py",
        test_name="test_unsafe",
        test_code="import subprocess\n\ndef test_unsafe():\n    assert True\n",
        expected_behavior="Unsafe imports are blocked.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "unsafe"
    assert results[0].error_type == "sanitizer_block"


def test_executor_blocks_dynamic_import_escape(tmp_path: Path):
    """AST sanitizer 应拦住 __import__('os').system(...) 这种字符串黑名单绕过。"""

    case = TestCase(
        source_agent="test",
        related_finding_ids=["finding_1"],
        target_file="pkg/math_utils.py",
        test_name="test_dynamic_import_escape",
        test_code='def test_escape():\n    __import__("os").system("echo unsafe")\n',
        expected_behavior="Dynamic imports are blocked.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "unsafe"
    assert "blocked_call:__import__" in results[0].failure_summary


def test_executor_does_not_block_safe_string_mentions(tmp_path: Path):
    """旧字符串黑名单会误杀普通文本；AST sanitizer 不应拦截字符串里的 open(。"""

    case = TestCase(
        source_agent="test",
        target_file="pkg/math_utils.py",
        test_name="test_safe_string",
        test_code='def test_safe_string():\n    assert "open(" in "call open( in docs only"\n',
        expected_behavior="String literals are not executable calls.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "passed"


def test_executor_marks_inconsistent_rerun_as_flaky_error(monkeypatch, tmp_path: Path):
    """第一次失败第二次通过时，不新增 flaky status，而是用 error_type 表达不稳定。"""

    first = TestResult(
        test_case_id="test_1",
        round_index=1,
        status="failed",
        failure_summary="first failure",
        related_finding_ids=["finding_1"],
    )
    second = TestResult(
        test_case_id="test_1",
        round_index=1,
        status="passed",
        related_finding_ids=["finding_1"],
    )
    results = iter([first, second])

    def fake_run_once(*args, **kwargs):
        return next(results)

    monkeypatch.setattr(executor, "_run_pytest_once", fake_run_once)
    case = TestCase(
        test_case_id="test_1",
        source_agent="test",
        related_finding_ids=["finding_1"],
        target_file="pkg/math_utils.py",
        test_name="test_flaky",
        test_code="def test_flaky():\n    assert True\n",
        expected_behavior="Flaky reruns become inconclusive evidence.",
    )

    result = executor.execute_pytest_case(
        case,
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert result.status == "error"
    assert result.error_type == "flaky_result"
    assert result.is_reproducible is False
