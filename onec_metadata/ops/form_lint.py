"""Проверка управляемой формы на конвенции оформления (EDT `Form.form` и Конфигуратор `Form.xml`).

Только чтение. Находки:
  title-lang      — у заголовка элемента нет нужного языка (по умолчанию ru, en);
  title-no-own    — у колонки таблицы нет своего заголовка: берётся синоним реквизита, где часто только ru;
  literal-font    — шрифт задан по месту (faceName/height/флаги), а не ссылкой на шрифт-элемент стиля;
  literal-color   — цвет задан по месту (RGB), а не элементом стиля (`style:`) или `web:`;
  command-bar     — у формы не скрыта командная панель (`commandBarLocation` не `None`).
  group-horizontal-if-possible — у обычной группы не задано направление (умолчание «горизонтально, если возможно»,
                    результат зависит от ширины окна); гайд: ориентироваться на 1280 × 768 и задать явно;
  group-title-word — показанный заголовок обычной группы начинается со слова «Группа» (его добавляет платформа).
Скрытые элементы и их содержимое не проверяются (EDT: нет `<visible>true</visible>`;
Конфигуратор: `<Visible>false</Visible>`).
"""
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError

_XT = cfg.q("xsi", "type")
_LF = cfg.NS["lf"]
_V8 = cfg.NS["v8"]


@dataclass(frozen=True)
class Issue:
    code: str
    item: str
    message: str

    def __str__(self):
        return f"{self.code:14} {self.item}: {self.message}"


CHECKS = {"titles": ("title-lang", "title-no-own"),
          "tokens": ("literal-font", "literal-color"),
          "command-bar": ("command-bar",),
          "layout": ("group-horizontal-if-possible", "group-title-word")}


def lint_form(form_path: Path, langs=("ru", "en"), checks=tuple(CHECKS)) -> list:
    """`checks` — группы проверок: titles, tokens, command-bar, layout (по умолчанию все). Формы, где фирменное
    оформление не принято (только компоновка), проверять `checks=("titles",)`."""
    unknown = set(checks) - set(CHECKS)
    if unknown:
        raise OpPreconditionError(f"form lint: неизвестные проверки {sorted(unknown)}; есть {sorted(CHECKS)}")
    form_path = Path(form_path)
    root = cfg.load(form_path).getroot()
    if form_path.suffix == ".form":
        issues = _lint_edt(root, langs)
    elif form_path.suffix == ".xml":
        issues = _lint_cfg(root, langs)
    else:
        raise OpPreconditionError("form lint: ожидается Form.form (EDT) или Form.xml (Конфигуратор)")
    wanted = {code for c in checks for code in CHECKS[c]}
    return [i for i in issues if i.code in wanted]


def _missing(present, langs):
    return [lg for lg in langs if lg not in present]


# ---------- EDT ----------

def _lint_edt(root, langs):
    out = []
    if (root.findtext("commandBarLocation") or "") != "None":
        out.append(Issue("command-bar", ".", "командная панель формы не скрыта (commandBarLocation=None)"))

    def walk(container, in_table):
        for it in container.findall("items"):
            if it.findtext("visible") != "true":         # EDT: у скрытого элемента <visible> нет вовсе
                continue
            name = it.findtext("name") or "?"
            titles = [t.findtext("key") for t in it.findall("title")]
            if titles:
                miss = _missing(titles, langs)
                if miss:
                    out.append(Issue("title-lang", name, "нет заголовка на: " + ", ".join(miss)))
            elif in_table and (it.get(_XT) or "").endswith("FormField"):
                out.append(Issue("title-no-own", name,
                                 "колонка без своего заголовка (возьмётся синоним реквизита) — задать " +
                                 "/".join(langs)))
            if (it.get(_XT) or "").endswith("FormGroup") and it.findtext("type") == "UsualGroup":
                direction = it.findtext("extInfo/group") or "HorizontalIfPossible"
                if direction == "HorizontalIfPossible":
                    out.append(Issue("group-horizontal-if-possible", name,
                                     "направление группы не задано — задать Vertical/Horizontal/AlwaysHorizontal"))
                ru = next((t.findtext("value") for t in it.findall("title") if t.findtext("key") == "ru"), "")
                if it.findtext("showTitle") != "false" and (ru or "").startswith("Группа"):
                    out.append(Issue("group-title-word", name, "в заголовке группы слово «Группа» — убрать"))
            own = list(it) + list(it.find("extInfo") if it.find("extInfo") is not None else [])
            for p in own:
                if not isinstance(p.tag, str) or p.tag == "items":
                    continue
                kind = p.get(_XT) or ""
                if kind == "core:FontDef" or (kind == "core:FontRef" and len(p) > 1 and not
                                              (len(p) == 2 and p[1].tag == "scale")):
                    out.append(Issue("literal-font", name, f"{p.tag} по месту — ссылка на шрифт-элемент стиля"))
                elif kind == "core:ColorDef":
                    out.append(Issue("literal-color", name, f"{p.tag} RGB по месту — элемент стиля"))
            walk(it, in_table or (it.get(_XT) or "").endswith("Table"))

    walk(root, False)
    return out


# ---------- Конфигуратор ----------

def _lint_cfg(root, langs):
    out = []
    if (root.findtext(f"{{{_LF}}}CommandBarLocation") or "") != "None":
        out.append(Issue("command-bar", ".", "командная панель формы не скрыта (CommandBarLocation=None)"))

    def walk(container, in_table):
        kids = container.find(f"{{{_LF}}}ChildItems")
        if kids is None:
            return
        for it in kids:
            if not isinstance(it.tag, str) or it.findtext(f"{{{_LF}}}Visible") == "false":
                continue
            tag = etree.QName(it).localname
            name = it.get("name") or "?"
            title = it.find(f"{{{_LF}}}Title")
            if title is not None:
                present = [x.findtext(f"{{{_V8}}}lang") for x in title.findall(f"{{{_V8}}}item")]
                miss = _missing(present, langs)
                if miss:
                    out.append(Issue("title-lang", name, "нет заголовка на: " + ", ".join(miss)))
            elif in_table and tag == "InputField":
                out.append(Issue("title-no-own", name,
                                 "колонка без своего заголовка (возьмётся синоним реквизита) — задать " +
                                 "/".join(langs)))
            if tag == "UsualGroup":
                direction = it.findtext(f"{{{_LF}}}Group") or "HorizontalIfPossible"
                if direction == "HorizontalIfPossible":
                    out.append(Issue("group-horizontal-if-possible", name,
                                     "направление группы не задано — задать Vertical/Horizontal/AlwaysHorizontal"))
                ru = ""
                if title is not None:
                    for x in title.findall(f"{{{_V8}}}item"):
                        if x.findtext(f"{{{_V8}}}lang") == "ru":
                            ru = x.findtext(f"{{{_V8}}}content") or ""
                if it.findtext(f"{{{_LF}}}ShowTitle") != "false" and ru.startswith("Группа"):
                    out.append(Issue("group-title-word", name, "в заголовке группы слово «Группа» — убрать"))
            for p in it:
                if not isinstance(p.tag, str):
                    continue
                pt = etree.QName(p).localname
                if pt.endswith("Font") and (p.get("kind") != "StyleItem" or
                                            any(p.get(a) for a in ("faceName", "height", "bold"))):
                    out.append(Issue("literal-font", name, f"{pt} по месту — ссылка на шрифт-элемент стиля"))
                elif pt.endswith("Color") and (p.text or "").startswith("#"):
                    out.append(Issue("literal-color", name, f"{pt} RGB по месту — элемент стиля"))
            walk(it, in_table or tag == "Table")

    walk(root, False)
    return out
