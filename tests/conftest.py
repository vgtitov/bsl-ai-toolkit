"""Общие настройки тестов. На Windows `sh` (из Git for Windows) может не быть в PATH сессии, например при запуске
через ssh: тесты git-хуков запускают его напрямую, поэтому добавляем каталоги Git в PATH, если `sh` не находится."""
import os
import shutil
import sys
from pathlib import Path

if sys.platform == "win32" and not shutil.which("sh"):
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        git = Path(root) / "Git"
        if (git / "bin" / "sh.exe").is_file():
            os.environ["PATH"] = f"{git / 'bin'}{os.pathsep}{git / 'usr' / 'bin'}{os.pathsep}{os.environ['PATH']}"
            break


import pytest


@pytest.fixture(autouse=True)
def _isolated_limit_pause(monkeypatch, tmp_path):
    """Пауза звена second_opinion лежит в домашней папке: настоящая пауза не должна ломать тесты, тесты не пишут в неё."""
    monkeypatch.setenv("AI_TOOLKIT_LIMIT_SKIP_DIR", str(tmp_path / "limit_skip"))
