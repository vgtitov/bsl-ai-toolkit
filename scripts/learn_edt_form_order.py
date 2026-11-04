"""Таблица порядка свойств управляемых форм по корпусу реальных выгрузок (для `1c-meta form set-style`).

Для каждого контекста (тег-родитель [+ xsi:type]) строит граф «A встречается раньше B» и
топологически сортирует. Конфликты (A<B и B<A примерно поровну) и циклы печатаются в stderr —
значит, порядок для этой пары не строгий; такой контекст стоит проверить глазами.

    python scripts/learn_edt_form_order.py --pattern Form.form --out onec_metadata/data/edt_form_order.json <src>...
    python scripts/learn_edt_form_order.py --pattern Form.xml  --out onec_metadata/data/cfg_form_order.json <dump>...

EDT — `Form.form` из src EDT-проектов; Конфигуратор — `Form.xml` из XML-выгрузки конфигурации.
"""
import argparse
import collections
import json
import sys
from pathlib import Path

from lxml import etree

XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def ctx_of(el):
    t = etree.QName(el).localname
    x = el.get(XSI)
    return f"{t}[{x}]" if x else t


def learn(files):
    pairs = collections.defaultdict(collections.Counter)
    tags = collections.defaultdict(collections.Counter)

    def walk(el):
        c = ctx_of(el)
        seen = []
        for ch in el:
            if not isinstance(ch.tag, str):
                continue
            k = etree.QName(ch).localname
            tags[c][k] += 1
            if k not in seen:
                seen.append(k)
        for i, a in enumerate(seen):
            for b in seen[i + 1:]:
                pairs[c][(a, b)] += 1
        for ch in el:
            if isinstance(ch.tag, str):
                walk(ch)

    for f in files:
        try:
            walk(etree.parse(str(f)).getroot())
        except etree.XMLSyntaxError as e:
            print(f"skip {f}: {e}", file=sys.stderr)

    out = {}
    for c in sorted(tags):
        names = list(tags[c])
        succ = collections.defaultdict(set)
        indeg = collections.Counter({t: 0 for t in names})
        for (a, b), n in pairs[c].items():
            if pairs[c].get((b, a), 0) >= n:          # не строгий порядок для пары
                if a < b:
                    print(f"conflict {c}: {a} <> {b}", file=sys.stderr)
                continue
            if b not in succ[a]:
                succ[a].add(b)
                indeg[b] += 1
        order = []
        queue = sorted((t for t in names if indeg[t] == 0), key=lambda t: -tags[c][t])
        while queue:
            t = queue.pop(0)
            order.append(t)
            for s in sorted(succ[t]):
                indeg[s] -= 1
                if indeg[s] == 0:
                    queue.append(s)
        rest = [t for t in names if t not in order]
        if rest:
            print(f"cycle {c}: {rest}", file=sys.stderr)
        out[c] = order + rest
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("roots", nargs="+", help="каталоги с формами")
    ap.add_argument("--pattern", default="Form.form", help="имя файла формы (Form.form / Form.xml / *.xml)")
    ap.add_argument("--out", required=True, help="куда записать JSON-таблицу")
    ns = ap.parse_args()
    files = [f for r in ns.roots for f in Path(r).rglob(ns.pattern)]
    if not files:
        sys.exit(f"нет файлов {ns.pattern} в {ns.roots}")
    table = learn(files)
    Path(ns.out).write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8", newline="\n")
    print(f"форм: {len(files)}, контекстов: {len(table)} -> {ns.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
