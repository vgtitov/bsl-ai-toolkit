"""Тесты глобального commit-msg хука (scripts/git-hooks/commit-msg).

Фильтр — СТРУКТУРНЫЙ (git-трейлер или фраза с attribution-глаголом), а не по списку имён
AI-инструментов: список всегда неполный (в команде используют Claude, Copilot, ChatGPT и
другие — заранее все не перечислить), и бэйр-упоминание инструмента как ТЕМЫ коммита
("Скилл интеграции с Copilot") не должно резаться наравне с реальной атрибуцией.

Запуск:  python -m pytest -q tests/test_commit_msg_hook.py
"""
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "git-hooks" / "commit-msg"


def run_hook(tmp_path: Path, message: str) -> str:
    message_file = tmp_path / "COMMIT_EDITMSG"
    message_file.write_text(message, encoding="utf-8")
    result = subprocess.run(
        ["sh", str(HOOK), str(message_file)],
        check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return message_file.read_text(encoding="utf-8")


def test_removes_claude_attribution_trailer(tmp_path):
    cleaned = run_hook(
        tmp_path,
        "Исправить обработку очереди\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
    )
    assert cleaned == "Исправить обработку очереди\n\n"


def test_removes_attribution_from_tool_not_in_any_denylist(tmp_path):
    # Ключевой тест: "FutureNeuroAssistant" нигде не перечислен — фильтр обязан снять
    # атрибуцию по СТРУКТУРЕ строки, а не по знанию конкретного имени инструмента.
    message = (
        "Исправить обработку очереди\n"
        "Co-Authored-By: FutureNeuroAssistant Zeta <bot@futureneuro.example>\n"
        "Generated with FutureNeuroAssistant Zeta\n"
        "Обычная заметка\n"
    )
    cleaned = run_hook(tmp_path, message)
    assert cleaned == "Исправить обработку очереди\nОбычная заметка\n"


def test_preserves_commit_about_ai_tooling_as_a_topic(tmp_path):
    # Регрессия: коммит ПРО AI-инструмент (не от его лица) не должен резаться только
    # потому что содержит слово copilot/claude/chatgpt.
    message = (
        "Скилл интеграции с M365 Copilot и ChatGPT для ревью кода\n\n"
        "Проверил живьём через Claude Code: Copilot агентно ищет по почте.\n"
    )
    assert run_hook(tmp_path, message) == message


def test_preserves_ordinary_text_containing_ai_letters(tmp_path):
    message = "Maintain idempotency and repair retry handling\n"
    assert run_hook(tmp_path, message) == message


def test_empties_message_when_every_line_is_attribution(tmp_path):
    # Регрессия исходной реализации: `grep -v ... && mv` не переносил результат, когда
    # ВСЕ строки атрибуция (grep -v возвращает код 1 = "ничего не прошло", не ошибка) —
    # `&&` это интерпretировал как сбой и оставлял сообщение НЕизменённым.
    message = "Generated with Claude Code\nCo-Authored-By: Codex <noreply@openai.com>\n"
    assert run_hook(tmp_path, message) == ""
