# /// script
# dependencies = []
# ///
"""Поставить глобальные git-хуки:
  - commit-msg — удаляет строки с названиями/атрибуцией AI-систем;
  - pre-commit — bsl-guard: блокирует staged *.bsl с обращением к БД в цикле (Запрос…Выполнить()/
    ПолучитьОбъект/.Ссылка. в Пока|Для). В чужих репозиториях (нет detector'а) молча пропускает.
Маленький org-agnostic установщик: всю остальную git-настройку (идентичность по площадкам, токен)
разработчик делает стандартными командами git — см. docs/git.md.

Идемпотентно, кросс-платформенно, только стандартная библиотека:
  - core.hooksPath: берётся существующий; если не задан — ставится ~/.git-global-hooks.
  - при каждом onboarding обновляет принадлежащие toolkit hooks из scripts/git-hooks/*;
  - чужой hook НЕ затирает (предупреждает).

Запуск:  uv run scripts/install_git_hooks.py   (или python scripts/install_git_hooks.py)
"""
import os, shutil, subprocess, sys
from pathlib import Path

# Windows-консоль по умолчанию cp1251 — печать '→'/'—'/кириллицы роняет скрипт. Принудительно UTF-8.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MARKER = "claude-no-coauthor"
SRC = Path(__file__).resolve().parent / "git-hooks"


def gget(key):
    r = subprocess.run(["git", "config", "--global", "--get", key], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def gset(key, value):
    subprocess.run(["git", "config", "--global", key, value], capture_output=True, text=True)


def repo_local_hooks_dir() -> Path:
    """Return the physical repo-local hooks dir, ignoring global core.hooksPath."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("текущий каталог не является git-репозиторием")
    git_common_dir = Path(result.stdout.strip())
    if not git_common_dir.is_absolute():
        git_common_dir = Path.cwd() / git_common_dir
    return git_common_dir / "hooks"


def copy_hook(src_hook: Path, target: Path) -> bool:
    """Обновить свой хук; чужой существующий хук не перезаписывать."""
    if target.exists():
        cur = target.read_text(encoding="utf-8", errors="ignore")
        if MARKER not in cur:
            print(
                f"[!] {target.name}: на машине уже есть ДРУГОЙ хук — не затираю. "
                f"Допиши строку фильтрации из {src_hook} вручную или объедини.",
                file=sys.stderr,
            )
            return False
    shutil.copyfile(src_hook, target)
    try:
        os.chmod(target, 0o755)
    except OSError:
        pass
    print(f"[ok] обновлён хук {src_hook.name} -> {target}")
    return True


def main():
    if not SRC.is_dir():
        print(f"[!] нет каталога с хуками: {SRC}", file=sys.stderr)
        return 1
    hooks_dir = gget("core.hooksPath")
    if hooks_dir:
        dst = Path(os.path.expanduser(hooks_dir))
        print(f"[i] core.hooksPath уже задан: {dst}")
    else:
        dst = Path.home() / ".git-global-hooks"
        gset("core.hooksPath", dst.as_posix())
        print(f"[ok] core.hooksPath = {dst.as_posix()}")
    dispatcher = dst / "commit-msg"
    if dispatcher.exists() and "operkontur-global-dispatcher" in dispatcher.read_text(
        encoding="utf-8",
        errors="ignore",
    ):
        try:
            dst = repo_local_hooks_dir()
        except RuntimeError as exc:
            print(f"[!] {exc}", file=sys.stderr)
            return 1
        print(f"[i] обнаружен общий dispatcher; toolkit hooks → {dst}")
    dst.mkdir(parents=True, exist_ok=True)

    all_installed = True
    for src_hook in SRC.iterdir():
        if not src_hook.is_file():
            continue
        target = dst / src_hook.name
        all_installed = copy_hook(src_hook, target) and all_installed

    if not all_installed:
        print(
            "[ошибка] onboarding hooks выполнен частично; устрани конфликт и повтори.",
            file=sys.stderr,
        )
        return 2
    print("[готово] сообщения коммитов теперь без названий/атрибуции AI-систем. "
          "Идентичность площадок и токен push — см. docs/git.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
