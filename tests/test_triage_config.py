import pytest

from sluice.triage.config import (
    TriageConfig,
    load_triage_config,
)


def test_shipped_defaults_express_no_role_preference(monkeypatch):
    # The whole point: which roles you want is personal and belongs in YOUR config.
    # Shipping opinionated defaults would silently filter other people's job hunts
    # (and leak the author's). Empty means the title gate abstains.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    cfg = load_triage_config(None)
    assert isinstance(cfg, TriageConfig)
    assert cfg.accept_titles == []
    assert cfg.reject_titles == []
    assert cfg.reject_companies == []
    assert cfg.reject_locations == []
    assert cfg.contract_floor_gbp_day == 0
    assert cfg.perm_floor_gbp == 0
    assert cfg.batch_size >= 1
    # digest note is named distinctly from the legacy "Rejected Leads/" folder
    assert cfg.rejected_note == "Job Applications/Rejected Leads Audit.md"


def test_yaml_supplies_the_titles(tmp_path, titles):
    accept, reject = titles
    p = tmp_path / "sluice.yaml"
    p.write_text(
        "triage:\n"
        "  batch_size: 12\n"
        "  contract_floor_gbp_day: 550\n"
        f"  accept_titles: [{accept[0]!r}]\n"
        f"  reject_titles: [{reject[0]!r}]\n")
    cfg = load_triage_config(str(p))
    assert cfg.batch_size == 12
    assert cfg.contract_floor_gbp_day == 550
    assert cfg.accept_titles == [accept[0]]
    assert cfg.reject_titles == [reject[0]]


def test_shipped_defaults_do_not_filter_on_geography(monkeypatch):
    # Regression: target_locations used to default to ["remote"], and classify
    # rejects anything not matching it -- so a fresh install silently binned every
    # job with a location on it. A shipped default must never filter a stranger's
    # job hunt. Empty means the gate abstains, NOT "match nothing".
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    cfg = load_triage_config(None)
    assert cfg.target_locations == []
    assert cfg.reject_locations == []


def test_the_retired_triage_dossier_concurrency_is_refused_not_dropped(tmp_path):
    """#309's key moved to the root config, and the old spelling must RAISE.

    Silence is the hazard, and this loader's own shape creates it: the overlay loop filters
    with `hasattr`, so a key the dataclass no longer declares is discarded without a word.
    An operator who had `triage: dossier_concurrency: 6` would get 1 -- sequential -- with
    their file unchanged and nothing said. Same reasoning, and the same remedy, as
    `refuse_retired_dossier_dir` at #80.

    The message must name where it went, or a refusal is just a different way to be stuck.
    """
    p = tmp_path / "sluice.yaml"
    p.write_text("triage:\n  dossier_concurrency: 6\n")
    with pytest.raises(ValueError, match="dossier_concurrency") as exc:
        load_triage_config(str(p))
    assert "root" in str(exc.value).lower(), "the refusal does not say where the key went"

    # A value-less key is refused too: `hasattr` would drop that one silently as well.
    p.write_text("triage:\n  dossier_concurrency:\n")
    with pytest.raises(ValueError, match="dossier_concurrency"):
        load_triage_config(str(p))
