"""pre-commit (scripts/git-hooks/pre-commit): пути с пробелом и кавычкой не обходят и не ломают проверку."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "git-hooks"
LOOP = ("Процедура П()\n    Для Каждого Стр Из Таблица Цикл\n        Запрос = Новый Запрос;\n"
        "        Результат = Запрос.Выполнить();\n    КонецЦикла;\nКонецПроцедуры\n")
CLEAN = "Процедура П()\nКонецПроцедуры\n"


def _commit(tmp_path: Path, rel: str, text: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ONEC_TOOLKIT_DIR=str(ROOT), GIT_CONFIG_GLOBAL=os.devnull,
               GIT_CONFIG_SYSTEM=os.devnull, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e.x",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e.x")

    def git(*a):
        return subprocess.run(["git", "-c", f"core.hooksPath={HOOKS}", *a], cwd=tmp_path, env=env,
                              capture_output=True, text=True, encoding="utf-8")
    git("init", "-q")
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git("add", rel)
    return git("commit", "-q", "-m", "t")


def test_query_in_loop_in_path_with_space_blocks_commit(tmp_path):
    r = _commit(tmp_path, "Общие модули/Мой модуль/Module.bsl", LOOP)
    assert r.returncode != 0, "файл с пробелом в пути прошёл мимо bsl_guard"
    assert "bsl-guard" in r.stderr


def test_clean_file_with_apostrophe_in_path_is_not_blocked(tmp_path):
    r = _commit(tmp_path, "q/it's.bsl", CLEAN)
    assert r.returncode == 0, r.stderr
