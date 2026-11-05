# Task 1 report: Settings and credentials

Status: DONE
Commit: 1136284 "Add Gramps Web settings and password lookup"

## What was done
- TDD: wrote tests first (`tests/test_config.py::test_gramps_web_trees_table`, `tests/test_gramps_credentials.py`); confirmed collection failure before implementation.
- `src/genea/config.py`: removed `gedcom_direct_write`; added `gramps_web_url`, `gramps_web_trees` (default_factory dict), `gramps_web_xml_snapshot` (True). `_apply` now goes through `_coerce`, which turns `gramps_web_trees` into `dict(v)` (and keeps the existing `exclude` tuple coercion).
- `src/genea/gramps/__init__.py` and `src/genea/gramps/credentials.py`: `env_name`, `get_password` (env first, then keyring service `genea-gramps`, lazy keyring import; ImportError counts as no keyring), `store_password`, `CredentialsMissing`. The missing-password message names `genea gramps login <user>` and the env var. If keyring is not installed, the message for the store path says `uv sync --extra gramps`.
- `pyproject.toml`: new extra `gramps = ["keyring>=25"]`; `keyring>=25` added to the dev group. `uv.lock` regenerated to match (needed for the new extra).
- Grep for `gedcom_direct_write` in src, tests and README.md: only the config.py field existed, now removed.

## Tests
- Targeted: `uv run pytest tests/test_config.py tests/test_gramps_credentials.py -v` -> 16 passed.
- Full: `uv run pytest` -> 973 passed, 2 skipped (90.84s).

## Deviations and notes
- `test_gramps_web_trees_table` takes the `isolated_home` fixture so a real global `~/.genea/config.toml` cannot leak into the test. The brief's signature did not include it.
- Added one extra test, `test_store_password_writes_keyring`, for the store path (not in the brief).
- `store_password` raises a plain ImportError with the install hint when keyring is missing (the brief did not specify this path).

## Concerns
- None blocking. `uv lock` rewrote `uv.lock`; the diff is limited to the keyring entries.

## Fix round 1

Changes:
- Important: `KEYRING_SERVICE` changed from `genea-gramps` to `genea-gramps-web`. Both fake keyrings in `tests/test_gramps_credentials.py` (get and set) now assert `service == "genea-gramps-web"`, so a wrong name fails a test.
- Minor: `test_env_wins_over_keyring` installs a fake keyring whose `get_password` raises AssertionError, proving the env variable takes priority without consulting the keyring.
- Minor: new `test_keyring_returns_none_is_missing` covers keyring installed and returning None, expecting `CredentialsMissing`.

Covering tests: `test_env_wins_over_keyring`, `test_keyring_used_when_no_env`, `test_store_password_writes_keyring`, `test_keyring_returns_none_is_missing`, `test_missing_names_both_ways`, `test_env_name`, plus the `tests/test_config.py` suite.

Command: `uv run pytest tests/test_gramps_credentials.py tests/test_config.py -v -p no:xdist -o addopts=""`
Output (tail): 17 passed in 6.35s.
