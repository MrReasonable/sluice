"""Every quoted frontmatter scalar built by interpolation, across the WHOLE package.

This is the third boundary this sweep has had, and the first two both let a live defect
through. #141 scanned `inspect.getsource(reconcile._advance)` -- one function -- and
`engine.confirm` was already writing the same field one module over. The replacement scanned
`sluice/track/*.py` -- one package -- and `sluice/triage/apply.py` was writing the model's
own `culture_flags` into a quoted scalar with no guard at all. Executed against the real
vault, one flag injected a second `status:` key, which YAML resolves last-wins: model output
could regress a lead's status.

Each time the boundary was drawn around the code the author happened to be looking at, and
each time the docstring called the result "enumerated". A sweep is only enumerated over the
population it actually walks, so this one walks `sluice/`.

Two shapes are matched, because the defect above used the one the previous sweep did not:
a subscript assignment (`fields["k"] = f'"{v}"'`) and a dict literal entry (`"k": f'"{v}"'`).
"""
import pathlib
import re

import pytest

import sluice

_PKG = pathlib.Path(sluice.__file__).resolve().parent

# Three shapes, because a quoted-scalar literal reaches frontmatter three ways here. The
# third was added after the second boundary failure: `core/vault.py` passes the literal
# straight to `_set_fm`, the module's single frontmatter setter, which neither of the first
# two patterns could see -- and that is the sink every `append_note` caller feeds model
# output into. Matching only the shapes already known is the same error as scanning only the
# packages already known, one level down.
#
# `_SET_FM` names the SINK rather than "any f-string argument", which would sweep in string
# building that never reaches a note (`engine.py`'s ambiguous-lead hint quotes candidate
# slugs into a dead-letter message, for instance). Precision here is not leniency: `_set_fm`
# is the only function in `sluice/` that writes a frontmatter key.
_SUBSCRIPT = re.compile(r"""\w+\[[^\]]+\]\s*=\s*f(['"])"['"]?\{""")
_DICT_ENTRY = re.compile(r"""['"][\w_]+['"]\s*:\s*f(['"])"['"]?\{""")
_SET_FM = re.compile(r"""_set_fm\([^)]*f(['"])"['"]?\{""")

# Values sanitised UPSTREAM of the write, where a line-local check cannot see the guard.
# Every entry is a DECISION that the value is already safe at this point; adding one means
# reading the producer, not silencing the sweep.
_GUARDED_UPSTREAM = {
    # `resolve.py` runs `frontmatter_safe` on the resolved company before it is ever
    # returned, so the write here cannot see an unsafe value.
    ("triage/engine.py", "company"),
}

# The naming convention every write in this file's OWN sweep relies on: a variable named
# exactly `safe` or prefixed `safe_...` (round-5 review finding) -- NOT a bare substring
# check, which also matched inside `unsafe`/`unsafe_value`, silently exempting the exact
# shape this file exists to catch. `\b` alone does not suffice on the trailing edge of
# `safe_...`: `_` is a word character, so `\bsafe\b` never terminates before it -- the
# second alternative names that shape explicitly.
_SANITISED_VALUE = re.compile(r"\b(?:safe|safe_[A-Za-z0-9_]+)\b")


def _writes():
    """(relative path, line number, line) for every interpolated quoted-scalar write."""
    out = []
    for py in sorted(_PKG.rglob("*.py")):
        rel = py.relative_to(_PKG).as_posix()
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if (_SUBSCRIPT.search(line) or _DICT_ENTRY.search(line)
                    or _SET_FM.search(line)):
                out.append((rel, i, line.strip()))
    return out


def test_the_sweep_finds_the_writes_it_is_checking():
    """Guards the sweep itself.

    A regex that stops matching passes silently and forever, which is the failure mode this
    whole file exists to prevent -- so the sweep's own liveness is asserted before its verdict
    is trusted.
    """
    found = _writes()
    assert len(found) >= 7, (
        f"the sweep matched {len(found)} frontmatter writes across {_PKG} -- it has drifted "
        "from the code it is supposed to walk")
    # ...and every SHAPE must still match something. A bare total cannot see one pattern
    # dying: deleting `_SET_FM` left the count above its floor, so that mutant survived --
    # which is the failure this file exists to stop, committed inside the file itself.
    for name, pattern in (("subscript", _SUBSCRIPT), ("dict-entry", _DICT_ENTRY),
                          ("_set_fm argument", _SET_FM)):
        assert any(pattern.search(line) for _rel, _i, line in found), (
            f"the {name} pattern matches nothing -- either that shape left the codebase or "
            "the pattern rotted, and either way it is no longer guarding anything")


def test_no_interpolated_frontmatter_write_is_unguarded():
    offenders = []
    for rel, lineno, line in _writes():
        # A parsed datetime cannot carry a quote or a backslash. Exempting the TYPE rather
        # than the field name means a later change routing a raw string through the same
        # field is still caught.
        if ".isoformat()" in line:
            continue
        if _SANITISED_VALUE.search(line):
            continue
        key = re.search(r"""['"]([\w_]+)['"]""", line)
        if key and (rel, key.group(1)) in _GUARDED_UPSTREAM:
            continue
        offenders.append(f"{rel}:{lineno}: {line}")
    assert not offenders, (
        "a value is interpolated into a quoted frontmatter scalar with no `frontmatter_safe`.\n"
        "A `\"` closes the scalar early and everything after it parses as frontmatter -- "
        "including a second `status:` key, which YAML resolves last-wins.\n  "
        + "\n  ".join(offenders))


def test_the_sanitised_value_pattern_does_not_exempt_unsafe():
    """The vacuity risk the round-5 review finding named directly: a bare `"safe" in line`
    substring check also matches inside `unsafe`/`unsafe_value`, which would silently
    exempt exactly the shape this sweep exists to catch. Pinned against the pattern
    itself, not against the live codebase (which has no such write today) -- a future
    write naming its interpolated variable `unsafe_value` must not slip past this sweep
    the same way this bug would have let it."""
    assert not _SANITISED_VALUE.search('fields["culture_flags"] = f\'"{unsafe_value}"\'')
    assert not _SANITISED_VALUE.search('fields["k"] = f\'"{unsafe}"\'')
    assert _SANITISED_VALUE.search('fields["k"] = f\'"{safe_value}"\'')
    assert _SANITISED_VALUE.search('fields["k"] = f\'"{safe}"\'')


@pytest.mark.parametrize("payload", ['a" \nstatus: rejected\nx: "b', "a\\b"])
def test_the_triage_verdict_fields_cannot_inject_frontmatter(tmp_path, payload):
    """The defect this file's boundary change actually found, pinned end to end.

    `culture_flags` is the model's own verdict JSON and `glassdoor_rating` comes off the
    fetched dossier. Both were written raw.
    """
    from sluice.core.vault import Vault
    from sluice.triage.apply import apply_verdict

    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    note_path = leads / "Example Tidal - Analyst.md"
    note_path.write_text(
        '---\ncompany: "Example Tidal"\nrole: "Analyst"\nstatus: shortlist\n---\n\nBODY\n')
    v = Vault(str(tmp_path))
    note = [n for n in v.read_leads() if n.slug == "Example Tidal - Analyst"][0]

    apply_verdict(v, note, {"verdict": "shortlist", "relevance_score": 5,
                            "culture_flags": [payload], "fit_reasoning": "ok"},
                  {"glassdoor": {"rating": payload}})
    text = note_path.read_text()
    assert "status: rejected" not in text, "model output regressed the lead's status"
    assert "\nx:" not in text, "model output injected a frontmatter key"
    assert text.count("status:") == 1, text


def test_an_ordinary_verdict_still_writes_both_fields(tmp_path):
    # Abstention must be narrow: the common case has to keep working, or the guard trades one
    # silent loss for another.
    from sluice.core.vault import Vault
    from sluice.triage.apply import apply_verdict

    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    note_path = leads / "Example Tidal - Analyst.md"
    note_path.write_text(
        '---\ncompany: "Example Tidal"\nrole: "Analyst"\nstatus: shortlist\n---\n\nBODY\n')
    v = Vault(str(tmp_path))
    note = [n for n in v.read_leads() if n.slug == "Example Tidal - Analyst"][0]

    apply_verdict(v, note, {"verdict": "shortlist", "relevance_score": 5,
                            "culture_flags": ["good wlb", "remote"], "fit_reasoning": "ok"},
                  {"glassdoor": {"rating": "4.2"}})
    text = note_path.read_text()
    assert 'culture_flags: "good wlb, remote"' in text
    assert 'glassdoor_rating: "4.2"' in text


def test_set_fm_is_still_the_only_frontmatter_setter():
    """The sweep's `_SET_FM` pattern names one function. That is only sound while that
    function is the sole way a frontmatter key gets written -- so assert it, rather than
    leave the sweep resting on a fact nobody rechecks.

    Asserts on SCOPE (exactly one match, in the right file) rather than an exact line
    number: this test is main's own, and a branch that adds unrelated content earlier in
    `core/vault.py` (a new decision comment, a new method) shifts every line number below
    it with zero change to whether a sibling setter exists -- which is exactly what this
    test found on #131/#132's branch, a false failure with nothing to fix. The line-pinned
    form only protects against ONE more thing (a setter moving within the file with the
    total count unchanged) than the scope-only form below does, and that shape is not
    something either test's own docstring claims to guard against."""
    setters = []
    for py in sorted(_PKG.rglob("*.py")):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"^\s*def _set_\w*fm\w*\(", line):
                setters.append(f"{py.relative_to(_PKG).as_posix()}:{i}")
    assert len(setters) == 1 and setters[0].startswith("core/vault.py:"), (
        f"the frontmatter setter moved to a different FILE or gained a sibling: {setters}. "
        "The sweep's _SET_FM pattern must be updated to match, or writes through the new "
        "one go unchecked.")


def test_an_append_note_cannot_inject_frontmatter(tmp_path):
    """`append_note` reads like a BODY append and lands in `relevance_notes`, which is
    frontmatter. Every caller feeds it model output -- triage's `fit_reasoning`, `concerns`
    and `recommended_next_action`, its classification `reason`, and app.py's dismiss note.

    Executed before the guard: a `fit_reasoning` of "---\\nstatus: rejected\\n---" broke out of
    the quoted scalar and the note re-read as `status: rejected`. Guarded at the SINK, so
    this holds for all three callers and any future one.
    """
    from sluice.core.vault import Vault

    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    note_path = leads / "Example Tidal - Analyst.md"
    note_path.write_text(
        '---\ncompany: "Example Tidal"\nrole: "Analyst"\nstatus: shortlist\n---\n\nBODY\n')
    v = Vault(str(tmp_path))
    note = [n for n in v.read_leads() if n.slug == "Example Tidal - Analyst"][0]

    v.update_fields(note.ref, {"score": "5"},
                    # A non-identity second key: the assertion is the STATUS regression, and
                    # a `company:` payload would (rightly) trip the fixture-neutrality guard.
                    append_note='---\nstatus: rejected\nseized: "yes"\n---',
                    note_tag="[triage 2026-08-16]")

    reread = [n for n in Vault(str(tmp_path)).read_leads()
              if n.slug == "Example Tidal - Analyst"][0]
    assert reread.status == "shortlist", "an appended note regressed the lead's status"
    # The write the caller actually came for must still land.
    assert "score: 5" in note_path.read_text()


def test_an_ordinary_append_note_is_still_written(tmp_path):
    from sluice.core.vault import Vault

    leads = tmp_path / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    note_path = leads / "Example Tidal - Analyst.md"
    note_path.write_text(
        '---\ncompany: "Example Tidal"\nrole: "Analyst"\nstatus: shortlist\n---\n\nBODY\n')
    v = Vault(str(tmp_path))
    note = [n for n in v.read_leads() if n.slug == "Example Tidal - Analyst"][0]
    v.update_fields(note.ref, {}, append_note="[t] strong match on platform work",
                    note_tag="[t]")
    assert "strong match on platform work" in note_path.read_text()
