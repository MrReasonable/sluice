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
                         source_ids=("example-board",), searches=searches or {},
                         source_defaults={"enabled": True, "tuning": {}})


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
                                       ("a\rb", "control or bidirectional character"),
                                       ("a" + chr(0x202e) + "b", "bidirectional"),
                                       ("", "empty")])
def test_the_prose_rule_sets_aside_with_its_own_reason(value, why):
    _, aside = propose([{"kind": "profile", "target": "## Who this candidate is",
                         "value": value}], snap(CONFIG, {"profile": PROFILE}))
    assert len(aside) == 1 and why in aside[0].reason


def test_a_value_of_any_height_or_length_is_taken_there_is_no_form_to_fit():
    """The per-change form capped a value at about one screen; setup_save shows nothing in a
    form, so a long section is a unit like any other (spec 2026-10-08, setup_save)."""
    tall = "\n".join(f"Example line {i}." for i in range(200))
    units, aside = propose([{"kind": "brief", "target": "Sources consulted", "value": tall}],
                           snap(CONFIG))
    assert aside == [] and [u.after for u in units] == [tall]


def test_a_second_change_to_the_same_unit_is_set_aside_not_merged():
    units, aside = propose([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                            {"kind": "config", "target": "lead_ttl_days", "value": "60"}],
                           snap(CONFIG))
    assert [u.key for u in units] == ["config:lead_ttl_days"] and "same thing" in aside[0].reason
    assert units[0].change.value == "30"


def test_an_add_and_a_remove_of_one_search_share_a_key_so_the_second_is_set_aside():
    """The one case the key check holds alone: the key leaves the verb out, so an add and a
    remove of one search are one key. Without the key check both
    units would pass -- two contradictory writes to one search in one save."""
    search = {"kind": "search", "target": "example-board", "label": "A",
              "url": "https://example.invalid/1"}
    # No search configured yet: the add is valid, so the remove reaches the duplicate check
    # rather than its own "not configured" refusal.
    units, aside = propose([search, {**search, "remove": True}], snap(CONFIG))
    assert [u.key for u in units] == ["search:example-board:A:https://example.invalid/1"]
    assert units[0].change.remove is False
    assert len(aside) == 1 and "same thing" in aside[0].reason


def test_two_searches_with_one_label_but_different_urls_are_two_units():
    units, _ = propose([{"kind": "search", "target": "example-board", "label": "A",
                         "url": "https://example.invalid/1"},
                        {"kind": "search", "target": "example-board", "label": "A",
                         "url": "https://example.invalid/2"}], snap(CONFIG))
    assert [u.key for u in units] == ["search:example-board:A:https://example.invalid/1",
                                      "search:example-board:A:https://example.invalid/2"]


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


def test_a_key_the_editor_cannot_place_is_set_aside_at_propose_time():
    """The edit is rehearsed at propose time: a value continuing past its line would otherwise
    pass propose and only be reported failed by the config check."""
    cfg = CONFIG + '\ntriage:\n  accept_titles: [a,\n    b]\n'
    units, aside = propose([{"kind": "config", "target": "accept_titles", "value": "x"}],
                           snap(cfg))
    assert units == [] and [a.key for a in aside] == ["config:accept_titles"]
    assert "several lines" in aside[0].reason


def test_a_plain_value_continuing_on_a_deeper_line_is_set_aside_at_propose_time():
    cfg = CONFIG + '\ntriage:\n  accept_titles: foo\n    bar\n'
    units, aside = propose([{"kind": "config", "target": "accept_titles", "value": "x"}],
                           snap(cfg))
    assert units == [] and "several lines" in aside[0].reason


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


def test_label_spacing_does_not_make_a_second_unit_for_one_search():
    units, aside = propose([{"kind": "search", "target": "example-board", "label": "B",
                             "url": "https://example.invalid/1"},
                            {"kind": "search", "target": "example-board", "label": "B ",
                             "url": " https://example.invalid/1 "}], snap(CONFIG))
    assert len(units) == 1 and len(aside) == 1 and "already proposes" in aside[0].reason
    assert aside[0].key == units[0].key


def test_an_add_and_a_remove_of_one_search_are_one_unit_not_two():
    units, aside = propose([{"kind": "search", "target": "example-board", "label": "B",
                             "url": "https://example.invalid/1"},
                            {"kind": "search", "target": "example-board", "label": "B",
                             "url": "https://example.invalid/1", "remove": True}], snap(CONFIG))
    assert len(units) == 1 and units[0].change.remove is False
    assert len(aside) == 1 and "already proposes" in aside[0].reason


@pytest.mark.parametrize("raw", ["/example/chosen-vault", "~/example-vault"])
def test_the_vault_dir_unit_writes_the_resolved_path(raw):
    """The config the unit writes names the path parse_path resolves, never a placeholder."""
    from sluice.onboard.questions import parse_path
    s = snap(None)
    units, aside = propose([{"kind": "config", "target": "vault_dir", "value": raw}], s)
    assert aside == [] and [u.key for u in units] == ["config:vault_dir"]
    writes, aside = review.build_writes(units, s)
    (config,) = [w for w in writes if w.artefact == "config"]
    assert aside == [] and f"vault_dir: {review.scalar(parse_path(raw))}" in config.text


@pytest.mark.parametrize("raw", ["notes", "./notes", "../notes", "", "~nosuchuser-sluice-test/notes"])
def test_a_relative_vault_dir_is_set_aside_and_names_no_vault_for_the_rest(raw):
    """A relative answer would resolve against the folder the server was started from. It is
    set aside, and it does not count as naming the vault, so the batch's other changes are set
    aside at propose time rather than when the writes are built."""
    units, aside = propose([{"kind": "config", "target": "vault_dir", "value": raw},
                            {"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                           snap(None))
    assert units == []
    by = {a.label: a.reason for a in aside}
    assert "relative path" in by["config: vault_dir"]
    assert "where your notes live" in by["config: lead_ttl_days"]


def test_clear_on_a_search_is_set_aside_by_name_not_read_as_an_add():
    units, aside = propose([{"kind": "search", "target": "example-board", "label": "Example",
                             "url": "https://example.invalid/a", "clear": True}], snap(CONFIG))
    assert units == [] and "`remove: true`" in aside[0].reason


_A = ["Example", "https://example.invalid/a"]
_B = ["Second", "https://example.invalid/b"]


@pytest.mark.parametrize("change,configured,why", [
    ({"label": _A[0], "url": _A[1]}, [_A], "already configured"),
    ({"label": _B[0], "url": _B[1], "remove": True}, [_A], "not configured"),
    ({"label": _A[0], "url": _A[1], "remove": True}, [_A], "last search"),
])
def test_a_search_that_could_not_be_written_is_set_aside_by_name(change, configured, why):
    """Checked against snap.searches at propose time: before this, each passed propose and
    only then set aside by build_writes' editor."""
    units, aside = propose([{"kind": "search", "target": "example-board", **change}],
                           snap(CONFIG, searches={"example-board": configured}))
    assert units == [] and why in aside[0].reason


def test_removing_one_of_two_configured_searches_is_a_unit():
    units, aside = propose([{"kind": "search", "target": "example-board", "label": _A[0],
                             "url": _A[1], "remove": True}],
                           snap(CONFIG, searches={"example-board": [_A, _B]}))
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


@pytest.mark.parametrize("env", [False, True], ids=["no-vault-env", "vault-env"])
def test_a_first_run_records_old_text_only_from_the_vault_it_will_write(env):
    """inv-004: with no $VAULT_DIR the snapshot read the cwd-relative default vault, not the one
    being chosen, so its text is not what a write would replace (and must never come back as
    `previous`). The units then record no old text, as `_note_writes(existing=False)` creates;
    with $VAULT_DIR the
    snapshot IS the target, so the old text is recorded."""
    candidate = build_plan({}, candidate_answers={"cv_email": "ada@example.invalid"}).candidate_text
    s = snap(None, {"profile": PROFILE, "candidate": candidate}, env=env)
    batch = [{"kind": "profile", "target": "Who this candidate is", "value": "Example."},
             {"kind": "candidate", "target": "cv_email", "value": "b@example.invalid"}]
    if not env:
        batch.append({"kind": "config", "target": "vault_dir", "value": "/example/v"})
    units, aside = propose(batch, s)
    befores = {u.kind: u.before for u in units}
    assert set(befores) >= {"profile", "candidate"}, aside
    if env:
        assert befores["profile"] and befores["candidate"] == "ada@example.invalid"
    else:
        assert befores["profile"] is None and befores["candidate"] is None


# ── previous: what a written change replaced ─────────────────────────────────

def _one(change, s):
    units, aside = propose([change], s)
    assert aside == [] and len(units) == 1, aside
    return units[0]


def test_previous_is_the_config_answer_a_set_key_held():
    cfg = CONFIG.replace("# lead_ttl_days:", "lead_ttl_days: 14  #")
    s = snap(cfg, settings={**_SETTINGS, "lead_ttl_days": 14})
    u = _one({"kind": "config", "target": "lead_ttl_days", "value": "30"}, s)
    assert review.previous(u, s) == "14"
    cleared = _one({"kind": "config", "target": "lead_ttl_days", "clear": True}, s)
    assert review.previous(cleared, s) == "14"


def test_previous_of_a_list_setting_is_the_list_itself():
    from sluice.onboard.questions import catalogue
    q = next(q for q in catalogue() if q.key == "accept_titles")
    old = ["Example one", "Example two"]
    cfg = CONFIG + '\ntriage:\n  accept_titles: ["Example one", "Example two"]\n'
    s = snap(cfg, settings={**_SETTINGS, q.writes_to[0]: old})
    u = _one({"kind": "config", "target": "accept_titles", "value": "Example three"}, s)
    assert review.previous(u, s) == old


def _list_previous(old, flow):
    from sluice.onboard.questions import catalogue
    q = next(q for q in catalogue() if q.key == "reject_companies")
    cfg = CONFIG.replace("  # reject_companies:", f"  reject_companies: {flow}  #")
    assert cfg != CONFIG
    s = snap(cfg, settings={**_SETTINGS, q.writes_to[0]: old})
    u = _one({"kind": "config", "target": "reject_companies", "value": "Example Three"}, s)
    return q, review.previous(u, s)


def test_a_list_item_holding_a_comma_comes_back_whole_in_previous():
    """inv-001: joined with ", " and split again on every comma, an item holding a comma came
    back as two items, so a restore would have written a broader filter than the user had.
    `previous` is the list itself, item by item, so sending it back restores it exactly."""
    q, prev = _list_previous(["Example, Inc.", "Other Co"], '["Example, Inc.", "Other Co"]')
    assert prev == ["Example, Inc.", "Other Co"]
    assert review.parse_value(q, prev) == ["Example, Inc.", "Other Co"]


@pytest.mark.parametrize("old,flow", [
    ([" Example Inc"], '[" Example Inc"]'),       # surrounding whitespace: stripped on the way in
    (["Example Inc", ""], '["Example Inc", ""]'),  # an empty item: refused on the way in
    ([2024], "[2024]"),                            # not text: refused on the way in
])
def test_a_hand_typed_list_no_list_could_reproduce_is_not_restorable_and_never_none(old, flow):
    """What remains of `not_restorable` for a list setting: a hand-typed item `parse_items`
    would not take back verbatim. Never None, which the coach would answer with `clear`."""
    q, prev = _list_previous(old, flow)
    assert isinstance(prev, review.NotRestorable)
    assert f"`{q.writes_to[0]}`" in prev.reason and "by hand" in prev.reason


# ── a list setting's value as a list ─────────────────────────────────────────

def _list_keys():
    from sluice.onboard import questions
    return [q.key for q in questions.catalogue() if questions.is_list(q)]


def test_the_list_settings_are_derived_from_the_catalogue_parsers():
    """`is_list` keys on the parser, so this pins the derivation against what each parser
    RETURNS, not against a hand-kept roster: a question whose answer parses to a list is a list
    setting, and no other is."""
    from sluice.onboard import questions
    for q in questions.catalogue():
        sample = {"listing_languages": "en, de"}.get(q.key, "/example/a, b")
        try:
            parsed = q.parse(getattr(q.parse, "allowed", (sample,))[0])
        except questions.BadAnswer:
            parsed = None
        assert questions.is_list(q) == isinstance(parsed, list), q.key
    assert "reject_companies" in _list_keys() and "listing_languages" in _list_keys()
    assert "lead_ttl_days" not in _list_keys()


def test_status_view_names_the_list_settings():
    assert review.status_view(snap(CONFIG))["list_settings"] == _list_keys()


ITEMS = ["Example, Inc.", 'Say "hi"', "a: b", "x # y", "Remote, Example"]


@pytest.mark.parametrize("key", _list_keys())
def test_a_list_value_keeps_each_item_whole(key):
    from sluice.onboard.questions import catalogue
    q = next(q for q in catalogue() if q.key == key)
    items = ["en", "de"] if key == "listing_languages" else ITEMS
    u = _one({"kind": "config", "target": key, "value": [f"  {i} " for i in items]},
             snap(CONFIG))
    assert review.parse_value(q, u.change.value) == items


def test_a_string_value_for_a_list_setting_is_still_split_on_commas():
    from sluice.onboard.questions import catalogue
    q = next(q for q in catalogue() if q.key == "target_locations")
    u = _one({"kind": "config", "target": "target_locations", "value": "Remote, Example, Placeland"},
             snap(CONFIG))
    assert review.parse_value(q, u.change.value) == ["Remote", "Example", "Placeland"]


def test_a_list_for_a_scalar_setting_is_set_aside_by_name():
    units, aside = propose([{"kind": "config", "target": "lead_ttl_days", "value": ["30"]}],
                           snap(CONFIG))
    assert units == [] and "`lead_ttl_days` takes one value, not a list" in aside[0].reason


def test_a_list_for_vault_dir_is_set_aside_on_a_first_run_without_crashing():
    units, aside = propose([{"kind": "config", "target": "vault_dir", "value": ["/example/v"]}],
                           snap(None))
    assert units == [] and len(aside) == 1


@pytest.mark.parametrize("kind,target", [("profile", "Who this candidate is"),
                                         ("candidate", "cv_email"),
                                         ("brief", "Pay structure")])
def test_a_list_for_a_text_kind_is_set_aside(kind, target):
    units, aside = propose([{"kind": kind, "target": target, "value": ["Example."]}],
                           snap(CONFIG, {"profile": PROFILE}, env=True))
    assert units == [] and review.LIST_FOR_TEXT in aside[0].reason


@pytest.mark.parametrize("bad,why", [(["Example", "  "], "empty"), (["Example", 7], "text"),
                                     (["Example", "a\nb"], "line break"),
                                     (["Example", "a" + chr(27) + "b"], "control character")])
def test_an_empty_or_non_text_item_is_set_aside(bad, why):
    units, aside = propose([{"kind": "config", "target": "accept_titles", "value": bad}],
                           snap(CONFIG))
    assert units == [] and why in aside[0].reason


def test_no_previous_for_a_key_that_was_not_set_or_did_not_change():
    s = snap(CONFIG)        # init's file: lead_ttl_days is commented, its default in force
    assert review.previous(_one({"kind": "config", "target": "lead_ttl_days", "value": "30"},
                                s), s) is None
    cfg = CONFIG.replace("# lead_ttl_days:", "lead_ttl_days: 30  #")
    s = snap(cfg, settings={**_SETTINGS, "lead_ttl_days": 30})
    assert review.previous(_one({"kind": "config", "target": "lead_ttl_days", "value": "30"},
                                s), s) is None


def test_previous_of_a_section_is_the_users_text_and_never_the_default():
    target = {"kind": "profile", "target": "Who this candidate is", "value": "New words."}
    mine = review.replace_section(PROFILE, "## Who this candidate is", ["", "Old words.", ""])
    s = snap(CONFIG, {"profile": mine})
    assert review.previous(_one(target, s), s) == "Old words."
    s = snap(CONFIG, {"profile": PROFILE})     # init's neutral default under the heading
    assert review.previous(_one(target, s), s) is None
    brief = review.render_role_brief({})       # every section holds the placeholder
    s = snap(CONFIG, {"brief": brief})
    assert review.previous(_one({"kind": "brief", "target": "Pay structure",
                                 "value": "New."}, s), s) is None


def test_previous_of_a_candidate_field_is_its_old_value_only_when_it_had_one():
    filled = build_plan({}, candidate_answers={
        "cv_email": "ada@example.invalid"}).candidate_text
    s = snap(CONFIG, {"candidate": filled})
    u = _one({"kind": "candidate", "target": "cv_email",
              "value": "example.person@example.invalid"}, s)
    assert review.previous(u, s) == "ada@example.invalid"
    blank = build_plan({}).candidate_text
    s = snap(CONFIG, {"candidate": blank})
    assert review.previous(_one({"kind": "candidate", "target": "cv_email",
                                 "value": "ada@example.invalid"}, s), s) is None


def test_a_search_carries_no_previous():
    s = snap(CONFIG)
    u = _one({"kind": "search", "target": "example-board", "label": "A",
              "url": "https://example.invalid/1"}, s)
    assert review.previous(u, s) is None
