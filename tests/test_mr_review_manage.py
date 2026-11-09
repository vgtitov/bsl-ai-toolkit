"""mr_review.py status/create/note/merge/sync-main: чистая логика и git без сети."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mr_review as mr  # noqa: E402


@pytest.mark.parametrize("branch,protected,expected", [
    ("feat/x", False, True),
    ("develop", False, False),  # MR develop->main не должен удалять develop
    ("main", False, False),
    ("master", False, False),
    ("release/2.5", True, False),
    ("feat/y", None, False),  # статус ветки не получен - не удаляем
])
def test_should_remove_source(branch, protected, expected):
    assert mr.should_remove_source(branch, protected) is expected


def test_is_protected_reads_branch_and_fails_safe():
    calls = []

    def ok(method, path, data):
        calls.append((method, path))
        return {"protected": True}
    assert mr.GitLab("git.example.org", "g/p", "t", call=ok).is_protected("feat/a b") is True
    assert calls == [("GET", "/repository/branches/feat%2Fa%20b")]

    def boom(method, path, data):
        raise OSError("HTTP 500")
    gl = mr.GitLab("git.example.org", "g/p", "t", call=boom)
    assert gl.is_protected("feat/x") is None
    assert mr.should_remove_source("feat/x", gl.is_protected("feat/x")) is False


def test_gitlab_rejects_host_with_path():
    with pytest.raises(ValueError):
        mr.GitLab("evil.example.org/x", "g/p", "t")


def test_gitlab_token(monkeypatch):
    for k in ("GITLAB_TOKEN", "GITLAB_PERSONAL_ACCESS_TOKEN", "GITLAB_TOKEN_ENV", "MY_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert mr.gitlab_token() == (None, None)
    monkeypatch.setenv("GITLAB_PERSONAL_ACCESS_TOKEN", "p")
    assert mr.gitlab_token() == ("GITLAB_PERSONAL_ACCESS_TOKEN", "p")
    monkeypatch.setenv("GITLAB_TOKEN", "g")
    assert mr.gitlab_token() == ("GITLAB_TOKEN", "g")
    monkeypatch.setenv("MY_TOKEN", "m")
    assert mr.gitlab_token("MY_TOKEN") == ("MY_TOKEN", "m")
    monkeypatch.setenv("GITLAB_TOKEN_ENV", "MY_TOKEN")
    assert mr.gitlab_token() == ("MY_TOKEN", "m")


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("﻿A_KEY=from_file\nB_KEY='x y'\n# C_KEY=no\nD_KEY=v # tail\n", encoding="utf-8")
    monkeypatch.setenv("A_KEY", "from_env")
    for k in ("B_KEY", "C_KEY", "D_KEY"):
        monkeypatch.delenv(k, raising=False)
    mr.load_dotenv(tmp_path)
    import os
    assert os.environ["A_KEY"] == "from_env" and os.environ["B_KEY"] == "x y" and os.environ["D_KEY"] == "v"
    assert "C_KEY" not in os.environ


def test_wait_mergeable_polls_until_known():
    statuses = iter(["checking", "unchecked", "preparing", "mergeable"])
    now = [0.0]
    got = mr.wait_mergeable(lambda: {"detailed_merge_status": next(statuses)}, timeout=60, step=3,
                            sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    assert got["detailed_merge_status"] == "mergeable" and now[0] == 9


def test_wait_mergeable_gives_up_after_timeout():
    now = [0.0]
    got = mr.wait_mergeable(lambda: {"detailed_merge_status": "checking"}, timeout=10, step=3,
                            sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0])
    assert got["detailed_merge_status"] == "checking" and now[0] == 12


GOOD = {"source_branch": "feat/x", "target_branch": "main", "state": "opened",
        "detailed_merge_status": "mergeable", "sha": "abc"}


def test_merge_problems():
    assert mr.merge_problems(GOOD, "feat/x", "feat/x", "abc", "abc", False) == []
    bad = mr.merge_problems({**GOOD, "detailed_merge_status": "ci_must_pass", "sha": "old"},
                            "feat/x", "other", "abc", "def", True)
    assert len(bad) == 5
    assert any("sync-main" in p for p in bad) and any("sha" in p for p in bad)


BASE = """# Changelog

## [Unreleased]

### Changed
- старое изменение

### Added
- база

## [1.0.0]
### Added
- релиз
"""


def test_merge_changelog_inserts_after_added():
    ours = BASE.replace("- база\n", "- база\n- моя строка\n  продолжение\n")
    theirs = BASE.replace("- база\n", "- база\n- из main\n")
    lines = mr.merge_changelog(BASE, ours, theirs).split("\n")
    i = lines.index("### Added")
    assert lines[i + 1:i + 5] == ["- моя строка", "  продолжение", "- база", "- из main"]


def test_merge_changelog_creates_added_and_dedupes():
    theirs = "# Changelog\n\n## [Unreleased]\n\n### Changed\n- x\n\n## [1.0.0]\n"
    ours = theirs.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Added\n- новое\n")
    assert "## [Unreleased]\n\n### Added\n- новое\n\n### Changed" in mr.merge_changelog(theirs, ours, theirs)
    assert mr.merge_changelog(theirs, ours, ours).count("- новое") == 1


@pytest.mark.parametrize("ours", [
    BASE.replace("- старое изменение\n", "- старое изменение\n- в Changed\n"),
    BASE.replace("## [1.0.0]\n", "### Fixed\n- новый подраздел\n\n## [1.0.0]\n"),
    BASE.replace("## [Unreleased]\n", "## [Unreleased]\n\n## [1.1.0]\n### Added\n- релиз ветки\n"),
    BASE.replace("- релиз\n", "- релиз\n- в старом релизе\n"),
])
def test_merge_changelog_refuses_outside_unreleased_added(ours):
    with pytest.raises(ValueError, match="вне"):
        mr.merge_changelog(BASE, ours, BASE)


def test_merge_changelog_refuses_edits():
    with pytest.raises(ValueError):
        mr.merge_changelog(BASE, BASE.replace("- база", "- база (правка)"), BASE)


def _git(cwd, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false",
                    "-c", f"core.hooksPath={Path(cwd) / 'nohooks'}", *a], cwd=cwd, check=True, capture_output=True)


def test_sync_main_resolves_changelog_conflict(tmp_path):
    origin, work, other = tmp_path / "origin.git", tmp_path / "work", tmp_path / "other"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(work))
    (work / "CHANGELOG.md").write_text(BASE, encoding="utf-8", newline="\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "base")
    _git(work, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(other))
    (other / "CHANGELOG.md").write_text(BASE.replace("- база\n", "- база\n- из main\n"), encoding="utf-8", newline="\n")
    _git(other, "commit", "-am", "main")
    _git(other, "push", "origin", "HEAD:main")
    _git(work, "checkout", "-b", "feat/x")
    (work / "CHANGELOG.md").write_text(BASE.replace("- база\n", "- база\n- из ветки\n"), encoding="utf-8", newline="\n")
    _git(work, "commit", "-am", "branch")
    for k, v in (("user.name", "t"), ("user.email", "t@example.org"), ("commit.gpgsign", "false"),
                 ("core.hooksPath", str(tmp_path / "nohooks"))):
        _git(work, "config", k, v)  # sync_main коммитит без -c: настройки - в репозиторий
    assert mr.sync_main(work) == 0
    text = (work / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- из ветки" in text and "- из main" in text and "<<<<" not in text
