"""Sluice.create_lead(job_description=...) (#369, spec §3): the section is appended on
CREATE only, through Store.append_body_section with the store-issued UpsertResult.ref."""
import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.jdsection import read
from sluice.core.protocols import UpsertResult, VaultConflict
from sluice.core.vault import Vault

_URL = "https://example.invalid/jobs/1"
_JD = "Placeholder posting: build example services.\n## Benefits\nexample perks"
_OUTCOMES = ("updated", "merged", "refused", "merged_away", "merged_away_unproven")


def _app(store):
    return Sluice(Config(), store=store)


@pytest.mark.parametrize("layout", ["", "active_archive"])
def test_created_with_a_jd_appends_the_section(tmp_path, layout):
    store = Vault(str(tmp_path), lead_layout=layout)
    r = _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL,
                                job_description=_JD)
    assert (r.outcome, r.job_description_written, r.job_description_note) == (
        "created", True, "")
    note = next(n for n in store.read_leads() if n.slug == r.slug)
    text, count = read(note.body)
    assert count == 1
    assert text == "Placeholder posting: build example services.\n### Benefits\nexample perks"


class _SpyStore:
    """Answers upsert with a chosen outcome and records every append. `append` decides what
    append_body_section does: a bool to return, or an exception to raise. Raises TypeError on
    a None ref, as the vault's _cas_write path would."""
    def __init__(self, outcome="created", ref="ref-1", append=True):
        self.outcome, self.ref, self.append, self.appends = outcome, ref, append, []

    def upsert(self, lead):
        has = self.outcome in ("created", "updated", "merged")
        return UpsertResult(outcome=self.outcome, slug="Example Ltd - Example Role" if has else "",
                            ref=self.ref if has else None)

    def append_body_section(self, ref, tag, section_md):
        self.appends.append(ref)
        if ref is None:
            raise TypeError("expected str, bytes or os.PathLike object, not NoneType")
        if isinstance(self.append, Exception):
            raise self.append
        return self.append


def _create(store, jd=_JD):
    return _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL,
                                   job_description=jd)


@pytest.mark.parametrize("outcome", _OUTCOMES)
def test_no_append_for_any_outcome_but_created(outcome):
    store = _SpyStore(outcome=outcome)
    r = _create(store)
    assert store.appends == []
    assert r.job_description_written is False
    assert r.job_description_note == "the lead already existed, or was not created"


@pytest.mark.parametrize("append, note", [
    (False, "a job description section was already present"),
    (VaultConflict("raced"), "the note changed before the job description could be added"),
    (FileNotFoundError("moved"), "the note changed before the job description could be added"),
])
def test_an_append_that_does_not_land_keeps_the_created_outcome(append, note):
    r = _create(_SpyStore(append=append))
    assert (r.outcome, r.slug) == ("created", "Example Ltd - Example Role")
    assert (r.job_description_written, r.job_description_note) == (False, note)


def test_created_with_no_ref_attempts_no_append():
    store = _SpyStore(ref=None)
    r = _create(store)
    assert store.appends == []
    assert r.outcome == "created"
    assert (r.job_description_written, r.job_description_note) == (
        False, "the store did not return a reference to the new note")


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_jd_is_refused_before_anything_is_written(tmp_path, blank):
    store = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="job_description"):
        _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL,
                                job_description=blank)
    assert store.read_leads() == []


def test_no_jd_is_todays_behaviour():
    store = _SpyStore()
    r = _app(store).create_lead(title="Example Role", company="Example Ltd", url=_URL)
    assert store.appends == []
    assert (r.outcome, r.job_description_written, r.job_description_note) == (
        "created", False, "")


def test_a_field_carrying_the_section_tag_is_refused_when_a_jd_is_given(tmp_path):
    # inv-001: append_body_section's idempotence is "the tag appears anywhere in the note",
    # and company/title are rendered into it -- so a field carrying the tag would turn the
    # append into a no-op reported as "already present". Refused up front, naming the field,
    # before anything is written.
    from sluice.core.jdsection import TAG
    store = Vault(str(tmp_path))
    with pytest.raises(ValueError, match="company"):
        _app(store).create_lead(title="Example Role", company=f"Example Ltd {TAG}", url=_URL,
                                job_description=_JD)
    assert store.read_leads() == []
