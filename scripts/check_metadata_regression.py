#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Гейт от МОЛЧАЛИВОЙ потери метаданных и прав 1С (формат исходников EDT).

Зачем. Файлы `.mdo` и `.rights` откатываются при слияниях из веток, отрезанных от старых точек:
IDE держит модель конфигурации в памяти и при сохранении перезаписывает файл ЦЕЛИКОМ из неё. Для git
это обычная правка, а не конфликт, поэтому при слиянии побеждает та сторона, где состояние старее.
Ревью такие файлы пропускает (XML на сотни строк), а `git log -- <файл>` про потерю не покажет:
слияние, результат которого совпал с одним из родителей, из истории пути выпадает.

Что проверяет:
  1. РЕГРЕСС `.mdo` — пропал подчинённый объект (реквизит, измерение, ресурс, форма, табличная часть,
     макет, команда, значение перечисления). Ловится универсально: в формате EDT у такого объекта
     есть атрибут `uuid` и дочерний `<name>`.
  2. РЕГРЕСС `.rights` — пропал объект прав, пропало право или ТИХО ИЗМЕНИЛОСЬ его значение
     (снятый запрет открывает доступ и в диффе выглядит безобидно).
  3. УДАЛЁННЫЕ ФАЙЛЫ — объект метаданных или файл прав исчез целиком.
  4. ЗАПРЕЩЁННЫЕ ПУТИ — возврат каталога, который в проекте удалён осознанно (их приносят слияния
     из старых ветвей). Список задаётся в репозитории:
     `git config --add hooks.metadataGateForbiddenPath "src/old-layout/"`.

Переименование выглядит как «пропал + появился» и тоже показывается — осознанно: переименование
объекта метаданных стоит увидеть глазами.

Известный пробел: содержимое `.form` (пропажа отдельных элементов формы) не сравнивается — только
регистрация самой формы в `.mdo`. Сравнение идёт с ЛОКАЛЬНЫМ состоянием базовой ветки: перед
проверкой имеет смысл `git fetch`, иначе устаревший снимок даёт ложное «чисто».

Запуск (в корне репозитория с исходниками):
  python scripts/check_metadata_regression.py                       # HEAD против origin/develop (или origin/main)
  python scripts/check_metadata_regression.py --base origin/main
  python scripts/check_metadata_regression.py --base A --head B
  python scripts/check_metadata_regression.py --allow "СтарыйРеквизит"   # осознанное удаление
  python scripts/check_metadata_regression.py --selftest             # самопроверка на временном репозитории

Код возврата: 0 — ок; 1 — есть находки; 2 — ошибка запуска (не git, нет базовой ревизии).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def forbidden_paths() -> tuple[str, ...]:
    """Каталоги, которые в этом репозитории удалены осознанно и не должны возвращаться.

    Задаются в репозитории (специфика проекта, не зашита в скрипт):
        git config --add hooks.metadataGateForbiddenPath "src/old-layout/"
    """
    out, code = git("config", "--get-all", "hooks.metadataGateForbiddenPath")
    return tuple(line.strip() for line in out.splitlines() if line.strip()) if code == 0 else ()


def git(*args: str) -> tuple[str, int]:
    res = subprocess.run(("git",) + args, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    return (res.stdout or "").strip(), res.returncode


def ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", ref)[1] == 0


def file_at(ref: str, path: str) -> str | None:
    out, code = git("show", f"{ref}:{path}")
    return out if code == 0 else None


def localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_text(elem: ET.Element, wanted: str) -> str | None:
    for child in elem:
        if localname(child.tag) == wanted:
            return (child.text or "").strip() or None
    return None


def parse(xml_text: str) -> ET.Element | None:
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError:
        return None


def collect_mdo(xml_text: str) -> set[tuple[str, str]] | None:
    """Подчинённые объекты метаданных как (вид, путь-имён).

    Универсальное правило EDT: подчинённый объект = элемент с атрибутом `uuid` и дочерним `<name>`.
    Путь включает владельцев, чтобы одноимённые реквизиты разных табличных частей не склеивались.
    """
    root = parse(xml_text)
    if root is None:
        return None

    found: set[tuple[str, str]] = set()

    def walk(elem: ET.Element, owners: tuple[str, ...]) -> None:
        for child in elem:
            if localname(child.tag) == "name":
                continue
            name = child_text(child, "name")
            if name and "uuid" in child.attrib:
                found.add((localname(child.tag), ".".join(owners + (name,))))
                walk(child, owners + (name,))
            else:
                walk(child, owners)

    walk(root, ())
    return found


def collect_rights(xml_text: str) -> dict[str, str] | None:
    """Права как {'Объект/Право': 'значение'} — чтобы видеть и пропажу, и тихую смену значения."""
    root = parse(xml_text)
    if root is None:
        return None

    rights: dict[str, str] = {}
    for obj in root:
        if localname(obj.tag) != "object":
            continue
        obj_name = child_text(obj, "name")
        if not obj_name:
            continue
        for right in obj:
            if localname(right.tag) != "right":
                continue
            right_name = child_text(right, "name")
            if right_name:
                rights[f"{obj_name}/{right_name}"] = child_text(right, "value") or ""
    return rights


def under_forbidden(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in forbidden_paths())


def changed(base: str, head: str, *patterns: str) -> list[str]:
    out, _ = git("diff", "--name-only", base, head, "--", *patterns)
    return [line for line in out.splitlines() if line.strip() and not under_forbidden(line)]


def touched_by_branch(base: str, head: str, paths: list[str]) -> set[str]:
    """Файлы, которые ИЗМЕНИЛА сама ветка (относительно точки расхождения с базой).

    Зачем фильтр: если ветка просто отстала от базы, файл в ней старее — но при слиянии победит
    версия базы и ничего не потеряется. Опасен только случай, когда ветка сама перезаписала файл
    (обычно это EDT из устаревшей модели) — тогда её версия и победит.
    """
    merge_base, code = git("merge-base", base, head)
    if code != 0 or not merge_base:
        return set(paths)                                   # нет общей истории - проверяем всё
    out, _ = git("diff", "--name-only", merge_base, head, "--", *paths) if paths else ("", 0)
    return {line for line in out.splitlines() if line.strip()}


_MDO_FULL = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:InformationRegister xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="a1">
  <name>Приоритеты</name>
  <resources uuid="r1"><name>Организация</name></resources>
  <resources uuid="r2"><name>Склад</name></resources>
  <forms uuid="f1"><name>ФормаСписка</name></forms>
</mdclass:InformationRegister>
"""
_MDO_CUT = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:InformationRegister xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="a1">
  <name>Приоритеты</name>
  <resources uuid="r2"><name>Склад</name></resources>
</mdclass:InformationRegister>
"""
_RIGHTS = """<?xml version="1.0" encoding="UTF-8"?>
<Rights xmlns="http://v8.1c.ru/8.2/roles">
  <object><name>Subsystem.Интеграции</name><right><name>View</name><value>%s</value></right></object>
</Rights>
"""


def selftest() -> int:
    """Проверка на одноразовом репозитории: ловим ровно то, ради чего гейт и сделан.

    Текущий репозиторий не трогается — всё происходит во временном каталоге."""
    script = Path(__file__).resolve()

    def run(cwd: str, *args: str) -> tuple[str, int]:
        res = subprocess.run([sys.executable, str(script), *args], cwd=cwd,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        return (res.stdout or "") + (res.stderr or ""), res.returncode

    def g(cwd: str, *args: str) -> None:
        res = subprocess.run(("git",) + args, cwd=cwd, capture_output=True, text=True)
        assert res.returncode == 0, f"git {' '.join(args)}: {res.stderr}"

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        g(tmp, "init", "-q", "-b", "main")
        g(tmp, "config", "user.email", "selftest@example.com")
        g(tmp, "config", "user.name", "selftest")
        mdo, rights = os.path.join(src, "reg.mdo"), os.path.join(src, "Rights.rights")
        Path(mdo).write_text(_MDO_FULL, encoding="utf-8")
        Path(rights).write_text(_RIGHTS % "false", encoding="utf-8")
        g(tmp, "add", ".")
        g(tmp, "commit", "-q", "-m", "base")
        base = subprocess.run(("git", "rev-parse", "HEAD"), cwd=tmp,
                              capture_output=True, text=True).stdout.strip()

        # 1. пропажа реквизита и формы — как после слияния устаревшего состояния
        Path(mdo).write_text(_MDO_CUT, encoding="utf-8")
        g(tmp, "commit", "-q", "-am", "IDE переписала файл из устаревшей модели")
        out, code = run(tmp, "--base", base)
        if code != 1 or "Организация" not in out or "ФормаСписка" not in out:
            failures.append(f"не поймана пропажа объектов .mdo (код {code}): {out.strip()[:200]}")

        # 2. осознанное удаление с --allow проходит
        out, code = run(tmp, "--base", base, "--allow", "Организация", "--allow", "ФормаСписка")
        if code != 0:
            failures.append(f"--allow не пропустил осознанное удаление (код {code}): {out.strip()[:200]}")

        # 3. тихая смена значения права
        g(tmp, "checkout", "-q", "-b", "rights-case", base)
        Path(rights).write_text(_RIGHTS % "true", encoding="utf-8")
        g(tmp, "commit", "-q", "-am", "запрет тихо снят")
        out, code = run(tmp, "--base", base)
        if code != 1 or "View" not in out:
            failures.append(f"не поймана смена значения права (код {code}): {out.strip()[:200]}")

        # 4. добавление объектов — не находка
        g(tmp, "checkout", "-q", "-b", "add-case", base)
        Path(mdo).write_text(_MDO_FULL.replace(
            "</mdclass:InformationRegister>",
            '  <resources uuid="r3"><name>Подразделение</name></resources>\n'
            "</mdclass:InformationRegister>"), encoding="utf-8")
        g(tmp, "commit", "-q", "-am", "добавлен реквизит")
        out, code = run(tmp, "--base", base)
        if code != 0:
            failures.append(f"ложная находка на добавлении объекта (код {code}): {out.strip()[:200]}")

        # 5. возврат удалённого каталога — по списку из конфигурации репозитория
        g(tmp, "checkout", "-q", "-b", "forbidden-case", base)
        g(tmp, "config", "--add", "hooks.metadataGateForbiddenPath", "src/old-layout/")
        os.makedirs(os.path.join(src, "old-layout"))
        Path(os.path.join(src, "old-layout", "old.mdo")).write_text(_MDO_FULL, encoding="utf-8")
        g(tmp, "add", ".")
        g(tmp, "commit", "-q", "-m", "старый каталог вернулся слиянием")
        out, code = run(tmp, "--base", base)
        if code != 1 or "src/old-layout/" not in out:
            failures.append(f"не пойман возврат удалённого каталога (код {code}): {out.strip()[:200]}")

    if failures:
        print("[selftest] ПРОВАЛЕНО:")
        for f in failures:
            print("  " + f)
        return 1
    print("[selftest] ок: пропажа объектов, смена права, --allow, добавление, запрещённый путь")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Гейт от молчаливой потери метаданных и прав 1С")
    parser.add_argument("--base", default=None,
                        help="ревизия-эталон (по умолчанию origin/develop, иначе origin/main)")
    parser.add_argument("--head", default="HEAD", help="проверяемая ревизия (по умолчанию HEAD)")
    parser.add_argument("--allow", action="append", default=[],
                        help="имя объекта/права, удаление которого осознанно (можно повторять)")
    parser.add_argument("--selftest", action="store_true",
                        help="самопроверка на временном репозитории (ничего не трогает в текущем)")
    parser.add_argument("--strict", action="store_true",
                        help="проверять и файлы, которых ветка не трогала (для аудита пары ревизий)")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    if args.base is None:
        args.base = next((r for r in ("origin/develop", "origin/main") if ref_exists(r)), "origin/develop")

    if git("rev-parse", "--git-dir")[1] != 0:
        print("ОШИБКА: запускать в корне git-репозитория", file=sys.stderr)
        return 2
    for ref in (args.base, args.head):
        if not ref_exists(ref):
            print(f"ОШИБКА: не найдена ревизия '{ref}'. Сделайте git fetch или укажите --base.",
                  file=sys.stderr)
            return 2

    allow = set(args.allow)
    problems: list[str] = []

    def allowed(name: str) -> bool:
        return name in allow or name.split(".")[-1] in allow or name.split("/")[0] in allow

    # 1, 3. Регресс внутри .mdo и объекты, удалённые целиком.
    mdo_paths = changed(args.base, args.head, "*.mdo")
    if not args.strict:
        own = touched_by_branch(args.base, args.head, mdo_paths)
        mdo_paths = [p for p in mdo_paths if p in own]
    for path in mdo_paths:
        before, after = file_at(args.base, path), file_at(args.head, path)
        if before is None:
            continue                                        # добавлен — норма
        if after is None:
            problems.append(f"УДАЛЁН ОБЪЕКТ МЕТАДАННЫХ: {path}")
            continue
        objects_before, objects_after = collect_mdo(before), collect_mdo(after)
        if objects_after is None:
            problems.append(f"НЕ РАЗБИРАЕТСЯ XML: {path}")
            continue
        if objects_before is None:
            continue                                        # эталон битый — сравнивать не с чем
        for kind, name in sorted(objects_before - objects_after):
            if not allowed(name):
                problems.append(f"ПРОПАЛО в {path}: {kind} «{name}»")

    # 2, 3. Регресс прав: пропажа объекта/права и тихая смена значения.
    rights_paths = changed(args.base, args.head, "*.rights")
    if not args.strict:
        own = touched_by_branch(args.base, args.head, rights_paths)
        rights_paths = [p for p in rights_paths if p in own]
    for path in rights_paths:
        before, after = file_at(args.base, path), file_at(args.head, path)
        if before is None:
            continue
        if after is None:
            problems.append(f"УДАЛЁН ФАЙЛ ПРАВ: {path}")
            continue
        rights_before, rights_after = collect_rights(before), collect_rights(after)
        if rights_after is None:
            problems.append(f"НЕ РАЗБИРАЕТСЯ XML: {path}")
            continue
        if rights_before is None:
            continue
        for key in sorted(set(rights_before) - set(rights_after)):
            if not allowed(key):
                problems.append(f"ПРОПАЛО ПРАВО в {path}: «{key}» (было {rights_before[key]})")
        for key in sorted(set(rights_before) & set(rights_after)):
            if rights_before[key] != rights_after[key] and not allowed(key):
                problems.append(f"ИЗМЕНЕНО ПРАВО в {path}: «{key}» "
                                f"{rights_before[key]} -> {rights_after[key]}")

    # 4. Возврат мёртвых каталогов.
    for prefix in forbidden_paths():
        if git("ls-tree", "-r", "--name-only", args.head, "--", prefix.rstrip("/"))[0].strip():
            problems.append(f"ВЕРНУЛСЯ УДАЛЁННЫЙ КАТАЛОГ: {prefix} — его приносят слияния из старых ветвей")

    if not problems:
        print(f"[metadata-gate] чисто: {args.head} против {args.base} — потерь нет")
        return 0

    print(f"[metadata-gate] НАЙДЕНО: {len(problems)}  ({args.head} против {args.base})\n")
    for problem in problems:
        print("  " + problem)
    print(f"\nПочти всегда причина одна: EDT перезаписал файл из устаревшей модели.")
    print(f"Как чинить: git merge {args.base} в свою ветку, затем Refresh (F5) в IDE, проверить объект,")
    print(f"при необходимости вернуть файл: git restore --source={args.base} -- <путь>")
    print("Если удаление осознанное — перечислите имена через --allow или пропустите: git push --no-verify")
    return 1


if __name__ == "__main__":
    sys.exit(main())
