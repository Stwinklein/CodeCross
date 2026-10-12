from __future__ import annotations

"""项目语言、包管理器和测试框架的确定性检测。

这个模块不执行项目代码，也不会自动安装依赖。它只读取仓库根目录中的清单文件，
再结合本次 diff 的变更文件后缀生成 ``ProjectProfile``。把“检测”和“执行”分开，
可以让公开 benchmark 在任何机器上得到相同的路由结论，同时避免扫描未知仓库时
意外触发脚本。
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ProjectProfile:
    """ProjectDetector 的稳定输出，随后会映射到 ``ProjectConfig``。"""

    language: str
    test_framework: str
    package_manager: str
    test_command: list[str]
    source_roots: list[str] = field(default_factory=lambda: ["."])
    test_roots: list[str] = field(default_factory=lambda: ["tests"])
    confidence: str = "medium"
    evidence: list[str] = field(default_factory=list)


_LANGUAGE_SUFFIXES = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".vue": "typescript",
    ".go": "go", ".java": "java", ".kt": "java", ".kts": "java", ".rs": "rust",
    ".rb": "ruby", ".rake": "ruby",
}


def changed_paths_from_diff(diff: str) -> list[str]:
    """从 unified diff 提取新文件路径，供混合语言仓库判断本次主语言。"""

    paths: list[str] = []
    for line in diff.splitlines():
        match = re.match(r"diff --git a/(.*?) b/(.*)", line)
        if match:
            paths.append(match.group(2).strip())
        elif line.startswith("+++ b/"):
            paths.append(line.removeprefix("+++ b/").strip())
    return list(dict.fromkeys(path for path in paths if path and path != "/dev/null"))


def _language_from_changed_paths(paths: list[str]) -> str | None:
    counts: dict[str, int] = {}
    for raw_path in paths:
        language = _LANGUAGE_SUFFIXES.get(Path(raw_path).suffix.lower())
        if language:
            counts[language] = counts.get(language, 0) + 1
    if not counts:
        return None
    # 数量相同时优先 TS：TS 项目常混有少量 JS，但测试工具链一般仍由 TS 配置决定。
    priority = {"typescript": 7, "javascript": 6, "python": 5, "ruby": 4, "go": 3, "java": 2, "rust": 1}
    return max(counts, key=lambda item: (counts[item], priority.get(item, 0)))


def _read_package_json(root: Path) -> dict:
    try:
        return json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _detect_python(root: Path, *, confidence: str) -> ProjectProfile:
    evidence = [name for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt") if (root / name).exists()]
    try:
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    except OSError:
        pyproject = ""

    if (root / "uv.lock").exists():
        package_manager, command = "uv", ["uv", "run", "pytest"]
        evidence.append("uv.lock")
    elif (root / "poetry.lock").exists():
        package_manager, command = "poetry", ["poetry", "run", "pytest"]
        evidence.append("poetry.lock")
    elif (root / "pdm.lock").exists():
        package_manager, command = "pdm", ["pdm", "run", "pytest"]
        evidence.append("pdm.lock")
    else:
        package_manager, command = "pip", ["python", "-m", "pytest"]

    framework = "pytest" if "pytest" in pyproject or (root / "pytest.ini").exists() else "unittest"
    if framework == "unittest":
        command = ["python", "-m", "unittest", "discover"]
    source_roots = [name for name in ("src", "app", "lib") if (root / name).is_dir()] or ["."]
    test_roots = [name for name in ("tests", "test") if (root / name).is_dir()] or ["tests"]
    return ProjectProfile("python", framework, package_manager, command, source_roots, test_roots, confidence, evidence)


def _detect_javascript(root: Path, *, language: str, confidence: str) -> ProjectProfile:
    package = _read_package_json(root)
    dependencies = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
    scripts = package.get("scripts") or {}
    evidence = ["package.json"] if package else []

    if (root / "pnpm-lock.yaml").exists():
        manager, command = "pnpm", ["pnpm", "test"]
        evidence.append("pnpm-lock.yaml")
    elif (root / "yarn.lock").exists():
        manager, command = "yarn", ["yarn", "test"]
        evidence.append("yarn.lock")
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        manager, command = "bun", ["bun", "test"]
        evidence.append("bun.lock")
    else:
        manager, command = "npm", ["npm", "test"]
        if (root / "package-lock.json").exists():
            evidence.append("package-lock.json")

    if "vitest" in dependencies or "vitest" in str(scripts.get("test", "")):
        framework = "vitest"
        command = [manager, "exec", "vitest", "run"] if manager != "npm" else ["npx", "vitest", "run"]
    elif "jest" in dependencies or "jest" in str(scripts.get("test", "")):
        framework = "jest"
    elif "mocha" in dependencies:
        framework = "mocha"
    else:
        framework = "package-script"

    source_roots = [name for name in ("src", "app", "packages", "lib") if (root / name).is_dir()] or ["."]
    test_roots = [name for name in ("tests", "test", "__tests__") if (root / name).is_dir()] or ["tests"]
    return ProjectProfile(language, framework, manager, command, source_roots, test_roots, confidence, evidence)


def detect_project(project_root: str | Path, *, diff: str = "") -> ProjectProfile:
    """检测本次 PR 的主语言和测试工具链，不执行任何外部命令。"""

    root = Path(project_root).resolve()
    changed_language = _language_from_changed_paths(changed_paths_from_diff(diff))
    confidence = "high" if changed_language else "medium"

    # diff 是本次审查最直接的证据，混合仓库应按实际改动语言选择工具链。
    if changed_language == "python":
        return _detect_python(root, confidence=confidence)
    if changed_language in {"javascript", "typescript"}:
        return _detect_javascript(root, language=changed_language, confidence=confidence)
    if changed_language == "go" or (not changed_language and (root / "go.mod").exists()):
        return ProjectProfile("go", "go test", "go modules", ["go", "test", "./..."], confidence=confidence, evidence=["go.mod"])
    if changed_language == "java" or (not changed_language and any((root / name).exists() for name in ("pom.xml", "build.gradle", "build.gradle.kts"))):
        if (root / "mvnw").exists() or (root / "mvnw.cmd").exists() or (root / "pom.xml").exists():
            # 优先使用仓库自带 wrapper，避免本机 Maven 版本和项目声明不一致。
            executable = "mvnw.cmd" if (root / "mvnw.cmd").exists() else "./mvnw" if (root / "mvnw").exists() else "mvn"
            return ProjectProfile("java", "maven", "maven", [executable, "test"], ["src/main/java"], ["src/test/java"], confidence, ["pom.xml"])
        executable = "gradlew.bat" if (root / "gradlew.bat").exists() else "./gradlew" if (root / "gradlew").exists() else "gradle"
        return ProjectProfile("java", "gradle", "gradle", [executable, "test"], ["src/main/java"], ["src/test/java"], confidence, ["build.gradle"])
    if changed_language == "rust" or (not changed_language and (root / "Cargo.toml").exists()):
        return ProjectProfile("rust", "cargo test", "cargo", ["cargo", "test"], ["src"], ["tests"], confidence, ["Cargo.toml"])
    if changed_language == "ruby" or (not changed_language and (root / "Gemfile").exists()):
        framework = "rspec" if (root / ".rspec").exists() or (root / "spec").is_dir() else "minitest"
        command = ["bundle", "exec", "rspec"] if framework == "rspec" else ["bundle", "exec", "rake", "test"]
        return ProjectProfile(
            "ruby", framework, "bundler", command,
            [name for name in ("app", "lib") if (root / name).is_dir()] or ["."],
            ["spec"] if framework == "rspec" else ["test"],
            confidence, [name for name in ("Gemfile", "Gemfile.lock", ".rspec") if (root / name).exists()],
        )
    if (root / "package.json").exists():
        language = "typescript" if (root / "tsconfig.json").exists() else "javascript"
        return _detect_javascript(root, language=language, confidence=confidence)
    if any((root / marker).exists() for marker in ("pyproject.toml", "setup.py", "requirements.txt")):
        return _detect_python(root, confidence=confidence)

    # 未知项目不能伪装成 pytest 项目；证据层据此返回 not_testable。
    return ProjectProfile(changed_language or "unknown", "unknown", "unknown", [], confidence="low")
