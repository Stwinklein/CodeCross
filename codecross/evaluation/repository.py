from __future__ import annotations

"""公开 benchmark 的只读仓库物化器。

正式仓库级评测需要 PR head 的完整文件树。该模块使用共享 bare mirror 缓存 Git 对象，
再为每个 PR 创建独立 detached worktree；因此多个 PR 不会互相切换分支，也不会把
benchmark golden 写进目标仓库。所有 subprocess 都使用参数列表且禁用 shell。
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepositoryMaterializationError(RuntimeError):
    """仓库无法安全检出时给调用方的结构化错误。"""


@dataclass(slots=True)
class RepositoryCheckout:
    project_root: Path
    head_sha: str
    ref: str
    cache_hit: bool


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = 180) -> str:
    """执行单条 Git 命令并裁剪错误文本，避免命令失败时淹没评测日志。"""

    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryMaterializationError(f"Git command could not complete: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise RepositoryMaterializationError(f"git {' '.join(args[:3])} failed: {detail}")
    return completed.stdout.strip()


def materialize_repository_ref(
    *,
    repository_url: str,
    ref: str,
    cache_key: str,
    cache_dir: str | Path,
) -> RepositoryCheckout:
    """把任意只读 Git ref 物化为 detached worktree；该底层函数便于离线测试。"""

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", cache_key):
        raise ValueError(f"非法 repository cache key: {cache_key}")
    root = Path(cache_dir).resolve()
    mirror = root / "mirrors" / f"{cache_key}.git"
    checkout = root / "checkouts" / f"{cache_key}__{re.sub(r'[^A-Za-z0-9_.-]+', '_', ref)}"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    checkout.parent.mkdir(parents=True, exist_ok=True)

    if not mirror.exists():
        _run_git(["clone", "--mirror", repository_url, str(mirror)], timeout=600)
    else:
        # mirror 的 remote URL 固定为创建时的仓库；显式校验可防止 cache_key 冲突
        # 把另一个仓库的代码当成本次上下文。
        cached_url = _run_git(["--git-dir", str(mirror), "remote", "get-url", "origin"])
        if Path(cached_url).resolve() != Path(repository_url).resolve() if not repository_url.startswith(("http://", "https://", "git@")) else cached_url != repository_url:
            raise RepositoryMaterializationError("Repository cache key points to a different remote URL.")

    # Windows 默认的 Git 长路径限制会让大型仓库（例如 Sentry 的 snapshot 测试）
    # 在 checkout 接近完成时失败。这里只修改 CodeCross 专属 bare mirror 的本地配置，
    # 不触碰用户的全局 git config。
    _run_git(["--git-dir", str(mirror), "config", "core.longpaths", "true"])

    # PR ref 不是普通 mirror fetch 默认一定包含的引用，因此每次显式获取。
    _run_git(["--git-dir", str(mirror), "fetch", "--force", "origin", f"{ref}:{ref}"], timeout=600)
    expected_sha = _run_git(["--git-dir", str(mirror), "rev-parse", ref])

    if checkout.exists():
        complete_marker = checkout / ".codecross_checkout_complete"
        try:
            actual_sha = _run_git(["rev-parse", "HEAD"], cwd=checkout)
            marker_sha = complete_marker.read_text(encoding="utf-8").strip()
            if actual_sha == expected_sha and marker_sha == expected_sha:
                return RepositoryCheckout(checkout, actual_sha, ref, True)
        except (OSError, RepositoryMaterializationError):
            pass
        # 这里只删除本模块专属 cache 根下、且已经解析过的具体 worktree；不会触碰用户仓库。
        try:
            _run_git(["--git-dir", str(mirror), "worktree", "remove", "--force", str(checkout)])
        except RepositoryMaterializationError:
            # checkout 中途失败时 Git 有时不会留下完整 worktree 元数据。路径已经由
            # cache_dir/cache_key/ref 生成并 resolve，确认仍在专用 cache 下才兜底清理。
            checkout.resolve().relative_to((root / "checkouts").resolve())
            shutil.rmtree(checkout, ignore_errors=False)
            _run_git(["--git-dir", str(mirror), "worktree", "prune"])
        if checkout.exists():
            shutil.rmtree(checkout)

    _run_git(["--git-dir", str(mirror), "worktree", "add", "--force", "--detach", str(checkout), expected_sha], timeout=600)
    (checkout / ".codecross_checkout_complete").write_text(expected_sha, encoding="utf-8")
    return RepositoryCheckout(checkout, expected_sha, ref, False)


def materialize_github_pr(
    *,
    owner: str,
    repo: str,
    number: int,
    cache_dir: str | Path,
) -> RepositoryCheckout:
    """物化 GitHub 的 ``refs/pull/<number>/head``，不需要 API token。"""

    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise ValueError("GitHub owner/repo 包含非法字符。")
    return materialize_repository_ref(
        repository_url=f"https://github.com/{owner}/{repo}.git",
        ref=f"refs/pull/{number}/head",
        # mirror 按仓库共享，PR 编号已经体现在 ref/checkouts 路径中。否则同一项目的
        # 10 个 benchmark PR 会重复下载 10 份完整 Git 对象。
        cache_key=f"{owner}__{repo}",
        cache_dir=cache_dir,
    )
