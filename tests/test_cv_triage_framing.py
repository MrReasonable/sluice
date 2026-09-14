"""The composer's framing-only TRIAGE NOTES section (#329): its shape, where it sits, and that an
empty framing leaves the prompt exactly as it was."""
import json

import pytest

from sluice.core.backends import Completion
from sluice.core.leads import FRAMING_KEYS, framing_entries, split_framing
from sluice.cv import compose as C
from sluice.cv.engine import run_one
from tests.conftest import FRAMING_CONCERNS, FRAMING_FLAGS
from tests.test_cv_engine import (CLEAN_CV, ENTRIES, FakeBackend, FakeCache, FakeRenderer,
                                  FakeVault, Note, RecordingBackend, _cfg, _served)

_NAME = "Example Candidate"
_FLAGS = ", ".join(FRAMING_FLAGS)
_CONCERNS = "; ".join(FRAMING_CONCERNS)


def _prompt(**kw):
    return C.build_prompt("BUNDLE", "JD", "Co", "Role", name=_NAME, **kw)


@pytest.mark.parametrize("flags,concerns,expected", [
    (_FLAGS, _CONCERNS, (f"culture flags: {_FLAGS}", f"concerns: {_CONCERNS}")),
    (_FLAGS, "", (f"culture flags: {_FLAGS}",)),
    ("", _CONCERNS, (f"concerns: {_CONCERNS}",)),
    ("   ", _CONCERNS, (f"concerns: {_CONCERNS}",)),
    ("", "", ()),
    (None, 5, ()),
    ("|", "", ()),
    ("", "|", ()),
    (">-", "", ()),
    ("", "|2", ()),
    ("", ">+", ()),
    ("", "|-2", ()),
    ("", "a | b", ("concerns: a | b",)),
    ("", "|x", ("concerns: |x",)),
    ("", "| # typed by hand", ()),
    ("", ">- # note", ()),
    # Controls, pinned on purpose (#329): a value that is ONLY a comment, or a
    # block-scalar-looking header with no space before the "#", is not a bare header and
    # still frames.
    ("", "# typed by hand", ("concerns: # typed by hand",)),
    ("", "|#x", ("concerns: |#x",)),
])
def test_framing_lines(flags, concerns, expected):
    assert C.framing_lines(flags, concerns) == expected


def test_every_framing_key_has_its_own_prompt_label():
    # #329: `FRAMING_KEYS` is the one roster; `_TRIAGE_FRAMING_PROMPT_LABELS` is
    # positional against it (`framing_lines` zips them). Names the cause directly -- a key
    # added to the roster with no matching prompt label -- rather than leaving it to be
    # inferred from whichever unrelated framing-row assertion such a mismatch happens to break.
    assert len(C._TRIAGE_FRAMING_PROMPT_LABELS) == len(FRAMING_KEYS)


@pytest.mark.parametrize("skills", [False, True])
def test_the_unframed_prompt_matches_the_pre_329_shape_at_both_splice_points(skills):
    """Replaces a vacuous byte-identity row: `triage_framing` defaults to `()`, so
    `_prompt(triage_framing=())` and `_prompt()` take the identical path and the comparison could
    never fail either way. Pins the pre-#329 shape directly instead, at both places #329 spliced
    something in -- the neighbourhood around the JD block's end and the `=== SOURCE BUNDLE`
    header, and the rules line the #329 rule is spliced before -- read off `git show
    origin/main:sluice/cv/compose.py` (c9d700e3, the commit this branch is rebased onto)."""
    lines = _prompt(skills_requested=skills).splitlines()
    jd_at = lines.index("=== THE ROLE (JD) ===")
    assert lines[jd_at:jd_at + 5] == [
        "=== THE ROLE (JD) ===", "JD", "",
        "=== SOURCE BUNDLE (the ONLY permitted source) ===", "BUNDLE",
    ]
    # Adjacent to the line immediately before the one the #329 rule is spliced before, with no
    # placeholder-collapse gap between them, exactly as it read before #329. `skills_attribution_
    # rule` sits in that same gap (#167), so its own line -- present only when `skills` is True --
    # is part of the expected slice rather than a second, unrelated placeholder.
    skills_at = lines.index(
        "- The SKILLS INVENTORY section is FRAMING, not a source. Use it to choose which "
        "experience entries to lead with and how to describe them. Never cite it, never quote "
        "a number from it, and never introduce a claim that rests on it alone: every fact in "
        "the CV must still come from the BASELINE CV or a VERIFIED EXPERIENCE ENTRY.")
    expected_next = ([C._SKILLS_ATTRIBUTION_PROMPT_RULE.rstrip("\n")] if skills else []) + [
        "- Every line of the SKILLS section must come from the SOURCE BUNDLE. Do not add a "
        "skill the bundle does not contain."]
    assert lines[skills_at + 1:skills_at + 1 + len(expected_next)] == expected_next


@pytest.mark.parametrize("skills", [False, True])
def test_framing_adds_exactly_its_rule_and_its_section(skills):
    """Standing, not a one-off measurement: remove the rule's own lines and the section block
    from a framed render, and what is left must be the unframed render, line for line. A
    placeholder that fails to collapse shows up here."""
    framing = C.framing_lines(_FLAGS, _CONCERNS)
    base = _prompt(skills_requested=skills).splitlines()
    remaining = _prompt(skills_requested=skills, triage_framing=framing).splitlines()
    for line in C._TRIAGE_FRAMING_PROMPT_RULE.strip("\n").splitlines():
        remaining.remove(line)
    section = [C._TRIAGE_FRAMING_PROMPT_HEADER, *[f"- {line}" for line in framing], ""]
    start = remaining.index(C._TRIAGE_FRAMING_PROMPT_HEADER)
    assert remaining[start:start + len(section)] == section
    del remaining[start:start + len(section)]
    assert remaining == base


def test_the_section_sits_after_the_jd_and_outside_the_source_bundle():
    p = _prompt(triage_framing=C.framing_lines(_FLAGS, _CONCERNS))
    assert (p.index("=== THE ROLE (JD) ===") < p.index(C._TRIAGE_FRAMING_PROMPT_HEADER)
            < p.index("=== SOURCE BUNDLE"))


def test_compose_forwards_the_framing_into_the_prompt_it_sends():
    # `compose()` passes its arguments to `build_prompt` one by one; a forgotten forward is
    # exactly the shape that leaves the section out of what the backend receives.
    class _Backend:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt):
            self.prompts.append(prompt)
            return Completion("CV")

    be = _Backend()
    C.compose(be, "BUNDLE", "JD", "Co", "Role", name=_NAME,
              triage_framing=C.framing_lines("", _CONCERNS))
    assert C._TRIAGE_FRAMING_PROMPT_HEADER in be.prompts[0]
    assert f"- concerns: {_CONCERNS}" in be.prompts[0]


def test_the_shipped_framing_text_models_nothing_it_forbids():
    shipped = [C._TRIAGE_FRAMING_PROMPT_RULE, C._TRIAGE_FRAMING_PROMPT_HEADER,
               *C._TRIAGE_FRAMING_PROMPT_LABELS]
    assert not any("--" in text for text in shipped), "the CV bans a double hyphen"
    assert not any("auditing" in text.lower() for text in shipped), "the CV test doubles route on it"
    assert "never state, paraphrase or allude to anything in it" in C._TRIAGE_FRAMING_PROMPT_RULE


def _framed_note(**fm):
    return Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst",
                 "culture_flags": _FLAGS, "triage_concerns": _CONCERNS, **fm})


def _run(note, backend, renderer=None):
    renderer = renderer or FakeRenderer()
    result = run_one(note, FakeVault(ENTRIES, notes=[note]), _cfg(), backend, FakeCache(),
                     renderer=renderer)
    return result, renderer


def test_a_lead_with_framing_puts_the_section_and_rule_in_the_compose_prompt(monkeypatch):
    _served(monkeypatch)
    be = RecordingBackend()
    _run(_framed_note(), be)
    prompt = be.prompts[0]
    assert C._TRIAGE_FRAMING_PROMPT_HEADER in prompt
    assert C._TRIAGE_FRAMING_PROMPT_RULE.strip("\n") in prompt
    for line in C.framing_lines(_FLAGS, _CONCERNS):
        assert f"- {line}" in prompt


def test_a_lead_with_no_framing_gets_neither_the_section_nor_the_rule(monkeypatch):
    # The mirror control. Without it, a `_run_one` that always passed framing would pass the row
    # above too.
    _served(monkeypatch)
    be = RecordingBackend()
    _run(Note({"status": "shortlist", "company": "Example Foundry", "role": "Analyst"}), be)
    assert be.prompts, "the compose call never happened; this row would pass vacuously"
    assert C._TRIAGE_FRAMING_PROMPT_HEADER not in be.prompts[0]
    assert C._TRIAGE_FRAMING_PROMPT_RULE.strip("\n") not in be.prompts[0]


def test_the_advisory_audit_is_never_shown_the_triage_notes(monkeypatch):
    # The audit's prompt opens "SOURCE BUNDLE is the ONLY truth"; showing it the notes would let a
    # claim resting on them read as supported and skip the sign-off hold.
    _served(monkeypatch)
    be = RecordingBackend()
    _run(_framed_note(), be)
    assert be.audit_prompts, "the audit never ran; this row would pass vacuously"
    assert C._TRIAGE_FRAMING_PROMPT_HEADER not in be.audit_prompts[0]
    assert not any(token in be.audit_prompts[0] for token in (*FRAMING_FLAGS, *FRAMING_CONCERNS))


_FIGURE = "4731"   # appears in neither ENTRIES, the fake baseline nor FakeCache's JD


def test_a_figure_only_in_the_triage_notes_is_refused_by_the_gate(monkeypatch):
    """The acceptance row. It is red only if the notes leaked into what the gate may license, and
    it carries its own wiring witness: without the section-contains-the-figure assertion it would
    pass on a tree where the notes never reach the composer at all."""
    _served(monkeypatch)
    note = _framed_note(triage_concerns=f"{FRAMING_CONCERNS[0]} {_FIGURE}")
    cv = CLEAN_CV.replace("I build reliable systems.",
                          f"I build reliable systems for {_FIGURE} users.")
    assert _FIGURE in cv, "the replace no-opped"
    be = RecordingBackend(cv_out=cv)
    r, rend = _run(note, be)
    section = (be.prompts[0].partition(C._TRIAGE_FRAMING_PROMPT_HEADER)[2]
               .partition("=== SOURCE BUNDLE")[0])
    assert _FIGURE in section, "wiring witness: the composer was shown the figure as framing"
    assert r.status == "skipped-gate"
    assert any(v.startswith(f"INVENTED PROFILE METRIC {_FIGURE}") for v in r.violations), (
        r.violations)
    assert rend.rendered == []


def test_the_same_framed_lead_renders_when_the_cv_does_not_use_the_figure(monkeypatch):
    # The separating control: nothing else about this lead or fixture refuses the CV.
    _served(monkeypatch)
    note = _framed_note(triage_concerns=f"{FRAMING_CONCERNS[0]} {_FIGURE}")
    r, _ = _run(note, RecordingBackend())
    assert r.status == "rendered", r.violations


def test_a_verdict_triage_wrote_reaches_the_composer(tmp_path, monkeypatch):
    """An integration pin from the triage write to the compose prompt through a REAL vault. It has
    no unique witness: renaming the key in `apply_verdict` also reddens the triage key rows, and
    renaming it in `_run_one` also reddens the rows above."""
    _served(monkeypatch)
    from sluice.triage.apply import apply_verdict
    from tests.test_cv_engine import _vault_with_candidate

    v = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example",
                                         "email": "ada@example.invalid"})
    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True, exist_ok=True)
    (leads / "Example Foundry - Analyst.md").write_text(
        '---\ncompany: "Example Foundry"\nrole: "Analyst"\nstatus: new\nscore: 0\n---\n# body\n',
        encoding="utf-8")
    apply_verdict(v, v.read_leads({"new"})[0],
                  {"verdict": "shortlist", "relevance_score": 80,
                   "culture_flags": list(FRAMING_FLAGS), "concerns": list(FRAMING_CONCERNS)}, {})
    be = RecordingBackend()
    run_one(v.read_leads({"shortlist"})[0], v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert be.prompts, "the compose call never happened"
    assert f"- culture flags: {_FLAGS}" in be.prompts[0]
    assert f"- concerns: {_CONCERNS}" in be.prompts[0]


@pytest.mark.parametrize("typed,framed", [
    (['triage_concerns: "HAND-TYPED-ONE; HAND-TYPED-TWO"'],
     "- concerns: HAND-TYPED-ONE; HAND-TYPED-TWO"),
    (["triage_concerns:", "  - HAND-TYPED-ONE", "  - HAND-TYPED-TWO"], None),
    (["triage_concerns: |", "  HAND-TYPED-ONE", "  HAND-TYPED-TWO"], None),
    (["triage_concerns: | # typed by hand", "  HAND-TYPED-ONE"], None),
    (["triage_concerns: HAND-TYPED-ONE", "  HAND-TYPED-TWO"], "- concerns: HAND-TYPED-ONE"),
], ids=["one-quoted-line", "block-list", "block-scalar", "block-scalar-commented",
        "plain-continuation"])
def test_a_hand_edited_note_frames_only_a_one_line_value(tmp_path, monkeypatch, typed, framed):
    """The manual route USAGE.md documents, end to end through a REAL vault: a value typed as one
    quoted line frames the CV, and one typed as a YAML list frames nothing (the vault's
    line-based reader sees only the key's own line). The block-list row is the control."""
    _served(monkeypatch)
    from tests.test_cv_engine import _vault_with_candidate

    v = _vault_with_candidate(tmp_path, {"forenames": "Ada", "surname": "Example",
                                         "email": "ada@example.invalid"})
    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True, exist_ok=True)
    (leads / "Example Foundry - Analyst.md").write_text(
        "---\n" + "\n".join(['company: "Example Foundry"', 'role: "Analyst"',
                             "status: shortlist", *typed]) + "\n---\n# body\n",
        encoding="utf-8")
    be = RecordingBackend()
    run_one(v.read_leads({"shortlist"})[0], v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert be.prompts, "the compose call never happened"
    if framed:
        assert framed in be.prompts[0]
    else:
        assert C._TRIAGE_FRAMING_PROMPT_HEADER not in be.prompts[0]


_UNSUPPORTED = "unsupported\tMotivated by placeholder\tNONE"


def test_framing_entries_round_trip_through_split_framing():
    lines = C.framing_lines(_FLAGS, _CONCERNS)
    stored = [_UNSUPPORTED, "style\tSLOP leverage: x", *framing_entries(lines)]
    assert split_framing(stored) == (list(lines), [_UNSUPPORTED, "style\tSLOP leverage: x"])


def test_split_framing_leaves_everything_else_alone_and_never_raises():
    # `needs_signoff` is hand-editable, so an entry can be anything JSON holds.
    assert split_framing([]) == ([], [])
    assert split_framing([_UNSUPPORTED]) == ([], [_UNSUPPORTED])
    assert split_framing([1, None, "framing"]) == ([], [1, None, "framing"])


def test_a_hold_records_the_framing_after_the_blockers(monkeypatch):
    _served(monkeypatch)
    note = _framed_note()
    v = FakeVault(ENTRIES, notes=[note])
    r = run_one(note, v, _cfg(), FakeBackend(CLEAN_CV, audit_out=_UNSUPPORTED), FakeCache(),
                renderer=FakeRenderer())
    assert r.status == "needs-signoff"
    assert json.loads(note.fm["needs_signoff"]) == [
        _UNSUPPORTED, *framing_entries(C.framing_lines(_FLAGS, _CONCERNS))]


class _ChangesConcernsMidCompose:
    """Composes CLEAN_CV and, DURING that compose call, changes the lead's `triage_concerns` in
    place. `_run_one` binds `fm = note.fm`, so the change is visible to anything re-reading the
    frontmatter at the hold site -- which is exactly the drift this row exists to catch. Audits
    `unsupported`, so the CV is held. Routes compose from audit like the CV engine's doubles."""
    last_backend = "primary"

    def __init__(self, note):
        self.note, self.prompts = note, []

    def complete(self, prompt):
        if "SOURCE BUNDLE" in prompt and "auditing" not in prompt:
            self.prompts.append(prompt)
            self.note.fm["triage_concerns"] = FRAMING_CONCERNS[1]
            return Completion(CLEAN_CV)
        return Completion(_UNSUPPORTED)


def test_the_hold_records_what_the_composer_was_given_not_a_later_edit(monkeypatch):
    _served(monkeypatch)
    note = _framed_note(triage_concerns=FRAMING_CONCERNS[0])
    v = FakeVault(ENTRIES, notes=[note])
    be = _ChangesConcernsMidCompose(note)
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert r.status == "needs-signoff"
    held, _ = split_framing(json.loads(note.fm["needs_signoff"]))
    assert held == list(C.framing_lines(_FLAGS, FRAMING_CONCERNS[0]))
    assert all(f"- {line}" in be.prompts[0] for line in held)


def test_framing_alone_never_holds_a_cv(monkeypatch):
    _served(monkeypatch)
    note = _framed_note()
    v = FakeVault(ENTRIES, notes=[note])
    be = RecordingBackend()                     # audits `supported`: no blocker at all
    r = run_one(note, v, _cfg(), be, FakeCache(), renderer=FakeRenderer())
    assert C._TRIAGE_FRAMING_PROMPT_HEADER in be.prompts[0], (
        "vacuous unless the composer was actually given framing")
    assert r.status == "rendered"
    assert note.fm.get("tailored_cv")
    assert "needs_signoff" not in note.fm
