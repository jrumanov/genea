"""Gramps Web passwords: the GENEA_GRAMPS_PASSWORD_<USER> environment variable, else the OS keyring."""

from __future__ import annotations

import os
import re

KEYRING_SERVICE = "genea-gramps-web"


class CredentialsMissing(Exception):
    """No password for a Gramps Web user in the environment or the keyring."""


def env_name(user: str) -> str:
    return "GENEA_GRAMPS_PASSWORD_" + re.sub(r"[^A-Za-z0-9]", "_", user).upper()


def _keyring():
    try:
        import keyring
    except ImportError as e:
        raise ImportError("keyring is not installed: run `uv sync --extra gramps`") from e
    return keyring


def get_password(user: str) -> str:
    value = os.environ.get(env_name(user))
    if value:
        return value
    try:
        stored = _keyring().get_password(KEYRING_SERVICE, user)
    except ImportError:
        stored = None
    if stored:
        return stored
    raise CredentialsMissing(
        f"No Gramps Web password for '{user}': run `genea gramps login {user}` "
        f"or set {env_name(user)}"
    )


def store_password(user: str, password: str) -> None:
    _keyring().set_password(KEYRING_SERVICE, user, password)
