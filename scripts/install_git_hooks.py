# /// script
# dependencies = []
# ///
"""Поставить глобальный commit-msg хук, который срезает соавторство/атрибуцию Claude/Anthropic из сообщений
коммитов. Маленький org-agnostic установщик: всю остальную git-настройку (идентичность по площадкам, токен)
разработчик делает стандартными командами git — см. docs/git.md.

Идемпотентно, кросс-платформенно, только стандартная библиотека:
  - core.hooksPath: берётся существующий; если не задан — ставится ~/.git-global-hooks.
  - копирует scripts/git-hooks/* в этот каталог; чужой commit-msg НЕ затирает (предупреждает).

Запуск:  uv run scripts/install_git_hooks.py   (или python scripts/install_git_hooks.py)
"""
import os, shutil, subprocess, sys
from pathlib import Path

MARKER = "claude-no-coauthor"
SRC = Path(__file__).resolve().parent / "git-hooks"


def gget(key):
    r = subprocess.run(["git", "config", "--global", "--get", key], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def gset(key, value):
    subprocess.run(["git", "config", "--global", key, value], capture_output=True, text=True)


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
    dst.mkdir(parents=True, exist_ok=True)

    for src_hook in SRC.iterdir():
        if not src_hook.is_file():
            continue
        target = dst / src_hook.name
        if target.exists():
            cur = target.read_text(encoding="utf-8", errors="ignore")
            if MARKER in cur:
                print(f"[ok] {src_hook.name}: уже стоит наш хук")
            else:
                print(f"[!] {src_hook.name}: на машине уже есть ДРУГОЙ хук — не затираю. "
                      f"Допиши строку фильтрации из {src_hook} вручную или объедини.", file=sys.stderr)
            continue
        shutil.copyfile(src_hook, target)
        try:
            os.chmod(target, 0o755)
        except OSError:
            pass
        print(f"[ok] поставлен хук {src_hook.name} -> {target}")

    print("[готово] коммиты из Claude Code теперь без соавторства/атрибуции Claude. "
          "Идентичность площадок и токен push — см. docs/git.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
