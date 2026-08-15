"""`Lead` coerces a None in any `str`-annotated field to "" at construction (#1).

`None` is outside the dataclass's own annotation, but it arrives from a store driven
directly, and the vault's blank-note refusal cannot catch it: that guard decides on the
RENDERED frontmatter, where `_render_new` has already written None as the literal string
`None` and `_is_lead_note` reads it back as a valid identity. Measured before the coercion:
`Lead(company=None, title=None)` was `created` at a visible `None - None.md`, which
`read_leads` then RETURNED.
"""
import os
from dataclasses import fields

import pytest

from sluice.core.leads import _LEAD_STR_FIELDS, Lead
from sluice.core.vault import Vault


def _lead(**kw):
    """`source`, `search` and `title` have no defaults on `Lead`, so they are supplied here
    and OVERRIDDEN by kw rather than passed alongside it -- `Lead(source=..., **{"source":
    None})` is a duplicate-kwarg TypeError, which would take the sweep below down to the
    three fields it most needs to cover."""
    return Lead(**{"source": "cord", "search": "Analyst", "title": "Analyst", **kw})


# ── the sweep's own scope ─────────────────────────────────────────────────────
def test_the_derived_field_list_is_not_empty():
    """SCOPE, and it is not decoration: the per-field test below is PARAMETRIZED over
    `_LEAD_STR_FIELDS`, so an empty tuple gives it zero cases and pytest reports it SKIPPED,
    not failed. Measured -- dropping the `str` class from the match (the state this module
    would be in the moment someone added `from __future__ import annotations`) turned that
    test into a silent `s` while `__post_init__` had become a no-op.

    Pins the whole SET rather than `len() > 0`, because a matcher that resolved only some
    annotations would satisfy a non-empty check while leaving the unresolved fields open."""
    assert set(_LEAD_STR_FIELDS) == {
        f.name for f in fields(Lead) if f.name != "raw_meta"}


def test_the_non_string_field_is_not_coerced():
    """The negative half. A matcher that swept EVERY field would also pass the test above by
    accident of the corpus, and would then rewrite `raw_meta` -- a dict, whose None is a
    caller error this has no business hiding."""
    assert "raw_meta" not in _LEAD_STR_FIELDS
    assert _lead(title="Analyst", raw_meta=None).raw_meta is None


# ── the coercion ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("field_name", _LEAD_STR_FIELDS)
def test_every_string_field_coerces_none_to_empty(field_name):
    """Enumerated, never hand-listed: a field added to `Lead` is covered without anyone
    remembering to add a case here."""
    lead = _lead(**{field_name: None})
    assert getattr(lead, field_name) == ""


def test_a_present_value_is_neither_stripped_nor_touched():
    """MIRROR HARM, and the reason the coercion is None-only. Stripping here would move the
    identity of every lead carrying a padded field -- the vault strips independently, and
    only to decide its refusal."""
    lead = _lead(title=" Analyst ", company="Acme", url="https://ex.invalid/1")
    assert lead.title == " Analyst " and lead.company == "Acme"
    assert lead.url == "https://ex.invalid/1"


def test_dedup_key_survives_a_none_company():
    """`dedup_key` calls `.lower()` on company and title. Before the coercion a url-less
    `Lead(company=None)` raised AttributeError there -- and an exception on this path is not
    caught by the ingest sink's `except OSError`, so it aborts the whole run."""
    assert _lead(title="Analyst", company=None).dedup_key.startswith("h:")


# ── through the store ─────────────────────────────────────────────────────────
def _upsert(tmp_path, **kw):
    v = Vault(str(tmp_path))
    outcome = v.upsert(_lead(**kw)).outcome
    leads = tmp_path / "Job Applications" / "Job Leads"
    on_disk = sorted(p.name for p in leads.iterdir()) if leads.is_dir() else []
    return outcome, on_disk, v


@pytest.mark.parametrize("kw", [
    {"company": None, "title": None},
    {"company": None, "title": ""},
    {"company": "", "title": None},
])
def test_a_lead_with_no_identity_is_refused_however_it_is_spelled(tmp_path, kw):
    """`None - None.md`, `None - .md` and ` - None.md` were all created before this."""
    outcome, on_disk, v = _upsert(tmp_path, **kw)
    assert outcome == "refused"
    assert on_disk == []
    assert v.read_leads() == []


@pytest.mark.parametrize("kw,expected", [
    ({"company": "Acme", "title": None}, "Acme - .md"),
    ({"company": None, "title": "Analyst"}, " - Analyst.md"),
])
def test_one_surviving_field_still_creates(tmp_path, kw, expected):
    """MIRROR HARM at the store. `_is_lead_note` is satisfied by EITHER field, so coercing
    the other to "" must not turn a company-only or title-only lead into a refusal -- that
    would put a real lead out of the vault, out of `seen.db`, and re-report it every run.
    Before this it was seated at `Acme - None.md` / `None - Analyst.md` instead."""
    outcome, on_disk, v = _upsert(tmp_path, **kw)
    assert outcome == "created"
    assert on_disk == [expected]
    assert len(v.read_leads()) == 1


def test_the_literal_none_note_is_unreachable(tmp_path):
    """The headline: no spelling of a None-carrying lead puts the string `None` in a name."""
    for kw in ({"company": None, "title": None}, {"company": None, "title": "Analyst"},
               {"company": "Acme", "title": None}):
        _upsert(tmp_path, **kw)
    leads = tmp_path / "Job Applications" / "Job Leads"
    names = [p.name for p in leads.iterdir()] if leads.is_dir() else []
    assert not any("None" in n for n in names), names
    assert os.path.isdir(leads) or True   # a total refusal need not create the dir
