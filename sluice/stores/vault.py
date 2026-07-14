"""The Obsidian-style markdown vault, registered as the `vault` store.

This is registration, not relocation: the implementation stays in `core/vault.py`, which
is where its never-clobber comments and its history live. All this module does is give
it a name the config can select.
"""
from sluice.core.vault import Vault
from sluice.stores import register


def _make(config):
    """Build the vault store. `dir` stays None so Vault keeps reading VAULT_DIR from the
    environment, exactly as before -- config selects WHICH store, the store still resolves
    its own location."""
    return Vault(baseline_rel=config.baseline_rel)


register("vault", _make)
