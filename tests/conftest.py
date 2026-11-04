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


import pytest  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture(autouse=True)
def _no_real_limit_pause(monkeypatch, tmp_path):
    """Состояние Copilot (пауза лимита, профиль входа) лежит в ~/.cache разработчика: без изоляции оно роняло тесты."""
    import second_opinion

    monkeypatch.setattr(second_opinion, "PAUSE_DIR", tmp_path / "limit_skip_conftest")
    # вход в Copilot у разработчика (профиль в ~/.cache) не должен менять результат тестов «доверенного ассистента нет»
    monkeypatch.setattr(second_opinion, "COPILOT_PROFILE", tmp_path / "no_copilot_profile")
