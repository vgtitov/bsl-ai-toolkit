# DISCIPLINE_ALLOW_TEST_EDIT: новый red-набор TDD — form lint (EDT и Конфигуратор)
"""form lint: проверка формы на конвенции оформления — заголовки на всех языках, шрифты и цвета
только ссылками на элементы стиля, скрытая командная панель формы. Только чтение: файл не меняется."""
import shutil
from pathlib import Path

from onec_metadata.ops.form_lint import lint_form
from onec_metadata.ops.form_style import set_style

FIX = Path(__file__).parent / "fixtures"


def _copy(tmp_path, name, as_name):
    work = tmp_path / as_name
    shutil.copy(FIX / name, work)
    return work


def _codes(issues):
    return sorted({i.code for i in issues})


def test_edt_title_without_en_and_column_without_title(tmp_path):
    work = _copy(tmp_path, "form_style_edt.form", "Form.form")
    raw = work.read_bytes()
    issues = lint_form(work, langs=("ru", "en"))
    assert work.read_bytes() == raw                       # только чтение
    by_item = {(i.code, i.item) for i in issues}
    assert ("title-no-own", "ЗаказыНомер") in by_item     # колонка без своего заголовка
    assert ("command-bar", ".") in by_item                # командная панель формы не скрыта


def test_edt_literal_font_and_color_flagged_tokens_pass(tmp_path):
    work = _copy(tmp_path, "form_style_edt.form", "Form.form")
    set_style(work, "ДекорацияЗаголовок", {"font": "style:NormalTextFont,face=Gilroy,height=14",
                                           "textColor": "rgb:1,2,3"})
    set_style(work, "ДекорацияПодсказка", {"font": "style:примШрифтПодсказка",
                                           "textColor": "style:примЦветПодписи"})
    lit = {(i.code, i.item) for i in lint_form(work) if i.code.startswith("literal")}
    assert ("literal-font", "ДекорацияЗаголовок") in lit
    assert ("literal-color", "ДекорацияЗаголовок") in lit
    assert not any(item == "ДекорацияПодсказка" for _, item in lit)


def test_edt_lang_missing(tmp_path):
    work = _copy(tmp_path, "form_style_edt.form", "Form.form")
    issues = lint_form(work, langs=("ru", "en", "kk"))
    assert any(i.code == "title-lang" and "kk" in i.message for i in issues)


def test_cfg_same_checks(tmp_path):
    work = _copy(tmp_path, "form_style_cfg.xml", "Form.xml")
    set_style(work, "ОбщаяИнформация", {"textColor": "rgb:1,2,3"})
    codes = _codes(lint_form(work, langs=("ru", "en")))
    assert "literal-color" in codes


def test_edt_hidden_column_not_checked(tmp_path):
    """Скрытая колонка (в EDT у неё нет <visible>true</visible>) — не находка."""
    work = _copy(tmp_path, "form_style_edt.form", "Form.form")
    items = {i.item for i in lint_form(work)}
    assert "ЗаказыOrderId" not in items and "ЗаказыНомерСтроки" not in items