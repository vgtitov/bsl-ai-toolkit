"""Дневной лимит Copilot — не ответ: отдельный код выхода адаптера и переход к следующему звену цепочки."""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture(autouse=True)
def _isolated_pause(monkeypatch, tmp_path):
    """Пауза Copilot лежит в домашней папке разработчика: тесты не должны её писать и читать."""
    monkeypatch.setenv("AI_TOOLKIT_LIMIT_SKIP_DIR", str(tmp_path / "limit_skip"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def adapter():
    return _load("copilot_adapter")


@pytest.mark.parametrize("text", [
    "Please try again later.",
    "You've used your daily chat limit. Please try again later. Upgrade to keep going",
    "You have reached your limit for today.",
    "Вы достигли дневного лимита сообщений. Попробуйте позже",
    "Исчерпан суточный лимит чата",
])
def test_limit_phrases_detected(adapter, text):
    assert adapter.is_limit(text)


def test_normal_and_long_answers_are_not_limit(adapter):
    assert not adapter.is_limit("Нашёл два дефекта: запрос в цикле и пустое исключение.")
    assert not adapter.is_limit("")
    long_review = "В коде есть проверка daily chat limit для клиента. " * 20       # ревью про лимиты в чужом коде
    assert not adapter.is_limit(long_review)


def test_main_returns_limit_exit_code(adapter, monkeypatch, capsys):
    def boom(*a, **k):
        raise adapter.LimitError("You've used your daily chat limit.")
    monkeypatch.setattr(adapter, "ask", boom)
    monkeypatch.setattr(sys, "argv", ["copilot_adapter.py", "вопрос"])
    assert adapter.main() == adapter.EXIT_LIMIT == 75
    assert "Лимит Copilot" in capsys.readouterr().err


def test_second_opinion_skips_limit_and_names_reason(monkeypatch, tmp_path):
    so = _load("second_opinion")
    fake = tmp_path / "fake_copilot.py"
    fake.write_text("import sys\nsys.stderr.write('limit')\nprint('You used your daily chat limit.')\nsys.exit(75)\n",
                    encoding="utf-8")
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_CMD", f'"{sys.executable}" "{fake}"')
    assert so.copilot("вопрос") is None                     # текст лимита не принят за ответ
    assert "лимит" in so.COPILOT_LAST_WHY.lower()


def test_second_opinion_still_takes_normal_answer(monkeypatch, tmp_path):
    so = _load("second_opinion")
    fake = tmp_path / "ok_copilot.py"
    fake.write_text("print('P1: 0, P2: 0')\n", encoding="utf-8")
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_CMD", f'"{sys.executable}" "{fake}"')
    assert "P1: 0" in so.copilot("вопрос")
    assert so.COPILOT_LAST_WHY == ""


class _Btn:
    def __init__(self, page):
        self.page = page
        self.first = self

    def count(self):
        return 1

    def is_enabled(self):
        return self.page.woken

    def click(self):
        self.page.clicked = self.page.woken


class _Box:
    def __init__(self, page):
        self.page = page
        self.keys = []

    def press(self, key):
        self.keys.append(key)
        if key == "Backspace":
            self.page.woken = True                 # настоящие клавиши включают Send
        if key == "Enter":
            self.page.entered = True


class _Page:
    def __init__(self, woken=False, has_button=True):
        self.woken, self.clicked, self.entered, self.has_button = woken, False, False, has_button

    def get_by_role(self, role, name=None):
        return _Btn(self) if self.has_button else type("Z", (), {"count": lambda s: 0})()


def test_send_wakes_disabled_button_then_clicks(adapter, monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    page = _Page()
    box = _Box(page)
    adapter._send(page, box, wait_s=1)
    assert box.keys == ["End", "Space", "Backspace"] and page.clicked and not page.entered


def test_send_enabled_button_needs_no_keys(adapter, monkeypatch):
    monkeypatch.setattr(adapter.time, "sleep", lambda s: None)
    page = _Page(woken=True)
    box = _Box(page)
    adapter._send(page, box)
    assert box.keys == [] and page.clicked


def test_send_without_button_presses_enter(adapter):
    page = _Page(has_button=False)
    box = _Box(page)
    adapter._send(page, box)
    assert page.entered


def test_limit_phrase_lists_do_not_drift(adapter):
    so = _load("second_opinion")
    assert so.LIMIT_PHRASES == adapter.LIMIT_PHRASES and so.LIMIT_MAX == adapter.LIMIT_MAX == 500


def test_second_opinion_is_limit_threshold():
    so = _load("second_opinion")
    assert so.is_limit("You've used your daily chat limit.")
    assert not so.is_limit("Диф чист. " + "try again later " * 40)          # длиннее 500 знаков: это не страница лимита


def test_pause_skips_copilot_without_calling_it(monkeypatch, tmp_path):
    so = _load("second_opinion")
    monkeypatch.setenv("AI_TOOLKIT_LIMIT_SKIP_DIR", str(tmp_path / "skip"))
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_CMD", f'"{sys.executable}" -c "raise SystemExit(99)"')
    so.set_pause("copilot")
    assert so.paused_until("copilot") > 0
    assert so.copilot("вопрос") is None and "пауза" in so.COPILOT_LAST_WHY     # звали бы процесс, был бы код 99, не пауза


def test_limit_text_with_exit_zero_is_not_an_answer(monkeypatch, tmp_path):
    so = _load("second_opinion")
    monkeypatch.setenv("AI_TOOLKIT_LIMIT_SKIP_DIR", str(tmp_path / "skip"))
    fake = tmp_path / "lim.py"
    fake.write_text("print('You have reached your limit. Try again later.')\n", encoding="utf-8")
    monkeypatch.setenv("AI_TOOLKIT_COPILOT_CMD", f'"{sys.executable}" "{fake}"')
    assert so.copilot("вопрос") is None and so.paused_until("copilot") > 0


def test_hints_name_variables_and_commands_without_values(monkeypatch):
    so = _load("second_opinion")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("AI_TOOLKIT_COPILOT_CMD", raising=False)
    monkeypatch.setattr(so, "COPILOT_PROFILE", Path("/nonexistent/profile"))
    monkeypatch.setattr(so.shutil, "which", lambda x: "/bin/uv" if x == "uv" else None)
    h = so.hints()
    assert h.startswith("Что сделать: ")
    assert "codex login" in h and "OPENROUTER_API_KEY" in h and "copilot_setup.py --login" in h


def test_chain_ignores_codex_limit_text(monkeypatch):
    so = _load("second_opinion")
    monkeypatch.delenv("AI_TOOLKIT_COPILOT_CMD", raising=False)
    monkeypatch.setattr(so, "copilot_cmd", lambda: "")
    chain = (("notrain", lambda p, f: "You've used your daily chat limit."),)
    who, body = so.ask("q", chain=chain, codex=lambda *a, **k: "usage: try again later", risk="normal")
    assert who == ""                                        # лимит с кодом 0 не принят ни за ответ звена, ни за ответ Codex
