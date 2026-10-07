from sluice.core.protocols import document_sha
from sluice.onboard import review
from sluice.onboard.plan import LEADS_VIEW_TEXT, build_plan
from tests.test_onboard_review import CONFIG, PROFILE, snap


def write_for(changes, s, tick=None, env_vault=None):
    parsed, _ = review.parse_changes(changes)
    units, _ = review.propose(parsed, s)
    ticked = [u for u in units if tick is None or u.key in tick]
    return review.build_writes(ticked, s, env_vault=env_vault)


def test_first_run_config_equals_inits_for_the_same_answers():
    writes, _ = write_for([{"kind": "config", "target": "vault_dir", "value": "/example/v"},
                           {"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                          snap(None))
    by = {w.artefact: w for w in writes}
    want = build_plan({"vault_dir": "/example/v", "lead_ttl_days": 30})
    assert by["config"].text == want.config_text and by["config"].expect_sha is None
    assert by["profile"].text == want.profile_text
    assert by["view"].text == LEADS_VIEW_TEXT
    assert "candidate" not in by          # nothing declared, as init
    assert ("lead_ttl_days", 30) in by["config"].expect


def test_first_run_with_env_vault_uses_it_and_needs_no_vault_unit():
    writes, _ = write_for([{"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                          snap(None, env=True), env_vault="/example/env-vault")
    assert {w.artefact for w in writes} >= {"config", "profile", "view"}
    cfg = writes[0]
    assert "/example/env-vault" in cfg.text
    # The config check must be allowed to see vault_dir change, or it refuses the write.
    assert "vault_dir" in cfg.settings and ("vault_dir", "/example/env-vault") in cfg.expect


def test_first_run_with_env_vault_updates_a_note_already_there():
    s = snap(None, {"profile": PROFILE}, env=True)
    writes, _ = write_for([{"kind": "profile", "target": "Who this candidate is",
                            "value": "Example."}], s, env_vault="/example/env-vault")
    prof = [w for w in writes if w.artefact == "profile"]
    assert len(prof) == 1 and prof[0].expect_sha == document_sha(PROFILE)


def test_first_run_without_env_ignores_notes_in_the_snapshot_vault():
    s = snap(None, {"profile": PROFILE})        # read from the cwd-relative default vault
    writes, _ = write_for([{"kind": "config", "target": "vault_dir", "value": "/example/v"},
                           {"kind": "profile", "target": "Who this candidate is",
                            "value": "Example."}], s)
    prof = [w for w in writes if w.artefact == "profile"]
    assert len(prof) == 1 and prof[0].expect_sha is None and "Example." in prof[0].text


CANDIDATE = build_plan({}, candidate_answers={"cv_email": "ada@example.invalid"}).candidate_text


def test_a_candidate_update_sets_one_field_under_the_shown_sha():
    writes, _ = write_for([{"kind": "candidate", "target": "cv_email",
                            "value": "example.person@example.invalid"}], snap(CONFIG, {"candidate": CANDIDATE}))
    assert writes[0].expect_sha == document_sha(CANDIDATE)
    assert 'email: "example.person@example.invalid"' in writes[0].text


def test_a_crlf_candidate_note_is_set_aside_with_the_reason():
    crlf = CANDIDATE.replace("\n", "\r\n")
    parsed, _ = review.parse_changes([{"kind": "candidate", "target": "cv_email",
                                       "value": "example.person@example.invalid"}])
    units, aside = review.propose(parsed, snap(CONFIG, {"candidate": crlf}))
    assert units == [] and "line endings" in aside[0].reason


def test_a_candidate_note_is_created_only_when_something_is_declared():
    writes, _ = write_for([{"kind": "candidate", "target": "cv_email", "clear": True}],
                          snap(CONFIG))
    assert writes == []


def test_a_search_write_may_change_only_its_own_source_and_must_read_the_full_list():
    s = snap(CONFIG, searches={"remoteok": [["A", "https://example.invalid/1"]]})
    text = review._edit.add_search(CONFIG, "remoteok", "A", "https://example.invalid/1")
    s = snap(text, searches={"remoteok": [["A", "https://example.invalid/1"]]})
    writes, _ = write_for([{"kind": "search", "target": "remoteok", "label": "B",
                            "url": "https://example.invalid/2"}], s)
    w = writes[0]
    assert w.settings == ("sources.remoteok.searches",)
    assert ("sources.remoteok.searches", [["A", "https://example.invalid/1"],
                                          ["B", "https://example.invalid/2"]]) in w.expect


def test_a_fan_out_key_that_fails_part_way_leaves_the_text_untouched():
    bad = CONFIG.replace("  # backend:   # <- uncomment and set YOUR OWN",
                         '  backend:\n    - "x"', 1)   # triage only: a block list
    writes, aside = write_for([{"kind": "config", "target": "lead_ttl_days", "value": "30"},
                               {"kind": "config", "target": "backend", "value": "anthropic"}],
                              snap(bad, settings={"triage.backend": "claude-max",
                                                  "cv.backend": "claude-max",
                                                  "track.backend": "claude-max",
                                                  "lead_ttl_days": 0}))
    assert "anthropic" not in writes[0].text and "several lines" in aside[0].reason


def test_an_unticked_vault_unit_writes_nothing_on_a_first_run():
    writes, aside = write_for([{"kind": "config", "target": "vault_dir", "value": "/example/v"},
                               {"kind": "config", "target": "lead_ttl_days", "value": "30"}],
                              snap(None), tick={"config:lead_ttl_days"})
    assert writes == [] and aside


def test_a_profile_update_splices_one_section_under_the_shown_sha():
    s = snap(CONFIG, {"profile": PROFILE})
    writes, _ = write_for([{"kind": "profile", "target": "Who this candidate is",
                            "value": "Example background."}], s)
    w = writes[0]
    assert w.expect_sha == document_sha(PROFILE) and "Example background." in w.text
    assert review.headings(w.text) == review.headings(PROFILE)


def test_an_absent_heading_is_appended_last():
    trimmed = PROFILE.split("## Industry filter")[0]
    s = snap(CONFIG, {"profile": trimmed})
    writes, _ = write_for([{"kind": "profile", "target": "## Industry filter (judgement-based,"
                            " not categorical)", "value": "Example."}], s)
    assert review.headings(writes[0].text) == review.headings(trimmed) + [
        "## Industry filter (judgement-based, not categorical)"]


def test_a_duplicated_heading_is_set_aside():
    dup = PROFILE + "\n## Who this candidate is\n\nagain\n"
    writes, aside = write_for([{"kind": "profile", "target": "Who this candidate is",
                                "value": "x"}], snap(CONFIG, {"profile": dup}))
    assert writes == [] and "more than once" in aside[0].reason


def test_a_crlf_profile_keeps_every_other_line_ending():
    crlf = PROFILE.replace("\n", "\r\n")
    writes, _ = write_for([{"kind": "profile", "target": "Who this candidate is",
                            "value": "x"}], snap(CONFIG, {"profile": crlf}))
    assert "\n" not in writes[0].text.replace("\r\n", "")


def test_clearing_a_profile_heading_restores_inits_default_section():
    edited = review.replace_section(PROFILE, "## Who this candidate is", ["", "x", ""])
    writes, _ = write_for([{"kind": "profile", "target": "Who this candidate is",
                            "clear": True}], snap(CONFIG, {"profile": edited}))
    assert writes[0].text == PROFILE


def test_config_edits_carry_their_settings_and_expected_values():
    writes, _ = write_for([{"kind": "config", "target": "backend", "value": "anthropic"}],
                          snap(CONFIG))
    w = writes[0]
    assert set(w.settings) == {"triage.backend", "cv.backend", "track.backend"}
    assert ("cv.backend", "anthropic") in w.expect and w.expect_sha == document_sha(CONFIG)


def test_a_first_brief_is_rendered_with_placeholders_for_the_rest():
    writes, _ = write_for([{"kind": "brief", "target": "Pay structure", "value": "Day rate."}],
                          snap(CONFIG))
    text = writes[0].text
    assert text.startswith("# Role Brief") and "Day rate." in text
    assert text.count(review.BRIEF_PLACEHOLDER) == len(review.ROLE_BRIEF_SECTIONS) - 1


def test_status_view_has_no_absolute_path_and_masks_vault_dir():
    s = snap(CONFIG, settings={"vault_dir": "/example/vault", "lead_ttl_days": 30})
    view = review.status_view(s)
    assert view["config"]["vault_dir"] == "set"
    assert "/example/vault" not in repr(view)


def test_nothing_ticked_writes_nothing_even_on_a_first_run():
    assert review.build_writes([], snap(None, env=True), env_vault="/example/ev") == ([], [])
