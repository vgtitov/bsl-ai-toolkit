"""Тесты установщика глобальных git-хуков (scripts/install_git_hooks.py).

Ключевая логика — распознавание ШТАТНОГО pre-push от `git lfs install`: его можно заменить своим
(наш сам вызывает git lfs pre-push), а любой ДРУГОЙ существующий хук затирать нельзя. Механизм
машинно-широкий: ошибка здесь либо молча не поставит защиту, либо снесёт чужой хук.

Запуск:  python -m pytest -q tests/test_install_git_hooks.py
"""
import importlib
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"

STOCK_LFS_HOOK = (
    "#!/bin/sh\n"
    'command -v git-lfs >/dev/null 2>&1 || { printf >&2 "\\n%s\\n\\n" "This repository is '
    "configured for Git LFS but 'git-lfs' was not found on your path.\"; exit 2; }\n"
    'git lfs pre-push "$@"\n'
)


def _mod():
    sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.reload(importlib.import_module("install_git_hooks"))


def test_our_hook_not_treated_as_stock_lfs():
    """Наш хук содержит маркер — трогать его не надо, он уже стоит."""
    m = _mod()
    ours = (SCRIPTS_DIR / "git-hooks" / "pre-push").read_text(encoding="utf-8")
    assert m.MARKER in ours, "в нашем pre-push должен быть маркер, иначе установка не идемпотентна"
    assert m.is_stock_lfs_hook("pre-push", ours) is False


def test_stock_lfs_hook_recognised():
    """Штатный хук от git lfs install заменяем: наш сам вызывает git lfs pre-push."""
    assert _mod().is_stock_lfs_hook("pre-push", STOCK_LFS_HOOK) is True


def test_foreign_hook_not_replaced():
    """Чужой pre-push не трогаем — в нём может быть что угодно."""
    foreign = "#!/bin/sh\n# самописный хук команды\n./ci/validate.sh || exit 1\n"
    assert _mod().is_stock_lfs_hook("pre-push", foreign) is False


def test_other_hook_names_never_match():
    """Признак завязан на pre-push: commit-msg/pre-commit заменять по этому правилу нельзя."""
    m = _mod()
    for name in ("commit-msg", "pre-commit", "post-merge"):
        assert m.is_stock_lfs_hook(name, STOCK_LFS_HOOK) is False


def test_our_pre_push_is_safe_without_lfs_repo():
    """Глобальный хук не должен блокировать push там, где LFS не используется.

    Проверяем на тексте: безусловного `exit 2` при отсутствии git-lfs быть не должно, выход
    только после проверки filter=lfs в .gitattributes.
    """
    ours = (SCRIPTS_DIR / "git-hooks" / "pre-push").read_text(encoding="utf-8")
    assert "filter=lfs" in ours, "нужна проверка, что репозиторий реально использует LFS"
    # позиция: сначала проверка filter=lfs, потом единственный exit 2
    assert ours.index("filter=lfs") < ours.index("exit 2")


def test_repo_hook_is_invoked_before_lfs():
    """Проверка репозитория идёт до LFS — иначе бинарники уедут перед отказом push."""
    ours = (SCRIPTS_DIR / "git-hooks" / "pre-push").read_text(encoding="utf-8")
    assert ours.index("scripts/hooks/pre-push") < ours.index("git lfs pre-push")


def _init_repo(path, gitattributes=None):
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, cwd=path, capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (path / "f.txt").write_text("x", encoding="utf-8")
    if gitattributes is not None:
        (path / ".gitattributes").write_text(gitattributes, encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "init")
    return path


def _repo_uses_lfs(path):
    """Та же проверка, которой пользуется хук: есть ли filter=lfs в .gitattributes."""
    r = subprocess.run(["git", "grep", "-qI", "--no-color", "-e", "filter=lfs",
                        "--", ".gitattributes", "*/.gitattributes"],
                       cwd=path, capture_output=True, text=True)
    return r.returncode == 0


def test_lfs_usage_detection(tmp_path):
    """Ключ к безопасности глобального хука: блокируем push без git-lfs ТОЛЬКО в LFS-репозитории.

    Отсутствие самого git-lfs в процессе теста смоделировать нечем (он ставится в системный PATH),
    поэтому проверяем ровно то условие, по которому хук решает — блокировать или молча пропустить.
    """
    without = _init_repo(tmp_path / "plain")
    with_lfs = _init_repo(tmp_path / "lfs", "*.bin filter=lfs diff=lfs merge=lfs -text\n")
    assert _repo_uses_lfs(without) is False, "в репозитории без LFS хук обязан пропускать push"
    assert _repo_uses_lfs(with_lfs) is True, "в репозитории с LFS хук обязан требовать git-lfs"
