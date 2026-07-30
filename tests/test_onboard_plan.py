"""`build_plan` is a pure function from a dict to two strings, which is what lets the load-bearing
property be a unit test instead of a wizard transcript."""
import dataclasses
import re

import pytest
import yaml

from sluice.core.config import load_config
from sluice.cv.config import load_cv_config
from sluice.onboard.plan import build_plan
from sluice.onboard.questions import catalogue
from sluice.track.config import load_track_config
from sluice.triage.config import load_triage_config

VAULT = "/example/vault"
LOADERS = (load_config, load_triage_config, load_cv_config, load_track_config)


def _plan(tmp_path, answers=None, **kw):
    return build_plan(answers or {}, config_dest=str(tmp_path / "config.yaml"),
                      profile_dest=str(tmp_path / "Profile.md"), default_vault=VAULT, **kw)


def _written(tmp_path, answers=None, **kw):
    path = tmp_path / "config.yaml"
    path.write_text(_plan(tmp_path, answers, **kw).config_text, encoding="utf-8")
    return str(path)


# ── the enumerated differential (replaces v1's 13-field hand-list) ───────────
@pytest.mark.parametrize("loader", LOADERS, ids=lambda f: f.__name__)
def test_an_unanswered_wizard_writes_a_config_identical_to_no_config_at_all(tmp_path, loader):
    """Field-for-field against the code defaults, ENUMERATED not hand-listed -- so a future
    catalogue key rendered with a value cannot slip past, and nothing has to be kept in step by
    hand. `vault_dir` is the one legitimate difference: it is the wizard's only required answer."""
    emitted = dataclasses.asdict(loader(_written(tmp_path)))
    baseline = dataclasses.asdict(loader(None))
    fields = set(emitted)
    assert fields, "the field sweep enumerated nothing"          # SCOPE
    for name in sorted(fields - {"vault_dir"}):
        assert emitted[name] == baseline[name], f"{loader.__name__}.{name} was overridden"


def test_the_template_contains_every_catalogue_key_COMMENTED(tmp_path):
    """SCOPE, paired with the differential above: that assertion passes just as happily on an
    EMPTY file, since the loaders would return the neutral code defaults and every gate would
    abstain for the wrong reason -- the all([]) shape that has shipped three times here.

    No `#?`: the key must be demonstrably COMMENTED, not merely present. And the match is anchored
    to a key LINE, so a comment that merely mentions the key mid-sentence cannot satisfy it."""
    text = _plan(tmp_path).config_text
    for q in catalogue(default_vault=VAULT):
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#\s*{re.escape(leaf)}:", text, re.M), \
                f"{dotted} is not present-and-commented in the template init writes"


def test_prose_mentioning_a_key_does_NOT_satisfy_the_scope_matcher():
    """NEGATIVE CONTROL. Widening the matcher to `^[#\\s]*` would let an explanatory comment stand
    in for the key -- the matched-by-adjacent-prose bug this repo already shipped."""
    prose = "# set accept_titles: to whatever you like\n"
    assert not re.search(r"^\s*#\s*accept_titles:", prose, re.M)


def test_answers_become_active_keys(tmp_path):
    path = _written(tmp_path, {"accept_titles": ["example role"], "perm_floor": 90000,
                               "lead_ttl_days": 90})
    assert load_triage_config(path).accept_titles == ["example role"]
    assert load_triage_config(path).perm_floor_gbp == 90000
    assert load_config(path).lead_ttl_days == 90


def test_one_backend_answer_fans_out_to_every_block(tmp_path):
    path = _written(tmp_path, {"primary_backend": "openai", "fallback_backend": "anthropic"})
    for loader in (load_triage_config, load_cv_config, load_track_config):
        assert loader(path).primary_backend == "openai"
        assert loader(path).fallback_backend == "anthropic"


def test_the_fan_out_covers_every_config_declaring_a_backend():
    """DISCOVERED, reusing the neutral-defaults sweep's own helper rather than a second, weaker
    copy. #63's lesson: a hand-list of dataclasses leaks exactly like the hand-list of fields it
    replaced -- four were named there and there were six.

    `.values()`, not the bare dict: the helper is keyed by CLASS NAME, so iterating it yields
    strings and `dataclasses.fields` raises TypeError. Measured, not assumed."""
    from tests.test_sluice_neutral_defaults import _discover_config_dataclasses
    declared = {cls.__module__.split(".")[1]
                for cls in _discover_config_dataclasses().values()
                if "primary_backend" in {f.name for f in dataclasses.fields(cls)}}
    assert declared, "the sweep found no config declaring primary_backend"
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["primary_backend"]
    assert {d.split(".")[0] for d in q.writes_to} == declared


def test_every_emitted_key_is_documented_in_the_example_config():
    example = open("sluice.yaml.example", encoding="utf-8").read()
    for q in catalogue(default_vault=VAULT):
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#?\s*{re.escape(leaf)}:", example, re.M), \
                f"{dotted} is written by init but undocumented in sluice.yaml.example"


def test_no_answer_emits_a_scalar_that_loads_as_a_bool_where_an_int_is_meant(tmp_path):
    data = yaml.safe_load(_plan(tmp_path, {"lead_ttl_days": 1, "perm_floor": 1}).config_text)
    assert data["lead_ttl_days"] is not True and isinstance(data["lead_ttl_days"], int)
    assert isinstance(data["triage"]["perm_floor_gbp"], int)


def test_nasty_answers_still_yield_loadable_yaml(tmp_path):
    path = _written(tmp_path, {"cv_name": 'O\'Example: "the #1"',
                               "accept_titles": ["yes", "#hash", "back\\slash"]})
    assert load_cv_config(path).name == 'O\'Example: "the #1"'
    assert load_triage_config(path).accept_titles == ["yes", "#hash", "back\\slash"]


def test_each_section_header_appears_once(tmp_path):
    """A fan-out question writes three blocks; without hoisting, its section header and hint were
    emitted three times."""
    text = _plan(tmp_path).config_text
    assert text.count("-- Providers") == 1
    assert text.count("-- Want") == 1


def test_notes_explain_what_a_configured_gate_will_do(tmp_path):
    notes = "\n".join(_plan(tmp_path, {"relevance_keep": ["example role"]}).notes)
    assert "example role" in notes and "dropped before triage" in notes


def test_an_unanswered_run_reports_no_gates(tmp_path):
    assert not any("keep ONLY" in n for n in _plan(tmp_path).notes)
