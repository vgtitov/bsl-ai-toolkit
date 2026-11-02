"""mr_size и mr_review: размер по объектам и ручным строкам BSL, описание MR, список P1 и P2."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import mr_review  # noqa: E402
import mr_size  # noqa: E402


def _git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                   capture_output=True)


def _repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    return tmp_path


def _commit_change(repo):
    obj = repo / "src" / "Catalogs" / "Товары"
    obj.mkdir(parents=True)
    (obj / "Товары.mdo").write_text("<x/>\n" * 500, encoding="utf-8")          # раздутый XML
    mod = repo / "src" / "CommonModules" / "Расчёты"
    mod.mkdir(parents=True)
    (mod / "Module.bsl").write_text("Процедура А()\nКонецПроцедуры\n", encoding="utf-8")   # 2 ручные строки
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")


def test_size_counts_objects_and_bsl_not_xml(tmp_path):
    repo = _repo(tmp_path)
    _commit_change(repo)
    res = mr_size.measure(repo, "main")
    assert res["objects"] == 2 and res["bsl_lines"] == 2
    assert mr_size.verdict(res)[0] == "размер в норме"


def test_threshold_requires_justification(tmp_path):
    repo = _repo(tmp_path)
    _commit_change(repo)
    res = mr_size.measure(repo, "main")
    v, why = mr_size.verdict(res, max_objects=1, max_bsl=400)
    assert v == "требует обоснования" and "объектов" in why[0]


def test_description_gaps():
    assert mr_review.description_gaps("Что меняется. Зачем: задача. Как проверить: тест. Риск: низкий.") == []
    assert mr_review.description_gaps("Что меняется и зачем") == ["как проверить", "риск"]


def test_findings_report_size_and_description(tmp_path):
    repo = _repo(tmp_path)
    _commit_change(repo)
    fl, _ = mr_review.findings(repo, "main", "Что меняется. Зачем.", max_objects=1, max_bsl=400)
    places = {w for _, w, _ in fl}
    assert "MR" in places and "описание MR" in places
    # с обоснованием размера замечание про размер пропадает
    fl2, _ = mr_review.findings(repo, "main", "Что. Зачем. Как проверить. Риск. Обоснование размера: один объект.", 1, 400)
    assert "MR" not in {w for _, w, _ in fl2}


def test_second_opinion_prompt_demands_defects_not_all_clear():
    p = mr_review.second_opinion_prompt("описание", True)
    assert "не пиши, что ошибок нет" in p.casefold() and "P1" in p and "постановка" in p


def test_defect_trace_finds_introducing_task(tmp_path):
    import defect_trace
    repo = _repo(tmp_path)
    f = repo / "m.bsl"
    f.write_text("a\nб\nc\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "PROJ-7 добавлен модуль")
    f.write_text("a\nXXX\nc\n", encoding="utf-8")                    # строка с дефектом
    _git(repo, "commit", "-qam", "PROJ-9 правка")
    f.write_text("a\nб\nc\n", encoding="utf-8")                       # исправление
    _git(repo, "commit", "-qam", "BUG-1 исправление")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    res = defect_trace.trace(repo, sha)
    assert list(res["tasks"]) == ["PROJ-9"]


def test_docs_sync_check_warns_and_is_quiet_when_documented(tmp_path):
    import docs_sync_check as dsc
    repo = _repo(tmp_path)
    (repo / ".env.example").write_text("EXISTING_VAR=\n", encoding="utf-8")
    (repo / "README.md").write_text("# r\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "docs base")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "tool.py").write_text('import os\nX = os.environ.get("NEW_SECRET_VAR")\nY = os.getenv("EXISTING_VAR")\n', encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "NEWDOC.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    w = " ".join(dsc.check(repo))
    assert "README.md и CHANGELOG.md" in w or "README.md" in w
    assert "NEWDOC.md" in w and "NEW_SECRET_VAR" in w and "EXISTING_VAR" not in w
    (repo / "README.md").write_text("# r\n- [doc](docs/NEWDOC.md)\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# c\n", encoding="utf-8")
    (repo / ".env.example").write_text("EXISTING_VAR=\nNEW_SECRET_VAR=\n", encoding="utf-8")
    _git(repo, "add", "-A")
    assert dsc.check(repo) == []


def test_remote_project_parses_ssh_and_https():
    assert mr_review.remote_project("git@gitlab.askona.ru:sng-1c/feedsvc.git") == ("gitlab.askona.ru", "sng-1c/feedsvc")
    assert mr_review.remote_project("https://gitlab.askona.ru/sng-1c/feedsvc.git") == ("gitlab.askona.ru", "sng-1c/feedsvc")
    assert mr_review.remote_project("ssh://git@gitlab.example:2222/g/p.git") == ("gitlab.example", "g/p")
    assert mr_review.remote_project("deploy@gitlab.example:g/p.git") == ("gitlab.example", "g/p")


def test_fetch_mr_reads_description_diff_and_pipeline():
    calls = []

    def fake(url, token):
        calls.append(url)
        if url.endswith("/merge_requests/12"):
            return {"title": "T", "description": "Что: x. Зачем: y.", "state": "opened", "head_pipeline": None,
                    "source_branch": "a", "target_branch": "b"}
        return {"changes": [{"new_path": "src/a.py", "diff": "@@\n+x\n+y\n-z\n"}]}

    mr = mr_review.fetch_mr("gitlab.example", "g/p", 12, "tok", get=fake)
    assert mr["description"].startswith("Что")
    assert mr["files"] == ["src/a.py"] and mr["added"] == 2 and mr["removed"] == 1
    assert mr["pipeline"] is None
    assert "/projects/g%2Fp/merge_requests/12" in calls[0]


def test_mr_findings_flag_missing_ci_and_description_gaps():
    mr = {"description": "только что", "files": ["a.py"], "added": 1, "removed": 0, "pipeline": None}
    out = mr_review.mr_findings(mr)
    assert any("CI" in what for _, _, what in out)
    failed = mr_review.mr_findings({**mr, "pipeline": {"status": "failed"}})
    assert any(p == "P1" and "failed" in what for p, _, what in failed)
    assert any("overflow" in what for _, _, what in mr_review.mr_findings({**mr, "overflow": True}))
    assert any("описание" in where for _, where, _ in out)


def test_prompt_is_generic_without_bsl():
    assert "1С" not in mr_review.second_opinion_prompt(None, False, generic=True)
    assert "1С" in mr_review.second_opinion_prompt(None, False)


def test_mr_number_must_be_positive():
    import pytest
    with pytest.raises(SystemExit):
        mr_review.main(["--mr", "0"])


def test_redirect_handler_blocks_redirects():
    import urllib.request
    assert mr_review._NoRedirect().redirect_request(urllib.request.Request("https://a"), None, 302, "x", {}, "https://evil") is None


def test_host_validation_blocks_userinfo_and_paths():
    for good in ("gitlab.askona.ru", "gitlab.example:8443", "localhost"):
        assert mr_review.valid_host(good), good
    for bad in ("gitlab.company@gitlab.evil", "a.b/x", "a.b?x=1", "a.b#f", "", "http://a.b", "a b", "a.b:port"):
        assert not mr_review.valid_host(bad), bad
    import pytest
    with pytest.raises(ValueError):
        mr_review.fetch_mr("gitlab.company@gitlab.evil", "g/p", 1, "tok", get=lambda *a: {})


def test_mr_cli_reports_http_errors_without_traceback(monkeypatch, capsys):
    import urllib.error
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example")

    def boom(*a, **k):
        raise urllib.error.HTTPError("https://gitlab.example", 401, "x", {}, None)
    monkeypatch.setattr(mr_review, "_gitlab_get", boom)
    monkeypatch.setattr(mr_review, "fetch_mr", lambda h, p, n, t, get=boom: get("u", t))
    assert mr_review.main(["--mr", "5", "--project", "g/p", "--no-second-opinion"]) == 2
    out = capsys.readouterr().out
    assert "HTTP 401" in out


def test_mr_cli_refuses_bad_host(monkeypatch, capsys):
    monkeypatch.setenv("GITLAB_TOKEN", "secret-token")
    assert mr_review.main(["--mr", "5", "--project", "g/p", "--host", "gitlab.company@evil.example", "--no-second-opinion"]) == 2
    assert "secret-token" not in capsys.readouterr().out
