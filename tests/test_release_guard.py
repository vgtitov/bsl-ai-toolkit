"""Release guard: real Git repositories prove that invalid tags cannot be released."""
import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "release_guard", ROOT / "scripts" / "release_guard.py")
release_guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_guard)


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=check)


@dataclass
class ReleaseRepo:
    work: Path
    remote: Path

    def git(self, *args: str, check: bool = True) -> str:
        return _run(self.work, "git", *args, check=check).stdout.strip()

    def write_changelog(self, *versions: str) -> None:
        sections = "\n".join(
            f"## [{version}] - 2026-07-31\n\n### Fixed\n- release {version}\n"
            for version in versions)
        (self.work / "CHANGELOG.md").write_text(
            f"# Changelog\n\n## [Unreleased]\n\n{sections}", encoding="utf-8")

    def preflight(
            self, version: str,
            verify_command: list[str] | None = None) -> None:
        release_guard.preflight_release(
            self.work,
            version,
            "origin",
            "main",
            verify_command or [sys.executable, "-c", "raise SystemExit(0)"],
        )

    def prepare_release(self, version: str) -> None:
        self.write_changelog(version.removeprefix("v"), "1.0.0")
        self.git("add", "CHANGELOG.md")
        self.git("commit", "-qm", f"prepare {version}")
        self.git("push", "-q", "origin", "main")

    def create_release_tag(self, version: str) -> None:
        release_guard.create_release_tag(
            self.work,
            version,
            "origin",
            "main",
            [sys.executable, "-c", "raise SystemExit(0)"],
            f"Release {version}",
        )

    def remote_has_tag(self, version: str) -> bool:
        result = _run(
            self.work,
            "git",
            f"--git-dir={self.remote}",
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/tags/{version}",
            check=False,
        )
        return result.returncode == 0


@pytest.fixture
def release_repo(tmp_path: Path) -> ReleaseRepo:
    remote = tmp_path / "remote.git"
    _run(tmp_path, "git", "init", "--bare", "-q", str(remote))
    work = tmp_path / "work"
    _run(tmp_path, "git", "clone", "-q", str(remote), str(work))
    _run(work, "git", "config", "user.email", "release@example.test")
    _run(work, "git", "config", "user.name", "Release Test")
    _run(work, "git", "switch", "-c", "main")
    repo = ReleaseRepo(work, remote)
    repo.write_changelog("1.0.0")
    (work / "payload.txt").write_text("initial\n", encoding="utf-8")
    repo.git("add", "CHANGELOG.md", "payload.txt")
    repo.git("commit", "-qm", "initial")
    repo.git("push", "-q", "-u", "origin", "main")
    repo.git("tag", "-a", "v1.0.0", "-m", "v1.0.0")
    repo.git("push", "-q", "origin", "v1.0.0")
    return repo


@pytest.mark.parametrize(
    "value", ["2.3.0", "v2.3", "v2.3.0-rc1", "v02.3.0", "v2.03.0"])
def test_parse_version_rejects_non_strict_semver(value: str):
    """Removing strict SemVer validation must make malformed release names fail."""
    with pytest.raises(release_guard.ReleaseError):
        release_guard.parse_version(value)


def test_parse_version_returns_numeric_tuple():
    """Version ordering must be numeric rather than lexical."""
    assert release_guard.parse_version("v12.3.40") == (12, 3, 40)


def test_preflight_requires_dated_changelog_section(release_repo: ReleaseRepo):
    """A tag without release notes would publish an undocumented version."""
    with pytest.raises(release_guard.ReleaseError, match="CHANGELOG"):
        release_repo.preflight("v1.0.1")


def test_preflight_refuses_dirty_worktree(release_repo: ReleaseRepo):
    """A release must not omit uncommitted files from its tag."""
    release_repo.prepare_release("v1.0.1")
    (release_repo.work / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(release_guard.ReleaseError, match="clean"):
        release_repo.preflight("v1.0.1")


def test_preflight_refuses_wrong_branch(release_repo: ReleaseRepo):
    """Creating a release from a feature branch bypasses reviewed main."""
    release_repo.prepare_release("v1.0.1")
    release_repo.git("switch", "-c", "feature")
    with pytest.raises(release_guard.ReleaseError, match="main"):
        release_repo.preflight("v1.0.1")


def test_preflight_refuses_remote_divergence(release_repo: ReleaseRepo):
    """A clean but unpushed commit must not become a public release."""
    release_repo.prepare_release("v1.0.1")
    (release_repo.work / "local-only.txt").write_text("x\n", encoding="utf-8")
    release_repo.git("add", "local-only.txt")
    release_repo.git("commit", "-qm", "local only")
    with pytest.raises(release_guard.ReleaseError, match="origin/main"):
        release_repo.preflight("v1.0.1")


def test_preflight_refuses_existing_tag(release_repo: ReleaseRepo):
    """An existing version is immutable and cannot be recreated."""
    release_repo.prepare_release("v1.0.1")
    release_repo.git("tag", "-a", "v1.0.1", "-m", "already exists")
    with pytest.raises(release_guard.ReleaseError, match="already exists"):
        release_repo.preflight("v1.0.1")


def test_preflight_refuses_non_incrementing_version(release_repo: ReleaseRepo):
    """A release version must advance beyond every published SemVer tag."""
    release_repo.write_changelog("0.9.9", "1.0.0")
    release_repo.git("add", "CHANGELOG.md")
    release_repo.git("commit", "-qm", "prepare old version")
    release_repo.git("push", "-q", "origin", "main")
    with pytest.raises(release_guard.ReleaseError, match="greater"):
        release_repo.preflight("v0.9.9")


def test_preflight_refuses_failed_verification(release_repo: ReleaseRepo):
    """A failing full verification command must block tag creation."""
    release_repo.prepare_release("v1.0.1")
    with pytest.raises(release_guard.ReleaseError, match="verification"):
        release_repo.preflight(
            "v1.0.1",
            [sys.executable, "-c", "raise SystemExit(7)"],
        )


def test_preflight_accepts_clean_synced_verified_release(
        release_repo: ReleaseRepo):
    """All guards together admit a reviewed and verified main commit."""
    release_repo.prepare_release("v1.0.1")
    release_repo.preflight("v1.0.1")


def test_create_release_tag_is_annotated_and_local_only(
        release_repo: ReleaseRepo):
    """The guard may create a local tag but must never push it."""
    release_repo.prepare_release("v1.0.1")
    release_repo.create_release_tag("v1.0.1")
    assert release_repo.git("cat-file", "-t", "v1.0.1") == "tag"
    assert release_repo.remote_has_tag("v1.0.1") is False


def test_validate_pushed_tag_accepts_tagged_remote_main(
        release_repo: ReleaseRepo):
    """CI accepts an exact immutable tag on the fetched release branch."""
    release_repo.prepare_release("v1.0.1")
    release_repo.git("tag", "-a", "v1.0.1", "-m", "Release v1.0.1")
    release_repo.git("push", "-q", "origin", "v1.0.1")
    release_guard.validate_pushed_tag(
        release_repo.work, "v1.0.1", "origin", "main")


def test_validate_pushed_tag_refuses_commit_outside_remote_main(
        release_repo: ReleaseRepo):
    """A manually tagged, unreviewed local commit cannot become a release."""
    release_repo.prepare_release("v1.0.1")
    (release_repo.work / "unreviewed.txt").write_text(
        "not pushed\n", encoding="utf-8")
    release_repo.git("add", "unreviewed.txt")
    release_repo.git("commit", "-qm", "unreviewed")
    release_repo.git("tag", "-a", "v1.0.1", "-m", "Release v1.0.1")
    with pytest.raises(release_guard.ReleaseError, match="origin/main"):
        release_guard.validate_pushed_tag(
            release_repo.work, "v1.0.1", "origin", "main")


def test_validate_pushed_tag_refuses_lightweight_tag(
        release_repo: ReleaseRepo):
    """Release tags carry metadata and therefore must be annotated objects."""
    release_repo.prepare_release("v1.0.1")
    release_repo.git("tag", "v1.0.1")
    with pytest.raises(release_guard.ReleaseError, match="annotated"):
        release_guard.validate_pushed_tag(
            release_repo.work, "v1.0.1", "origin", "main")


def test_cli_check_accepts_explicit_verification_command(
        release_repo: ReleaseRepo):
    """Maintainers can run a deterministic preflight without shell evaluation."""
    release_repo.prepare_release("v1.0.1")
    result = _run(
        release_repo.work,
        sys.executable,
        str(ROOT / "scripts" / "release_guard.py"),
        "check",
        "v1.0.1",
        "--remote",
        "origin",
        "--branch",
        "main",
        "--verify-command",
        sys.executable,
        "-c",
        "raise SystemExit(0)",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_tag_creates_annotated_local_tag(release_repo: ReleaseRepo):
    """The CLI preserves the deliberate manual push boundary."""
    release_repo.prepare_release("v1.0.1")
    result = _run(
        release_repo.work,
        sys.executable,
        str(ROOT / "scripts" / "release_guard.py"),
        "tag",
        "v1.0.1",
        "--remote",
        "origin",
        "--branch",
        "main",
        "--message",
        "Release v1.0.1",
        "--verify-command",
        sys.executable,
        "-c",
        "raise SystemExit(0)",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert release_repo.git("cat-file", "-t", "v1.0.1") == "tag"
    assert release_repo.remote_has_tag("v1.0.1") is False


def test_cli_validate_tag_accepts_remote_main_release(
        release_repo: ReleaseRepo):
    """The workflow-facing command validates the pushed immutable tag."""
    release_repo.prepare_release("v1.0.1")
    release_repo.git("tag", "-a", "v1.0.1", "-m", "Release v1.0.1")
    release_repo.git("push", "-q", "origin", "v1.0.1")
    result = _run(
        release_repo.work,
        sys.executable,
        str(ROOT / "scripts" / "release_guard.py"),
        "validate-tag",
        "v1.0.1",
        "--remote",
        "origin",
        "--branch",
        "main",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_reports_contract_error_without_traceback(tmp_path: Path):
    """A malformed maintainer command returns one actionable error."""
    result = _run(
        tmp_path,
        sys.executable,
        str(ROOT / "scripts" / "release_guard.py"),
        "check",
        "not-a-version",
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr.startswith("ERROR:")
    assert "Traceback" not in result.stderr


def test_release_workflow_runs_tests_and_guard_before_release():
    """Tag publication repeats verification before creating a release."""
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8")
    full_tests = (
        'uv run --no-project --with "mcp<2" --with pytest --with lxml '
        "--with openpyxl --with xlrd pytest tests/ -q"
    )
    guard = 'python scripts/release_guard.py validate-tag "$TAG"'
    publish = 'gh release create "$TAG"'
    assert "fetch-depth: 0" in workflow
    assert full_tests in workflow
    assert guard in workflow
    assert workflow.index(full_tests) < workflow.index(guard)
    assert workflow.index(guard) < workflow.index(publish)
