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
    assert [u.after for u in units] == ["30"] and "same thing" in aside[0].reason


def test_an_add_and_a_remove_of_one_search_share_a_key_so_the_second_is_set_aside():
    """The one case the key check holds alone: the key leaves the verb out, the title keeps it,
    so an add and a remove of one search are two titles but one key. Without the key check both
    boxes would be shown -- two contradictory writes to one search behind two ticks."""
    search = {"kind": "search", "target": "remoteok", "label": "A",
              "url": "https://example.invalid/1"}
    # No search configured yet: the add is valid, so the remove reaches the duplicate check
    # rather than its own "not configured" refusal.
    units, aside = propose([search, {**search, "remove": True}], snap(CONFIG))
    assert [u.title for u in units] == ["Search on remoteok: add A (https://example.invalid/1)"]
    assert len(aside) == 1 and "same thing" in aside[0].reason


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


@pytest.mark.parametrize("raw", ["/example/chosen-vault", "~/example-vault"])
def test_the_vault_dir_box_shows_the_resolved_path_it_approves(raw):
    """The user must see every value before it is written, and this one decides where every
    note goes: the box carries the path parse_path will write, never a placeholder."""
    from sluice.onboard.questions import parse_path
    units, aside = propose([{"kind": "config", "target": "vault_dir", "value": raw}], snap(None))
    assert aside == [] and len(units) == 1
    assert review.unit_body(units[0]) == f"New:\n{review.scalar(parse_path(raw))}"


@pytest.mark.parametrize("raw", ["notes", "./notes", "../notes", "", "~nosuchuser-sluice-test/notes"])
def test_a_relative_vault_dir_is_set_aside_and_names_no_vault_for_the_rest(raw):
    """A relative answer would resolve against the folder the server was started from. It is
    set aside, and it does not count as naming the vault, so the batch's other boxes are not
    shown only to be set aside on retry."""
    units, aside = propose([{"kind": "config", "target": "vault_dir", "value": raw},
                            {"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                           snap(None))
    assert units == []
    by = {a.label: a.reason for a in aside}
    assert "relative path" in by["config: vault_dir"]
    assert "where your notes live" in by["config: lead_ttl_days"]


def test_clear_on_a_search_is_set_aside_by_name_not_read_as_an_add():
    units, aside = propose([{"kind": "search", "target": "remoteok", "label": "Example",
                             "url": "https://example.invalid/a", "clear": True}], snap(CONFIG))
    assert units == [] and "`remove: true`" in aside[0].reason


_A = ["Example", "https://example.invalid/a"]
_B = ["Second", "https://example.invalid/b"]


@pytest.mark.parametrize("change,configured,why", [
    ({"label": _A[0], "url": _A[1]}, [_A], "already configured"),
    ({"label": _B[0], "url": _B[1], "remove": True}, [_A], "not configured"),
    ({"label": _A[0], "url": _A[1], "remove": True}, [_A], "last search"),
])
def test_a_search_box_that_could_not_be_written_is_never_shown(change, configured, why):
    """Checked against snap.searches at propose time: before this, each was shown, ticked, and
    only then set aside by build_writes' editor."""
    units, aside = propose([{"kind": "search", "target": "remoteok", **change}],
                           snap(CONFIG, searches={"remoteok": configured}))
    assert units == [] and why in aside[0].reason


def test_removing_one_of_two_configured_searches_is_shown():
    units, aside = propose([{"kind": "search", "target": "remoteok", "label": _A[0],
                             "url": _A[1], "remove": True}],
                           snap(CONFIG, searches={"remoteok": [_A, _B]}))
    assert aside == [] and len(units) == 1


@pytest.mark.parametrize("first,second", [
    ({"kind": "profile", "target": "## Who this candidate is"},
     {"kind": "profile", "target": "Who this candidate is"}),
    ({"kind": "profile", "target": "Who this candidate is"},
     {"kind": "profile", "target": "#  Who this candidate is "}),
    ({"kind": "candidate", "target": "cv_surname"},
     {"kind": "candidate", "target": "surname"}),
])
def test_two_spellings_of_one_target_are_one_unit_and_a_duplicate(first, second):
    """The unit key normalises a heading's spelling and a candidate field's name, so two
    spellings of one target are caught as the SAME thing (not merely the same title)."""
    units, aside = propose([{**first, "value": "Example one."},
                            {**second, "value": "Example two."}],
                           snap(CONFIG, {"profile": PROFILE}))
    assert len(units) == 1 and units[0].change.value == "Example one."
    assert len(aside) == 1 and "the same thing" in aside[0].reason
