"""Операции над схемой компоновки данных (СКД, DataCompositionSchema).

Новое поле набора данных создаётся копией существующего (deepcopy последнего
DataSetFieldField): так наследуются отступы (CRLF-хвосты) и структура title —
ровно то, что сгенерировал бы Конфигуратор. Из копии остаются только
dataPath / field / title.
"""
import copy
from pathlib import Path

from lxml import etree

from onec_metadata.formats import configurator as cfg
from onec_metadata.ops import OpPreconditionError
from onec_metadata.validate import validate_name

_KEEP = {"dataPath", "field", "title"}


def _ln(el) -> str:
    return etree.QName(el).localname


def _dataset(doc, dataset: str):
    hits = doc.xpath(f"//dcs:dataSet[dcs:name='{dataset}']", namespaces=cfg.NS)
    if not hits:
        raise OpPreconditionError(f"Набор данных '{dataset}' не найден")
    return hits[0]


def add_field(schema_path: Path, dataset: str, field: str, data_path: str,
              title: str) -> None:
    validate_name(field)
    doc = cfg.load(schema_path)
    ds = _dataset(doc, dataset)

    if ds.xpath(f"dcs:field[dcs:dataPath='{data_path}']", namespaces=cfg.NS):
        raise OpPreconditionError(
            f"Поле '{data_path}' уже есть в наборе '{dataset}'")

    fields = ds.xpath("dcs:field", namespaces=cfg.NS)
    samples = [f for f in fields
               if (f.get(cfg.q("xsi", "type")) or "").endswith("DataSetFieldField")]
    if not samples:
        raise OpPreconditionError(
            f"В наборе '{dataset}' нет поля-образца DataSetFieldField")
    sample = samples[-1]

    new = copy.deepcopy(sample)
    closing_tail = list(sample)[-1].tail  # отступ перед </field>
    for child in list(new):
        if _ln(child) not in _KEEP:
            new.remove(child)
    kids = {_ln(k): k for k in new}
    missing = _KEEP - set(kids)
    if missing:
        raise OpPreconditionError(
            f"Поле-образец не содержит {sorted(missing)} — структура неожиданna")
    kids["dataPath"].text = data_path
    kids["field"].text = field
    for content in kids["title"].xpath(".//v8:content", namespaces=cfg.NS):
        content.text = title
    list(new)[-1].tail = closing_tail

    cfg.place_after(sample, new)
    cfg.save(doc, schema_path)


def validate_schema(schema_path: Path) -> list:
    """Smoke-валидатор целостности схемы компоновки данных (СКД).

    Возвращает список проблем (пустой = валидна). НЕ бросает исключение на битом
    XML — сообщает проблемой, чтобы гвард ловил именно поломку СКД при правке:
    невалидный XML, не-СКД корень, отсутствие наборов, поля без dataPath, дубли
    полей, битые ссылки связей наборов, ссылка на несуществующий источник.

    Это ENFORCEMENT (проверяемый, с тестами), дополняющий platform round-trip:
    работает офлайн, до загрузки в 1С.
    """
    issues = []
    try:
        doc = cfg.load(schema_path)
    except Exception as e:  # битый/невалидный XML
        return [f"XML не разобран (вероятно, повреждён при правке): {e}"]

    root = doc.getroot()
    if _ln(root) != "DataCompositionSchema":
        return [f"Корень '{_ln(root)}' — это не схема компоновки данных "
                "(ожидался DataCompositionSchema); СКД повреждена или не тот файл"]

    # источники данных верхнего уровня
    sources = {s.text for s in root.xpath("dcs:dataSource/dcs:name", namespaces=cfg.NS)
               if s.text}

    datasets = root.xpath("dcs:dataSet", namespaces=cfg.NS)
    if not datasets:
        issues.append("В схеме нет ни одного набора данных (dataSet) — "
                      "СКД пуста/повреждена")

    seen_ds = set()
    for ds in datasets:
        names = ds.xpath("dcs:name", namespaces=cfg.NS)
        ds_name = names[0].text if names and names[0].text else None
        if not ds_name:
            issues.append("Набор данных без имени (<name>)")
            ds_name = "<без имени>"
        elif ds_name in seen_ds:
            issues.append(f"Дублирующееся имя набора данных '{ds_name}'")
        seen_ds.add(ds_name)

        # ссылка на источник данных должна существовать
        for src in ds.xpath("dcs:dataSource", namespaces=cfg.NS):
            if src.text and sources and src.text not in sources:
                issues.append(
                    f"Набор '{ds_name}': источник '{src.text}' не объявлен "
                    "в dataSource схемы")

        # поля набора
        seen_paths = set()
        for field in ds.xpath("dcs:field", namespaces=cfg.NS):
            ftype = field.get(cfg.q("xsi", "type")) or ""
            dp = field.xpath("dcs:dataPath", namespaces=cfg.NS)
            dp_text = dp[0].text if dp and dp[0].text else None
            if ftype.endswith("DataSetFieldField"):
                if not dp_text:
                    issues.append(
                        f"Набор '{ds_name}': поле без непустого <dataPath> — "
                        "СКД отвергнет такое поле")
                    continue
                if not field.xpath("dcs:field", namespaces=cfg.NS):
                    issues.append(
                        f"Набор '{ds_name}': поле '{dp_text}' без <field> "
                        "(имя источника)")
            if dp_text:
                if dp_text in seen_paths:
                    issues.append(
                        f"Набор '{ds_name}': дублирующийся dataPath '{dp_text}'")
                seen_paths.add(dp_text)

    # связи наборов данных должны ссылаться на существующие наборы
    for link in root.xpath("dcs:dataSetLink", namespaces=cfg.NS):
        for tag in ("sourceDataSet", "destinationDataSet"):
            ref = link.xpath(f"dcs:{tag}", namespaces=cfg.NS)
            ref_text = ref[0].text if ref and ref[0].text else None
            if ref_text and ref_text not in seen_ds:
                issues.append(
                    f"Связь наборов ({tag}) ссылается на несуществующий "
                    f"набор '{ref_text}'")

    return issues


def get_dataset_query(schema_path: Path, dataset: str) -> str:
    doc = cfg.load(schema_path)
    ds = _dataset(doc, dataset)
    q = ds.xpath("dcs:query", namespaces=cfg.NS)
    if not q:
        raise OpPreconditionError(f"У набора '{dataset}' нет запроса")
    return q[0].text or ""


def set_dataset_query(schema_path: Path, dataset: str, query_text: str) -> None:
    doc = cfg.load(schema_path)
    ds = _dataset(doc, dataset)
    q = ds.xpath("dcs:query", namespaces=cfg.NS)
    if not q:
        raise OpPreconditionError(f"У набора '{dataset}' нет запроса")
    q[0].text = query_text
    cfg.save(doc, schema_path)
