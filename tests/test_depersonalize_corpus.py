"""Общий корпус для веб-движка (depersonalize-web/tests/corpus.json) и Python-движка: тот же файл, чтобы детекторы не расходились.
keep: должно остаться в результате (и результат не заблокирован); hide: не должно быть в результате (блок результата тоже считается защитой).
py: "gap: причина" в записи значит известное расхождение (в README таблица расхождений)."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import depersonalize as d  # noqa: E402

CORPUS = json.loads((Path(__file__).parent / "depersonalize_corpus.json").read_text(encoding="utf-8"))


def _run(c):
    return d.depersonalize(c["text"], profile="toolkit", level="standard", kind=c["kind"], lang="generic", destination="external", data_class="corp")


@pytest.mark.parametrize("c", CORPUS, ids=[c["id"] for c in CORPUS])
def test_corpus(c):
    if c.get("py"):
        pytest.skip(c["py"])
    r = _run(c)
    out = r.clean if r.ok else ""
    if c["keep"]:
        assert r.ok, f"заблокировано: {r.reason} {[str(x) for x in r.leaks][:3]}"
    lost = [k for k in c["keep"] if k not in out]
    left = [h for h in c["hide"] if r.ok and h in out]
    assert not lost and not left, f"потеряно {lost}, осталось {left}: {out!r}"
