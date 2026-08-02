"""`lead_layout` construction, validation and the factory wire (#1).

The ABSTAIN guards for this knob do NOT live here -- they are in
`tests/test_sluice_neutral_defaults.py`, which is the file the docs and the review agents point at
as THE neutrality guard, and where both other sweep-invisible knobs already sit. What lives here is
feature mechanics: that a typo raises, and that the configured value actually reaches the store.
"""
import pytest

from sluice.core.config import Config, load_config
from sluice.core.vault import Vault


def test_lead_layout_round_trips_through_load_config(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("lead_layout: active_archive\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert load_config(None).lead_layout == "active_archive"


def test_the_vault_defaults_to_the_flat_layout(tmp_path):
    assert Vault(str(tmp_path)).lead_layout == ""


def test_the_vault_accepts_a_valid_layout(tmp_path):
    assert Vault(str(tmp_path), lead_layout="active_archive").lead_layout == "active_archive"


def test_the_vault_refuses_an_unknown_layout_at_construction(tmp_path):
    """Fail loudly at CONSTRUCTION, listing the valid names. Matched on the MESSAGE: the
    constructor can raise ValueError for other reasons, so `pytest.raises(ValueError)` alone
    would pass with the validation deleted."""
    with pytest.raises(ValueError, match="active_archive"):
        Vault(str(tmp_path), lead_layout="activearchive")


def test_load_config_rejects_an_unknown_layout(tmp_path, monkeypatch):
    """The loader check and the constructor check are COMPLEMENTARY, not alternatives. A
    loader-only check is an equivalent mutant for every direct `Vault(...)` construction (the
    suite has ~150); a constructor-only check lets a YAML typo reach the user as a traceback."""
    p = tmp_path / "c.yaml"
    p.write_text("lead_layout: activearchive\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    with pytest.raises(ValueError, match="lead_layout"):
        load_config(None)


def test_main_renders_an_unknown_layout_as_a_usage_error(tmp_path, monkeypatch, capsys):
    """rc 2 and a sentence, not a traceback -- what `lead_ttl_days` already does. Asserted
    through `main`, because that is the only place the ValueError becomes an exit code."""
    from sluice.cli import main
    p = tmp_path / "c.yaml"
    p.write_text("lead_layout: activearchive\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    assert main(["leads", "dedupe"]) == 2
    assert "lead_layout" in capsys.readouterr().err


def test_the_store_factory_passes_the_configured_layout(tmp_path, monkeypatch):
    """The knob is inert unless the FACTORY carries it. Enumerating the dataclass field and the
    constructor parameter separately would leave the wire between them untested -- which is
    exactly how a config key ships dead."""
    from sluice.stores.vault import _make
    monkeypatch.delenv("VAULT_DIR", raising=False)
    cfg = Config(vault_dir=str(tmp_path), lead_layout="active_archive")
    assert _make(cfg).lead_layout == "active_archive"
