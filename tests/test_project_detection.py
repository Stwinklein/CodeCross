"""项目检测和评测模式契约测试。"""

import json
from pathlib import Path

from codecross.config import load_project_config
from codecross.project_detection import detect_project
from codecross.workflow import run_workflow


def test_detects_python_uv_pytest_project(tmp_path: Path):
    """Python 清单、uv 锁文件和 pytest 配置应组成完整 profile。"""

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="demo"\n[tool.pytest.ini_options]\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/a.py b/a.py\n")

    assert profile.language == "python"
    assert profile.test_framework == "pytest"
    assert profile.package_manager == "uv"
    assert profile.test_command == ["uv", "run", "pytest"]
    assert profile.confidence == "high"


def test_detects_typescript_pnpm_vitest_project(tmp_path: Path):
    """JS/TS 工具链从 package.json 与 lockfile 检测，不再回退 pytest。"""

    (tmp_path / "package.json").write_text(
        json.dumps({"devDependencies": {"vitest": "latest"}, "scripts": {"test": "vitest"}}),
        encoding="utf-8",
    )
    (tmp_path / "pnpm-lock.yaml").write_text("", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/src/a.ts b/src/a.ts\n")

    assert profile.language == "typescript"
    assert profile.test_framework == "vitest"
    assert profile.package_manager == "pnpm"
    assert profile.test_command == ["pnpm", "exec", "vitest", "run"]


def test_diff_language_wins_in_mixed_repository(tmp_path: Path):
    """monorepo 同时有 Python/Node 清单时，本次 diff 语言应拥有更高优先级。"""

    (tmp_path / "pyproject.toml").write_text("[project]\nname='backend'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/web/view.tsx b/web/view.tsx\n")

    assert profile.language == "typescript"


def test_detects_ruby_bundler_rspec_project(tmp_path: Path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    (tmp_path / ".rspec").write_text("--format progress\n", encoding="utf-8")
    (tmp_path / "spec").mkdir()

    profile = detect_project(tmp_path, diff="diff --git a/app/service.rb b/app/service.rb\n")

    assert profile.language == "ruby"
    assert profile.test_framework == "rspec"
    assert profile.package_manager == "bundler"
    assert profile.test_command == ["bundle", "exec", "rspec"]


def test_detects_python_unittest_when_pytest_is_not_configured(tmp_path: Path):
    """Python 项目没有 pytest 配置时应明确路由到 unittest discover。"""

    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n', encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/pkg/a.py b/pkg/a.py\n")

    assert profile.test_framework == "unittest"
    assert profile.test_command == ["python", "-m", "unittest", "discover"]


def test_detects_go_test_project(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module example.test/demo\n", encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/service/a.go b/service/a.go\n")

    assert profile.language == "go"
    assert profile.test_framework == "go test"
    assert profile.test_command == ["go", "test", "./..."]


def test_detects_maven_wrapper_project(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project />", encoding="utf-8")
    (tmp_path / "mvnw.cmd").write_text("@echo off\n", encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/src/main/java/App.java b/src/main/java/App.java\n")

    assert profile.test_framework == "maven"
    assert profile.test_command == ["mvnw.cmd", "test"]


def test_detects_gradle_wrapper_project(tmp_path: Path):
    (tmp_path / "build.gradle.kts").write_text("plugins { java }\n", encoding="utf-8")
    (tmp_path / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/src/main/java/App.java b/src/main/java/App.java\n")

    assert profile.test_framework == "gradle"
    assert profile.test_command == ["gradlew.bat", "test"]


def test_detects_rust_cargo_project(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="demo"\nversion="0.1.0"\n', encoding="utf-8")

    profile = detect_project(tmp_path, diff="diff --git a/src/lib.rs b/src/lib.rs\n")

    assert profile.language == "rust"
    assert profile.test_framework == "cargo test"
    assert profile.test_command == ["cargo", "test"]


def test_review_only_skips_evidence_loop():
    """review_only 仍运行双 Reviewer，但不生成或执行验证测试。"""

    root = Path.cwd()
    diff = (root / "examples" / "diffs" / "safe_divide.diff").read_text(encoding="utf-8")
    config = load_project_config(root, mock_llm=True, diff=diff, evaluation_mode="review_only")

    state = run_workflow(diff, project_root=root, config=config)

    assert state["findings"]
    assert state["all_test_cases"] == []
    assert state["all_test_results"] == []
    assert state["board_summary"].test_round_count == 0
    assert state["stop_reason"] == "review_only_complete"
    assert "evaluator_report" not in state
    assert "final_report" not in state
