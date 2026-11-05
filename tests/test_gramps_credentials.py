import sys
import types

import pytest

from genea.gramps.credentials import CredentialsMissing, env_name, get_password, store_password


def test_env_name():
    assert env_name("genea-jrumanovsky") == "GENEA_GRAMPS_PASSWORD_GENEA_JRUMANOVSKY"


def test_env_wins_over_keyring(monkeypatch):
    def fail(service, user):
        raise AssertionError("keyring must not be consulted when the env variable is set")

    monkeypatch.setenv("GENEA_GRAMPS_PASSWORD_ME", "pw")
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_password=fail))
    assert get_password("me") == "pw"


def test_keyring_used_when_no_env(monkeypatch):
    monkeypatch.delenv("GENEA_GRAMPS_PASSWORD_ME", raising=False)
    def fake_get(service, user):
        assert service == "genea-gramps-web"
        return "from-keyring"

    fake = types.SimpleNamespace(get_password=fake_get)
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert get_password("me") == "from-keyring"


def test_store_password_writes_keyring(monkeypatch):
    saved = {}
    def fake_set(service, user, pw):
        assert service == "genea-gramps-web"
        saved.update(user=user, pw=pw)

    fake = types.SimpleNamespace(set_password=fake_set)
    monkeypatch.setitem(sys.modules, "keyring", fake)
    store_password("me", "secret")
    assert saved == {"user": "me", "pw": "secret"}


def test_keyring_returns_none_is_missing(monkeypatch):
    monkeypatch.delenv("GENEA_GRAMPS_PASSWORD_ME", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", types.SimpleNamespace(get_password=lambda s, u: None))
    with pytest.raises(CredentialsMissing):
        get_password("me")


def test_missing_names_both_ways(monkeypatch):
    monkeypatch.delenv("GENEA_GRAMPS_PASSWORD_ME", raising=False)
    monkeypatch.setitem(sys.modules, "keyring", None)  # import fails: keyring not installed
    with pytest.raises(CredentialsMissing, match="genea gramps login .*GENEA_GRAMPS_PASSWORD_ME"):
        get_password("me")
