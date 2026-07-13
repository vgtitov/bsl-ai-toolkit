# DISCIPLINE_ALLOW_TEST_EDIT: новый тест, TDD red-фаза (Фаза 2: EDT-стиль XML)
"""Формат-слой обязан сохранять СТИЛЬ файла, а не навязывать свой:

- Конфигуратор-выгрузка: BOM + CRLF (+ смешанные LF в текстах) — уже покрыто.
- EDT-файлы (.mdo, .dcs): БЕЗ BOM, переводы строк LF или CRLF по файлу.
"""
from pathlib import Path


def _roundtrip(raw: bytes, tmp_path: Path) -> bytes:
    from onec_metadata.formats import configurator as cfg

    src = tmp_path / "f.xml"
    src.write_bytes(raw)
    doc = cfg.load(src)
    out = tmp_path / "out.xml"
    cfg.save(doc, out)
    return out.read_bytes()


def test_edt_style_no_bom_lf(tmp_path):
    raw = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<DataCompositionSchema xmlns="http://v8.1c.ru/8.1/data-composition-system/schema">\n'
           "\t<dataSource>\n\t\t<name>Источник</name>\n\t</dataSource>\n"
           "</DataCompositionSchema>").encode("utf-8")
    assert _roundtrip(raw, tmp_path) == raw


def test_edt_style_no_bom_crlf(tmp_path):
    raw = ('<?xml version="1.0" encoding="UTF-8"?>\r\n'
           '<mdclass:Catalog xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="x">\r\n'
           "  <name>Тест</name>\r\n"
           "</mdclass:Catalog>").encode("utf-8")
    assert _roundtrip(raw, tmp_path) == raw


def test_configurator_style_bom_crlf_kept(tmp_path):
    raw = ("﻿" + '<?xml version="1.0" encoding="UTF-8"?>\r\n'
           '<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">\r\n'
           "\t<a>б</a>\r\n"
           "</MetaDataObject>").encode("utf-8")
    assert _roundtrip(raw, tmp_path) == raw
