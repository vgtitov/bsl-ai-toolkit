from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path


HOOK = Path(__file__).parents[1] / "scripts" / "git-hooks" / "commit-msg"
INSTALLER = HOOK.parents[1] / "install_git_hooks.py"
ONBOARD_SH = HOOK.parents[2] / "onboard" / "onboard.sh"
ONBOARD_PS1 = HOOK.parents[2] / "onboard" / "onboard.ps1"


def installer_module():
    spec = importlib.util.spec_from_file_location("onec_install_git_hooks", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_keeps_installer_ownership_marker() -> None:
    assert installer_module().MARKER in HOOK.read_text(encoding="utf-8")


def test_hook_removes_new_ai_system_names(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("Исправить импорт\nGenerated with Codex\nChatGPT review\n", encoding="utf-8")

    result = subprocess.run(["/bin/sh", str(HOOK), str(message)], check=False)

    assert result.returncode == 0
    assert message.read_text(encoding="utf-8") == "Исправить импорт\n"


def test_installer_refreshes_owned_hook_after_toolkit_update(tmp_path: Path) -> None:
    target = tmp_path / "commit-msg"
    target.write_text("#!/bin/sh\n# claude-no-coauthor\n# old version\n", encoding="utf-8")

    copied = installer_module().copy_hook(HOOK, target)

    assert copied is True
    assert target.read_text(encoding="utf-8") == HOOK.read_text(encoding="utf-8")


def test_installer_does_not_claim_global_dispatcher(tmp_path: Path) -> None:
    target = tmp_path / "commit-msg"
    original = "#!/bin/sh\n# operkontur-global-dispatcher\nexit 0\n"
    target.write_text(original, encoding="utf-8")

    copied = installer_module().copy_hook(HOOK, target)

    assert copied is False
    assert target.read_text(encoding="utf-8") == original


def test_installer_returns_failure_for_partial_hook_install(tmp_path: Path, monkeypatch) -> None:
    module = installer_module()
    src = tmp_path / "src"
    src.mkdir()
    (src / "commit-msg").write_text(
        "#!/bin/sh\n# claude-no-coauthor\n",
        encoding="utf-8",
    )
    dst = tmp_path / "hooks"
    dst.mkdir()
    (dst / "commit-msg").write_text("#!/bin/sh\n# foreign\n", encoding="utf-8")
    monkeypatch.setattr(module, "SRC", src)
    monkeypatch.setattr(module, "gget", lambda key: str(dst))

    assert module.main() != 0


def test_installer_uses_repo_local_hooks_behind_global_dispatcher(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = installer_module()
    src = tmp_path / "src"
    src.mkdir()
    (src / "commit-msg").write_text(
        "#!/bin/sh\n# claude-no-coauthor\n",
        encoding="utf-8",
    )
    global_hooks = tmp_path / "global-hooks"
    global_hooks.mkdir()
    (global_hooks / "commit-msg").write_text(
        "#!/bin/sh\n# operkontur-global-dispatcher\n",
        encoding="utf-8",
    )
    local_hooks = tmp_path / "repo" / ".git" / "hooks"
    monkeypatch.setattr(module, "SRC", src)
    monkeypatch.setattr(module, "gget", lambda key: str(global_hooks))
    monkeypatch.setattr(module, "repo_local_hooks_dir", lambda: local_hooks)

    assert module.main() == 0
    assert (local_hooks / "commit-msg").read_text(encoding="utf-8") == (
        src / "commit-msg"
    ).read_text(encoding="utf-8")


def test_shell_onboarding_does_not_swallow_hook_installer_failure() -> None:
    text = ONBOARD_SH.read_text(encoding="utf-8")
    hook_lines = [
        line for line in text.splitlines()
        if "install_git_hooks.py" in line and not line.lstrip().startswith("#")
    ]

    assert hook_lines
    assert all("|| warn" not in line for line in hook_lines)


def test_powershell_onboarding_checks_native_installer_exit_code() -> None:
    text = ONBOARD_PS1.read_text(encoding="utf-8")
    section = text.split('Say "8/8 Git:', 1)[1].split('Say "Готово.', 1)[0]

    assert "$LASTEXITCODE" in section
    assert "throw" in section


def test_hook_removes_standalone_provider_names(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("Исправить импорт\nReviewed by Copilot\nCursor\n", encoding="utf-8")

    result = subprocess.run(["/bin/sh", str(HOOK), str(message)], check=False)

    assert result.returncode == 0
    assert message.read_text(encoding="utf-8") == "Исправить импорт\n"


def test_hook_preserves_message_when_filter_command_fails(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    original = "Исправить импорт\n"
    message.write_text(original, encoding="utf-8")
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_grep = fake_bin / "grep"
    fake_grep.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")
    fake_grep.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        ["/bin/sh", str(HOOK), str(message)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert message.read_text(encoding="utf-8") == original
