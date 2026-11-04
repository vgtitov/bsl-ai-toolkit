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

def test_checks_titles_only_for_layout_forms(tmp_path):
    """Только компоновка (без фирменного оформления): шрифты по месту и командная панель — не находки."""
    work = _copy(tmp_path, "form_style_cfg.xml", "Form.xml")
    set_style(work, "ОбщаяИнформация", {"textColor": "rgb:1,2,3"})
    codes = _codes(lint_form(work, langs=("ru", "en"), checks=("titles",)))
    assert "literal-color" not in codes and "command-bar" not in codes

# ---------- layout: расположение и группировка по гайду ----------

def test_cfg_group_default_direction_and_title_word_flagged(tmp_path):
    work = _copy(tmp_path, "form_style_cfg.xml", "Form.xml")
    text = work.read_text(encoding="utf-8")
    # ГруппаВерсияУстановленногоМодуля: направление не задано (умолчание «горизонтально, если возможно»),
    # заголовок показан и начинается со слова «Группа»
    text = text.replace("<Group>Horizontal</Group>", "", 1).replace("<ShowTitle>false</ShowTitle>", "", 1)
    work.write_text(text, encoding="utf-8")
    by_item = {(i.code, i.item) for i in lint_form(work, checks=("layout",))}
    assert ("group-horizontal-if-possible", "ГруппаВерсияУстановленногоМодуля") in by_item
    assert ("group-title-word", "ГруппаВерсияУстановленногоМодуля") in by_item


def test_cfg_explicit_direction_and_hidden_title_pass(tmp_path):
    work = _copy(tmp_path, "form_style_cfg.xml", "Form.xml")
    flagged = {i.item for i in lint_form(work, checks=("layout",)) if i.code == "group-title-word"}
    assert "ГруппаВерсияУстановленногоМодуля" not in flagged        # заголовок скрыт (ShowTitle=false)
    dirs = {i.item for i in lint_form(work, checks=("layout",)) if i.code == "group-horizontal-if-possible"}
    assert "ГруппаОбщаяИнформация" not in dirs                      # Group=Vertical задан явно


def test_edt_group_without_direction_flagged(tmp_path):
    work = _copy(tmp_path, "form_style_edt.form", "Form.form")
    issues = lint_form(work, checks=("layout",))
    assert all(i.code in ("group-horizontal-if-possible", "group-title-word") for i in issues)
    assert any(i.code == "group-horizontal-if-possible" for i in issues)   # extInfo группы пустой
    set_style(work, "ГруппаШапка", {"group": "AlwaysHorizontal"})
    after = {i.item for i in lint_form(work, checks=("layout",)) if i.code == "group-horizontal-if-possible"}
    assert "ГруппаШапка" not in after


def test_unknown_check_lists_layout(tmp_path):
    import pytest
    from onec_metadata.ops import OpPreconditionError
    work = _copy(tmp_path, "form_style_edt.form", "Form.form")
    with pytest.raises(OpPreconditionError, match="layout"):
        lint_form(work, checks=("nope",))
