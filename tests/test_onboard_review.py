import pytest

from sluice.core.protocols import SetupSnapshot
from sluice.onboard import review
from sluice.onboard.plan import build_plan

_SETTINGS = {"triage.backend": "claude-max", "cv.backend": "claude-max",
             "track.backend": "claude-max", "lead_ttl_days": 0}


def snap(config=None, notes=None, *, env=False, default=False, settings=None, searches=None):
    base = {"profile": None, "candidate": None, "brief": None, "view": None}
    return SetupSnapshot(config_text=config, notes={**base, **(notes or {})}, unreadable={},
                         vault_from_env=env, vault_is_default=default,
                         settings=settings or dict(_SETTINGS), defaults=dict(_SETTINGS),
                         source_ids=("remoteok",), searches=searches or {})


CONFIG = build_plan({"vault_dir": "/example/vault"}).config_text
PROFILE = build_plan({}).profile_text


def propose(changes, s):
    parsed, bad = review.parse_changes(changes)
    units, aside = review.propose(parsed, s)
    return units, bad + aside


def test_an_unknown_kind_or_key_is_set_aside_by_name():
    units, aside = propose([{"kind": "nope", "target": "x", "value": "y"},
                            {"kind": "config", "target": "not_a_key", "value": "y"}],
                           snap(CONFIG))
    assert units == [] and {a.label for a in aside} == {"nope: x", "config: not_a_key"}


def test_a_change_naming_verified_is_set_aside_in_every_kind():
    for kind in review.UNIT_KINDS:
        units, aside = propose([{"kind": kind, "target": "verified", "value": "yes"}],
                               snap(CONFIG))
        assert units == [] and len(aside) == 1


def test_a_config_value_goes_through_the_questions_own_parser():
    units, aside = propose([{"kind": "config", "target": "lead_ttl_days", "value": "yes"}],
                           snap(CONFIG))
    assert units == [] and "yes/no word" in aside[0].reason


def test_vault_dir_is_first_run_only_and_never_when_vault_dir_env_decides():
    _, aside = propose([{"kind": "config", "target": "vault_dir", "value": "/x"}], snap(CONFIG))
    assert "seen" in aside[0].reason
    _, aside = propose([{"kind": "config", "target": "vault_dir", "value": "/x"}],
                       snap(None, env=True))
    assert "VAULT_DIR" in aside[0].reason


def test_first_run_without_a_vault_sets_every_unit_aside():
    units, aside = propose([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                           snap(None))
    assert units == [] and "where your notes live" in aside[0].reason


def test_an_existing_hunt_on_the_default_vault_sets_note_units_aside():
    units, aside = propose([{"kind": "profile", "target": "## Who this candidate is",
                             "value": "Example text."}], snap(CONFIG, default=True))
    assert units == [] and "vault_dir" in aside[0].reason


@pytest.mark.parametrize("value,why", [("# a heading", "heading"), ("---", "break the note"),
                                       ("a <!-- b", "comment marker"),
                                       ("b --> c", "comment marker"),
                                       ("a\rb", "control character"), ("", "empty")])
def test_the_prose_rule_sets_aside_with_its_own_reason(value, why):
    _, aside = propose([{"kind": "profile", "target": "## Who this candidate is",
                         "value": value}], snap(CONFIG, {"profile": PROFILE}))
    assert len(aside) == 1 and why in aside[0].reason


def test_a_value_one_line_taller_than_the_form_is_set_aside_and_one_line_shorter_is_not():
    from sluice.core.formfit import FORM_LINES, entry_lines
    def value(n):
        return "\n".join(["x"] * n)
    n = 1
    while entry_lines("Role Brief: Pay structure", "New:\n" + value(n + 1)) <= FORM_LINES:
        n += 1
    fits, _ = propose([{"kind": "brief", "target": "Pay structure", "value": value(n)}],
                      snap(CONFIG))
    over, aside = propose([{"kind": "brief", "target": "Pay structure",
                            "value": value(n + 1)}], snap(CONFIG))
    assert len(fits) == 1 and over == [] and "does not fit" in aside[0].reason


def test_a_second_change_to_the_same_unit_is_set_aside_not_merged():
    units, aside = propose([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                            {"kind": "config", "target": "lead_ttl_days", "value": "60"}],
                           snap(CONFIG))
    assert [u.after for u in units] == ["30"] and "already proposes" in aside[0].reason


def test_two_searches_with_one_label_but_different_urls_are_two_units_with_two_titles():
    units, _ = propose([{"kind": "search", "target": "remoteok", "label": "A",
                         "url": "https://example.invalid/1"},
                        {"kind": "search", "target": "remoteok", "label": "A",
                         "url": "https://example.invalid/2"}], snap(CONFIG))
    assert len({u.key for u in units}) == 2 and len({u.title for u in units}) == 2


def test_every_set_aside_reason_names_no_preference():
    from sluice.onboard.questions import expresses_a_preference
    bad = [{"kind": "nope", "target": "x"}, {"kind": "config", "target": "not_a_key"},
           {"kind": "config", "target": "lead_ttl_days", "value": "yes"},
           {"kind": "config", "target": "lead_ttl_days", "value": "1"},
           {"kind": "config", "target": "lead_ttl_days", "value": "2"},
           {"kind": "profile", "target": "Nope", "value": "x"},
           {"kind": "brief", "target": "Pay structure", "value": "# h"},
           {"kind": "candidate", "target": "cv_surname", "value": '"q"'},
           {"kind": "search", "target": "nope", "label": "a", "url": "https://example.invalid"}]
    _, aside = propose(bad, snap(CONFIG))
    assert len(aside) >= 7 and all(expresses_a_preference(a.reason) == [] for a in aside)


def test_every_set_aside_carries_its_own_units_key():
    _, aside = propose([{"kind": "config", "target": "lead_ttl_days", "value": "yes"},
                        {"kind": "config", "target": "min_jd_chars", "value": "200x"}],
                       snap(CONFIG))
    assert {a.key for a in aside} == {"config:lead_ttl_days", "config:min_jd_chars"}


def test_backend_is_set_aside_when_the_stages_disagree():
    s = snap(CONFIG, settings={**_SETTINGS, "cv.backend": "anthropic"})
    _, aside = propose([{"kind": "config", "target": "backend", "value": "anthropic"}], s)
    assert "disagree" in aside[0].reason


def test_backend_is_set_aside_when_a_stage_names_its_own_model():
    cfg = CONFIG + "\ntriage:\n  model: \"example-model\"\n"
    _, aside = propose([{"kind": "config", "target": "backend", "value": "anthropic"}],
                       snap(cfg))
    assert "model" in aside[0].reason


def test_a_candidate_value_that_cannot_round_trip_is_set_aside():
    _, aside = propose([{"kind": "candidate", "target": "cv_surname", "value": '"Quoted"'}],
                       snap(CONFIG))
    assert len(aside) == 1


def test_units_carry_before_and_after_for_an_update():
    units, _ = propose([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                       snap(CONFIG))
    assert units[0].before == "0" and units[0].after == "30"


def test_label_spacing_does_not_make_a_second_box_for_one_search():
    units, aside = propose([{"kind": "search", "target": "remoteok", "label": "B",
                             "url": "https://example.invalid/1"},
                            {"kind": "search", "target": "remoteok", "label": "B ",
                             "url": " https://example.invalid/1 "}], snap(CONFIG))
    assert len(units) == 1 and len(aside) == 1 and "already proposes" in aside[0].reason
    assert aside[0].key == units[0].key


def test_an_add_and_a_remove_of_one_search_are_one_box_not_two():
    units, aside = propose([{"kind": "search", "target": "remoteok", "label": "B",
                             "url": "https://example.invalid/1"},
                            {"kind": "search", "target": "remoteok", "label": "B",
                             "url": "https://example.invalid/1", "remove": True}], snap(CONFIG))
    assert len(units) == 1 and "add" in units[0].title
    assert len(aside) == 1 and "already proposes" in aside[0].reason
