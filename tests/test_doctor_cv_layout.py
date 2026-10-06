"""Doctor's rows for the structured CV (#364/#365/#368 spec §9.1, D14, §8, §6.6), each in
each state, asserted through the DEFAULT printed view -- a row's state alone is not what a
user reads."""
import os

import pytest

from sluice.core.doctor import (DEAD, DEGRADED, NOTICE, OK, SETUP, ComponentCheck, DoctorReport,
                                classify_attribution, classify_cv_eligibility,
                                classify_cv_layout, classify_decoys, classify_skill_labels,
                                classify_tools)
from sluice.core.protocols import CV_LAYOUT_RELPATH, CvLayout, LayoutError, LayoutRole
from sluice.cv.engine import missing_prerequisites
from tests.conftest import layout_yaml
from tests.test_cv_prerequisites import _layout_path, _vault

LAYOUT = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                    employers=("Example Alpha",)),),
                  omitted=("Example Tidal",))


def _entry(company="Example Alpha", tools="", skills=""):
    # Keywords, not dict literals keyed by the field names: the fixture-name sweep reads such
    # a literal's value as a skill NAME, and these are parameters.
    return {"title": "SYNTHETIC-ENTRY", "company": company,
            "fields": dict(Tools=tools, Skills=skills)}


def _printed(capsys, *rows, strict=False):
    from sluice.cli import _print_doctor_verdict
    report = DoctorReport(checks=[], components=list(rows))
    _print_doctor_verdict(report, offline=True, strict=strict,
                          exit_code=report.exit_code(strict=strict))
    return " ".join(capsys.readouterr().out.split())


# --- the CV Layout row -------------------------------------------------------------------

def test_a_parsed_layout_is_ok_with_its_role_count():
    row = classify_cv_layout(LAYOUT)
    assert (row.subject, row.state, row.detail) == ("cv_layout", OK, "1 role")


def test_an_absent_layout_is_setup_blocking_cv_and_says_where(capsys):
    row = classify_cv_layout(None)
    assert (row.state, row.blocks) == (SETUP, ("cv",))
    out = _printed(capsys, row)
    assert "Still to set up:" in out and CV_LAYOUT_RELPATH in out


def test_a_malformed_layout_is_dead_and_lists_every_problem(capsys):
    row = classify_cv_layout(None, LayoutError(["roles[0].from: required",
                                                "roles[0].to: required"]))
    assert (row.state, row.blocks) == (DEAD, ("cv",))
    out = _printed(capsys, row)
    assert "Not working:" in out
    assert "roles[0].from: required" in out and "roles[0].to: required" in out


@pytest.mark.parametrize("exc,strerror", [
    (PermissionError(13, "Permission denied", "/abs/vault/x"), "Permission denied"),
    (IsADirectoryError(21, "Is a directory", "/abs/vault/x"), "Is a directory"),
])
def test_an_unreadable_layout_is_dead_never_absent_and_names_no_path(exc, strerror):
    row = classify_cv_layout(None, exc)
    assert (row.state, row.blocks) == (DEAD, ("cv",))
    assert "could not be read" in row.detail and strerror in row.detail
    assert "/abs" not in row.detail


# --- an unusable Tools item, which the gate cannot use -------------------------------------

def test_an_unusable_tools_item_is_dead_and_counted_never_named(capsys):
    rows = classify_tools([_entry(tools="Examplelang"), _entry(tools="9001 Examplestandard")])
    assert [(r.state, r.blocks) for r in rows] == [(DEAD, ("cv",))]
    out = _printed(capsys, *rows)
    assert "1 verified entry" in out and "job-sluice experience list" in out
    assert "9001" not in out and "SYNTHETIC-ENTRY" not in out


def test_usable_tools_draw_no_row():
    assert classify_tools([_entry(tools="Examplelang, .Examplenet"), _entry()]) == []


# --- entries no CV can cite (#364 D6, D14) -------------------------------------------------------

def test_unmatched_and_companyless_entries_are_warned_about_by_default(capsys):
    rows = classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Robotics"),
                                            _entry("Example Robotics"), _entry("")])
    assert [(r.subject, r.state, r.blocks, r.warn_by_default) for r in rows] == [
        ("cv_layout (not on your CV)", DEGRADED, (), True),
        ("cv_layout (no company)", DEGRADED, (), True)]
    out = _printed(capsys, *rows)
    assert "Worth a look" in out
    assert "2 verified experience entries" in out and "1 verified experience entry" in out
    assert "Example Robotics" not in out
    assert "more degraded" not in out, "a listed warning must not also be counted as quiet"


def test_the_eligibility_wording_agrees_with_the_count():
    # A citable entry rides along so the vault stays composable: the warning rows' own
    # wording is the subject, not the DEAD row a wholly uncitable vault adds.
    one = classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Robotics"), _entry("")])
    assert len(one) == 2 and all("can cite it " in r.detail for r in one)
    many = classify_cv_eligibility(
        LAYOUT, [_entry()] + [_entry("Example Robotics")] * 2 + [_entry("")] * 2)
    assert len(many) == 2 and all("can cite them " in r.detail for r in many)


def test_an_omitted_or_matched_entry_draws_nothing():
    assert classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Tidal")]) == []


@pytest.mark.parametrize("companies", [["Example Robotics", ""], ["Example Tidal"]])
def test_no_entry_citable_by_any_role_is_dead_and_blocks_cv(capsys, companies):
    # `cv run` refuses the whole run for this (cv/engine.py::missing_prerequisites), so the
    # row must block `cv` rather than sit among the warnings. All-omitted has no warning row
    # of its own, which is why it is a case here.
    rows = classify_cv_eligibility(LAYOUT, [_entry(c) for c in companies])
    [dead] = [r for r in rows if r.state == DEAD]
    assert (dead.subject, dead.blocks) == ("cv_layout (no citable entry)", ("cv",))
    assert f"any of your {len(companies)} verified experience entr" in dead.detail
    out = _printed(capsys, *rows)
    assert "Not working:" in out
    assert "Example Robotics" not in out and "Example Tidal" not in out


def test_a_partly_citable_vault_only_warns():
    rows = classify_cv_eligibility(LAYOUT, [_entry(), _entry("Example Robotics")])
    assert [(r.state, r.blocks) for r in rows] == [(DEGRADED, ())]


def test_a_layout_asking_for_no_bullets_is_not_blocked_for_want_of_a_citable_entry():
    # Every role at bullets_max 0 renders headings only (#364 D10), so nothing needs citing.
    zero = CvLayout(roles=(LayoutRole("Example Alpha", "01/2020", "present",
                                      employers=("Example Alpha",), bullets_max=0),))
    rows = classify_cv_eligibility(zero, [_entry("Example Robotics")])
    assert [(r.state, r.blocks) for r in rows] == [(DEGRADED, ())]


# --- the attribution check going dark (#364 spec §6.6) ---------------------------------------

def test_a_vault_with_skills_but_no_tools_gets_a_notice_that_the_check_is_off():
    # The owner's model: `Skills` are general soft skills tied to no job, so a vault with
    # Skills and no Tools is a legitimate shape, not a fault. A NOTICE: verbose view only,
    # never a default-view warning, never blocking, never failing --strict.
    [row] = classify_attribution([_entry(skills="examplecoach"), _entry()])
    assert (row.state, row.blocks, row.warn_by_default) == (NOTICE, (), False)
    assert "though 1 declares Skills" in row.detail


def test_the_attribution_notice_never_fails_strict_and_stays_out_of_the_default_view(capsys):
    [row] = classify_attribution([_entry(skills="examplecoach")])
    report = DoctorReport(checks=[], components=[row])
    assert (report.exit_code(), report.exit_code(strict=True)) == (0, 0)
    assert "cv attribution check" not in _printed(capsys, row, strict=True)
    # ...and IS in the verbose table, which is where a NOTICE lives.
    from sluice.cli import _print_doctor
    _print_doctor(report, offline=True)
    assert "cv attribution check" in capsys.readouterr().out


def test_the_off_notice_says_what_tools_and_skills_each_hold():
    # Replaces 4.0's "copy each entry's named tools into Tools:" advice. The owner's model:
    # the `Tools` field holds the specific tools and hard skills tied to the job, the `Skills`
    # field general soft skills tied to none, for the SKILLS list only. The row must neither
    # call it retired nor tell the user to move it. Count-only: no vault value is echoed.
    [row] = classify_attribution([_entry(skills="examplecoach")])
    assert "(specific tools and hard skills tied to the job)" in row.detail
    assert "turns the check on" in row.detail
    assert "holds general soft skills" in row.detail and "skills list only" in row.detail
    for gone in ("retired", "copy", "move", "no longer reads"):
        assert gone not in row.detail, gone
    assert "examplecoach" not in row.detail


def test_a_vault_with_neither_field_is_an_unconfigured_install_and_draws_nothing():
    assert classify_attribution([_entry(), _entry()]) == []


def test_any_declared_tools_turns_the_check_on_and_draws_nothing():
    assert classify_attribution([_entry(skills="examplecoach"), _entry(tools="Examplelang")]) == []


# --- a decoy contradicting the user's own data (#364 spec §8) ------------------------------------

@pytest.mark.parametrize("decoys,entries,names,layout", [
    (["examplelang"], [_entry(tools="Examplelang")], [], None),
    # An entry's `Skills` items feed the pool too (owner decision 2026-10-06), so a decoy matching
    # one keeps it off every SKILLS list exactly as it would a tool -- and must be said.
    (["examplecoach"], [_entry(skills="examplecoach")], [], None),
    (["Example Query"], [], ["Example Query"], None),
    (["example tidal"], [], [], LAYOUT),
])
def test_a_decoy_matching_the_users_own_data_is_warned_about_by_position(
        decoys, entries, names, layout):
    [row] = classify_decoys(["Example Zephyr"] + decoys, entries, names, layout)
    assert (row.state, row.blocks, row.warn_by_default) == (DEGRADED, (), True)
    assert "entry 2" in row.detail
    assert decoys[0] not in row.detail and "Example Zephyr" not in row.detail


def test_several_matching_decoys_read_as_a_plural_by_position():
    [row] = classify_decoys(["examplelang", "Example Zephyr", "example tidal"],
                            [_entry(tools="Examplelang")], [], LAYOUT)
    assert "entries 1 and 3 match" in row.detail


def _split_layout(first, second):
    return CvLayout(roles=(LayoutRole(first, "01/2020", "present"),
                           LayoutRole(second, "01/2015", "12/2019")))


def test_a_decoy_spanning_two_layout_strings_is_no_contradiction():
    # The user never wrote "Example Zephyr": one heading ends in the first word and the next
    # begins with the second. A row here is a DEGRADED warning (`doctor --strict` exits 1)
    # about text that exists nowhere, so each layout string is searched on its own.
    assert classify_decoys(["Example Zephyr"], [], [],
                           _split_layout("Alpha Example", "Zephyr Beta")) == []


def test_a_decoy_wholly_inside_one_layout_string_is_still_warned_about():
    [row] = classify_decoys(["Example Zephyr"], [], [],
                            _split_layout("Alpha Example Zephyr", "Beta"))
    assert row.state == DEGRADED


def test_a_decoy_inside_a_longer_token_is_no_contradiction():
    assert classify_decoys(["examplelang"], [_entry(tools="Examplelangscript")], [],
                           None) == []


# --- the flag itself ------------------------------------------------------------------

def test_warn_by_default_is_refused_on_a_row_that_is_not_degraded():
    with pytest.raises(ValueError, match="warn_by_default"):
        ComponentCheck("store", "x", OK, "fine", warn_by_default=True)


def test_a_warning_row_blocks_nothing_and_strict_still_fails_on_it(capsys):
    # Ported off the attribution row (now a NOTICE) onto the decoy row, another
    # warn_by_default DEGRADED row, so the flag's own mechanics stay pinned.
    [row] = classify_decoys(["examplelang"], [_entry(tools="Examplelang")], [], None)
    assert row.warn_by_default
    report = DoctorReport(checks=[], components=[row])
    assert report.verdict().buckets["cv"] == report.verdict().buckets["ingest"]
    assert (report.exit_code(), report.exit_code(strict=True)) == (0, 1)
    out = _printed(capsys, row, strict=True)
    assert out.count("cv.fabrication_decoys entry 1") == 1, "listed once, not twice, under --strict"


# --- wired into Sluice.doctor, and agreeing with `cv run` (#364 spec §9.1) -----------------

MALFORMED = "---\nroles:\n  - heading: Example Foundry\n---\n"


def _doctor(v):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    return Sluice(Config(vault_dir=v.dir)).doctor(offline=True)


def _with_layout(tmp_path, state, entries=(("alpha", {"Company": "Example Foundry"}),)):
    v = _vault(tmp_path, layout={"valid": "default", "malformed": MALFORMED}.get(state),
               entries=entries)
    if state == "unreadable":
        outside = tmp_path / "outside.md"
        outside.write_text(layout_yaml(), encoding="utf-8")
        os.symlink(outside, _layout_path(v))
    return v


@pytest.mark.parametrize("state", ["valid", "absent", "malformed", "unreadable"])
def test_doctor_and_the_refusal_agree_about_every_layout_state(tmp_path, state):
    # Compared at the ROW: `--require cv` also reads the renderer row, which is DEAD wherever
    # WeasyPrint is not installed -- CI included -- so the capability bucket cannot isolate
    # the layout's own verdict.
    v = _with_layout(tmp_path, state)
    [row] = [c for c in _doctor(v).components if c.subject == "cv_layout"]
    refused = [m for m in missing_prerequisites(v) if "CV Layout" in m]
    assert (row.state != OK) == bool(refused) == ("cv" in row.blocks), (state, row, refused)


@pytest.mark.parametrize("tools,refused", [("Examplelang", False),
                                           ("9001 Examplestandard", True)])
def test_doctor_and_the_refusal_agree_about_tools(tmp_path, tools, refused):
    v = _vault(tmp_path, entries=(("alpha", dict({"Company": "Example Foundry"}, Tools=tools)),))
    rows = [c for c in _doctor(v).components if c.subject == "Experience Library (Tools)"]
    assert bool(rows) == refused == bool(missing_prerequisites(v))


@pytest.mark.parametrize("entries,refused", [
    ((("alpha", {"Company": "Example Foundry"}),), False),
    ((("alpha", {"Company": "Example Robotics"}), ("beta", {})), True),
    ((("alpha", {"Company": "Example Foundry"}), ("beta", {"Company": "Example Robotics"})),
     False),
])
def test_doctor_and_the_refusal_agree_about_a_vault_no_role_can_cite(tmp_path, entries,
                                                                     refused):
    v = _vault(tmp_path, entries=entries)
    blocking = [c for c in _doctor(v).components
                if c.subject.startswith("cv_layout (") and "cv" in c.blocks]
    assert bool(blocking) == refused == bool(missing_prerequisites(v)), entries


_HEADINGS_ONLY = layout_yaml([{"heading": "Example Foundry", "from": "02/2023",
                               "to": "present", "bullets_max": 0}])


@pytest.mark.parametrize("layout,entries,refused", [
    (_HEADINGS_ONLY, (), False),
    ("default", (), True),
    ("default", (("alpha", {"Company": "Example Foundry"}),), False),
], ids=["headings-only+empty", "bullets+empty", "bullets+entries"])
def test_doctor_blocks_cv_exactly_when_the_run_refuses(tmp_path, layout, entries, refused):
    # Compared on the rows that block `cv` for a STORE fact, not the capability bucket:
    # `--require cv` also reads the renderer row, which is DEAD wherever WeasyPrint is not
    # installed (CI included), so the bucket cannot isolate this verdict.
    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH
    v = _vault(tmp_path, layout=layout, entries=entries)
    # A declared identity, so the Candidate Profile row -- a per-lead refusal inside
    # run_one, not one of missing_prerequisites' -- is OK and cannot decide this row.
    v.write_document(CANDIDATE_PROFILE_RELPATH,
                     "---\nforenames: Jane\nsurname: Roe\nmobile: +1 555 0100\n---\n")
    blocking = [c for c in _doctor(v).components
                if c.component == "store" and "cv" in c.blocks]
    assert bool(blocking) == refused == bool(missing_prerequisites(v)), (blocking, refused)


@pytest.mark.parametrize("state", ["absent", "malformed", "unreadable"])
def test_no_eligibility_row_without_a_parsed_layout(tmp_path, state):
    v = _with_layout(tmp_path, state, entries=(("alpha", {"Company": "Example Robotics"}),
                                               ("beta", {})))
    subjects = {c.subject for c in _doctor(v).components}
    assert not {"cv_layout (not on your CV)", "cv_layout (no company)"} & subjects


def test_doctor_lists_the_eligibility_warnings_by_default(tmp_path, capsys):
    from sluice.cli import _print_doctor_verdict
    v = _vault(tmp_path, entries=(("alpha", {"Company": "Example Robotics"}), ("beta", {}),
                                  ("gamma", {"Company": "Example Foundry"})))
    report = _doctor(v)
    _print_doctor_verdict(report, offline=True, strict=False, exit_code=report.exit_code())
    out = " ".join(capsys.readouterr().out.split())
    assert "Worth a look" in out
    assert "1 verified experience entry names a Company:" in out
    assert "1 verified experience entry has no Company:" in out


def test_doctor_warns_when_a_decoy_bans_a_declared_tool(tmp_path, monkeypatch):
    config = tmp_path / "sluice.yaml"
    config.write_text("cv:\n  fabrication_decoys: [examplelang]\n", encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(config))
    v = _vault(tmp_path, entries=(("alpha", {"Company": "Example Foundry",
                                             "Tools": "Examplelang"}),))
    [row] = [c for c in _doctor(v).components if c.subject == "cv.fabrication_decoys"]
    assert row.warn_by_default and "entry 1" in row.detail


@pytest.mark.parametrize("item", ["Examplelang#", "Example.lang", ".Examplenet", "Examplelang.",
                                  "9Examplelang"])
def test_doctor_and_the_gate_agree_on_each_token_edge_case(tmp_path, item):
    """#364 spec §9.5: doctor and the gate share core/tokens.py, so they cannot disagree about a
    `#`-suffixed token, a dotted name, a leading-dot name or a trailing dot. Pinned, against
    the day one of them grows its own parsing."""
    v = _vault(tmp_path, entries=(("alpha", dict({"Company": "Example Foundry"}, Tools=item)),))
    doctor_refuses = any(c.subject == "Experience Library (Tools)" for c in _doctor(v).components)
    assert doctor_refuses == bool(missing_prerequisites(v)), item


def test_doctor_reports_no_baseline_and_no_skills_rows(tmp_path):
    subjects = {c.subject for c in _doctor(_vault(tmp_path)).components}
    assert "baseline_rel" not in subjects
    assert not [s for s in subjects if "Skills" in s and s.endswith(")")]


def test_the_cv_rows_read_the_verified_corpus_the_gate_itself_reads(tmp_path):
    """Ported from the retired skills-request row's own witness: doctor's CV rows must read
    `read_evidence("experience", verified_only=True)`, the corpus the gate and
    `missing_prerequisites` read. Reading a WIDER one lets an UNVERIFIED note raise a row
    about an entry no CV can cite -- here a `Tools:` item the run would never refuse. The
    two reads disagree about this vault, which is what makes the row's absence evidence."""
    v = _vault(tmp_path)
    exp = os.path.join(v.dir, "Job Applications", "Experience Library")
    # Seated in place rather than under `_inbox/` (which `read_evidence` cannot see at
    # either setting): an unverified note is one carrying no `verified:` key.
    with open(os.path.join(exp, "beta.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nCompany: "Example Foundry"\nTools: 9001 Examplestandard\n---\nBody.\n')
    assert len(v.read_evidence("experience", verified_only=False)) == 2, "premise"
    assert missing_prerequisites(v) == [], "premise: the run ignores the unverified entry"
    assert not [c for c in _doctor(v).components if c.subject == "Experience Library (Tools)"]


# --- a verified skill note with no Label (the 4.0 upgrade) ---------------------------------

def _skill(label=None, named="synthetic-slug"):
    return {"title": named, "fields": {} if label is None else dict(Label=label)}


def test_a_skill_note_with_no_label_is_warned_about_by_count_never_by_title(capsys):
    rows = classify_skill_labels([_skill(), _skill("  "), _skill("Example Query")],
                                 ("skills",))
    assert [(r.subject, r.state, r.blocks, r.warn_by_default) for r in rows] == [
        ("cv skills (no Label)", DEGRADED, (), True)]
    out = _printed(capsys, *rows)
    assert "Worth a look" in out
    assert "2 verified skill notes have a slug for a title and no Label:" in out
    assert "job-sluice skills list" in out and "add `Label:`" in out
    assert "synthetic-slug" not in out and "Example Query" not in out


def test_only_a_slug_titled_note_without_a_label_is_counted():
    # A pre-4.0 `skills add` note is titled with evidence_slug's reduction of the typed
    # name; a hand-made note titled with its real name already reaches a CV under that
    # name, so warning about it would fire on every run (and fail --strict) with nothing
    # to fix. The rows straddle the condition: same missing Label, title shape varies.
    def count(entries):
        rows = classify_skill_labels(entries, ("skills",))
        return [r.detail.split(" ", 1)[0] for r in rows]

    assert count([_skill(named="example-query")]) == ["1"], "slug title, no Label: warns"
    assert count([_skill(named="Example Query")]) == [], "hand title, no Label: does not"
    assert count([_skill(named="example query")]) == [], "a space is not slug-shaped"
    assert count([_skill(named="---")]) == [], "a title reducing to nothing is not a slug"
    assert count([_skill("Example Query", named="example-query")]) == [], "Label set: does not"
    assert count([_skill(named="example-query"), _skill(named="Example Query")]) == ["1"]


def test_labelled_skill_notes_draw_nothing():
    assert classify_skill_labels([_skill("Example Query")], ("skills",)) == []
    assert classify_skill_labels([], ("skills",)) == []


def test_doctor_counts_the_verified_skill_notes_without_a_label(tmp_path):
    # Through the real store and Sluice.doctor: a note proposed straight to the store
    # carries no Label (only `skills add`, through Sluice.add_evidence, fills it in).
    v = _vault(tmp_path)
    for name, fields in (("examplequery", {}), ("examplelang", dict(Label="Examplelang"))):
        v.propose_evidence("skills", name=name, fields=fields)
        pending = {e["title"]: e for e in v.read_pending_evidence("skills")}
        with open(pending[name]["path"], encoding="utf-8") as fh:
            raw = fh.read()
        assert v.verify_evidence("skills", name, today="2026-09-03", reviewed=raw), name
    # A note seated by hand under its real name, verified and Label-less: a CV already
    # shows it as titled, so it must not join the count.
    with open(os.path.join(v._evidence_dir("skills"), "Example Query.md"), "w",
              encoding="utf-8") as fh:
        fh.write("---\nverified: 2026-09-03\n---\nBody.\n")
    assert "Example Query" in {e["title"] for e in v.read_evidence("skills")}, "premise"
    [row] = [c for c in _doctor(v).components if c.subject == "cv skills (no Label)"]
    assert row.detail.startswith("1 verified skill note has a slug for a title and no Label:")
