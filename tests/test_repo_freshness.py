"""repo_freshness: строка в контекст агента, только когда ветка реально отстала от upstream."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import repo_freshness as rf  # noqa: E402


def _run(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": __import__("os").environ["PATH"],
                        "HOME": str(cwd)})


def _pair(tmp_path):
    origin = tmp_path / "origin"; origin.mkdir()
    _run(origin, "init", "-q", "-b", "main")
    (origin / "a.txt").write_text("1")
    _run(origin, "add", "."); _run(origin, "commit", "-qm", "первый")
    _run(tmp_path, "clone", "-q", str(origin), "clone")
    return origin, tmp_path / "clone"


def test_fresh_clone_is_silent(tmp_path):
    _, clone = _pair(tmp_path)
    assert rf.check(clone, every_hours=0) == ""


def test_behind_upstream_is_reported_with_hint(tmp_path):
    origin, clone = _pair(tmp_path)
    (origin / "a.txt").write_text("2")
    _run(origin, "commit", "-qam", "правило про SSH")
    msg = rf.check(clone, every_hours=0, hint="скажи: подтяни изменения")
    assert "отстаёт" in msg and "на 1 коммит" in msg and "правило про SSH" in msg
    assert "скажи: подтяни изменения" in msg


def test_detached_head_on_pin_tag_is_silent(tmp_path):
    origin, clone = _pair(tmp_path)
    _run(clone, "checkout", "-q", "--detach")
    (origin / "a.txt").write_text("2")
    _run(origin, "commit", "-qam", "новое")
    assert rf.check(clone, every_hours=0) == ""


def test_not_a_repo_and_cache_never_fail(tmp_path):
    assert rf.check(tmp_path / "nowhere", every_hours=0) == ""
    assert rf.main(["--repo", str(tmp_path)]) == 0
