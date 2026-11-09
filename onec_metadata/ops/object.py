"""Создание, заимствование и удаление объектов метаданных верхнего уровня (`1c-meta object`).

Новый объект — по эталону, снятому у самого EDT (`scripts/learn_object_templates.py`,
`onec_metadata/templates/objects`), а не клоном соседа: клон тащит чужие свойства и ссылки.
Оба формата: EDT (`src/<Вид>/<Имя>/<Имя>.mdo` + строка в `Configuration/Configuration.mdo`) и
Конфигуратор (`<Вид>/<Имя>.xml` + строка в `<ChildObjects>` `Configuration.xml`).

Заимствование: эталон MCP EDT дополнен тем, что делает GUI EDT и что есть у всех заимствованных
объектов корпуса: ссылка на объект типовой (`extendedConfigurationObject`) и контроль этой ссылки
(`<extendedConfigurationObject>Checked`); флаги общего модуля переносятся из типовой. Плагин MCP
2.6.1 ссылку не пишет — эталон «как есть» перенёс бы его дефект.

Удаление — только без ссылок на объект в проекте (список находок в ошибке); заимствованный —
только «голый» (без своих реквизитов, форм, модулей).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name, validate_object_ref

TPL = Path(__file__).resolve().parents[1] / "templates" / "objects"
META = json.loads((TPL / "meta.json").read_text(encoding="utf-8"))
ORDER = [k for k, _ in META["order"]]                    # канонический порядок видов в манифесте
MANIFEST_TAG = {k: t for k, t in META["order"]}          # вид → тег строки манифеста EDT
SUPPORTED = sorted(META["kinds"])

# Русское имя вида (как в BSL и в «Тип.Имя» по-русски) и корни слов для поиска ссылок
RU = {
    "CommonModule": ("ОбщийМодуль", ["ОбщийМодуль"]),
    "InformationRegister": ("РегистрСведений", ["РегистрСведений\\w*", "РегистрыСведений"]),
    "AccumulationRegister": ("РегистрНакопления", ["РегистрНакопления\\w*", "РегистрыНакопления"]),
    "Catalog": ("Справочник", ["Справочник\\w*"]),
    "Document": ("Документ", ["Документ\\w*"]),
    "Enum": ("Перечисление", ["Перечислени\\w*"]),
    "Constant": ("Константа", ["Констант\\w*"]),
    "DataProcessor": ("Обработка", ["Обработк\\w*"]),
    "Report": ("Отчет", ["Отчет\\w*"]),
}
KIND_ALIASES = {k.lower(): k for k in SUPPORTED} | {ru.lower(): k for k, (ru, _) in RU.items()}

# Параметры видов: (свойство EDT, значение по умолчанию — его EDT не пишет, допустимые значения)
REGISTER_PARAMS = {
    "InformationRegister": {
        "periodicity": ("informationRegisterPeriodicity", "Nonperiodical",
                        ("Nonperiodical", "Second", "Day", "Month", "Quarter", "Year", "RecorderPosition")),
        "write_mode": ("writeMode", "Independent", ("Independent", "RecorderSubordinate")),
    },
    "AccumulationRegister": {
        "register_type": ("registerType", "Balance", ("Balance", "Turnovers")),
    },
}
# Контекст общего модуля → вариант эталона (пресеты EDT, которые принимает проверка типа модуля)
CM_CONTEXT = {"server": "", "server-call": "ServerCall", "client": "ClientManaged",
              "client-server": "ClientServer", "global": "Global"}
# Заимствованный без «своего»: такие узлы в .mdo / каталог рядом с .xml значат, что в нём уже работают
_OWN_CONTENT = ("<attributes", "<tabularSections", "<forms", "<commands", "<templates", "<dimensions",
                "<resources", "<enumValues", "<accountingFlags", "<addressingAttributes")
_REF_SUFFIXES = {".mdo", ".xml", ".bsl", ".form", ".rights", ".dcs", ".dcss", ".mxlx", ".cmi", ".hpwt"}


@dataclass
class Project:
    root: Path              # EDT: каталог src; Конфигуратор: каталог выгрузки
    fmt: str                # "edt" | "cfg"
    manifest: Path
    extension: bool
    prefix: str
    newline: str

    def main_file(self, kind: str, name: str) -> Path:
        d = self.root / META["kinds"][kind]["dir"]
        return d / name / f"{name}.mdo" if self.fmt == "edt" else d / f"{name}.xml"

    def object_paths(self, kind: str, name: str) -> list[Path]:
        d = self.root / META["kinds"][kind]["dir"]
        return [d / name] if self.fmt == "edt" else [d / f"{name}.xml", d / name]


def open_project(src: Path) -> Project:
    src = Path(src)
    for root in (src, src / "src"):
        mdo = root / "Configuration" / "Configuration.mdo"
        xml = root / "Configuration.xml"
        for fmt, manifest in (("edt", mdo), ("cfg", xml)):
            if manifest.is_file():
                text = manifest.read_bytes().decode("utf-8-sig")
                tag = "namePrefix" if fmt == "edt" else "NamePrefix"
                m = re.search(rf"<{tag}>([^<]*)</{tag}>", text)
                if fmt == "cfg":
                    ver = re.search(r'<MetaDataObject [^>]*version="([\d.]+)"', text)
                    if ver and ver.group(1) != META["cfg_format"]:
                        raise OpPreconditionError(
                            f"версия формата выгрузки {ver.group(1)}, эталоны сняты для {META['cfg_format']} "
                            f"(EDT {META['edt_version']}): переснять эталоны learn_object_templates.py")
                return Project(root, fmt, manifest,
                               "configurationextensionpurpose" in text.lower(),
                               m.group(1) if m else "", "\r\n" if "\r\n" in text else "\n")
    raise OpPreconditionError(f"{src}: нет Configuration/Configuration.mdo (EDT) и Configuration.xml "
                              "(Конфигуратор) — укажи каталог исходников проекта")


def resolve_kind(kind: str) -> str:
    k = KIND_ALIASES.get(kind.lower())
    if not k:
        raise OpPreconditionError(f"вид '{kind}' не поддержан; поддержаны: {', '.join(SUPPORTED)}. "
                                  "Остальные виды — пока человек в IDE (COVERAGE_MAP.md)")
    return k


# ---------- шаблоны ----------

def _template(fmt: str, sub: str, key: str) -> str:
    return (TPL / fmt / sub / f"{key}.{'mdo' if fmt == 'edt' else 'xml'}").read_text(encoding="utf-8")


def _synonyms(text: str, fmt: str, synonyms: dict[str, str]) -> str:
    if fmt == "edt":
        rx = re.compile(r"^( *)<synonym>\n\1  <key>ru</key>\n\1  <value>\{\{synonym\}\}</value>\n\1</synonym>\n",
                        re.M)

        def block(m):
            i = m.group(1)
            return "".join(f"{i}<synonym>\n{i}  <key>{escape(lang)}</key>\n{i}  <value>{escape(val)}</value>\n"
                           f"{i}</synonym>\n" for lang, val in synonyms.items())
    else:
        rx = re.compile(r"^(\t*)<Synonym>\n\1\t<v8:item>\n\1\t\t<v8:lang>ru</v8:lang>\n"
                        r"\1\t\t<v8:content>\{\{synonym\}\}</v8:content>\n\1\t</v8:item>\n\1</Synonym>\n", re.M)

        def block(m):
            i = m.group(1)
            if not synonyms:
                return f"{i}<Synonym/>\n"
            items = "".join(f"{i}\t<v8:item>\n{i}\t\t<v8:lang>{escape(lang)}</v8:lang>\n"
                            f"{i}\t\t<v8:content>{escape(val)}</v8:content>\n{i}\t</v8:item>\n"
                            for lang, val in synonyms.items())
            return f"{i}<Synonym>\n{items}{i}</Synonym>\n"
    text, n = rx.subn(block, text)
    if "{{synonym}}" in text or n > 1:
        raise OpPreconditionError("эталон: блок синонима не распознан — переснять эталоны")
    return text


def render(text: str, fmt: str, name: str, synonyms: dict[str, str], base_uuid: str | None = None) -> str:
    fresh: dict[str, str] = {}
    text = re.sub(r"\{\{uuid:(\d+)\}\}", lambda m: fresh.setdefault(m.group(1), str(uuid.uuid4())), text)
    text = text.replace("{{name}}", name)
    if base_uuid:
        text = text.replace("{{base_uuid}}", base_uuid)
    if "{{synonym}}" in text:
        text = _synonyms(text, fmt, synonyms)
    left = re.findall(r"\{\{\w+(?::\d+)?\}\}", text)
    if left:
        raise OpPreconditionError(f"эталон: не подставлено {left[:3]}")
    return text


def _set_edt_prop(text: str, kind: str, tag: str, value: str, default: str) -> str:
    """Свойство верхнего уровня .mdo: EDT не пишет умолчание; иначе — на место по порядку эталонов."""
    text = re.sub(rf"^  <{tag}>[^<]*</{tag}>\n", "", text, flags=re.M)
    if value == default:
        return text
    order = META["kinds"][kind]["edt_order"]
    before = order[:order.index(tag)]
    lines = text.splitlines(keepends=True)
    at = None
    for i, line in enumerate(lines):
        m = re.match(r"  <(\w+)", line)
        if m and m.group(1) in before:
            at = i
            if not re.search(rf"</{m.group(1)}>|/>\s*$", line):    # многострочное — до закрывающего
                at = next(j for j in range(i, len(lines)) if lines[j].startswith(f"  </{m.group(1)}>"))
    if at is None:
        raise OpPreconditionError(f"эталон {kind}: не найдено место для {tag}")
    lines.insert(at + 1, f"  <{tag}>{value}</{tag}>\n")
    return "".join(lines)


def _set_cfg_prop(text: str, tag: str, value: str) -> str:
    cap = tag[0].upper() + tag[1:]
    text, n = re.subn(rf"(<{cap}>)[^<]*(</{cap}>)", rf"\g<1>{value}\g<2>", text, count=1)
    if not n:
        raise OpPreconditionError(f"эталон: нет свойства {cap}")
    return text


# ---------- манифест ----------

def _manifest_line(p: Project, kind: str, name: str) -> str:
    if p.fmt == "edt":
        tag = MANIFEST_TAG[kind]
        return f"  <{tag}>{kind}.{name}</{tag}>"
    return f"<{kind}>{name}</{kind}>"


def _manifest_entries(p: Project, text: str) -> list[tuple[int, int, str, str]]:
    """[(начало строки, конец строки, вид, имя)] — строки объектов в манифесте."""
    out = []
    if p.fmt == "edt":
        tag_kind = {t: k for k, t in META["order"]}
        for m in re.finditer(r"^  <(\w+)>(\w+)\.([^<]+)</\1>\r?\n", text, re.M):
            if m.group(1) in tag_kind:
                out.append((m.start(), m.end(), tag_kind[m.group(1)], m.group(3)))
    else:
        co = re.search(r"<ChildObjects>(.*?)</ChildObjects>", text, re.S)
        if co:
            for m in re.finditer(r"^\t*<(\w+)>([^<]+)</\1>\r?\n", text[co.start(1):co.end(1)], re.M):
                out.append((co.start(1) + m.start(), co.start(1) + m.end(), m.group(1), m.group(2)))
    return out


def _rank(kind: str) -> int:
    return ORDER.index(kind) if kind in ORDER else -1           # Language и прочее — до объектов


def _manifest_add(p: Project, kind: str, name: str) -> str:
    raw = p.manifest.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    entries = _manifest_entries(p, text)
    if any(k == kind and n.lower() == name.lower() for _, _, k, n in entries):
        raise OpPreconditionError(f"{kind}.{name} уже есть в манифесте {p.manifest.name}")
    same = [e for e in entries if e[2] == kind]
    earlier = [e for e in entries if _rank(e[2]) < _rank(kind)]
    later = [e for e in entries if _rank(e[2]) > _rank(kind)]
    if same:                                    # в конец блока своего вида (порядок добавления, как в IDE)
        pos, ref = same[-1][1], same[-1]
    elif earlier:
        pos, ref = max(earlier, key=lambda e: e[1])[1], max(earlier, key=lambda e: e[1])
    elif later:
        pos, ref = later[0][0], later[0]
    else:                                       # объектов ещё нет (свежее расширение)
        pos, ref = _empty_list_pos(p, text), None
        if p.fmt == "cfg" and text[pos - len("<ChildObjects/>"):pos] == "<ChildObjects/>":
            start = pos - len("<ChildObjects/>")
            indent = re.search(r"([ \t]*)$", text[:start]).group(1)
            text = text[:start] + "<ChildObjects>" + p.newline + indent + "</ChildObjects>" + text[pos:]
            pos = start + len("<ChildObjects>") + len(p.newline)
    if p.fmt == "edt":
        line = _manifest_line(p, kind, name) + p.newline
    elif ref:
        line = re.match(r"\t*", text[ref[0]:ref[1]]).group() + _manifest_line(p, kind, name) + p.newline
    else:
        closing = re.search(r"([ \t]*)</ChildObjects>", text[pos:]).group(1)
        line = closing + "\t" + _manifest_line(p, kind, name) + p.newline
    new = text[:pos] + line + text[pos:]
    return ("﻿" if bom else "") + new


def _empty_list_pos(p: Project, text: str) -> int:
    """Куда вставить первую строку объекта: EDT — перед закрывающим тегом конфигурации,
    Конфигуратор — сразу после строки `<ChildObjects>` (или на месте `<ChildObjects/>`)."""
    if p.fmt == "edt":
        m = re.search(r"^</mdclass:Configuration>", text, re.M)
        if m:
            return m.start()
    else:
        m = re.search(r"<ChildObjects>\r?\n", text) or re.search(r"<ChildObjects/>", text)
        if m:
            return m.end()
    raise OpPreconditionError(f"{p.manifest.name}: не найден список объектов, куда вставить строку")


def _manifest_remove(p: Project, kind: str, name: str) -> str:
    raw = p.manifest.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    hit = [e for e in _manifest_entries(p, text) if e[2] == kind and e[3] == name]
    if not hit:
        raise OpPreconditionError(f"{kind}.{name} нет в манифесте {p.manifest.name}")
    s, e, _, _ = hit[0]
    return ("﻿" if bom else "") + text[:s] + text[e:]


# ---------- запись ----------

def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _commit_new(p: Project, path: Path, text: str, manifest_text: str) -> None:
    """Новый файл объекта + манифест. Сбой на манифесте — файл объекта убирается."""
    text = text.replace("\n", p.newline)                # эталоны в репо — LF; цель — как её манифест
    if p.fmt == "cfg":
        text = "﻿" + text
    created_dir = None
    d = path.parent
    while not d.exists():
        created_dir = d
        d = d.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as f:
            f.write(text.encode("utf-8"))
        _write_atomic(p.manifest, manifest_text)
    except BaseException:
        path.unlink(missing_ok=True)
        if created_dir:
            shutil.rmtree(created_dir, ignore_errors=True)
        raise


def _check_absent(p: Project, kind: str, name: str) -> None:
    for path in p.object_paths(kind, name):
        if path.exists():
            raise OpPreconditionError(f"{kind}.{name} уже есть: {path}")


# ---------- операции ----------

def add(src: Path, kind: str, name: str, synonyms: dict[str, str] | None = None, *,
        context: str | None = None, privileged: bool = False, periodicity: str | None = None,
        write_mode: str | None = None, register_type: str | None = None) -> Path:
    """Собственный объект по эталону IDE. Возвращает путь к файлу объекта."""
    kind = resolve_kind(kind)
    validate_name(name)
    synonyms = dict(synonyms or {})
    p = open_project(src)
    if p.extension and not name.lower().startswith(p.prefix.lower()):
        raise OpPreconditionError(f"в расширении имя собственного объекта начинается с префикса "
                                  f"'{p.prefix}': {p.prefix}{name}?")
    variant = ""
    if kind == "CommonModule":
        if context not in CM_CONTEXT:
            raise OpPreconditionError("общий модуль: укажи --context " + "|".join(CM_CONTEXT)
                                      + " (server включает и внешнее соединение)")
        if privileged and context != "server":
            raise OpPreconditionError("--privileged только с --context server")
        variant = "ServerPrivileged" if privileged else CM_CONTEXT[context]
    elif context or privileged:
        raise OpPreconditionError("--context/--privileged — только для общего модуля")
    given = {"periodicity": periodicity, "write_mode": write_mode, "register_type": register_type}
    params = REGISTER_PARAMS.get(kind, {})
    for opt, value in given.items():
        if value is not None and opt not in params:
            raise OpPreconditionError(f"--{opt.replace('_', '-')} не относится к виду {kind}")
    for opt, (_, _, allowed) in params.items():
        if given[opt] is None:
            raise OpPreconditionError(f"{kind}: укажи --{opt.replace('_', '-')} {'|'.join(allowed)}")
        if given[opt] not in allowed:
            raise OpPreconditionError(f"--{opt.replace('_', '-')} {given[opt]}: допустимо {'|'.join(allowed)}")
    _check_absent(p, kind, name)
    text = render(_template(p.fmt, "own", kind + (f"@{variant}" if variant else "")), p.fmt, name, synonyms)
    for opt, (tag, default, _) in params.items():
        text = (_set_edt_prop(text, kind, tag, given[opt], default) if p.fmt == "edt"
                else _set_cfg_prop(text, tag, given[opt]))
    path = p.main_file(kind, name)
    _commit_new(p, path, text, _manifest_add(p, kind, name))
    return path


def _base_object(base: Project, kind: str, name: str) -> tuple[str, str]:
    """(uuid объекта в типовой, текст его файла)."""
    path = base.main_file(kind, name)
    if not path.is_file():
        raise OpPreconditionError(f"{kind}.{name} нет в основной конфигурации {base.root}")
    text = path.read_bytes().decode("utf-8-sig")
    rx = (r'^<mdclass:\w+ [^>]*?\buuid="([0-9a-f-]{36})"' if base.fmt == "edt"
          else rf'<{kind} uuid="([0-9a-f-]{{36}})"')
    m = re.search(rx, text, re.M)
    if not m:
        raise OpPreconditionError(f"{path}: не найден uuid объекта")
    if "<objectBelonging>Adopted" in text or "<ObjectBelonging>Adopted" in text:
        raise OpPreconditionError(f"--base {base.root}: это расширение, нужна основная конфигурация")
    return m.group(1), text


def adopt(src: Path, base_src: Path, ref: str) -> Path:
    """Заимствовать объект основной конфигурации в расширение."""
    validate_object_ref(ref)
    kind_raw, name = ref.split(".")
    kind = resolve_kind(kind_raw)
    p, base = open_project(src), open_project(base_src)
    if not p.extension:
        raise OpPreconditionError(f"{p.root}: не расширение — заимствовать некуда")
    if base.extension:
        raise OpPreconditionError(f"--base {base.root}: это расширение, нужна основная конфигурация")
    if base.fmt != p.fmt:
        raise OpPreconditionError("расширение и основная конфигурация — в разных форматах (EDT/Конфигуратор)")
    if not META["kinds"][kind].get("adopt"):
        raise OpPreconditionError(f"заимствование вида {kind} не поддержано")
    base_uuid, base_text = _base_object(base, kind, name)
    _check_absent(p, kind, name)
    text = render(_template(p.fmt, "adopted", kind), p.fmt, name, {}, base_uuid)
    text = _copy_controlled(text, p.fmt, base_text, _template("edt", "adopted", kind))
    path = p.main_file(kind, name)
    _commit_new(p, path, text, _manifest_add(p, kind, name))
    return path


def _copy_controlled(text: str, fmt: str, base_text: str, edt_tpl: str) -> str:
    """Контролируемые свойства заимствованного (`<свойство>Checked` в блоке extension эталона: флаги
    общего модуля, периодичность и режим записи регистра сведений, вид регистра накопления…) — значением
    из типовой, иначе EDT: «значение контролируемого свойства не совпадает с расширяемой конфигурацией»."""
    controlled = [t for t in re.findall(r"^    <(\w+)>Checked</\1>", edt_tpl, re.M)
                  if t != "extendedConfigurationObject"]
    base_text = base_text.replace("\r\n", "\n")
    if "<typeExtension>" in edt_tpl:                     # тип объекта (константа) — контролируется целиком
        text = _copy_type(text, fmt, base_text)
    if fmt == "edt":
        lines = [m.group(0) for m in re.finditer(r"^  <(\w+)>[^<\n]*</\1>\n", base_text, re.M)
                 if m.group(1) in controlled]          # EDT пишет только не-умолчания — их и переносим
        close = re.search(r"^</mdclass:\w+>", text, re.M)
        return text[:close.start()] + "".join(lines) + text[close.start():]
    for tag in controlled:
        cap = tag[0].upper() + tag[1:]
        m = re.search(rf"<{cap}>([^<]*)</{cap}>", base_text)
        if m and re.search(rf"<{cap}>", text):
            text = _set_cfg_prop(text, tag, m.group(1))
    return text


def _copy_type(text: str, fmt: str, base_text: str) -> str:
    """Тип заимствованного = тип объекта типовой. EDT: `typeExtension` — по `<types>` на каждый тип
    (`state` Checked) и квалификаторы типовой, как у реквизитов анЯдро; Конфигуратор: блок `<Type>` целиком."""
    if fmt == "edt":
        m = re.search(r"^  <type>\n(.*?)^  </type>\n", base_text, re.M | re.S)
        if not m:
            raise OpPreconditionError("у объекта типовой нет типа — заимствование не собрать")
        body = m.group(1)
        types = "".join(f"      <types>\n        <state>Checked</state>\n        <type>{t}</type>\n      </types>\n"
                        for t in re.findall(r"^    <types>([^<]+)</types>", body, re.M))
        quals = "".join(re.sub(r"^(?=.)", "  ", q.group(0), flags=re.M)
                        for q in re.finditer(r"^    <(\w+Qualifiers)>\n.*?^    </\1>\n", body, re.M | re.S))
        new = f"    <typeExtension>\n{types}{quals}    </typeExtension>\n"
        text, n = re.subn(r"^    <typeExtension>\n.*?^    </typeExtension>\n", lambda _: new, text,
                          count=1, flags=re.M | re.S)
    else:
        m = re.search(r"^(\t+)<Type>\n.*?^\1</Type>\n", base_text, re.M | re.S)
        if not m:
            raise OpPreconditionError("у объекта типовой нет типа — заимствование не собрать")
        text, n = re.subn(r"^(\t+)<Type>\n.*?^\1</Type>\n", lambda _: m.group(0), text, count=1, flags=re.M | re.S)
    if not n:
        raise OpPreconditionError("эталон заимствования: блок типа не найден — переснять эталоны")
    return text


def references(p: Project, kind: str, name: str, code: bool = True, limit: int = 20) -> list[str]:
    """Упоминания объекта в проекте вне его собственных файлов и манифеста: «файл:строка»."""
    ru_alts = RU[kind][1]
    alts = [f"{kind}\\w*", *ru_alts]
    rx = re.compile(rf"(?<![\w.])(?:{'|'.join(alts)})\.{re.escape(name)}(?!\w)", re.I)
    bare = re.compile(rf"(?<![\w.]){re.escape(name)}\s*\.", re.I) if kind == "CommonModule" else None
    own = [x.resolve() for x in p.object_paths(kind, name)]
    hits = []
    for f in p.root.rglob("*"):
        if f.suffix.lower() not in _REF_SUFFIXES or not f.is_file() or f.resolve() == p.manifest.resolve():
            continue
        if not code and f.suffix.lower() == ".bsl":
            continue
        rf = f.resolve()
        if any(rf == o or o in rf.parents for o in own):
            continue
        try:
            text = f.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if rx.search(line) or (bare and f.suffix.lower() == ".bsl" and bare.search(line)):
                hits.append(f"{f.relative_to(p.root).as_posix()}:{no}")
                if len(hits) >= limit:
                    return hits
    return hits


def delete(src: Path, ref: str) -> list[Path]:
    """Удалить объект: свой — без ссылок на него; заимствованный — только «голый»."""
    validate_object_ref(ref)
    kind_raw, name = ref.split(".")
    kind = resolve_kind(kind_raw)
    p = open_project(src)
    main = p.main_file(kind, name)
    if not main.is_file():
        raise OpPreconditionError(f"{kind}.{name} не найден: {main}")
    text = main.read_bytes().decode("utf-8-sig")
    adopted = "<objectBelonging>Adopted" in text or "<ObjectBelonging>Adopted" in text
    if adopted:
        extra = [x for x in _OWN_CONTENT if x in text]
        if p.fmt == "edt":
            extra += [f.name for f in main.parent.iterdir() if f != main]
        else:
            if (main.parent / name).exists():
                extra.append(f"{name}/")
            if re.search(r"<ChildObjects>\s*<", text):
                extra.append("ChildObjects")
        if extra:
            raise OpPreconditionError(f"в заимствованном {kind}.{name} есть своё ({', '.join(sorted(set(extra)))}) "
                                      "— сначала убери это в IDE, потом снимай заимствование")
    hits = references(p, kind, name, code=not adopted)
    if hits:
        raise OpPreconditionError(f"на {kind}.{name} есть ссылки — сначала убери их:\n  " + "\n  ".join(hits))
    manifest_text = _manifest_remove(p, kind, name)
    removed = [x for x in p.object_paths(kind, name) if x.exists()]
    # сначала файлы — в корзину рядом (тот же том, перенос атомарный), потом манифест; сбой — всё назад
    trash = Path(tempfile.mkdtemp(prefix=".object_delete_", dir=p.root))
    moved = []
    try:
        for i, x in enumerate(removed):
            dst = trash / f"{i}_{x.name}"
            os.replace(x, dst)
            moved.append((x, dst))
        _write_atomic(p.manifest, manifest_text)
    except BaseException:
        for x, dst in reversed(moved):
            os.replace(dst, x)
        shutil.rmtree(trash, ignore_errors=True)
        raise
    shutil.rmtree(trash, ignore_errors=True)
    return removed
