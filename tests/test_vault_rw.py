import os

import pytest

from sluice.core.vault import Vault, _set_fm, frontmatter_safe


def _write_note(vault, name, fm_lines, body="body\n"):
    leads = os.path.join(vault.dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, name), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm_lines) + "\n---\n" + body)


def test_read_leads_filters_and_normalizes_status(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "A.md", ['company: "Acme"', 'role: "Analyst"', "status: new"])
    _write_note(v, "B.md", ['company: "Beta"', 'role: "Analyst"', 'status: "dismissed"'])
    _write_note(v, "C.md", ['company: "Gamma"', 'role: "Analyst"', "status: applied"])

    new = v.read_leads({"new"})
    assert [n.fm["company"] for n in new] == ["Acme"]

    # 'dismissed' normalizes to 'dismiss' for filtering
    assert len(v.read_leads({"dismiss"})) == 1
    assert v.read_leads({"dismiss"})[0].status == "dismiss"

    assert len(v.read_leads()) == 3  # no filter -> all


def test_update_fields_sets_values_preserves_body_and_is_idempotent(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "A.md",
                ['company: "Acme"', "status: new", "score: 0",
                 'relevance_notes: ""'],
                body="# Acme\n\nDetailed body text.\n")
    path = v.read_leads()[0].ref

    v.update_fields(path, {"status": "dismiss", "score": "20"},
                    append_note="[triage] IC role.", note_tag="[triage]")
    note = v.read_leads()[0]
    assert note.status == "dismiss"
    assert note.fm["score"] == "20"
    assert "[triage] IC role." in note.fm["relevance_notes"]
    assert "Detailed body text." in note.body  # body preserved

    # second identical apply does not duplicate the note annotation
    v.update_fields(path, {"status": "dismiss"},
                    append_note="[triage] IC role.", note_tag="[triage]")
    assert v.read_leads()[0].fm["relevance_notes"].count("[triage]") == 1

    # a key that did not exist gets added
    v.update_fields(path, {"glassdoor_rating": '"3.9"'})
    assert v.read_leads()[0].fm["glassdoor_rating"] == "3.9"


@pytest.mark.parametrize("literal, expected", [
    # A backslash the OLD f-string replacement template turned into a regex escape:
    # `\B` is not one, so re.sub raised re.PatternError from inside the write. Not a
    # VaultConflict, so no caller's `except VaultConflict` could catch it.
    ('"Foo\\Bar Ltd"', 'company: "Foo\\Bar Ltd"'),
    # Worse, because it did not raise: `\n` IS a valid escape in a replacement
    # template, so the value silently gained a real newline and split the frontmatter.
    ('"Foo\\nBar"', 'company: "Foo\\nBar"'),
    # Worse again: a group reference expanded to the whole matched line, so the
    # written value contained the OLD line it was replacing.
    ('"Foo\\g<0>Bar"', 'company: "Foo\\g<0>Bar"'),
])
def test_set_fm_writes_a_backslash_literal_verbatim(literal, expected):
    """_set_fm's replacement must be a CALLABLE: re.sub interprets escapes in a string
    replacement template, so every literal above was rewritten on its way through.

    This is the layer that can fix it once for every caller. `frontmatter_safe`
    still rejects a backslash, but for the INDEPENDENT reason that a raw
    backslash inside a double-quoted YAML scalar is a YAML escape -- a different
    failure, in a different reader, that this function cannot see."""
    assert _set_fm('company: ""\nstatus: new', "company", literal) == \
        expected + "\nstatus: new"


def test_set_fm_leaves_an_ordinary_quoted_literal_byte_identical():
    """The paired control: the callable replacement must not change what every
    existing quoted caller (`glassdoor_rating`, `culture_flags`) already writes."""
    inner = 'company: "Acme"\nglassdoor_rating: ""\nstatus: new'
    assert _set_fm(inner, "glassdoor_rating", '"3.9"') == \
        'company: "Acme"\nglassdoor_rating: "3.9"\nstatus: new'
    assert _set_fm(inner, "culture_flags", '"a, b"') == inner + '\nculture_flags: "a, b"'


@pytest.mark.parametrize("unsafe", ['Example "Co"', "Example\nCo", "Example\rCo",
                                    "Example\\Co", "Example\x0bCo"])
def test_frontmatter_safe_rejects_a_structural_or_unprintable_character(unsafe):
    # #111: the guard resolve.py's `_safe` used for a scraped company name, generalized
    # so every `_set_fm` caller writing unmediated external content (a parsed email link,
    # a CLI-supplied URL) can reject the same class before it reaches a quoted scalar.
    assert frontmatter_safe(unsafe) is None


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_frontmatter_safe_rejects_blank_or_whitespace_only(blank):
    assert frontmatter_safe(blank) is None


def test_frontmatter_safe_returns_an_ordinary_value_unchanged():
    assert frontmatter_safe("https://example.invalid/x") == "https://example.invalid/x"


def test_normalize_all_statuses(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "A.md", ['company: "A"', 'status: "dismissed"'])   # value drift
    _write_note(v, "B.md", ['company: "B"', "status: new"])           # already ok
    _write_note(v, "C.md", ['company: "C"', 'status: "Researching"']) # value drift
    _write_note(v, "D.md", ['company: "D"', 'status: "new"'])         # quoting drift

    dry = v.normalize_all_statuses(dry_run=True)
    assert dry["changed"] == 3
    assert v.read_leads()[0].fm["status"] in ("dismissed", "dismiss")  # unwritten

    real = v.normalize_all_statuses(dry_run=False)
    assert real["changed"] == 3
    statuses = sorted(n.status for n in v.read_leads())
    assert statuses == ["dismiss", "new", "new", "research"]
    # canonical form is unquoted, for both value-drift and quoting-drift notes
    dismiss_raw = open(v.read_leads({"dismiss"})[0].ref).read()
    assert "status: dismiss" in dismiss_raw and 'status: "dismiss"' not in dismiss_raw
    d_raw = open(os.path.join(v.leads_dir, "D.md")).read()
    assert "status: new" in d_raw and 'status: "new"' not in d_raw


def test_normalize_all_statuses_treats_unjudgeable_as_known(tmp_path):
    """#169 (Task 4): `unjudgeable` joined TRIAGE_OWNED, which widens CANONICAL, which is
    the ONLY thing this sweep's `is_canonical(canonical)` check (core/vault.py) reads to
    decide `summary["unknown"]` membership -- unlike every other status predicate
    (can_apply/can_advance/can_transition/is_application_owned), none of which reference
    TRIAGE_OWNED at all, so none of them could have changed behaviour for this new member.
    No existing test exercised `summary["unknown"]` for this sweep at all (grepped: only
    `leads reconcile`'s SEPARATE `unknown` list, vault.py:1695, was covered), so a status
    vocabulary regression here -- e.g. `unjudgeable` silently dropping back out of
    TRIAGE_OWNED -- would land in `summary["unknown"]` with nothing red anywhere."""
    v = Vault(str(tmp_path))
    _write_note(v, "E.md", ['company: "E"', 'status: "unjudgeable"'])
    summary = v.normalize_all_statuses(dry_run=False)
    assert "unjudgeable" not in summary["unknown"], summary["unknown"]
    assert v.read_leads({"unjudgeable"})[0].status == "unjudgeable"


def test_normalize_collapses_consistent_duplicate_status_lines(tmp_path):
    v = Vault(str(tmp_path))
    # legacy corruption: two status lines, same value, mixed quoting
    _write_note(v, "dup.md",
                ['company: "A"', "status: dismiss", "score: 0",
                 'culture_flags: ""', 'status: "dismiss"'])
    summary = v.normalize_all_statuses(dry_run=False)
    assert summary["changed"] == 1
    raw = open(os.path.join(v.leads_dir, "dup.md")).read()
    assert raw.count("status:") == 1                     # collapsed to one line
    assert "status: dismiss" in raw and 'status: "dismiss"' not in raw


def test_normalize_flags_conflicting_status_without_touching(tmp_path):
    v = Vault(str(tmp_path))
    _write_note(v, "conflict.md",
                ['company: "B"', "status: dismiss", 'status: "shortlist"'])
    summary = v.normalize_all_statuses(dry_run=False)
    assert ("conflict.md", ["dismiss", "shortlist"]) in summary["conflicts"]
    raw = open(os.path.join(v.leads_dir, "conflict.md")).read()
    # left untouched: both original lines still present
    assert "status: dismiss" in raw and 'status: "shortlist"' in raw
