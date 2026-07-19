"""Офлайн property-level scope-guard: «не тронул незатронутое».

Дополняет apply.dumpload.roundtrip_verify, у которого две границы:
  1) гранулярность ФАЙЛА (filecmp целых файлов) — не показывает, КАКОЕ свойство
     разъехалось внутри файла;
  2) нужна живая ТЕСТ-база + платформа (полный upload/load/dump цикл).

Этот модуль сравнивает два дерева выгрузки БЕЗ платформы и НА УРОВНЕ СВОЙСТВ:
для каждого XML-файла строится плоская карта {путь-элемента → значение}, и любое
изменение по пути, который правка менять не должна была, — блокируется с точным
указанием «файл :: путь». Ловит баг «у нетронутых объектов расширения разъехались
свойства»: незатронутый объект обязан остаться байт-в-байт, а внутри затронутого
разрешается менять только объявленные свойства.

Использование:
    assert_in_scope(before_dir, after_dir, {
        "Catalogs/Товары/Ext/...xml": "ALL",     # весь файл разрешено менять
        "Catalogs/Заказы/...xml": {"Comment"},   # только свойство Comment
    })
Файл, не указанный в scope, обязан остаться неизменным (кроме ignore).
"""
import unicodedata
from pathlib import Path

from lxml import etree

# служебные файлы выгрузки: платформа переписывает при каждом дампе
IGNORE_DUMP_FILES = frozenset({"ConfigDumpInfo.xml"})

ALL = "ALL"  # маркер «весь файл разрешено менять целиком»


class ScopeViolation(Exception):
    """Правка затронула то, что не должна была (объект/свойство вне scope)."""


def _ln(el) -> str:
    return etree.QName(el).localname


def flatten(root) -> dict:
    """Плоская карта {позиционный путь → значение} по дереву.

    Путь = цепочка localname с индексом среди СОБРАТЬЕВ того же имени, чтобы
    перестановка/добавление соседей не маскировала подмену. Значение листа —
    текст + отсортированные атрибуты (без учёта форматирующих пробелов).
    """
    out = {}

    def walk(el, prefix):
        counts = {}
        for child in el:
            if not isinstance(child.tag, str):  # комментарии/PI
                continue
            name = _ln(child)
            idx = counts.get(name, 0)
            counts[name] = idx + 1
            path = f"{prefix}/{name}[{idx}]"
            attrs = ";".join(f"{_ln_attr(k)}={v}"
                             for k, v in sorted(child.attrib.items()))
            text = (child.text or "").strip()
            out[path] = f"text={text}|attrs={attrs}"
            walk(child, path)

    walk(root, "")
    return out


def _ln_attr(key: str) -> str:
    # {ns}local → local (namespace в имени атрибута для сравнения не нужен)
    return key.split("}")[-1] if "}" in key else key


def _parse(path: Path):
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False,
                             huge_tree=True)
    return etree.parse(str(path), parser).getroot()


def diff_file(before: Path, after: Path) -> list:
    """Список изменённых путей свойств между двумя XML-файлами (localname-пути)."""
    fb, fa = flatten(_parse(before)), flatten(_parse(after))
    changed = []
    for path in sorted(set(fb) | set(fa)):
        if fb.get(path) != fa.get(path):
            changed.append(path)
    return changed


def _leaf_name(path: str) -> str:
    # ".../Properties[0]/Synonym[0]" → "Synonym"
    last = path.rsplit("/", 1)[-1]
    return last.split("[", 1)[0]


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _rel_map(root: Path) -> dict:
    return {_nfc(p.relative_to(root).as_posix()): p
            for p in root.rglob("*") if p.is_file()}


def assert_in_scope(before: Path, after: Path, scope: dict,
                    ignore: frozenset = IGNORE_DUMP_FILES) -> None:
    """Проверить, что изменения между before/after не выходят за `scope`.

    scope: {relpath → "ALL" | set(имён свойств-листьев, которые можно менять)}.
    Файл вне scope обязан остаться неизменным. Любое изменение вне scope →
    ScopeViolation с перечнем «relpath :: путь».
    """
    before, after = Path(before), Path(after)
    scope = {_nfc(k): v for k, v in scope.items()}
    ignored = {_nfc(s) for s in ignore}
    mb, ma = _rel_map(before), _rel_map(after)

    violations = []
    for rel in sorted(set(mb) | set(ma)):
        if rel in ignored:
            continue
        allowed = scope.get(rel)
        in_b, in_a = rel in mb, rel in ma

        if in_b != in_a:  # файл добавлен или удалён
            if allowed is None:
                kind = "добавлен" if in_a else "удалён"
                violations.append(f"{rel} :: файл {kind} вне scope")
            continue

        if allowed == ALL:
            continue

        changed = diff_file(mb[rel], ma[rel])
        if not changed:
            continue
        if allowed is None:
            # незатронутый файл изменился — дрейф свойств нетронутого объекта
            for p in changed:
                violations.append(f"{rel} :: {_leaf_name(p)} ({p})")
        else:
            for p in changed:
                if _leaf_name(p) not in allowed:
                    violations.append(f"{rel} :: {_leaf_name(p)} ({p}) вне scope")

    if violations:
        raise ScopeViolation(
            "Правка вышла за scope (затронуто незатронутое):\n  "
            + "\n  ".join(violations))
