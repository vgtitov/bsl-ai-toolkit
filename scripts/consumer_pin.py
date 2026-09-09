#!/usr/bin/env python3
"""Validate or atomically update one consumer-owned toolkit pin."""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


STRICT_VERSION = re.compile(
    r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class PinError(RuntimeError):
    """Consumer pin contract violation."""


def _require_version(value: str) -> str:
    if not STRICT_VERSION.fullmatch(value):
        raise PinError(f"invalid strict SemVer tag: {value}")
    return value


def read_pin(pin_file: Path) -> str:
    try:
        lines = pin_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise PinError(f"cannot read pin file {pin_file}: {error}") from error
    if len(lines) != 1:
        raise PinError(
            f"pin file {pin_file} must contain exactly one SemVer line")
    return _require_version(lines[0])


def upstream_has_tag(repo: str, tag: str) -> bool:
    _require_version(tag)
    result = subprocess.run(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--tags",
            repo,
            f"refs/tags/{tag}",
            f"refs/tags/{tag}^{{}}",
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        reason = result.stderr.strip() or result.stdout.strip()
        detail = f": {reason}" if reason else ""
        raise PinError(
            f"upstream tag {tag} is unavailable in {repo}{detail}")
    if not result.stdout.strip():
        raise PinError(f"upstream tag {tag} is unavailable in {repo}")
    return True


def check_pin(repo: str, pin_file: Path) -> str:
    tag = read_pin(pin_file)
    upstream_has_tag(repo, tag)
    return tag


def bump_pin(repo: str, pin_file: Path, tag: str) -> None:
    read_pin(pin_file)
    _require_version(tag)
    upstream_has_tag(repo, tag)

    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            dir=pin_file.parent,
            prefix=f".{pin_file.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(raw_path)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{tag}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, pin_file.stat().st_mode)
        os.replace(temporary_path, pin_file)
        temporary_path = None
    except OSError as error:
        raise PinError(f"cannot replace pin file {pin_file}: {error}") from error
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or update one consumer-owned SemVer pin.")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="validate the current pin")
    check.add_argument("--repo", required=True)
    check.add_argument("--pin-file", required=True, type=Path)

    bump = commands.add_parser(
        "bump", help="validate and atomically update the pin")
    bump.add_argument("--repo", required=True)
    bump.add_argument("--pin-file", required=True, type=Path)
    bump.add_argument("--tag", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            tag = check_pin(args.repo, args.pin_file)
            print(f"OK: {args.pin_file} pins existing upstream tag {tag}")
        else:
            bump_pin(args.repo, args.pin_file, args.tag)
            print(f"OK: {args.pin_file} now pins {args.tag}")
    except PinError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
