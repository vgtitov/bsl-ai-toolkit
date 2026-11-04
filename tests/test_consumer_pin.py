"""Consumer pin helper tests against a real local Git remote."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "consumer_pin", ROOT / "scripts" / "consumer_pin.py")
consumer_pin = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(consumer_pin)


def _run(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args), cwd=cwd, text=True, capture_output=True, check=check)


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    remote = tmp_path / "upstream.git"
    _run(tmp_path, "git", "init", "--bare", "-q", str(remote))
    work = tmp_path / "work"
    _run(tmp_path, "git", "clone", "-q", str(remote), str(work))
    _run(work, "git", "config", "user.email", "pin@example.test")
    _run(work, "git", "config", "user.name", "Pin Test")
    _run(work, "git", "switch", "-c", "main")
    (work / "payload.txt").write_text("payload\n", encoding="utf-8")
    _run(work, "git", "add", "payload.txt")
    _run(work, "git", "commit", "-qm", "initial")
    _run(work, "git", "push", "-q", "-u", "origin", "main")
    _run(work, "git", "tag", "-a", "v1.2.3", "-m", "v1.2.3")
    _run(work, "git", "push", "-q", "origin", "v1.2.3")
    return remote


def test_check_accepts_existing_annotated_tag(
        upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.3\n", encoding="utf-8")
    assert consumer_pin.check_pin(str(upstream), pin) == "v1.2.3"


@pytest.mark.parametrize(
    "content",
    ["main\n", "v1.2\n", "v01.2.3\n", "v1.2.3\nextra\n", "\n"],
)
def test_check_rejects_invalid_pin(
        upstream: Path, tmp_path: Path, content: str):
    pin = tmp_path / "toolkit.ref"
    pin.write_text(content, encoding="utf-8")
    with pytest.raises(consumer_pin.PinError):
        consumer_pin.check_pin(str(upstream), pin)


def test_check_rejects_unknown_upstream_tag(
        upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.4\n", encoding="utf-8")
    with pytest.raises(consumer_pin.PinError, match="upstream"):
        consumer_pin.check_pin(str(upstream), pin)


def test_bump_changes_only_pin_file(upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    neighbor = tmp_path / "profile.json"
    pin.write_text("v1.2.2\n", encoding="utf-8")
    neighbor.write_text('{"keep": true}\n', encoding="utf-8")

    consumer_pin.bump_pin(str(upstream), pin, "v1.2.3")

    assert pin.read_text(encoding="utf-8") == "v1.2.3\n"
    assert neighbor.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_bump_unknown_target_leaves_pin_unchanged(
        upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.2\n", encoding="utf-8")

    with pytest.raises(consumer_pin.PinError, match="upstream"):
        consumer_pin.bump_pin(str(upstream), pin, "v1.2.4")

    assert pin.read_text(encoding="utf-8") == "v1.2.2\n"


def test_cli_check_reports_valid_pin(upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.3\n", encoding="utf-8")
    result = _run(
        tmp_path,
        sys.executable,
        str(ROOT / "scripts" / "consumer_pin.py"),
        "check",
        "--repo",
        str(upstream),
        "--pin-file",
        str(pin),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_error_is_actionable_without_traceback(
        upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("main\n", encoding="utf-8")
    result = _run(
        tmp_path,
        sys.executable,
        str(ROOT / "scripts" / "consumer_pin.py"),
        "check",
        "--repo",
        str(upstream),
        "--pin-file",
        str(pin),
        check=False,
    )
    assert result.returncode == 2
    assert result.stderr.startswith("ERROR:")
    assert "Traceback" not in result.stderr


def test_unreachable_upstream_warns_and_passes_unless_strict(tmp_path: Path, capsys):
    # Нет сети/VPN до апстрима: проверка наличия тега не должна блокировать чужой push.
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.3\n", encoding="utf-8")
    gone = str(tmp_path / "нет-такого.git")
    assert consumer_pin.check_pin(gone, pin) == "v1.2.3"
    assert "WARNING" in capsys.readouterr().err
    with pytest.raises(consumer_pin.PinError):
        consumer_pin.check_pin(gone, pin, strict=True)


def test_ls_remote_has_timeout_and_never_prompts(tmp_path: Path, monkeypatch):
    seen = {}

    def hang(cmd, env, timeout):
        seen.update(env=env, timeout=timeout)
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(consumer_pin, "_run_limited", hang)
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v1.2.3\n", encoding="utf-8")
    assert consumer_pin.check_pin("https://example.invalid/x.git", pin) == "v1.2.3"
    assert 0 < seen["timeout"] <= 60
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"
    with pytest.raises(consumer_pin.PinError, match="timeout"):
        consumer_pin.bump_pin("https://example.invalid/x.git", pin, "v1.2.4")   # bump всегда строгий


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX: группа процессов; Windows-ветка (taskkill /T) не проверена")
def test_timeout_kills_whole_process_tree(tmp_path: Path, monkeypatch):
    # git порождает транспорт (git-remote-https / ssh); убитый по таймауту родитель оставлял его висеть.
    import os
    import time
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pidfile = tmp_path / "child.pid"
    fake = bindir / "git"
    fake.write_text(f"#!/bin/sh\nsleep 60 &\necho $! > {pidfile}\nwait\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(consumer_pin, "LS_REMOTE_TIMEOUT", 1)
    started = time.monotonic()
    with pytest.raises(consumer_pin.UpstreamUnreachable):
        consumer_pin.upstream_has_tag("https://example.invalid/x.git", "v1.2.3")
    assert time.monotonic() - started < 15
    child = int(pidfile.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_missing_tag_still_fails_even_when_not_strict(upstream: Path, tmp_path: Path):
    pin = tmp_path / "toolkit.ref"
    pin.write_text("v9.9.9\n", encoding="utf-8")
    with pytest.raises(consumer_pin.PinError, match="upstream"):
        consumer_pin.check_pin(str(upstream), pin)


def test_ssh_batch_mode_only_for_default_openssh(monkeypatch):
    monkeypatch.delenv("GIT_SSH", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    assert consumer_pin._non_interactive_env()["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    monkeypatch.setenv("GIT_SSH_COMMAND", "plink -i /k")           # чужой клиент не трогаем
    assert consumer_pin._non_interactive_env()["GIT_SSH_COMMAND"] == "plink -i /k"
    monkeypatch.delenv("GIT_SSH_COMMAND")
    monkeypatch.setenv("GIT_SSH", "/w/ssh-wrapper")
    assert "GIT_SSH_COMMAND" not in consumer_pin._non_interactive_env()
