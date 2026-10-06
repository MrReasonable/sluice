"""The structured CV loop (#364/#365/#368), driven through fakes.

#364 spec §6.0 (the order per attempt), §6.4 (audit scope), §7.1
(assembly), §9.1 (the layout refusal), §12.1 (Retention, Audit scope, Vault text, Budgets,
Provenance) and §12.2 (the engine contract).
"""
import dataclasses
import inspect
import json
import os
import typing

import pytest

from sluice.core.backends import BackendError
from sluice.core.protocols import (CANDIDATE_PROFILE_RELPATH, CV_LAYOUT_RELPATH,
                                   CandidateProfile, CvDocument)
from sluice.cv.document import to_text
from sluice.cv.engine import run_one
from tests.structured_cv import (ENTRIES, GOOD_R1, GOOD_R2, LAYOUT, Cache, FakeVault, Note,
                                 RecordingRenderer, ReplyBackend, cfg, layout_with, reply)

CAPPED = layout_with(role0={"bullets_max": 1})
OUT_SLUG = "example-alpha-synthetic-role"     # cv/engine.py::_slug of the Note's company/role

# A hard-dirty reply (an invented figure) and a hard-clean reply with one slop stem.
DIRTY = reply(roles={"R1": [{"text": "Grew the team to 99", "cites": ["EA1"]}],
                     "R2": [GOOD_R2]})
STYLED = reply(roles={"R1": [{"text": "Fostered a team that grew from 3 to 8",
                              "cites": ["EA1"]}],
                      "R2": [GOOD_R2]})
# #364 spec §12.1 Retention: attempt 1 is hard-clean with one surviving style finding plus drops
# (an off-pool pick, an over-budget bullet); attempt 2 is hard-dirty with DIFFERENT drops.
ATTEMPT_1 = reply(roles={"R1": [{"text": "Fostered a team that grew from 3 to 8",
                                 "cites": ["EA1"]},
                                {"text": "Ran the Examplelang rollout", "cites": ["EA1"]}],
                         "R2": [GOOD_R2]},
                  skills=["Examplelang", "Example Ghost"])
ATTEMPT_2 = reply(profile="I ship platforms.",
                  roles={"R1": [{"text": "Grew the team from 3 to 99", "cites": ["EA1"]}],
                         "R2": [GOOD_R2]},
                  skills=["Example Zephyr"])


def _run(replies, *, vault=None, config=None, dry_run=False, **backend_kw):
    be, rend, v = ReplyBackend(replies, **backend_kw), RecordingRenderer(), vault or FakeVault()
    res = run_one(Note(), v, config or cfg(), be, Cache(), renderer=rend,
                             dry_run=dry_run)
    # #364 spec §12.1: every scripted draft must be composed. A row that scripts a retry the loop
    # never asked for would otherwise pass while testing an attempt that never ran -- so
    # the check is here, on every row, rather than on the rows that remember to ask.
    be.assert_consumed()
    return res, be, rend, v


def _out_dir():
    return os.path.join(cfg().output_dir, OUT_SLUG)


# --- refusals before any spend ------------------------------------------------------

def test_a_vault_without_a_cv_layout_is_refused_before_any_spend():
    cache, be = Cache(), ReplyBackend([])
    res = run_one(Note(), FakeVault(layout=None), cfg(), be, cache,
                             renderer=RecordingRenderer())
    assert (res.status, cache.calls, be.compose_prompts) == ("skipped-config", 0, [])
    assert CV_LAYOUT_RELPATH in res.error


def test_a_blank_identity_is_refused_naming_the_candidate_profile():
    res, be, _rend, _v = _run([], vault=FakeVault(candidate=CandidateProfile()))
    assert (res.status, be.compose_prompts) == ("skipped-config", [])
    assert CANDIDATE_PROFILE_RELPATH in res.error


# --- one attempt, end to end ----------------------------------------------------------

def test_a_clean_reply_renders_the_document_sluice_assembled():
    res, be, rend, v = _run([reply()])
    assert res.status == "rendered"
    assert (len(be.compose_prompts), len(be.audit_prompts)) == (1, 1)
    [(doc, _out)] = rend.rendered
    assert isinstance(doc, CvDocument)
    assert [(r.company, r.dates, r.location, r.title, r.bullets) for r in doc.work] == [
        ("Example Alpha", "02/2023–present", "Example Location A", "SYNTHETIC-TITLE-1",
         [GOOD_R1["text"]]),
        ("Example Beta", "06/2020–01/2023", "Example Location B", "SYNTHETIC-TITLE-2",
         [GOOD_R2["text"]])]
    assert (doc.name, doc.contact, doc.skills, doc.certificates, doc.education) == (
        "JANE ROE", "+1 555 0100", ["Examplelang"], ["Example Scrum Master"],
        ["Example University, BSc Example"])
    assert list(v.tailored) == [Note().ref] and v.tailored[Note().ref].startswith(res.served)
    assert (res.skills_dropped, res.bullets_trimmed, res.attribution_check_off) == (
        [], [], False)


def test_the_composer_is_shown_the_role_slots_the_pool_and_each_entrys_tools():
    _res, be, _rend, _v = _run([reply()])
    [prompt] = be.compose_prompts
    assert ("R1: Example Alpha | 02/2023–present | SYNTHETIC-TITLE-1 | may cite: EA1 | "
            "any number of bullets") in prompt
    assert "- Example Query" in prompt and "- Examplelang" in prompt
    assert "tools=Examplelang" in prompt


def test_an_unsupported_audit_flag_holds_the_cv_for_sign_off():
    res, _be, _rend, v = _run([reply()], audit_out="unsupported\tclaim\tNONE")
    assert (res.status, v.tailored) == ("needs-signoff", {})
    [(pending, claims)] = v.holds.values()
    assert pending.startswith(res.served) and claims[0].startswith("unsupported")


# --- the retry and what it is fed -----------------------------------------------------

def test_a_reply_that_is_not_json_feeds_the_retry_and_a_second_one_skips_the_lead():
    res, be, rend, _v = _run(["Sure! Here is your CV.", "Still no JSON."])
    assert len(be.compose_prompts) == 2
    assert "- REPLY: no JSON object in the reply" in be.compose_prompts[1]
    assert (res.status, res.violations, rend.rendered) == (
        "skipped-gate", ["REPLY: no JSON object in the reply -- reply with one JSON object "
                         "and nothing else"], [])


def test_a_retry_lists_the_previous_replys_drops():
    first = reply(roles={"R1": [{"text": "Grew the team to 99", "cites": ["EA1"]}],
                         "R2": [GOOD_R2]},
                  skills=["Example Ghost"])
    _res, be, _rend, _v = _run([first, reply()])
    retry = be.compose_prompts[1]
    assert "=== DROPPED FROM YOUR PREVIOUS REPLY" in retry
    assert "- 'Example Ghost': not one of your skills" in retry


# --- the engine contract (#364 spec §12.2) -------------------------------------------------

def test_a_reply_with_no_bullets_anywhere_is_refused_on_both_attempts():
    # A hard-clean reply with an empty role list everywhere would otherwise assemble a CV of
    # headings alone; only a layout whose every budget is 0 may ask for that (#364 D10).
    empty = reply(roles={"R1": [], "R2": []})
    res, be, rend, _v = _run([empty, empty])
    assert len(be.compose_prompts) == 2
    assert (res.status, res.violations, rend.rendered) == (
        "skipped-gate",
        ["REPLY: no bullets in any role that can carry them -- write bullets for the roles "
         "that list citable entries"], [])


def test_a_reply_keyed_entirely_by_role_headings_renders_on_one_compose():
    # Measured: a composer keyed `roles` by heading on both attempts, because the retry
    # never said which ids were valid. A heading names exactly one slot, so it is read.
    keyed = reply(roles={"Example Alpha": [GOOD_R1], "example beta": [GOOD_R2]})
    res, be, rend, _v = _run([keyed])
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    [(doc, _out)] = rend.rendered
    assert [r.bullets for r in doc.work] == [[GOOD_R1["text"]], [GOOD_R2["text"]]]


def test_the_retry_happens_exactly_once():
    res, be, rend, _v = _run([DIRTY, DIRTY])
    assert len(be.compose_prompts) == 2
    assert (res.status, rend.rendered) == ("skipped-gate", [])


@pytest.mark.parametrize("sequence,status,composes", [
    (["clean"], "rendered", 1),
    (["dirty", "clean"], "rendered", 2),
    (["dirty", "dirty"], "skipped-gate", 2),
    (["styled", "dirty"], "rendered", 2)])
def test_skipped_gate_if_and_only_if_no_attempt_was_hard_clean(sequence, status, composes):
    replies = {"clean": reply(), "dirty": DIRTY, "styled": STYLED}
    res, be, _rend, _v = _run([replies[s] for s in sequence])
    assert (len(be.compose_prompts), res.status) == (composes, status)


def test_the_retained_attempt_is_rendered_with_its_own_drops():
    res, be, rend, _v = _run([ATTEMPT_1, ATTEMPT_2], vault=FakeVault(layout=CAPPED))
    assert len(be.compose_prompts) == 2
    be.assert_consumed()
    [(doc, _out)] = rend.rendered
    assert doc.work[0].bullets == ["Fostered a team that grew from 3 to 8"]
    assert res.skills_dropped == ["'Example Ghost': not one of your skills"]
    assert res.bullets_trimmed == ["R1 (Example Alpha): kept 1 of 2"]
    assert res.status == "rendered" and [s.split(":")[0] for s in res.slop] == ["SLOP foster"]


def test_the_retained_attempt_is_the_one_audited():
    _res, be, _rend, _v = _run([ATTEMPT_1, ATTEMPT_2], vault=FakeVault(layout=CAPPED))
    [audited] = be.audited()
    assert "Fostered a team that grew from 3 to 8 [EA1]" in audited
    assert "99" not in audited and "I ship platforms." not in audited


def test_a_first_compose_that_raises_bins_the_lead():
    with pytest.raises(BackendError):
        _run([BackendError("compose timeout")])


def test_a_retry_that_raises_ships_the_draft_attempt_one_earned():
    res, be, rend, _v = _run([STYLED, BackendError("compose timeout")])
    assert (len(be.compose_prompts), res.status) == (2, "rendered")
    [(doc, _out)] = rend.rendered
    assert doc.work[0].bullets == ["Fostered a team that grew from 3 to 8"]


def test_the_voice_check_is_not_spent_on_a_hard_dirty_attempt():
    _res, be, _rend, _v = _run([DIRTY, DIRTY], config=cfg(voice_check=True))
    assert be.voice_prompts == []


def test_voice_check_off_spends_no_extra_call():
    _res, be, _rend, _v = _run([reply()])
    assert (len(be.compose_prompts), len(be.audit_prompts), be.voice_prompts) == (1, 1, [])


def test_the_voice_judge_is_shown_only_the_models_text():
    _res, be, _rend, _v = _run([reply()], config=cfg(voice_check=True))
    [prompt] = be.voice_prompts
    assert prompt.partition("=== EXCERPT ===\n")[2] == (
        f"I build reliable systems.\n{GOOD_R1['text']}\n{GOOD_R2['text']}\n")


# --- audit scope (#364 spec §6.4) ----------------------------------------------------------

def test_the_auditor_reads_only_the_models_text_and_the_tools_of_what_it_cites():
    capped = reply(roles={"R1": [GOOD_R1, {"text": "Ran the Examplelang rollout",
                                           "cites": ["EA1"]}],
                          "R2": [GOOD_R2]},
                   skills=["Example Query"])
    _res, be, _rend, _v = _run([capped], vault=FakeVault(layout=CAPPED))
    [audited] = be.audited()
    assert audited == ("PROFILE\nI build reliable systems.\n\nExample Alpha\n"
                       f"- {GOOD_R1['text']} [EA1]\n\nExample Beta\n- {GOOD_R2['text']} [EB1]\n")
    for absent in ("02/2023", "Example Scrum Master", "Example University", "Example Query",
                   "Ran the Examplelang rollout"):
        assert absent not in audited, absent
    assert "tools=Examplelang" in be.audit_prompts[0]


# --- vault text is never refused (#364 spec §6.1, §6.3) -------------------------------------

DASHED = layout_with(role0={"heading": "Example Alpha — Example Northgate",
                            "employers": ("Example Alpha",)},
                     certificates=("Example Synergy — Master",),
                     education=("Example University — BSc Example",))
SYNERGY_SKILL = [{"title": "Example Synergy", "best_for": "", "category": "", "metrics": "",
                  "body": "", "fields": {"Domain": "people"}}]


def test_vault_text_with_an_em_dash_or_a_slop_stem_renders_without_a_finding():
    res, be, rend, _v = _run([reply(skills=["Example Synergy"])],
                             vault=FakeVault(layout=DASHED, skills=SYNERGY_SKILL))
    assert (res.status, res.violations, res.slop, len(be.compose_prompts)) == (
        "rendered", [], [], 1)
    [(doc, _out)] = rend.rendered
    assert doc.work[0].company == "Example Alpha — Example Northgate"
    assert (doc.skills, doc.certificates) == (["Example Synergy"], ["Example Synergy — Master"])


def test_a_non_latin_letter_against_a_digit_in_vault_text_renders_untouched():
    # The digit look-alike rule (cv/reply.py::_lookalike) reads the MODEL's text only: the
    # same Cyrillic capital O in a certificate the user wrote is theirs and renders as is.
    cert = "Example Scrum Master 8" + chr(0x041E)
    res, _be, rend, _v = _run([reply()],
                              vault=FakeVault(layout=layout_with(certificates=(cert,))))
    assert (res.status, res.violations) == ("rendered", [])
    [(doc, _out)] = rend.rendered
    assert doc.certificates == [cert]


def test_the_same_em_dash_or_slop_stem_in_a_bullet_is_the_models_and_is_found():
    dashed = reply(roles={"R1": [{"text": "Grew the team — from 3 to 8", "cites": ["EA1"]}],
                          "R2": [{"text": "Built synergy while cutting build time by 40%",
                                  "cites": ["EB1"]}]})
    res, _be, _rend, _v = _run([dashed, dashed])
    assert (res.status, res.violations) == ("skipped-gate", [])
    assert any(s.startswith("SLOP EM-DASH") for s in res.slop)
    assert any(s.startswith("SLOP synergy") for s in res.slop)


# --- budgets (#364 spec §4.1, D10) -----------------------------------------------------------

def test_an_over_budget_bullet_costs_no_retry_and_is_never_audited():
    over = reply(roles={"R1": [GOOD_R1, {"text": "Leveraged Examplezz to reach 99",
                                         "cites": ["EA1"]}],
                        "R2": [GOOD_R2]})
    res, be, _rend, _v = _run([over], vault=FakeVault(layout=CAPPED))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    assert res.bullets_trimmed == ["R1 (Example Alpha): kept 1 of 2"]
    assert (res.violations, res.slop, res.terms) == ([], [], [])
    assert "Examplezz" not in be.audited()[0]


# A bullet the reply checks would refuse: a bracket AND a comma decimal.
REFUSED_TEXT = {"text": "Grew [the team] 2,5x", "cites": ["EA1"]}


def test_a_trimmed_bullet_whose_text_is_refused_costs_no_retry():
    # #364 spec §2, §4.3: a bullet over budget never renders, so its text can never cost a retry.
    over = reply(roles={"R1": [GOOD_R1, REFUSED_TEXT], "R2": [GOOD_R2]})
    res, be, _rend, _v = _run([over], vault=FakeVault(layout=CAPPED))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    assert res.bullets_trimmed == ["R1 (Example Alpha): kept 1 of 2"]
    assert res.violations == []


def test_a_refused_bullet_in_a_slot_with_no_eligible_entry_costs_no_retry():
    # No entry names Example Gamma as its company, so R3's budget is 0 and its bullets trim.
    gamma = dataclasses.replace(LAYOUT.roles[1], heading="Example Gamma",
                                employers=("Example Gamma",))
    layout = dataclasses.replace(LAYOUT, roles=(*LAYOUT.roles, gamma))
    ghost = reply(roles={"R1": [GOOD_R1], "R2": [GOOD_R2],
                         "R3": [{"text": "Grew 2,5x", "cites": ["EA1"]}]})
    res, be, _rend, _v = _run([ghost], vault=FakeVault(layout=layout))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    assert res.violations == []


def test_a_kept_bullet_whose_text_is_refused_drives_the_retry():
    # The control for the two rows above: the same text, KEPT, is refused as before.
    kept = reply(roles={"R1": [REFUSED_TEXT], "R2": [GOOD_R2]})
    res, be, rend, _v = _run([kept, kept], vault=FakeVault(layout=CAPPED))
    assert (res.status, len(be.compose_prompts), rend.rendered) == ("skipped-gate", 2, [])
    finding = ('REPLY: R1 bullet 1 contains a bracket -- put entry ids in "cites", never in '
               "the text")
    assert finding in res.violations and f"- {finding}" in be.compose_prompts[1]


def test_a_layout_of_zero_budgets_renders_headings_only():
    zero = dataclasses.replace(CAPPED, roles=tuple(
        dataclasses.replace(r, bullets_max=0) for r in CAPPED.roles))
    res, be, rend, _v = _run([reply(roles={})], vault=FakeVault(layout=zero))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    [(doc, _out)] = rend.rendered
    assert [(r.company, r.bullets) for r in doc.work] == [
        ("Example Alpha", []), ("Example Beta", [])]


def test_a_layout_of_zero_budgets_renders_with_no_experience_entry_at_all():
    # The headings-only CV cites nothing, so missing_prerequisites lets it through without a
    # verified entry (#364 spec §5.2, D10) -- and the loop must then render it, not fail on an
    # empty bundle.
    zero = dataclasses.replace(CAPPED, roles=tuple(
        dataclasses.replace(r, bullets_max=0) for r in CAPPED.roles))
    res, be, rend, _v = _run([reply(roles={}, skills=())],
                             vault=FakeVault(layout=zero, experience=[]))
    assert (res.status, len(be.compose_prompts)) == ("rendered", 1)
    [(doc, _out)] = rend.rendered
    assert [r.bullets for r in doc.work] == [[], []]


# --- the attribution switch (#364 spec §4.2, §6.6) -------------------------------------------

MISATTRIBUTED = reply(roles={"R1": [GOOD_R1],
                             "R2": [{"text": "Cut build time by 40% with Examplelang",
                                     "cites": ["EB1"]}]})


def test_a_misattributed_tool_is_refused_while_any_entry_declares_tools():
    res, _be, _rend, _v = _run([MISATTRIBUTED, MISATTRIBUTED])
    assert (res.status, res.attribution_check_off) == ("skipped-gate", False)
    assert any(v.startswith("MISATTRIBUTED TOOL 'Examplelang'") for v in res.violations)


def test_a_configured_decoy_in_the_profile_skips_the_lead_through_run_one():
    # `cv.fabrication_decoys` must REACH the gate: check_selection's decoy rows are unit
    # rows, and the engine's call is what a configured list depends on. Otherwise clean,
    # so the decoy is the only reason either attempt fails.
    decoyed = reply(profile="I build reliable systems on Exampledecoy.")
    res, be, rend, _v = _run([decoyed, decoyed],
                             config=cfg(fabrication_decoys=["Exampledecoy"]))
    assert (res.status, len(be.compose_prompts), rend.rendered) == ("skipped-gate", 2, [])
    assert "FABRICATED: contains 'Exampledecoy'" in res.violations


def test_with_no_entry_declaring_tools_the_check_is_off_and_the_result_says_so():
    # Examplelang moves into EA1's body so the unbundled-term check still knows the word:
    # this row isolates the switch.
    no_tools = [{**ENTRIES[0], "fields": {}, "body": "Grew 3 to 8 with CI on Examplelang."},
                {**ENTRIES[1], "fields": {}}]
    res, _be, _rend, _v = _run([MISATTRIBUTED], vault=FakeVault(experience=no_tools))
    assert (res.status, res.attribution_check_off, res.violations) == ("rendered", True, [])


# --- the Skills Inventory never costs a lead (#165) --------------------------------------

def test_each_evidence_kind_is_read_once_per_lead():
    # The framing and the pool come from ONE read of the inventory, so one compose cannot
    # frame one revision of it and offer picks from another.
    _res, _be, _rend, v = _run([reply()])
    assert sorted(v.evidence_reads) == ["experience", "skills"]


def test_a_reader_asked_for_unverified_evidence_raises_past_the_inventory_handler(
        monkeypatch):
    # The per-lead reader refuses an unverified read, and that refusal is a BUG, so it must
    # not be degraded to the unreadable-inventory warning the #165 handler gives.
    import sluice.cv.engine as engine
    monkeypatch.setattr(engine, "named_entries",
                        lambda reader: reader("skills", verified_only=False))
    with pytest.raises(TypeError, match="verified evidence only"):
        run_one(Note(), FakeVault(), cfg(), ReplyBackend([reply()]), Cache(),
                renderer=RecordingRenderer())


def test_an_unreadable_skills_inventory_composes_without_its_framing_or_its_names():
    res, be, _rend, _v = _run([reply()], vault=FakeVault(skills_error=OSError("unreadable")))
    assert (res.status, res.skills_unreadable) == ("rendered", True)
    [prompt] = be.compose_prompts
    assert "Example Query" not in prompt and "- Examplelang" in prompt


# --- what a run leaves behind (#364 spec §7.4) ------------------------------------------------

def test_each_reply_is_kept_raw_and_the_run_record_carries_the_selection_report():
    chatty = "Here you go: " + reply(
        roles={"R1": [GOOD_R1, {"text": "Ran the Examplelang rollout", "cites": ["EA1"]}],
               "R2": [GOOD_R2]},
        skills=["Examplelang", "Example Ghost"])
    res, _be, _rend, _v = _run([chatty], vault=FakeVault(layout=CAPPED))
    with open(os.path.join(_out_dir(), "reply.attempt-1.txt"), encoding="utf-8") as fh:
        assert fh.read() == chatty
    with open(os.path.join(_out_dir(), "run.json"), encoding="utf-8") as fh:
        record = json.load(fh)
    assert (record["skills_dropped"], record["bullets_trimmed"],
            record["attribution_check_off"]) == (
        res.skills_dropped, res.bullets_trimmed, False)
    assert record["skills_dropped"] == ["'Example Ghost': not one of your skills"]
    assert record["bullets_trimmed"] == ["R1 (Example Alpha): kept 1 of 2"]


def test_a_dry_run_audits_and_records_but_renders_and_writes_nothing():
    res, be, rend, v = _run([reply()], dry_run=True)
    assert (res.status, rend.rendered, v.tailored, v.holds) == ("dry-run", [], {}, {})
    assert len(be.audit_prompts) == 1
    with open(os.path.join(_out_dir(), "cv.rendered.md"), encoding="utf-8") as fh:
        assert f"- {GOOD_R1['text']} [EA1]" in fh.read()


# --- provenance (#364 spec §12.1, four parts) -------------------------------------------------

# Part 1: every leaf of the document, and where its value comes from. Closed: no default
# arm, so a field added to CvDocument or Role without a decision fails here.
PROVENANCE = {
    "name": "vault", "contact": "vault", "profile": "model",
    "work[].company": "vault", "work[].dates": "vault", "work[].location": "vault",
    "work[].title": "vault", "work[].bullets[]": "model",
    "skills[]": "model-selected, vault-valued",
    "certificates[]": "vault", "education[]": "vault",
}


def _leaf_paths(cls, prefix=""):
    hints, out = typing.get_type_hints(cls), set()
    for f in dataclasses.fields(cls):
        t, path = hints[f.name], prefix + f.name
        if typing.get_origin(t) is list:
            (inner,) = typing.get_args(t)
            out |= (_leaf_paths(inner, path + "[].") if dataclasses.is_dataclass(inner)
                    else {path + "[]"})
        elif dataclasses.is_dataclass(t):
            out |= _leaf_paths(t, path + ".")
        else:
            out.add(path)
    return out


def _leaves(doc):
    out = {"name": [doc.name], "contact": [doc.contact], "profile": [doc.profile],
           "skills[]": list(doc.skills), "certificates[]": list(doc.certificates),
           "education[]": list(doc.education),
           "work[].bullets[]": [b for r in doc.work for b in r.bullets]}
    for key in ("company", "dates", "location", "title"):
        out[f"work[].{key}"] = [getattr(r, key) for r in doc.work]
    return out


def test_every_document_leaf_has_a_declared_provenance():
    assert _leaf_paths(CvDocument) == set(PROVENANCE)


def test_every_fixture_leaf_is_non_empty_and_unique():
    # Part 2: a scope assertion on VALUES, so the canary rows below can attribute every
    # rendered string to exactly one source.
    _res, _be, rend, _v = _run([reply(skills=["Examplelang", "Example Query"])])
    [(doc, _out)] = rend.rendered
    leaves = _leaves(doc)
    assert set(leaves) == set(PROVENANCE)
    values = [value for group in leaves.values() for value in group]
    assert all(values) and len(values) == len(set(values)), values


CANARIES = {"name": "CANARY-NAME", "contact": "CANARY-CONTACT", "company": "CANARY-COMPANY",
            "dates": "CANARY-DATES", "location": "CANARY-LOCATION", "title": "CANARY-TITLE",
            "certificates": ["CANARY-CERT"], "education": ["CANARY-EDU"],
            "work": ["CANARY-WORK"]}


def test_a_hostile_reply_cannot_reach_a_vault_owned_field():
    # Part 3: the parser ignores unknown keys, at the top level and inside a bullet, so a
    # reply may carry keys named like every vault-owned field. The row first proves the CV
    # RENDERED, so it cannot pass by rendering nothing.
    bullet = {**GOOD_R1, **{k: v for k, v in CANARIES.items() if isinstance(v, str)}}
    hostile = reply(roles={"R1": [bullet], "R2": [GOOD_R2]}, **CANARIES)
    res, be, rend, _v = _run([hostile])
    assert (res.status, len(rend.rendered)) == ("rendered", 1)
    [(doc, out_dir)] = rend.rendered
    assert doc.work and doc.profile
    with open(os.path.join(out_dir, "cv.rendered.md"), encoding="utf-8") as fh:
        recorded = fh.read()
    for surface in (to_text(doc), recorded, be.audited()[0]):
        assert "CANARY" not in surface


def test_the_document_carries_the_retained_attempts_text():
    # Part 4: through the engine, on the Retention sequence.
    _res, _be, rend, _v = _run([ATTEMPT_1, ATTEMPT_2], vault=FakeVault(layout=CAPPED))
    [(doc, _out)] = rend.rendered
    first = json.loads(ATTEMPT_1)
    assert doc.profile == first["profile"]
    assert doc.work[0].bullets == [first["roles"]["R1"][0]["text"]]
    assert doc.skills == ["Examplelang"]


# --- the fake is the store it fakes -------------------------------------------------------

def test_the_fake_vault_matches_the_store_it_fakes():
    from sluice.core.protocols import Store
    from sluice.core.vault import Vault
    shared = sorted(n for n in dir(Store) if not n.startswith("_")
                    and callable(getattr(Store, n)) and hasattr(FakeVault, n))
    assert {"read_cv_layout", "read_candidate_profile", "read_evidence", "read_leads",
            "set_tailored_cv", "hold_for_signoff"} <= set(shared), shared
    for name in shared:
        fake, real = (inspect.signature(getattr(c, name)) for c in (FakeVault, Vault))
        assert list(fake.parameters) == list(real.parameters), (name, fake, real)
