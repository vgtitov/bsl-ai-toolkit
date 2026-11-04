"""learn_cfg_form_defaults: умолчания берутся только у визуальных элементов и только однозначные."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "learn_cfg_form_defaults",
    Path(__file__).resolve().parents[2] / "scripts" / "learn_cfg_form_defaults.py")
learn_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(learn_mod)

LF = "http://v8.1c.ru/8.3/xcf/logform"


def _pair(root: Path, form: str, edt_items: str, xml_body: str):
    e = root / "edt" / "DataProcessors" / "Обр" / "Forms" / form
    x = root / "xml" / "DataProcessors" / "Обр" / "Forms" / form / "Ext"
    e.mkdir(parents=True)
    x.mkdir(parents=True)
    (e / "Form.form").write_text(f"<form>{edt_items}</form>", encoding="utf-8")
    (x / "Form.xml").write_text(f'<Form xmlns="{LF}">{xml_body}</Form>', encoding="utf-8")


def test_attribute_with_same_name_as_field_is_not_a_context(tmp_path):
    # реквизит «Поле» называется так же, как поле формы: его в таблицу не берём
    _pair(tmp_path, "Ф1",
          "<items><name>Поле</name><wrap>false</wrap></items>",
          '<ChildItems><InputField name="Поле" id="1"/></ChildItems>'
          '<Attributes><Attribute name="Поле" id="2"/></Attributes>')
    table, pairs = learn_mod.learn(tmp_path / "edt", tmp_path / "xml")
    assert pairs == 1
    assert table == {"InputField": {"Wrap": "false"}}


def test_conflicting_omitted_values_are_skipped(tmp_path):
    _pair(tmp_path, "Ф1", "<items><name>А</name><wrap>false</wrap></items>",
          '<ChildItems><InputField name="А" id="1"/></ChildItems>')
    _pair(tmp_path, "Ф2", "<items><name>Б</name><wrap>true</wrap></items>",
          '<ChildItems><InputField name="Б" id="1"/></ChildItems>')
    table, _ = learn_mod.learn(tmp_path / "edt", tmp_path / "xml")
    assert "Wrap" not in table.get("InputField", {})
