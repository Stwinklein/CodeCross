from __future__ import annotations

"""Run generated tests only in an ephemeral, resource-limited Docker container.

This is a process/filesystem boundary, not a promise that arbitrary source code is
harmless. The Docker daemon and images must be trusted; source dependencies must
be prepared separately. A missing runtime never falls back to host execution.
"""

import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from .config import ProjectConfig, RunnerConfig
from .schemas import TestCase, TestResult


IMAGE_BY_TEST_TYPE = {
    "pytest": "codecross-sandbox-python:3.12",
    "static_check": "codecross-sandbox-python:3.12",
    "unittest": "codecross-sandbox-python:3.12",
    "jest": "node:22-bookworm-slim",
    "vitest": "node:22-bookworm-slim",
    "go_test": "golang:1.24-bookworm",
    "maven": "maven:3.9-eclipse-temurin-21",
    "gradle": "gradle:8-jdk21",
    "cargo_test": "rust:1.85-bookworm",
    "rspec": "ruby:3.3-bookworm",
}
_SKIP_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", ".codecross", ".codecross-tmp",
    "__pycache__", ".pytest_cache", ".idea", ".tox", "dist", "build",
})
_SKIP_FILES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "*credentials*.json")
_MAX_SNAPSHOT_FILES = 20_000
_MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
_AVAILABILITY_LOCK = threading.Lock()
_AVAILABILITY_CACHE: tuple[float, bool] = (0.0, False)
_DEPENDENCY_ERROR = re.compile(
    r"(module not found|no module named|cannot find module|could not resolve dependencies|"
    r"missing go\.sum entry|failed to download|failed to get|package .* does not exist|"
    r"no space left on device)",
    re.I,
)


class SnapshotError(RuntimeError):
    """The project cannot be copied safely into the disposable workspace."""


def _excluded(path: Path) -> bool:
    return any(part.lower() in _SKIP_DIRECTORIES for part in path.parts) or any(
        fnmatch.fnmatch(path.name.lower(), pattern) for pattern in _SKIP_FILES
    )


def _tracked_paths(root: Path) -> list[Path] | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "-z"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    paths = [Path(os.fsdecode(raw)) for raw in completed.stdout.split(b"\0") if raw]
    return paths or None


def _candidate_paths(root: Path) -> list[Path]:
    tracked = _tracked_paths(root)
    if tracked is not None:
        return tracked
    paths: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = [
            name for name in dirs
            if not _excluded((base / name).relative_to(root)) and not (base / name).is_symlink()
        ]
        paths.extend((base / name).relative_to(root) for name in files)
        if len(paths) > _MAX_SNAPSHOT_FILES:
            raise SnapshotError("project snapshot exceeds file count limit")
    return paths


def snapshot_project(root: Path, destination: Path) -> None:
    """Copy only regular source files; never follow links or include local secrets."""

    if not root.is_dir():
        raise SnapshotError("project root does not exist")
    destination.chmod(0o777)
    count = total_bytes = 0
    for relative in _candidate_paths(root):
        if relative.is_absolute() or ".." in relative.parts or _excluded(relative):
            continue
        source = root / relative
        cursor = root
        linked = False
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                linked = True
                break
        if linked or not source.is_file():
            continue
        size = source.stat().st_size
        count += 1
        total_bytes += size
        if count > _MAX_SNAPSHOT_FILES or total_bytes > _MAX_SNAPSHOT_BYTES:
            raise SnapshotError("project snapshot exceeds size limit")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        current = target.parent
        while current != destination.parent:
            current.chmod(0o777)
            if current == destination:
                break
            current = current.parent
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode | 0o222)


def _make_generated_test_readable(test_file: Path, workspace: Path) -> None:
    """Let the non-root container user traverse generated paths in the copy.

    ``TemporaryDirectory`` uses mode 0700 on Linux. The Docker bind mount is
    read-only, so chmod only the disposable snapshot, never the source checkout.
    """

    try:
        test_file.resolve().relative_to(workspace.resolve())
    except ValueError as exc:
        raise SnapshotError("generated test escaped the disposable workspace") from exc
    cursor = test_file.parent
    while True:
        cursor.chmod(cursor.stat().st_mode | 0o555)
        if cursor == workspace:
            break
        cursor = cursor.parent
    test_file.chmod(test_file.stat().st_mode | 0o444)


def _docker_available() -> bool:
    global _AVAILABILITY_CACHE
    with _AVAILABILITY_LOCK:
        expires, cached = _AVAILABILITY_CACHE
        if time.monotonic() < expires:
            return cached
        if not shutil.which("docker"):
            ready = False
        else:
            try:
                completed = subprocess.run(
                    ["docker", "info", "--format", "{{.OSType}}"],
                    capture_output=True, text=True, timeout=2, check=False,
                )
                ready = completed.returncode == 0 and completed.stdout.strip() == "linux"
            except (OSError, subprocess.TimeoutExpired):
                ready = False
        _AVAILABILITY_CACHE = (time.monotonic() + 10.0, ready)
        return ready


def _docker_command(
    *, workspace: Path, image: str, name: str, command: list[str], config: RunnerConfig,
    test_timeout: int = 20,
) -> list[str]:
    if config.sandbox_memory_mb < 128 or config.sandbox_cpu_count <= 0 or config.sandbox_pids_limit < 8:
        raise ValueError("invalid sandbox resource limits")
    return [
        "docker", "run", "--rm", "--pull", "never", "--init", "--name", name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "10001:10001",
        "--pids-limit", str(config.sandbox_pids_limit),
        "--memory", f"{config.sandbox_memory_mb}m",
        "--memory-swap", f"{config.sandbox_memory_mb}m",
        "--cpus", str(config.sandbox_cpu_count),
        "--tmpfs", "/tmp:rw,nosuid,size=64m,mode=1777",
        "--tmpfs", "/workspace:rw,nosuid,size=256m,mode=1777",
        "--mount", f"type=bind,src={workspace},dst=/input,readonly",
        "--workdir", "/workspace",
        "--env", "HOME=/tmp", "--env", "PYTHONPATH=/workspace",
        "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "GOCACHE=/tmp/go-cache",
        "--env", "GOPATH=/tmp/go", "--env", "CARGO_HOME=/tmp/cargo",
        "--env", "CARGO_TARGET_DIR=/tmp/cargo-target",
        image, "sh", "-c",
        'cp -R /input/. /workspace/ && exec timeout -k 1s "$@"',
        "codecross-init", f"{max(1, test_timeout)}s", *command,
    ]


def _remove_container(name: str) -> None:
    # The random name belongs to this invocation; remove only that exact container.
    try:
        subprocess.run(
            ["docker", "rm", "-f", name], capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _run_bounded(command: list[str], *, name: str, timeout: int, output_limit: int) -> tuple[int | None, str, str, str]:
    """Bound wall time and host-side output, then kill the whole container."""

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            process = subprocess.Popen(command, stdout=stdout_file, stderr=stderr_file)
        except OSError as exc:
            return None, "", str(exc), "not_testable_sandbox_start"
        deadline = time.monotonic() + max(1, timeout)
        reason = ""
        while process.poll() is None:
            if time.monotonic() >= deadline:
                reason = "timeout"
                break
            if os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size > output_limit:
                reason = "output_limit"
                break
            time.sleep(0.05)
        if reason:
            _remove_container(name)
            if process.poll() is None:
                process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(output_limit).decode("utf-8", errors="replace")
        stderr = stderr_file.read(output_limit).decode("utf-8", errors="replace")
        return process.returncode, stdout, stderr, reason


def _not_testable(case: TestCase, round_index: int, reason: str, detail: str = "") -> TestResult:
    return TestResult(
        test_case_id=case.test_case_id, round_index=round_index, status="skipped",
        failure_summary=detail or reason, error_type=reason,
        related_finding_ids=case.related_finding_ids,
        executor_name="docker", evidence_kind="native_test" if case.test_type not in {"pytest", "static_check"} else "pytest",
    )


def run_sandboxed_test_once(
    case: TestCase, *, project_root: str | Path, round_index: int,
    runner_config: RunnerConfig, project_config: ProjectConfig | None = None,
) -> TestResult:
    """Run one pre-screened generated test, with no local-execution fallback."""

    if not _docker_available():
        return _not_testable(case, round_index, "not_testable_sandbox_unavailable", "Docker Linux engine is unavailable")
    image = IMAGE_BY_TEST_TYPE.get(case.test_type)
    if not image:
        return _not_testable(case, round_index, "not_testable_sandbox_image_missing", "No sandbox image is configured")
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="codecross-sandbox-") as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            snapshot_project(Path(project_root).resolve(), workspace)
            target = Path(case.target_file)
            if target.is_absolute() or ".." in target.parts or (case.target_file and not (workspace / target).is_file()):
                return _not_testable(case, round_index, "not_testable_target_missing", "Target source file is absent from the safe snapshot")
            if case.test_type in {"pytest", "static_check"}:
                test_dir = workspace / ".codecross-tmp"
                test_dir.mkdir()
                test_dir.chmod(0o777)
                safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", case.test_case_id)
                test_file = test_dir / f"test_{safe_id}.py"
                test_file.write_text(case.test_code, encoding="utf-8")
                inner_command = ["python", "-m", "pytest", f".codecross-tmp/{test_file.name}", "-q", "-p", "no:cacheprovider"]
            else:
                from .native_executor import ADAPTERS, build_native_command, prepared_native_test_file

                adapter = ADAPTERS.get(case.test_type)
                if adapter is None:
                    return _not_testable(case, round_index, "not_testable_unsupported_native_type")
                # File placement and command selection are reused, but only in the copy.
                with prepared_native_test_file(case, root=workspace, adapter=adapter) as test_file:
                    _make_generated_test_readable(test_file, workspace)
                    inner_command = build_native_command(
                        case, test_file=test_file, root=workspace,
                        project_config=project_config or ProjectConfig(),
                        runner_config=runner_config,
                    )
                    if inner_command and (inner_command[0].lower().endswith("python.exe") or inner_command[0] == os.sys.executable):
                        inner_command[0] = "python"
                    return _invoke_container(case, round_index, workspace, image, inner_command, runner_config, started)
            return _invoke_container(case, round_index, workspace, image, inner_command, runner_config, started)
    except (OSError, ValueError, SnapshotError) as exc:
        return _not_testable(case, round_index, "not_testable_sandbox_snapshot", str(exc))


def _invoke_container(
    case: TestCase, round_index: int, workspace: Path, image: str,
    inner_command: list[str], config: RunnerConfig, started: float,
) -> TestResult:
    name = f"codecross-{uuid.uuid4().hex}"
    test_timeout = case.timeout_seconds or config.test_timeout_seconds
    command = _docker_command(
        workspace=workspace, image=image, name=name, command=inner_command,
        config=config, test_timeout=test_timeout,
    )
    exit_code, stdout, stderr, reason = _run_bounded(
        command, name=name,
        timeout=test_timeout + 15,
        output_limit=config.sandbox_output_limit_bytes,
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    if reason == "timeout" or exit_code == 124:
        status, error_type = "timeout", "timeout"
    elif reason == "output_limit":
        status, error_type = "error", "sandbox_output_limit"
    elif exit_code == 125 or exit_code is None:
        status, error_type = "skipped", "not_testable_sandbox_runtime"
    elif exit_code == 0:
        status, error_type = "passed", ""
    elif _DEPENDENCY_ERROR.search(f"{stdout}\n{stderr}"):
        status, error_type = "skipped", "not_testable_build_or_dependency"
    elif exit_code == 1:
        status, error_type = "failed", "pytest_failure" if case.test_type in {"pytest", "static_check"} else f"{case.test_type}_failure"
    else:
        status, error_type = "error", f"sandbox_exit_{exit_code}"
    return TestResult(
        test_case_id=case.test_case_id, round_index=round_index,
        status=status, exit_code=exit_code, stdout=stdout, stderr=stderr,
        duration_ms=elapsed, failure_summary=(stderr or stdout)[-2000:],
        error_type=error_type, is_reproducible=status == "failed",
        related_finding_ids=case.related_finding_ids,
        evidence_kind="native_test" if case.test_type not in {"pytest", "static_check"} else "pytest",
        executor_name=f"docker:{case.test_type}", executor_command=inner_command,
    )
