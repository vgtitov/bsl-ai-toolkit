"""Тесты чистого ядра rds_setup: сборка и идемпотентный upsert блока ~/.ssh/config."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "rds_setup", Path(__file__).resolve().parents[1] / "scripts" / "rds_setup.py"
)
rds_setup = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rds_setup)

build_host_block = rds_setup.build_host_block
upsert_host_block = rds_setup.upsert_host_block
admin_handoff_text = rds_setup.admin_handoff_text


def test_handoff_covers_both_key_locations():
    """Текст для админа должен покрыть обе учётки: обычную (профиль) и админскую."""
    pub = "ssh-ed25519 AAAAKEY test@host"
    txt = admin_handoff_text("rds-01", "10.0.0.10", "DOMAIN\\vuser", pub)
    assert pub in txt
    assert "rds-01" in txt and "10.0.0.10" in txt
    assert "DOMAIN\\vuser" in txt
    # обычная учётка → профиль; админская → administrators_authorized_keys
    assert "authorized_keys" in txt
    assert "administrators_authorized_keys" in txt


def test_block_quotes_domain_login():
    r"""User с доменом ОБЯЗАН быть в кавычках — иначе ssh съедает \v в логине."""
    block = build_host_block("rds-01", "10.0.0.10", "DOMAIN\\vuser", "~/.ssh/onec_rds")
    assert "Host rds-01" in block
    assert "HostName 10.0.0.10" in block
    assert 'User "DOMAIN\\vuser"' in block
    assert "IdentityFile ~/.ssh/onec_rds" in block
    assert "IdentitiesOnly yes" in block
    assert "BatchMode yes" in block


def test_block_plain_login_still_quoted():
    """Даже логин без домена берём в кавычки — единообразно и безопасно."""
    block = build_host_block("h", "1.2.3.4", "user", "~/.ssh/k")
    assert 'User "user"' in block


def test_upsert_appends_when_absent():
    existing = 'Host github\n    HostName github.com\n'
    block = build_host_block("rds-01", "10.0.0.10", "DOMAIN\\vuser", "~/.ssh/onec_rds")
    out = upsert_host_block(existing, "rds-01", block)
    assert "Host github" in out  # чужой блок цел
    assert "Host rds-01" in out
    assert out.count("Host rds-01") == 1


def test_upsert_replaces_existing_block_only():
    existing = (
        "Host github\n    HostName github.com\n\n"
        "Host rds-01\n    HostName 10.0.0.9\n    User \"OLD\\me\"\n\n"
        "Host other\n    HostName other.host\n"
    )
    block = build_host_block("rds-01", "10.0.0.10", "DOMAIN\\vuser", "~/.ssh/onec_rds")
    out = upsert_host_block(existing, "rds-01", block)
    assert out.count("Host rds-01") == 1
    assert "10.0.0.9" not in out           # старый адрес вытеснен
    assert "10.0.0.10" in out              # новый на месте
    assert 'User "OLD\\me"' not in out     # старый User вытеснен
    assert "Host github" in out and "Host other" in out  # соседи целы
    assert "other.host" in out


def test_upsert_is_idempotent():
    block = build_host_block("rds-01", "10.0.0.10", "DOMAIN\\vuser", "~/.ssh/onec_rds")
    once = upsert_host_block("", "rds-01", block)
    twice = upsert_host_block(once, "rds-01", block)
    assert once.count("Host rds-01") == 1
    assert twice.count("Host rds-01") == 1
