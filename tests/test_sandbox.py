"""Fail-closed and filesystem-boundary tests for generated code execution."""

from pathlib import Path

import pytest

import codecross.executor as executor
import codecross.native_executor as native
import codecross.sandbox as sandbox
from codecross.config import ProjectConfig, RunnerConfig
from codecross.schemas import TestCase


def _python_case() -> TestCase:
    return TestCase(
        source_agent="test", target_file="module.py", test_name="test_value",
        test_code="def test_value():\n    assert 1 == 1\n", expected_behavior="value",
    )


def test_default_docker_mode_never_falls_back_to_host(monkeypatch, tmp_path: Path):
    assert RunnerConfig().execution_mode == "docker"
    monkeypatch.setattr(sandbox, "_docker_available", lambda: False)
    monkeypatch.setattr(executor, "_run_pytest_once", lambda *args, **kwargs: pytest.fail("host execution"))

    result = executor.execute_pytest_case(
        _python_case(), project_root=tmp_path, round_index=1,
        runner_config=RunnerConfig(),
    )

    assert result.status == "skipped"
    assert result.error_type == "not_testable_sandbox_unavailable"


def test_native_case_does_not_write_into_real_project_without_sandbox(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(sandbox, "_docker_available", lambda: False)
    (tmp_path / "go.mod").write_text("module example.com/test\n", encoding="utf-8")
    (tmp_path / "calc.go").write_text("package test\n", encoding="utf-8")
    case = TestCase(
        source_agent="test", test_type="go_test", target_file="calc.go",
        test_name="TestCalc", test_code="package test\nfunc TestCalc(t *testing.T) { t.Error(\"bad\") }\n",
        expected_behavior="error",
    )

    result = native.execute_native_test_case(
        case, project_root=tmp_path, round_index=1,
        runner_config=RunnerConfig(), project_config=ProjectConfig(),
    )

    assert result.status == "skipped"
    assert result.error_type == "not_testable_sandbox_unavailable"
    assert not list(tmp_path.glob("*codecross*"))


def test_snapshot_excludes_secrets_and_symlinks(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
    (source / "private.pem").write_text("private\n", encoding="utf-8")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "large.js").write_text("ignored", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    try:
        (source / "link.txt").symlink_to(outside)
    except OSError:
        pass  # Windows accounts without symlink privilege still test secret filtering.
    destination = tmp_path / "copy"
    destination.mkdir()

    sandbox.snapshot_project(source, destination)

    assert (destination / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (destination / ".env").exists()
    assert not (destination / "private.pem").exists()
    assert not (destination / "node_modules").exists()
    assert not (destination / "link.txt").exists()


def test_container_command_enforces_security_limits(tmp_path: Path):
    command = sandbox._docker_command(
        workspace=tmp_path, image="codecross-sandbox-python:3.12", name="codecross-test",
        command=["python", "-m", "pytest", "test.py"], config=RunnerConfig(),
    )

    assert command[:3] == ["docker", "run", "--rm"]
    for pair in (("--network", "none"), ("--cap-drop", "ALL"), ("--user", "10001:10001")):
        index = command.index(pair[0])
        assert command[index + 1] == pair[1]
    assert "--read-only" in command
    assert any(token.endswith("dst=/input,readonly") for token in command)
    assert "/workspace:rw,nosuid,size=256m,mode=1777" in command
    assert "--pids-limit" in command
    assert "--memory" in command
    assert "--cpus" in command
    assert "--pull" in command and command[command.index("--pull") + 1] == "never"
    assert not any("SECRET=" in token for token in command)
