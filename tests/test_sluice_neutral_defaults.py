from sluice.cv.config import CvConfig, load_cv_config
from sluice.triage.config import TriageConfig, load_triage_config


def test_cv_defaults_carry_no_pii():
    # CvConfig ships with entirely neutral defaults: no owner name, no contact
    # info, no employer roster, no fabrication decoys, no personal filename or
    # prefix map baked into source. A blocklist of real names would defeat the
    # point of this test in a public repo (it would just relist the PII it's
    # guarding against), so this asserts structural neutrality instead:
    # personal values only ever arrive via the `cv:` block of sluice.yaml
    # (see sluice.yaml.example), never hardcoded here.
    c = CvConfig()
    assert c.name == "Your Name"
    assert c.contact == ""
    assert c.employers == []
    assert c.fabrication_decoys == []
    assert c.negatives == []
    assert c.prefix_map == {}
    assert c.neutral_filename == "CV.pdf"
    assert c.baseline_rel == "My CV/CV.md"


def test_triage_defaults_carry_no_pii():
    # TriageConfig ships with NO geo or company preference. target_locations was
    # once ["remote"], which is not neutral: classify rejects anything that does not
    # match it, so a fresh install silently binned every job with a location on it.
    t = TriageConfig()
    assert t.reject_companies == []
    assert t.target_locations == []
    assert t.reject_locations == []


def test_config_overlay_restores_neutralized_defaults(tmp_path, monkeypatch):
    """Neutralizing the code defaults must not cost override capability: a
    sluice.yaml with triage: and cv: blocks should still fully round-trip
    through load_triage_config()/load_cv_config(), proving the owner (or
    anyone else) can restore their own real values via a git-ignored local
    config file."""
    p = tmp_path / "sluice.local.yaml"
    p.write_text(
        "triage:\n"
        "  reject_companies: [acme]\n"
        "  target_locations: [jenningsfort, baldwinberg]\n"
        "  reject_locations: [india]\n"
        "cv:\n"
        "  name: \"Someone\"\n"
        "  negatives: [\"X\"]\n"
        "  prefix_map: {Foo: FO}\n"
    )
    monkeypatch.setenv("SLUICE_CONFIG", str(p))

    tcfg = load_triage_config()
    assert tcfg.reject_companies == ["acme"]
    assert tcfg.target_locations == ["jenningsfort", "baldwinberg"]
    assert tcfg.reject_locations == ["india"]

    ccfg = load_cv_config()
    assert ccfg.name == "Someone"
    assert ccfg.negatives == ["X"]
    assert ccfg.prefix_map == {"Foo": "FO"}
