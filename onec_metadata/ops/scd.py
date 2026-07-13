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
