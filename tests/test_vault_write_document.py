"""`only_if_absent=True` is the never-clobber primitive `sluice init` scaffolds through. A
parameter on the existing writer, not a second write function: CodeQL reads a new write function
as a new sink (#9's `require_status` precedent)."""
import pytest

from sluice.core.protocols import CRITERIA_RELPATH
from sluice.core.vault import DEFAULT_VAULT, Vault


def test_creates_when_absent(tmp_path):
    v = Vault(str(tmp_path))
    assert v.write_document(CRITERIA_RELPATH, "first", only_if_absent=True)
    assert v.read_criteria() == "first"


def test_abstains_and_leaves_the_file_byte_identical(tmp_path):
    v = Vault(str(tmp_path))
    v.write_document(CRITERIA_RELPATH, "human wrote this", only_if_absent=True)
    assert v.write_document(CRITERIA_RELPATH, "SCAFFOLD", only_if_absent=True) == ""
    assert v.read_criteria() == "human wrote this"


def test_the_default_still_overwrites_so_the_digest_caller_is_unchanged(tmp_path):
    v = Vault(str(tmp_path))
    v.write_document("Job Applications/Digest.md", "old")
    path = v.write_document("Job Applications/Digest.md", "new")
    assert open(path, encoding="utf-8").read() == "new"


def test_escape_guard_still_fires_under_only_if_absent(tmp_path):
    with pytest.raises(ValueError, match="escapes the store root"):
        Vault(str(tmp_path)).write_document("../outside.md", "x", only_if_absent=True)


def test_the_criteria_path_has_one_home():
    import sluice.core.vault as vault_mod
    import sluice.triage.prompt as prompt_mod
    assert vault_mod._CRITERIA_RELPATH is CRITERIA_RELPATH
    assert prompt_mod._CRITERIA_RELPATH is CRITERIA_RELPATH
    assert DEFAULT_VAULT == "./vault"
