"""RepositoryMaterializer 使用本地 Git 仓库做完全离线测试。"""

import subprocess
from pathlib import Path

from codecross.evaluation.repository import materialize_repository_ref


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_materializes_and_reuses_detached_checkout(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "CodeCross Test")
    (source / "service.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(source, "add", "service.py")
    _git(source, "commit", "-m", "initial")
    sha = _git(source, "rev-parse", "HEAD")

    first = materialize_repository_ref(
        repository_url=str(source),
        ref="refs/heads/master",
        cache_key="local_demo",
        cache_dir=tmp_path / "cache",
    )
    second = materialize_repository_ref(
        repository_url=str(source),
        ref="refs/heads/master",
        cache_key="local_demo",
        cache_dir=tmp_path / "cache",
    )

    assert first.head_sha == sha
    assert (first.project_root / "service.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert first.cache_hit is False
    assert second.cache_hit is True
    mirror = tmp_path / "cache" / "mirrors" / "local_demo.git"
    assert _git(tmp_path, "--git-dir", str(mirror), "config", "--get", "core.longpaths") == "true"


def test_two_refs_share_one_mirror_but_use_separate_checkouts(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "CodeCross Test")
    (source / "value.txt").write_text("one", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "one")
    first_ref = _git(source, "branch", "--show-current")
    _git(source, "checkout", "-b", "second")
    (source / "value.txt").write_text("two", encoding="utf-8")
    _git(source, "commit", "-am", "two")

    first = materialize_repository_ref(
        repository_url=str(source), ref=f"refs/heads/{first_ref}",
        cache_key="shared", cache_dir=tmp_path / "cache"
    )
    second = materialize_repository_ref(
        repository_url=str(source), ref="refs/heads/second",
        cache_key="shared", cache_dir=tmp_path / "cache"
    )

    assert first.project_root != second.project_root
    assert (first.project_root / "value.txt").read_text(encoding="utf-8") == "one"
    assert (second.project_root / "value.txt").read_text(encoding="utf-8") == "two"
    assert len(list((tmp_path / "cache" / "mirrors").glob("*.git"))) == 1
