"""Executor 测试。

重点验证两件事：
1. 生成的 pytest 真的会被执行；
2. 不安全的测试代码会被 sanitizer 拦截。
"""

from pathlib import Path
from functools import partial

import codecross.executor as executor
from codecross.config import RunnerConfig as _RunnerConfig
from codecross.executor import execute_test_cases
from codecross.schemas import TestCase, TestResult


# These legacy tests exercise the explicitly trusted host path. New tests cover
# Docker as the default and verify that missing isolation never falls back.
RunnerConfig = partial(_RunnerConfig, execution_mode="trusted_local")


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


def test_executor_does_not_treat_confirm_as_rm_command(tmp_path: Path):
    """命令黑名单要匹配独立 token，不能把 confirm 里的 rm 当 shell 命令。"""

    case = TestCase(
        source_agent="test",
        target_file="pkg/source.py",
        test_name="test_confirm_comment_is_safe",
        test_code=(
            "def test_confirm_comment_is_safe():\n"
            "    # confirm the expected branch without shell commands\n"
            "    assert 'value' == 'value'\n"
        ),
        expected_behavior="Normal words containing rm should not be blocked.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "passed"


def test_executor_skips_placeholder_assert_true_tests(tmp_path: Path):
    """占位测试不能因为 pytest 通过就反驳 reviewer finding。"""

    case = TestCase(
        source_agent="test",
        related_finding_ids=["finding_1"],
        target_file="src/server/queue.js",
        test_name="test_placeholder",
        test_code=(
            "def test_placeholder():\n"
            "    # Placeholder; actual implementation would import the real function.\n"
            "    assert True\n"
        ),
        expected_behavior="Placeholder tests are not valid evidence.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "skipped"
    assert results[0].error_type == "low_value_test"
    assert results[0].related_finding_ids == ["finding_1"]


def test_executor_allows_placeholder_comment_when_real_assert_exists(tmp_path: Path):
    """有真实断言时，注释里的 placeholder 字样不应让静态检查被跳过。"""

    case = TestCase(
        source_agent="test",
        target_file="pkg/source.py",
        test_name="test_real_assert_with_placeholder_comment",
        test_code=(
            "def test_real_assert_with_placeholder_comment():\n"
            "    found = True\n"
            "    assert found, 'real check failed'\n"
            "    # placeholder note in a comment should not override the real assertion\n"
        ),
        expected_behavior="Meaningful assertions make the test usable evidence.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "passed"


def test_executor_records_over_budget_tests_as_skipped(tmp_path: Path):
    """超过执行预算的生成测试要留下 skipped 记录，方便解释覆盖缺口。"""

    cases = [
        TestCase(
            source_agent="test",
            target_file="pkg/math_utils.py",
            test_name=f"test_case_{index}",
            test_code=f"def test_case_{index}():\n    assert {index} == {index}\n",
            expected_behavior="Budget accounting remains visible.",
        )
        for index in range(5)
    ]
    config = RunnerConfig(max_tests_per_round=2)

    results = execute_test_cases(
        cases,
        project_root=tmp_path,
        round_index=1,
        runner_config=config,
    )

    assert len(results) == 5
    assert [result.status for result in results].count("passed") == 4
    assert results[-1].status == "skipped"
    assert results[-1].error_type == "max_tests_per_round_exceeded"


def test_executor_runs_static_check_as_pytest(tmp_path: Path):
    """static_check 是给 JS/TS 等项目用的 Python 静态断言，也应该被执行。"""

    source = tmp_path / "src" / "server"
    source.mkdir(parents=True)
    (source / "queue.js").write_text(
        "function resultToContent(result) {\n  return result?.text || '生成失败';\n}\n",
        encoding="utf-8",
    )
    case = TestCase(
        source_agent="test",
        related_finding_ids=["finding_1"],
        test_type="static_check",
        target_file="src/server/queue.js",
        test_name="test_static_check_reads_js_source",
        test_code=(
            "from pathlib import Path\n\n\n"
            "def test_static_check_reads_js_source():\n"
            "    source = Path('src/server/queue.js').read_text(encoding='utf-8')\n"
            "    assert \"result?.text || '生成失败'\" in source\n"
        ),
        expected_behavior="Static source assertions can verify non-Python projects.",
    )

    results = execute_test_cases(
        [case],
        project_root=tmp_path,
        round_index=1,
        runner_config=RunnerConfig(),
    )

    assert results[0].status == "passed"
    assert results[0].related_finding_ids == ["finding_1"]


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
        test_code="def test_flaky():\n    assert 1 == 1\n",
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
