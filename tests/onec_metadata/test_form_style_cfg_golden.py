# DISCIPLINE_ALLOW_TEST_EDIT: golden-тест set-style (Конфигуратор) против экспорта EDT
"""Эталон: `form_style_cfg_golden.xml` — форма рабочего места «Отложенные заказы», оформленная по рецепту и
выгруженная САМИМ EDT в XML Конфигуратора (export_configuration_to_xml, 03.11.2026).
Снять все свойства рецепта и поставить их движком — байты должны совпасть с выгрузкой EDT:
это проверяет и порядок свойств, и формат значений, и пропуск умолчаний."""
import shutil
from pathlib import Path

from onec_metadata.ops.form_style import set_style

GOLDEN = Path(__file__).parent / "fixtures" / "form_style_cfg_golden.xml"

G = "style:NormalTextFont,face=Gilroy"
CARD = {"representation": "WeakSeparation", "showTitle": "true", "backColor": "style:примЦветБелый",
        "titleFont": f"{G},height=11,bold", "titleTextColor": "rgb:74,74,74", "group": "Vertical",
        "united": "true", "showLeftMargin": "true"}
RECIPE = {
    ".": {"commandBarLocation": "None"},
    "ГруппаСтраница": {"group": "Vertical", "showTitle": "false", "backColor": "style:примЦветФона",
                       "horizontalSpacing": "None", "verticalSpacing": "Half", "united": "true",
                       "showLeftMargin": "true"},
    "ГруппаШапка": {"group": "AlwaysHorizontal", "representation": "WeakSeparation", "showTitle": "false",
                    "backColor": "style:примЦветОсновнойБирюзовый", "verticalAlign": "Center",
                    "horizontalSpacing": "None", "verticalSpacing": "None", "united": "true",
                    "showLeftMargin": "true", "groupVerticalAlign": "Top"},
    "ДекорацияЗаголовок": {"textColor": "style:примЦветБелый", "font": f"{G},height=14,bold",
                           "height": "2", "verticalAlign": "Center", "autoMaxWidth": "false"},
    "ДекорацияКоличество": {"textColor": "style:примЦветБелый", "font": f"{G},height=10",
                            "horizontalAlign": "Right"},
    "ГруппаОтбор": dict(CARD, verticalAlign="Bottom"),
    "ГруппаСписок": dict(CARD),
    "ГруппаПоискПоля": {"group": "AlwaysHorizontal", "representation": "None", "showTitle": "false",
                        "verticalAlign": "Bottom"},
    "ГруппаКнопок": {"group": "AlwaysHorizontal", "representation": "None", "showTitle": "false",
                     "horizontalAlign": "Right"},
    "ПоискПоНомеру": {"titleTextColor": "rgb:116,116,116", "titleFont": f"{G},height=10", "width": "25"},
    "ОтборКлиент": {"titleTextColor": "rgb:116,116,116", "titleFont": f"{G},height=10", "width": "25"},
    "КнопкаНайти": {"representation": "Picture", "picture": "common:примКнопкаНайти",
                    "shapeRepresentation": "None", "groupVerticalAlign": "Bottom"},
    "КнопкаВыбрать": {"representation": "Picture", "picture": "common:примКнопкаЗагрузитьВРабочееМесто",
                      "shapeRepresentation": "None", "groupHorizontalAlign": "Right"},
    "Заказы": {"borderColor": "style:примЦветБелый", "commandBarLocation": "None", "autoMaxWidth": "false"},
    "ДекорацияПодсказка": {"textColor": "rgb:116,116,116", "font": f"{G},height=9", "autoMaxWidth": "false"},
}
for _col, _w in {"Номер": 9, "Дата": 11, "Клиент": 16, "Процесс": 13, "Сумма": 9, "Оплачено": 9,
                 "Менеджер": 12}.items():
    RECIPE[f"Заказы{_col}"] = {"titleTextColor": "rgb:74,74,74",
                               "titleFont": "style:NormalTextFont,face=Gilroy Bold,height=10",
                               "titleBackColor": "style:примЦветШапкиТаблицы", "textColor": "rgb:74,74,74",
                               "font": f"{G},height=8", "width": str(_w), "horizontalStretch": "false"}


def test_strip_and_reapply_equals_edt_export(tmp_path):
    work = tmp_path / "Form.xml"
    shutil.copy(GOLDEN, work)
    golden = GOLDEN.read_bytes()
    for item, props in RECIPE.items():
        set_style(work, item, {k: "" for k in props})
    assert len(work.read_bytes()) < len(golden) - 5000      # оформление действительно снято
    for item, props in RECIPE.items():
        set_style(work, item, props)
    assert work.read_bytes() == golden
