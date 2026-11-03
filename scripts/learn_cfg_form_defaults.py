"""Таблица умолчаний свойств форм в формате Конфигуратора (для `1c-meta form set-style`).

Выгрузка Конфигуратора (и экспорт EDT в XML) НЕ пишет свойство, равное умолчанию платформы.
Источник истины — пары «форма EDT (`Form.form`, свойства записаны явно) ↔ та же форма в XML-выгрузке
(`Form.xml`)»: свойство с простым значением, которое есть в `.form`, но пропало в `Form.xml`, — умолчание.
Сопоставление элементов по имени (`name`), свойство EDT → имя Конфигуратора с большой буквы.

    python scripts/learn_cfg_form_defaults.py --edt <src EDT-проекта> --xml <каталог XML-выгрузки> --out onec_metadata/data/cfg_form_defaults.json

Значение попадает в таблицу, только если ни разу не встретилось записанным в `Form.xml` (иначе это не умолчание).
"""
import argparse
import collections
import json
import sys
from pathlib import Path

from lxml import etree

LF = "http://v8.1c.ru/8.3/xcf/logform"


def _simple(el):
    return len(el) == 0 and not el.attrib and (el.text or "").strip() != ""


# в XML это атрибуты (name, id) или сам тег элемента (type) — не свойства
_NOT_PROPS = {"Name", "Id", "Type"}


def _props_edt(item):
    """Простые свойства элемента EDT: уровень элемента + extInfo."""
    out = {}
    for c in list(item) + list(item.find("extInfo") if item.find("extInfo") is not None else []):
        if isinstance(c.tag, str) and _simple(c):
            p = c.tag[0].upper() + c.tag[1:]
            if p not in _NOT_PROPS:
                out[p] = c.text.strip()
    return out


def _key(path: Path):
    """(владелец, форма) для `…/<Тип>/<Объект>/Forms/<Форма>[/Ext]/Form.*` и `CommonForms/<Форма>[/Ext]/Form.*`."""
    dirs = [p for p in path.parts[:-1] if p != "Ext"]
    if len(dirs) >= 3 and dirs[-2] == "Forms":
        return dirs[-3], dirs[-1]
    return dirs[-2], dirs[-1]


def learn(edt_root: Path, xml_root: Path):
    omitted = collections.defaultdict(collections.Counter)    # (ctx, prop) -> value -> раз пропущено
    written = collections.defaultdict(set)                    # (ctx, prop) -> значения, встреченные в XML
    pairs = 0
    xml_by_rel ={_key(x): x for x in xml_root.rglob("Form.xml")}
    for f in edt_root.rglob("Form.form"):
        x = xml_by_rel.get(_key(f))
        if x is None:
            continue
        pairs += 1
        edt = etree.parse(str(f)).getroot()
        xml = etree.parse(str(x)).getroot()
        xml_items = {e.get("name"): e for e in xml.iter() if isinstance(e.tag, str) and e.get("name")}
        for item in edt.iter("items"):
            name = item.findtext("name")
            xe = xml_items.get(name)
            if xe is None:
                continue
            ctx = etree.QName(xe).localname
            xprops = {etree.QName(c).localname: (c.text or "").strip() for c in xe
                      if isinstance(c.tag, str) and len(c) == 0}
            for p, v in _props_edt(item).items():
                if p in xprops:
                    written[(ctx, p)].add(xprops[p])
                else:
                    omitted[(ctx, p)][v] += 1
    table = collections.defaultdict(dict)
    for (ctx, p), vals in omitted.items():
        v, _ = vals.most_common(1)[0]
        if v not in written[(ctx, p)]:
            table[ctx][p] = v
    return {k: dict(sorted(v.items())) for k, v in sorted(table.items())}, pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--edt", required=True, type=Path)
    ap.add_argument("--xml", required=True, type=Path)
    ap.add_argument("--out", required=True)
    ns = ap.parse_args()
    table, pairs = learn(ns.edt, ns.xml)
    if not pairs:
        sys.exit("не найдено ни одной пары Form.form ↔ Form.xml")
    Path(ns.out).write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8", newline="\n")
    print(f"пар форм: {pairs}, контекстов: {len(table)} -> {ns.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
