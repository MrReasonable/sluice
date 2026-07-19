from sluice.core.config import Config, load_config
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


def test_triage_defaults_carry_no_pii():
    # TriageConfig ships with NO geo or company preference. target_locations was
    # once ["remote"], which is not neutral: classify rejects anything that does not
    # match it, so a fresh install silently binned every job with a location on it.
    t = TriageConfig()
    assert t.reject_companies == []
    assert t.target_locations == []
    assert t.reject_locations == []
    # Title and pay preferences are equally personal. These were guarded only in
    # test_triage_config.py, so this file -- the one the docs and the review agents
    # point at as THE neutrality guard -- did not actually cover them.
    assert t.accept_titles == []
    assert t.reject_titles == []
    assert t.contract_floor_gbp_day == 0
    assert t.perm_floor_gbp == 0


def test_ingest_defaults_carry_no_preference(monkeypatch):
    # The root Config gates ingest, and its defaults were NOT guarded here at all:
    # `locations` shipped as ["Remote"] (the same geo-preference-in-source shape as
    # the 672ad2a bug), and relevance_keep/relevance_drop had no assertion anywhere
    # in the suite -- a regression to relevance_keep = ["engineer"] would have shipped
    # green. An unset gate must express no opinion.
    c = Config()
    assert c.locations == []
    assert c.relevance_keep == []
    assert c.relevance_drop == []
    assert c.location_noise_words == []   # #5 gate abstains: no noise subtracted by default
    # baseline_rel moved here from CvConfig (only the store can honour it, and
    # Sluice.store() only ever sees the root Config). The assertion had to move WITH it:
    # the refactor deleted it from the CvConfig test and nothing replaced it, so a
    # regression to an absolute personal path would have shipped green. Caught by review.
    assert c.baseline_rel == "My CV/CV.md"
    assert not c.baseline_rel.startswith("/"), \
        "baseline_rel must be RELATIVE to the store: an absolute path is someone's machine"
    # The adapter selectors name shipped implementations, never a person's setup.
    assert c.store == "vault"
    assert c.fetcher == "camofox"

    # ...and the same must hold through the real loader with no config file, which is
    # what a fresh install actually gets. Both env overrides are cleared: without this
    # the assertion would silently read the developer's own SLUICE_CONFIG and pass for
    # the wrong reason.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    loaded = load_config(None)
    assert loaded.locations == []
    assert loaded.relevance_keep == []
    assert loaded.relevance_drop == []
    assert loaded.location_noise_words == []


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
