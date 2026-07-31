#!/usr/bin/env python3
"""Fail-closed checks for immutable bsl-ai-toolkit releases."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


STRICT_VERSION = re.compile(
    r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
DEFAULT_VERIFY_COMMAND = [
    "uv",
    "run",
    "--python",
    "3.12",
    "--no-project",
    "--with",
    "mcp<2",
    "--with",
    "pytest",
    "--with",
    "lxml",
    "--with",
    "openpyxl",
    "--with",
    "xlrd",
    "pytest",
    "tests/",
    "-q",
]


class ReleaseError(RuntimeError):
    """Release contract violation."""


def parse_version(value: str) -> tuple[int, int, int]:
    match = STRICT_VERSION.fullmatch(value)
    if not match:
        raise ReleaseError(f"invalid strict SemVer tag: {value}")
    return tuple(int(part) for part in match.groups())


def _require_changelog(root: Path, version: str) -> None:
    number = version.removeprefix("v")
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8") if changelog.is_file() else ""
    heading = re.compile(
        rf"^## \[{re.escape(number)}\] - \d{{4}}-\d{{2}}-\d{{2}}$",
        re.MULTILINE,
    )
    if not heading.search(text):
        raise ReleaseError(
            f"CHANGELOG.md has no dated section for {number}")


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {reason}")
    return result.stdout.strip()


def _fetch_release_ref(root: Path, remote: str, branch: str) -> None:
    _run_git(
        root,
        "fetch",
        "--quiet",
        "--tags",
        remote,
        f"refs/heads/{branch}:refs/remotes/{remote}/{branch}",
    )


def _require_clean_release_branch(
        root: Path, remote: str, branch: str) -> None:
    if _run_git(root, "status", "--porcelain"):
        raise ReleaseError("release worktree must be clean")
    actual_branch = _run_git(root, "branch", "--show-current")
    if actual_branch != branch:
        raise ReleaseError(
            f"release branch must be {branch}, got {actual_branch or 'detached HEAD'}")
    _fetch_release_ref(root, remote, branch)
    head = _run_git(root, "rev-parse", "HEAD")
    remote_head = _run_git(
        root, "rev-parse", f"refs/remotes/{remote}/{branch}")
    if head != remote_head:
        raise ReleaseError(
            f"HEAD does not match {remote}/{branch}: {head} != {remote_head}")


def _require_newer_unused_tag(root: Path, version: str) -> None:
    target = parse_version(version)
    tag_names = _run_git(root, "tag", "--list").splitlines()
    if version in tag_names:
        raise ReleaseError(f"tag {version} already exists")
    semver_tags = []
    for name in tag_names:
        try:
            semver_tags.append((parse_version(name), name))
        except ReleaseError:
            continue
    if semver_tags:
        latest_value, latest_name = max(semver_tags)
        if target <= latest_value:
            raise ReleaseError(
                f"release {version} must be greater than {latest_name}")


def _run_verification(root: Path, command: list[str]) -> None:
    if not command:
        raise ReleaseError("verification command must not be empty")
    result = subprocess.run(
        command,
        cwd=root,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip()
        suffix = f": {output}" if output else ""
        raise ReleaseError(
            f"verification command failed with exit {result.returncode}{suffix}")


def preflight_release(
        root: Path,
        version: str,
        remote: str,
        branch: str,
        verify_command: list[str]) -> None:
    parse_version(version)
    _require_clean_release_branch(root, remote, branch)
    _require_newer_unused_tag(root, version)
    _require_changelog(root, version)
    _run_verification(root, verify_command)


def create_release_tag(
        root: Path,
        version: str,
        remote: str,
        branch: str,
        verify_command: list[str],
        message: str) -> None:
    preflight_release(root, version, remote, branch, verify_command)
    _run_git(root, "tag", "-a", version, "-m", message)


def validate_pushed_tag(
        root: Path,
        version: str,
        remote: str,
        branch: str) -> None:
    parse_version(version)
    _require_changelog(root, version)
    _fetch_release_ref(root, remote, branch)
    tag_commit = _run_git(root, "rev-parse", f"{version}^{{}}")
    head = _run_git(root, "rev-parse", "HEAD")
    if tag_commit != head:
        raise ReleaseError(
            f"tag {version} points to {tag_commit}, current HEAD is {head}")
    remote_ref = f"refs/remotes/{remote}/{branch}"
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", tag_commit, remote_ref],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if ancestry.returncode != 0:
        raise ReleaseError(
            f"tag {version} commit is not on {remote}/{branch}")


def _add_release_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("version")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and create fail-closed release tags.")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="run the release preflight")
    _add_release_location(check)
    check.add_argument(
        "--verify-command",
        nargs=argparse.REMAINDER,
        default=None,
        help="verification argv; this option must be last",
    )

    tag = commands.add_parser(
        "tag", help="run the preflight and create a local annotated tag")
    _add_release_location(tag)
    tag.add_argument("--message")
    tag.add_argument(
        "--verify-command",
        nargs=argparse.REMAINDER,
        default=None,
        help="verification argv; this option must be last",
    )

    validate = commands.add_parser(
        "validate-tag", help="validate a pushed tag in release CI")
    _add_release_location(validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path.cwd()
    try:
        if args.command == "check":
            preflight_release(
                root,
                args.version,
                args.remote,
                args.branch,
                args.verify_command or DEFAULT_VERIFY_COMMAND,
            )
            print(f"OK: {args.version} is ready for a local release tag")
        elif args.command == "tag":
            create_release_tag(
                root,
                args.version,
                args.remote,
                args.branch,
                args.verify_command or DEFAULT_VERIFY_COMMAND,
                args.message or f"Release {args.version}",
            )
            print(
                f"OK: created local annotated tag {args.version}; "
                f"review it, then push it explicitly")
        else:
            validate_pushed_tag(
                root, args.version, args.remote, args.branch)
            print(
                f"OK: {args.version} points to the checked-out "
                f"{args.remote}/{args.branch} release commit")
    except ReleaseError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
