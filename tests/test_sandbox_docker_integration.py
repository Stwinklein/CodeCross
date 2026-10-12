"""Real Docker boundary checks; skip when the local engine/images are absent."""

import subprocess
from pathlib import Path

import pytest

from codecross.config import ProjectConfig, RunnerConfig
from codecross.sandbox import _docker_available, run_sandboxed_test_once
from codecross.schemas import TestCase


def _require_image(image: str) -> None:
    if not _docker_available():
        pytest.skip("Linux Docker engine is unavailable")
    inspected = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=5)
    if inspected.returncode != 0:
        pytest.skip(f"sandbox image is missing: {image}")


@pytest.mark.docker
def test_generated_python_cannot_see_host_secret_or_network_and_cannot_change_source(monkeypatch, tmp_path: Path):
    _require_image("codecross-sandbox-python:3.12")
    monkeypatch.setenv("CODECROSS_CANARY_SECRET", "host-only")
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=host-only\n", encoding="utf-8")
    case = TestCase(
        source_agent="integration", target_file="module.py", test_name="test_boundary",
        test_code=(
            "import os\nimport socket\nfrom pathlib import Path\nimport pytest\n\n"
            "def test_boundary():\n"
            "    assert 'CODECROSS_CANARY_SECRET' not in os.environ\n"
            "    assert not Path('.env').exists()\n"
            "    with pytest.raises(OSError):\n"
            "        socket.create_connection(('1.1.1.1', 80), timeout=0.5)\n"
            "    Path('module.py').write_text('changed only in the copy')\n"
        ),
        expected_behavior="host isolation",
    )

    result = run_sandboxed_test_once(
        case, project_root=tmp_path, round_index=1,
        runner_config=RunnerConfig(test_timeout_seconds=15),
    )

    assert result.status == "passed", result.stderr
    assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=host-only\n"


@pytest.mark.docker
def test_native_javascript_test_runs_in_node_container(tmp_path: Path):
    _require_image("node:22-bookworm-slim")
    (tmp_path / "add.js").write_text("exports.add = (a, b) => a + b;\n", encoding="utf-8")
    case = TestCase(
        source_agent="integration", test_type="jest", target_file="add.js", test_name="adds",
        test_code=(
            "const { test } = require('node:test');\n"
            "const assert = require('node:assert/strict');\n"
            "test('adds', () => { assert.equal(require('../add.js').add(1, 2), 3); });\n"
        ),
        expected_behavior="3",
    )
    config = RunnerConfig(test_command_override=["node", "--test", "{test_file}"])
    project = ProjectConfig(language="javascript", test_framework="jest")

    result = run_sandboxed_test_once(
        case, project_root=tmp_path, round_index=1,
        runner_config=config, project_config=project,
    )

    assert result.status == "passed", result.stderr
    assert not list(tmp_path.glob("*codecross*"))
