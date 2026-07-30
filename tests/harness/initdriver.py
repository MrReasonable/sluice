"""A `main(argv)` driver for `sluice init` ONLY.

Deliberately NOT the `cli` fixture: that one calls `build_harness`, which writes a config and
setenvs SLUICE_CONFIG and VAULT_DIR -- so `config_file()` would always resolve to an existing file
and `cmd_init` would always take the skip branch. `init` needs no browser, renderer, backend or
seeded vault, so it runs under the autouse `_pin_paths` sandbox alone, which is the only tier that
can witness XDG resolution at all (`tests/conftest.py:46`).
"""
import pytest

from sluice.cli import main


@pytest.fixture
def run_init(capsys, monkeypatch):
    """`run_init(argv) -> (rc, out, err)`, plus `run_init.config_dest` derived from the resolver
    rather than written as a literal.

    VAULT_DIR is UNSET here. The autouse `_pin_paths` sets it to sandbox the vault, but `cmd_init`
    REFUSES when `--vault` and `$VAULT_DIR` name different directories (`stores/vault.py:_make` is
    env-first, so a precedence rule would write to the env path while the report named the flag).
    Left in place, every `--vault` test would take that refusal instead of the branch it is about --
    measured: seven of eleven did. So the vault comes from `--vault` explicitly, which is what these
    tests are asserting on; nothing falls back to a real vault, because a run with no vault at all
    raises MissingAnswer before writing anything. The disagreement test sets the variable back
    itself.
    """
    monkeypatch.delenv("VAULT_DIR", raising=False)

    def _run(argv):
        capsys.readouterr()
        rc = main(argv)
        cap = capsys.readouterr()
        return rc, cap.out, cap.err

    from sluice.core.paths import config_file
    _run.config_dest = config_file
    _run.monkeypatch = monkeypatch
    return _run
