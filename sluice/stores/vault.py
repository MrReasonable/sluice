"""The Obsidian-style markdown vault, registered as the `vault` store.

This is registration, not relocation: the implementation stays in `core/vault.py`, which
is where its never-clobber comments and its history live. All this module does is give
it a name the config can select.
"""
import os

from sluice.core.vault import Vault
from sluice.stores import register


def _make(config):
    """Build the vault store.

    The store still owns its own DEFAULT (`Vault.__init__`'s `./vault`); what the
    factory now supplies is a configured value, exactly as it already does for
    `baseline_rel`. `None` falls through to the constructor's own chain.

    Precedence is computed HERE and not moved into `Vault.__init__` (#80). Putting
    `os.environ.get("VAULT_DIR")` ahead of the constructor's `dir` parameter would make
    the environment beat an EXPLICIT argument -- retargeting the ~150 positional
    `Vault(str(tmp_path))` constructions in the test suite at a developer's real vault,
    green in CI throughout. The constructor keeps `dir or env or default`; the factory
    decides what to inject.

    The vault is also the one relocated-adjacent path that deliberately does NOT move to
    an XDG root: it is the user's Obsidian directory, their data, not sluice's state. It
    gains a config key because an env var does not survive a new shell, so there was
    nowhere to persist it.
    """
    return Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None,
                 baseline_rel=config.baseline_rel,
                 location_noise_words=config.location_noise_words)


register("vault", _make)
