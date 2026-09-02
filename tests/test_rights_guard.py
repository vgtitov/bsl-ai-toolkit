# DISCIPLINE_ALLOW_TEST_EDIT: синтетические обезличенные фикстуры
"""rights_guard: забытые права, дыра в RLS, запрос без РАЗРЕШЕННЫЕ."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "rights_guard.py"

RIGHTS_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<Rights xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://v8.1c.ru/8.2/roles" xsi:type="Rights">
\t<setForNewObjects>false</setForNewObjects>
{objects}
</Rights>
"""

OBJ_TMPL = """\t<object>
\t\t<name>{name}</name>
\t\t<right>
\t\t\t<name>{right}</name>
\t\t\t<value>true</value>
\t\t</right>
\t</object>"""

MDO_NATIVE = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:InformationRegister xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" name="{name}">
  <name>{name}</name>
</mdclass:InformationRegister>
"""

MDO_ADOPTED = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Catalog xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" name="{name}">
  <name>{name}</name>
  <objectBelonging>Adopted</objectBelonging>
</mdclass:Catalog>
"""


def build_tree(tmp_path, granted=(), native=(), adopted=()):
    src = tmp_path / "src"
    (src / "Configuration").mkdir(parents=True)
    (src / "Roles" / "TestRole").mkdir(parents=True)
    objects = "\n".join(OBJ_TMPL.format(name=n, right=r) for n, r in granted)
    (src / "Roles" / "TestRole" / "Rights.rights").write_text(
        RIGHTS_TMPL.format(objects=objects), encoding="utf-8")
    for name in native:
        d = src / "InformationRegisters" / name
        d.mkdir(parents=True)
        (d / f"{name}.mdo").write_text(MDO_NATIVE.format(name=name), encoding="utf-8")
    for name in adopted:
        d = src / "Catalogs" / name
        d.mkdir(parents=True)
        (d / f"{name}.mdo").write_text(MDO_ADOPTED.format(name=name), encoding="utf-8")
    return src


def run(*paths, extra=()):
    cmd = [sys.executable, str(GUARD), "--json", *[str(p) for p in paths], *extra]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    import json
    return json.loads(out.stdout or "[]"), out.returncode


def codes(findings):
    return sorted(f["code"] for f in findings)


def test_native_object_without_rights_is_error(tmp_path):
    src = build_tree(tmp_path, native=["ЯдроТестовыйРегистр"])
    findings, rc = run(src)
    assert codes(findings) == ["object-without-rights"]
    assert findings[0]["severity"] == "error"
    assert rc == 1


def test_native_object_with_rights_is_clean(tmp_path):
    src = build_tree(tmp_path,
                     granted=[("InformationRegister.ЯдроТестовыйРегистр", "Read")],
                     native=["ЯдроТестовыйРегистр"])
    findings, rc = run(src)
    assert findings == []
    assert rc == 0


def test_right_on_attribute_counts_as_covered(tmp_path):
    src = build_tree(tmp_path,
                     granted=[("InformationRegister.ЯдроТестовыйРегистр.Attribute.Поле", "View")],
                     native=["ЯдроТестовыйРегистр"])
    findings, _ = run(src)
    assert findings == []


def test_unconditional_right_on_adopted_is_warning(tmp_path):
    src = build_tree(tmp_path,
                     granted=[("Catalog.ЗаимствованныйСправочник", "Read")],
                     adopted=["ЗаимствованныйСправочник"])
    findings, rc = run(src)
    assert codes(findings) == ["unconditional-right-on-adopted"]
    assert findings[0]["severity"] == "warning"
    assert rc == 0, "предупреждение не должно валить сборку без --strict"


def test_view_right_on_adopted_is_not_flagged(tmp_path):
    """View/Edit не снимают RLS — шумим только на правах доступа к данным."""
    src = build_tree(tmp_path,
                     granted=[("Catalog.ЗаимствованныйСправочник", "View")],
                     adopted=["ЗаимствованныйСправочник"])
    findings, _ = run(src)
    assert findings == []


def test_query_without_allowed_is_warning(tmp_path):
    f = tmp_path / "Module.bsl"
    f.write_text('Запрос.Текст = "ВЫБРАТЬ\n'
                 '|\tТ.Ссылка\n'
                 '|ИЗ\n'
                 '|\tДокумент.ЗаказКлиента КАК Т";\n', encoding="utf-8")
    findings, rc = run(f)
    assert codes(findings) == ["query-without-allowed"]
    assert rc == 0


def test_query_with_allowed_is_clean(tmp_path):
    f = tmp_path / "Module.bsl"
    f.write_text('Запрос.Текст = "ВЫБРАТЬ РАЗРЕШЕННЫЕ\n'
                 '|\tТ.Ссылка\n'
                 '|ИЗ\n'
                 '|\tДокумент.ЗаказКлиента КАК Т";\n', encoding="utf-8")
    findings, _ = run(f)
    assert findings == []


def test_pragma_suppresses_query_finding(tmp_path):
    f = tmp_path / "Module.bsl"
    f.write_text('// rights-guard: ok - чтение настройки привилегированно\n'
                 'Запрос.Текст = "ВЫБРАТЬ\n'
                 '|\tТ.Ссылка\n'
                 '|ИЗ\n'
                 '|\tСправочник.Склады КАК Т";\n', encoding="utf-8")
    findings, _ = run(f)
    assert findings == []


def test_temp_table_query_is_not_flagged(tmp_path):
    """Запрос к временной таблице к правам отношения не имеет."""
    f = tmp_path / "Module.bsl"
    f.write_text('Запрос.Текст = "ВЫБРАТЬ\n'
                 '|\tТ.Ссылка\n'
                 '|ИЗ\n'
                 '|\tВТДанные КАК Т";\n', encoding="utf-8")
    findings, _ = run(f)
    assert findings == []


def test_strict_makes_warnings_fatal(tmp_path):
    src = build_tree(tmp_path,
                     granted=[("Catalog.ЗаимствованныйСправочник", "Read")],
                     adopted=["ЗаимствованныйСправочник"])
    _, rc = run(src, extra=["--strict"])
    assert rc == 1
