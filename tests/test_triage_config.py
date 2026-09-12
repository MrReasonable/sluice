import pytest

from sluice.triage.config import (
    DOSSIER_CONCURRENCY_MAX,
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


def test_dossier_concurrency_defaults_to_one(monkeypatch):
    # 1 is the shipped fetch rate: one page in flight. Opt-in, because raising it points
    # more concurrent tabs at job boards from one browser profile.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_triage_config(None).dossier_concurrency == 1


def test_dossier_concurrency_round_trips_from_yaml(tmp_path):
    """THE WIRING TEST. A knob parsed and never forwarded is this project's worst defect
    class, and it is invisible from either end alone: the dataclass default is right and
    the YAML is right, while the value never arrives."""
    p = tmp_path / "sluice.yaml"
    p.write_text("triage:\n  dossier_concurrency: 4\n")
    assert load_triage_config(str(p)).dossier_concurrency == 4


def test_dossier_concurrency_rejects_a_yaml_boolean(tmp_path):
    """`true` is the natural spelling of "yes, fetch in parallel", and bool SUBCLASSES
    int -- so unguarded it loads as 1 and runs SEQUENTIALLY. The knob the operator
    switched on would be off, with nothing going red. Same shape as #228's
    `dossier_settle_ms: yes`."""
    p = tmp_path / "sluice.yaml"
    p.write_text("triage:\n  dossier_concurrency: true\n")
    with pytest.raises(ValueError, match="dossier_concurrency"):
        load_triage_config(str(p))


def test_dossier_concurrency_rejects_a_quoted_integer(tmp_path):
    """Unguarded this setattrs the STRING "4", which survives construction and then
    raises a bare TypeError from the comparison inside the engine -- mid-run, after the
    classify pass has already written verdicts, and naming neither the key nor the file.
    cli.main converts only ValueError, so it surfaces as a raw traceback."""
    p = tmp_path / "sluice.yaml"
    p.write_text('triage:\n  dossier_concurrency: "4"\n')
    with pytest.raises(ValueError, match="dossier_concurrency"):
        load_triage_config(str(p))


def test_dossier_concurrency_rejects_zero_and_negatives(tmp_path):
    """There is no "off": 0 would be a second spelling of 1. Unguarded, both 0 and -4
    degrade to sequential silently -- the same do-nothing-quietly failure as the boolean."""
    for raw in ("0", "-4"):
        p = tmp_path / f"sluice{raw}.yaml"
        p.write_text(f"triage:\n  dossier_concurrency: {raw}\n")
        with pytest.raises(ValueError, match="dossier_concurrency"):
            load_triage_config(str(p))


def test_dossier_concurrency_has_a_ceiling(tmp_path):
    """The field exists to stop a board being burst with tabs, so a number must exist
    above which that is refused -- otherwise the knob guarding against excess has no
    limit of its own.

    The VALUE is hand-written here and only the probe derives from it. Deriving both
    makes the test move with the constant: narrowed 16 -> 4 it would re-partition and
    stay green, which is this repo's "a sweep keyed on the constant it checks cannot see
    a change" shape. 16 is also stated in `sluice.yaml.example` and docs/CONFIGURATION.md,
    so a silent narrowing would leave all three disagreeing with nothing to catch it.
    """
    assert DOSSIER_CONCURRENCY_MAX == 16, (
        "the ceiling changed; update sluice.yaml.example and docs/CONFIGURATION.md with it")
    p = tmp_path / "sluice.yaml"
    # The ceiling itself must be ACCEPTED -- a refusal at the boundary would make the
    # documented maximum unusable, and only the value above it is meant to raise.
    p.write_text(f"triage:\n  dossier_concurrency: {DOSSIER_CONCURRENCY_MAX}\n")
    assert load_triage_config(str(p)).dossier_concurrency == DOSSIER_CONCURRENCY_MAX
    p.write_text(f"triage:\n  dossier_concurrency: {DOSSIER_CONCURRENCY_MAX + 1}\n")
    with pytest.raises(ValueError, match="dossier_concurrency"):
        load_triage_config(str(p))
