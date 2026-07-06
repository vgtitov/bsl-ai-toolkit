# -*- coding: utf-8 -*-
"""atlassian.py: чистые функции — md→storage, разбор ссылок на страницы, html→текст."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import atlassian  # noqa: E402


def test_md_to_storage_basics():
    md = "# Титул\n## Раздел\n### Под\nтекст **жирный** и `код`\n- пункт\n\n| А | Б |\n|---|---|\n| 1 | 2 |\n"
    out = atlassian.md_to_storage(md)
    assert "Титул" not in out  # h1 — титул страницы, в тело не идёт
    assert "<h2>Раздел</h2>" in out and "<h3>Под</h3>" in out
    assert "<strong>жирный</strong>" in out and "<code>код</code>" in out
    assert "<ul><li>пункт</li></ul>" in out
    assert "<table><tbody><tr><th>А</th><th>Б</th></tr><tr><td>1</td><td>2</td></tr></tbody></table>" in out


def test_md_link_and_escape():
    out = atlassian.md_to_storage("[лист](https://e.x/a b?x=1&y=2) и a<b\n")
    assert '<a href="https://e.x/a%20b?x=1&amp;y=2">лист</a>' in out
    assert "a&lt;b" in out


def test_conf_page_id_parsing(monkeypatch):
    # числовой id и оба вида URL разбираются без сети
    assert atlassian.conf_page_id("209491238") == "209491238"
    assert atlassian.conf_page_id("https://c.x/pages/viewpage.action?pageId=123") == "123"
    assert atlassian.conf_page_id("https://c.x/spaces/AD/pages/456/T") == "456"


def test_html_to_text():
    txt = atlassian.html_to_text("<h2>З</h2><p>а&nbsp;б</p><table><tr><td>1</td><td>2</td></tr></table>")
    assert "З" in txt and "а б" in txt
    assert "1 | 2" in txt


def test_headers_bearer_and_basic(monkeypatch):
    monkeypatch.setenv("JIRA_PAT", "t0k")
    monkeypatch.delenv("JIRA_USER", raising=False)
    assert atlassian._headers("JIRA")["Authorization"] == "Bearer t0k"
    monkeypatch.setenv("JIRA_USER", "u@e.x")
    assert atlassian._headers("JIRA")["Authorization"].startswith("Basic ")
