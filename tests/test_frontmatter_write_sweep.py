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

# `f'"{...}"'` reached either by assignment or as a dict-literal value.
_SUBSCRIPT = re.compile(r"""\w+\[[^\]]+\]\s*=\s*f(['"])"['"]?\{""")
_DICT_ENTRY = re.compile(r"""['"][\w_]+['"]\s*:\s*f(['"])"['"]?\{""")

# Values sanitised UPSTREAM of the write, where a line-local check cannot see the guard.
# Every entry is a DECISION that the value is already safe at this point; adding one means
# reading the producer, not silencing the sweep.
_GUARDED_UPSTREAM = {
    # `resolve.py` runs `frontmatter_safe` on the resolved company before it is ever
    # returned, so the write here cannot see an unsafe value.
    ("triage/engine.py", "company"),
}


def _writes():
    """(relative path, line number, line) for every interpolated quoted-scalar write."""
    out = []
    for py in sorted(_PKG.rglob("*.py")):
        rel = py.relative_to(_PKG).as_posix()
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if _SUBSCRIPT.search(line) or _DICT_ENTRY.search(line):
                out.append((rel, i, line.strip()))
    return out


def test_the_sweep_finds_the_writes_it_is_checking():
    """Guards the sweep itself.

    A regex that stops matching passes silently and forever, which is the failure mode this
    whole file exists to prevent -- so the sweep's own liveness is asserted before its verdict
    is trusted.
    """
    found = _writes()
    assert len(found) >= 4, (
        f"the sweep matched {len(found)} frontmatter writes across {_PKG} -- it has drifted "
        "from the code it is supposed to walk")


def test_no_interpolated_frontmatter_write_is_unguarded():
    offenders = []
    for rel, lineno, line in _writes():
        # A parsed datetime cannot carry a quote or a backslash. Exempting the TYPE rather
        # than the field name means a later change routing a raw string through the same
        # field is still caught.
        if ".isoformat()" in line:
            continue
        if "safe" in line:
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
