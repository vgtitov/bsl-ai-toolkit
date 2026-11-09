"""Снять эталоны новых объектов метаданных у самого EDT (для `1c-meta object add|adopt`).

Во ВРЕМЕННЫХ проектах работающего EDT (MCP :8765) создаётся объект каждого вида с именем-маркером,
в проекте-расширении — заимствование каждого из них и попытка создать собственный объект того же
вида (что расширению нельзя — видно по отказу EDT). Затем оба проекта выгружаются в формат
Конфигуратора. Итог в --out:

    edt/own/<Вид>/…          файлы объекта из src основной конфигурации (.mdo, модули)
    edt/adopted/<Вид>/…      то же из расширения (заимствованный)
    edt/ext_own/<Вид>/…      собственный объект расширения
    edt/Configuration.mdo, edt/ext_Configuration.mdo   манифесты
    cfg/base/…, cfg/ext/…    выгрузка в XML (Конфигуратор) целиком
    report.json              что создалось, что EDT отверг и почему

Проекты пользователя не трогаются; временные удаляются в конце (--keep — оставить для разбора).
Повторяемо: новая версия EDT/платформы — переснять и сравнить с onec_metadata/templates/objects.

    uv run --no-project python scripts/learn_object_templates.py --out <каталог> [--kinds Catalog,Document]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from onec_metadata.apply.edt_cascade import CascadeClient, CascadeError  # noqa: E402

MARK = "МаркерЭталона"
EXT_PREFIX = "мр_"
SYNONYM = "Маркер синоним"

# Вид в FQN EDT → каталог в src (EDT) и в выгрузке Конфигуратора (они совпадают)
KINDS = {
    "Subsystem": "Subsystems", "CommonModule": "CommonModules", "SessionParameter": "SessionParameters",
    "Role": "Roles", "CommonAttribute": "CommonAttributes", "ExchangePlan": "ExchangePlans",
    "FilterCriterion": "FilterCriteria", "EventSubscription": "EventSubscriptions",
    "ScheduledJob": "ScheduledJobs", "FunctionalOption": "FunctionalOptions",
    "FunctionalOptionsParameter": "FunctionalOptionsParameters", "DefinedType": "DefinedTypes",
    "SettingsStorage": "SettingsStorages", "CommonForm": "CommonForms", "CommonCommand": "CommonCommands",
    "CommandGroup": "CommandGroups", "CommonTemplate": "CommonTemplates", "CommonPicture": "CommonPictures",
    "XDTOPackage": "XDTOPackages", "WebService": "WebServices", "HTTPService": "HTTPServices",
    "WSReference": "WSReferences", "StyleItem": "StyleItems", "Style": "Styles",
    "Constant": "Constants", "Catalog": "Catalogs", "Document": "Documents",
    "DocumentNumerator": "DocumentNumerators", "Sequence": "Sequences", "DocumentJournal": "DocumentJournals",
    "Enum": "Enums", "Report": "Reports", "DataProcessor": "DataProcessors",
    "ChartOfCharacteristicTypes": "ChartsOfCharacteristicTypes", "ChartOfAccounts": "ChartsOfAccounts",
    "ChartOfCalculationTypes": "ChartsOfCalculationTypes", "InformationRegister": "InformationRegisters",
    "AccumulationRegister": "AccumulationRegisters", "AccountingRegister": "AccountingRegisters",
    "CalculationRegister": "CalculationRegisters", "BusinessProcess": "BusinessProcesses",
    "Task": "Tasks", "ExternalDataSource": "ExternalDataSources", "IntegrationService": "IntegrationServices",
}


# Варианты вида: пресет EDT (аргументы create_metadata) и/или свойства (modify_metadata) с НЕ умолчательными
# значениями — EDT умолчания не пишет, вариант показывает, где в .mdo стоит каждое свойство.
VARIANTS = {
    "CommonModule": {
        "ServerCall": ({"commonModuleKind": "ServerCall"}, []),
        "ClientManaged": ({"commonModuleKind": "ClientManaged"}, []),
        "ClientServer": ({"commonModuleKind": "ClientServer"}, []),
        "Global": ({"commonModuleKind": "Global"}, []),
        "ServerPrivileged": ({"commonModuleKind": "Server", "privileged": True}, []),
    },
    "InformationRegister": {
        "Periodic": ({}, [{"name": "informationRegisterPeriodicity", "value": "Month"},
                          {"name": "writeMode", "value": "RecorderSubordinate"}]),
    },
    "AccumulationRegister": {
        "Turnover": ({}, [{"name": "registerType", "value": "Turnovers"}]),
    },
}


def project_path(cli: CascadeClient, name: str) -> Path:
    for line in cli.call_tool("list_projects", {})["_text"].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[0] == name:
            return Path(cells[2])
    raise CascadeError(f"проект {name} не найден в workspace")


def call(cli, tool, args, wait=300):
    """call_tool с повтором, пока EDT отвечает «Project is building» (после каждой правки — сборка)."""
    deadline = time.time() + wait
    while True:
        try:
            return cli.call_tool(tool, args)
        except CascadeError as e:
            if "is building" not in str(e) or time.time() > deadline:
                raise
            time.sleep(3)


def create(cli, report, key, project, fqn, **extra):
    try:
        res = call(cli, "create_metadata", {"projectName": project, "fqn": fqn, **extra})
        report[key] = {"ok": True, "text": res["_text"][:300]}
    except CascadeError as e:
        report[key] = {"ok": False, "error": str(e)[:600]}
    return report[key]["ok"]


def copy_object(src_root: Path, kind: str, name: str, dst: Path) -> list[str]:
    folder = src_root / KINDS[kind] / name
    if not folder.is_dir():
        return []
    shutil.copytree(folder, dst, dirs_exist_ok=True)
    return sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="каталог для сырых эталонов (будет очищен)")
    ap.add_argument("--kinds", help="только эти виды, через запятую")
    ap.add_argument("--version", default="8.3.27", help="версия платформы временной конфигурации")
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--keep", action="store_true", help="не удалять временные проекты")
    a = ap.parse_args(argv)

    kinds = [k.strip() for k in a.kinds.split(",")] if a.kinds else list(KINDS)
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise SystemExit(f"неизвестные виды: {unknown}")
    out = Path(a.out).resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    cli = CascadeClient(a.url, timeout=600)
    cli.initialize()
    stamp = time.strftime("%H%M%S")
    base, ext_name = f"ЭталоныОбъектов{stamp}", f"ЭталоныРасширение{stamp}"
    report = {"edt": cli.call_tool("get_edt_version", {})["_text"][:200], "version": a.version,
              "base_project": base, "own": {}, "adopt": {}, "ext_own": {}, "files": {}}
    created = []
    try:
        cli.call_tool("create_project", {"projectKind": "configuration", "name": base, "version": a.version,
                                         "standardChecks": False, "commonChecks": False})
        created.append(base)
        if not cli.wait_project(base):
            raise CascadeError(f"проект {base} не стал ready")
        for k in kinds:
            extra = {"commonModuleKind": "Server"} if k == "CommonModule" else {}
            create(cli, report["own"], k, base, f"{k}.{MARK}",
                   properties=[{"name": "synonym", "value": SYNONYM, "language": "ru"}], **extra)
            for var, (args, props) in VARIANTS.get(k, {}).items():
                key, fqn = f"{k}@{var}", f"{k}.{MARK}{var}"
                if create(cli, report["own"], key, base, fqn,
                          properties=[{"name": "synonym", "value": SYNONYM, "language": "ru"}], **args) and props:
                    try:
                        call(cli, "modify_metadata", {"projectName": base, "fqn": fqn, "properties": props})
                    except CascadeError as e:
                        report["own"][key] = {"ok": False, "error": str(e)[:600]}

        r = cli.call_tool("create_project", {"projectKind": "extension", "name": ext_name, "baseProjectName": base,
                                             "prefix": EXT_PREFIX, "standardChecks": False, "commonChecks": False})
        ext = r.get("project") or f"{base}.{ext_name}"
        created.append(ext)
        report["ext_project"] = ext
        if not cli.wait_project(ext):
            raise CascadeError(f"проект {ext} не стал ready")
        for k in kinds:
            if report["own"][k]["ok"]:
                try:
                    res = call(cli, "adopt_metadata_object", {"projectName": base, "fqn": f"{k}.{MARK}",
                                                                  "extensionProjectName": ext})
                    report["adopt"][k] = {"ok": True, "text": res["_text"][:300]}
                except CascadeError as e:
                    report["adopt"][k] = {"ok": False, "error": str(e)[:600]}
            extra = {"commonModuleKind": "Server"} if k == "CommonModule" else {}
            create(cli, report["ext_own"], k, ext, f"{k}.{EXT_PREFIX}{MARK}",
                   properties=[{"name": "synonym", "value": SYNONYM, "language": "ru"}], **extra)

        time.sleep(5)                                   # дать EDT дописать .mdo на диск
        base_src, ext_src = project_path(cli, base) / "src", project_path(cli, ext) / "src"
        (out / "edt").mkdir(parents=True)
        shutil.copy2(base_src / "Configuration" / "Configuration.mdo", out / "edt" / "Configuration.mdo")
        shutil.copy2(ext_src / "Configuration" / "Configuration.mdo", out / "edt" / "ext_Configuration.mdo")
        for k in kinds:
            report["files"][k] = {
                "own": copy_object(base_src, k, MARK, out / "edt" / "own" / k),
                "adopted": copy_object(ext_src, k, MARK, out / "edt" / "adopted" / k),
                "ext_own": copy_object(ext_src, k, EXT_PREFIX + MARK, out / "edt" / "ext_own" / k)}
            for var in VARIANTS.get(k, {}):
                report["files"][f"{k}@{var}"] = {
                    "own": copy_object(base_src, k, MARK + var, out / "edt" / "own" / f"{k}@{var}")}
        for proj, sub in ((base, "base"), (ext, "ext")):
            try:
                call(cli, "export_configuration_to_xml", {"projectName": proj,
                                                              "outputPath": str(out / "cfg" / sub)})
            except CascadeError as e:
                report[f"export_{sub}"] = str(e)[:600]
    finally:
        if not a.keep:
            for proj in reversed(created):
                try:
                    cli.call_tool("delete_project", {"projectName": proj, "deleteContent": True, "confirm": True})
                except CascadeError as e:
                    report.setdefault("cleanup_errors", []).append(f"{proj}: {str(e)[:300]}")
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    own_ok = sum(v["ok"] for v in report["own"].values())
    print(f"собственные: {own_ok}/{len(kinds)}; заимствовано: {sum(v['ok'] for v in report['adopt'].values())}; "
          f"собственные в расширении: {sum(v['ok'] for v in report['ext_own'].values())}; отчёт: {out / 'report.json'}")


# ---------- сырьё → шаблоны onec_metadata/templates/objects ----------

UUID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
CM_FLAGS = ("global", "clientManagedApplication", "server", "externalConnection", "serverCall",
            "clientOrdinaryApplication")
ADOPTED_KEEP = {"producedTypes", "name", "objectBelonging", "extension"}


def templatize(text: str, name: str) -> str:
    """Имя → {{name}}, синоним-маркер → {{synonym}}, каждый uuid → {{uuid:N}} по первому появлению."""
    text = text.replace(name, "{{name}}").replace(SYNONYM, "{{synonym}}")
    seen = {}
    return UUID_RX.sub(lambda m: "{{uuid:%d}}" % seen.setdefault(m.group(), len(seen) + 1), text)


def fix_adopted_edt(text: str, kind: str) -> str:
    """Заимствование MCP → как в GUI EDT: ссылка на типовой, блок extension с Checked, без копий свойств
    (флаги общего модуля движок переносит из типовой)."""
    text = re.sub(r'( uuid="\{\{uuid:1\}\}")', r'\1 extendedConfigurationObject="{{base_uuid}}"', text, count=1)
    check = "    <extendedConfigurationObject>Checked</extendedConfigurationObject>\n"
    text, n = re.subn(r'(  <extension xsi:type="[^"]+")/>\n', r'\1>\n' + check + "  </extension>\n", text)
    if not n:
        text = re.sub(r'(  <extension xsi:type="[^"]+">\n)', r'\1' + check, text, count=1)
    out, skip = [], None
    for line in text.splitlines(keepends=True):
        if skip:                                         # многострочное свойство верхнего уровня
            if line.startswith(f"  </{skip}>"):
                skip = None
            continue
        m = re.match(r"  <(\w+)[ >/]", line)
        if m and m.group(1) not in ADOPTED_KEEP:
            if not re.search(rf"</{m.group(1)}>|/>\s*$", line):
                skip = m.group(1)
            continue
        out.append(line)
    return "".join(out)


def fix_adopted_cfg(text: str) -> str:
    return re.sub(r"(\n(\s*)<ObjectBelonging>Adopted</ObjectBelonging>)",
                  r"\1\n\2<ExtendedConfigurationObject>{{base_uuid}}</ExtendedConfigurationObject>", text, count=1)


def edt_order(texts) -> list[str]:
    """Порядок свойств верхнего уровня .mdo, слитый по базовому шаблону и вариантам."""
    order: list[str] = []
    for text in texts:
        tags = [m for m in re.findall(r"^  <(\w+)", text, re.M)]
        prev = None
        for t in tags:
            if t not in order:
                order.insert(order.index(prev) + 1 if prev in order else len(order), t)
            prev = t
    return order


def emit(raws: list[Path], dest: Path, only: set | None = None) -> None:
    """Собрать шаблоны из сырых каталогов learn (поздние перекрывают ранние)."""
    meta = {"kinds": {}, "order": []}
    for raw in raws:
        rep = json.loads((raw / "report.json").read_text(encoding="utf-8"))
        meta["edt_version"], meta["platform"] = rep["edt"], rep["version"]
        cfg_text = (raw / "cfg" / "base" / "Configuration.xml").read_text(encoding="utf-8-sig")
        meta["cfg_format"] = re.search(r'<MetaDataObject [^>]*version="([\d.]+)"', cfg_text).group(1)
        manifest = (raw / "edt" / "Configuration.mdo").read_text(encoding="utf-8")
        order = re.findall(rf"^  <(\w+)>(\w+)\.{MARK}</\1>", manifest, re.M)
        if len(order) > len(meta["order"]):
            meta["order"] = [[kind, tag] for tag, kind in order]
    tag_of = {k: t for k, t in meta["order"]}
    for raw in raws:
        rep = json.loads((raw / "report.json").read_text(encoding="utf-8"))
        for key, files in rep["files"].items():
            kind, _, var = key.partition("@")
            if not files.get("own") or (only and kind not in only):
                continue
            name = MARK + var
            mdo = (raw / "edt" / "own" / key / f"{name}.mdo").read_text(encoding="utf-8")
            xml = (raw / "cfg" / "base" / KINDS[kind] / f"{name}.xml").read_text(encoding="utf-8-sig")
            for sub, ext, text in (("edt", "mdo", mdo), ("cfg", "xml", xml)):
                p = dest / sub / "own" / f"{key}.{ext}"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(templatize(text, name), encoding="utf-8", newline="\n")
            info = meta["kinds"].setdefault(kind, {"dir": KINDS[kind], "manifest": tag_of[kind], "variants": []})
            if var:
                info["variants"].append(var)
            if not var and files.get("adopted"):
                a_mdo = (raw / "edt" / "adopted" / kind / f"{MARK}.mdo").read_text(encoding="utf-8")
                a_xml = (raw / "cfg" / "ext" / KINDS[kind] / f"{MARK}.xml").read_text(encoding="utf-8-sig")
                for sub, ext, text in (("edt", "mdo", fix_adopted_edt(templatize(a_mdo, MARK), kind)),
                                       ("cfg", "xml", fix_adopted_cfg(templatize(a_xml, MARK)))):
                    p = dest / sub / "adopted" / f"{kind}.{ext}"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(text, encoding="utf-8", newline="\n")
                info["adopt"] = True
    for kind, info in meta["kinds"].items():
        texts = [(dest / "edt" / "own" / f"{kind}{'@' + v if v else ''}.mdo").read_text(encoding="utf-8")
                 for v in ["", *info["variants"]]]
        info["edt_order"] = edt_order(texts)
    (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                    newline="\n")
    print(f"шаблоны: {len(meta['kinds'])} видов → {dest}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "emit":
        ap = argparse.ArgumentParser(prog="learn_object_templates.py emit")
        ap.add_argument("raw", nargs="+", help="каталоги сырья (--out прошлых прогонов)")
        ap.add_argument("--to", required=True, help="onec_metadata/templates/objects")
        ap.add_argument("--kinds", help="только эти виды (порядок видов в meta — всегда полный)")
        e = ap.parse_args(sys.argv[2:])
        emit([Path(r) for r in e.raw], Path(e.to), set(e.kinds.split(",")) if e.kinds else None)
    else:
        main()
