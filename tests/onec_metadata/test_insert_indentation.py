# DISCIPLINE_ALLOW_TEST_EDIT: golden-регресс на ОТСТУП вставки (последний ребёнок)
"""Регресс: вставленный элемент получает отступ собрата, а не закрывающего тега.

Прежние «byte_perfect» тесты проверяли лишь стабильность round-trip и не ловили
неверный отступ, когда образец — последний ребёнок контейнера. Здесь сверяем
отступ добавленной строки с отступом существующего собрата (golden).
"""
import shutil
from pathlib import Path

from onec_metadata.ops.add_child import add_child
from onec_metadata.ops.attribute_add import add_attribute
from onec_metadata.ops.subsystem import add_content

FIX = Path(__file__).parent / "fixtures"


def _indent(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def _line_with(text: str, token: str) -> str:
    for line in text.splitlines():
        if token in line:
            return line
    raise AssertionError(f"нет строки с {token!r}")


def test_subsystem_new_item_indent_matches_sibling(tmp_path):
    work = tmp_path / "subsystem.xml"
    shutil.copy(FIX / "subsystem.xml", work)
    add_content(work, "Document.Заказ")
    txt = work.read_text(encoding="utf-8")
    sibling = _line_with(txt, "Report.Отчет1")
    new = _line_with(txt, "Document.Заказ")
    assert _indent(new) == _indent(sibling) == "\t\t\t\t"


def test_add_child_last_child_indent_matches_sibling(tmp_path):
    # catalog.xml: оба Attribute — последние дети ChildObjects (нет иного тега после)
    work = tmp_path / "catalog.xml"
    shutil.copy(FIX / "catalog.xml", work)
    # DISCIPLINE_ALLOW_TEST_EDIT: убрать неиспользуемую переменную
    add_child(work, "Attribute", "Реквизит3", "Р3", type_ref="xs:string")
    txt = work.read_text(encoding="utf-8")
    # отступ открывающего тега существующего и нового Attribute должен совпадать
    lines = [l for l in txt.splitlines() if "<Attribute uuid=" in l]
    assert len(lines) == 3
    assert _indent(lines[0]) == _indent(lines[-1]) == "\t\t\t"


def test_attribute_add_last_child_indent(tmp_path):
    work = tmp_path / "catalog.xml"
    shutil.copy(FIX / "catalog.xml", work)
    add_attribute(work, "Реквизит9", "xs:string", "Р9")
    txt = work.read_text(encoding="utf-8")
    lines = [l for l in txt.splitlines() if "<Attribute uuid=" in l]
    assert _indent(lines[0]) == _indent(lines[-1]) == "\t\t\t"


def test_subsystem_edt_new_content_indent(tmp_path):
    work = tmp_path / "subsystem.mdo"
    shutil.copy(FIX / "subsystem.mdo", work)
    add_content(work, "Document.Заказ")
    txt = work.read_text(encoding="utf-8")
    sibling = _line_with(txt, "Report.Отчет1")
    new = _line_with(txt, "Document.Заказ")
    assert _indent(new) == _indent(sibling) == "  "
