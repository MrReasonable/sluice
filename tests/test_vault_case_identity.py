"""#205: a lead's identity must not be case-variant. Boards render one employer several
ways ("Example Co", "EXAMPLE CO", "example co"), the note name is built from the company
string VERBATIM, and `_locate` probes a CONSTRUCTED path -- so on a case-sensitive
filesystem each spelling seats its own note, with its own status. In the reported store
one spelling held a live shortlist at score 86 while its twin held a dismissal, and the
pair also wedged Syncthing on the case-insensitive machine, which had never received a
version of either note.

WHY THESE TESTS GATE THEMSELVES ON THE FILESYSTEM. On a case-INSENSITIVE filesystem this
defect does not exist: `_locate`'s `os.path.isfile("EXAMPLE CO - X.md")` answers True for
a note seated at "Example Co - X.md", the walk finds it, and the second scrape UPDATES.
Measured on both, against the shipped code:

    case-insensitive (macOS APFS default)  ->  created, updated  -> 1 note
    case-sensitive   (Linux, and CI)       ->  created, created  -> 2 notes

So a test asserting "one note results" is RED on CI and vacuously GREEN on a developer's
Mac -- the repo's own "a guard that discovers nothing passes" shape, one rung out from the
code. It is gated on a MEASURED probe of the actual leads dir rather than on `sys.platform`
(a Mac can mount a case-sensitive volume, and a Linux CI runner could in principle not be
one), and it SKIPS with the reason named rather than passing quietly, so the local reader
is told the guard did not run instead of being shown a green tick that certifies nothing.
"""
import os

import pytest

from sluice.core.leads import Lead
from sluice.core.vault import Vault
from tests.conftest import LOCATIONS


def _lead(company, **kw):
    # `ex-board`, not a shipped adapter's name: `source` is persisted into the note's
    # frontmatter (`Vault._render`), and nothing here asserts adapter identity, so naming a
    # real board writes a claim the test never makes. `ex-board` is this suite's dominant
    # synthetic id already, which is why it is used in preference to inventing a new one.
    base = dict(source="ex-board", search="Engineering Manager", title="Engineering Manager",
                company=company, url="https://ex.invalid/1", location=LOCATIONS[0],
                salary="", job_type="permanent",
                first_seen="2026-07-07", last_seen="2026-07-07")
    base.update(kw)
    return Lead(**base)


def _notes(vault):
    """Every note name under leads_dir, as a sorted list of basenames without .md."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(vault.leads_dir):
        out.extend(n[:-3] for n in filenames if n.endswith(".md"))
    return sorted(out)


def _require_case_sensitive_fs(tmp_path):
    """Skip unless the filesystem under `tmp_path` distinguishes case. PROBED, never
    inferred from the platform: this test's whole subject is what the filesystem does with
    two names differing only in case, so asking it directly is the only answer that cannot
    be wrong. The probe writes into a dedicated subdirectory so it cannot collide with a
    vault the caller has already built."""
    probe = tmp_path / "_case_probe"
    probe.mkdir(exist_ok=True)
    (probe / "CaseProbe").write_text("")
    collides = (probe / "caseprobe").exists()
    for p in probe.iterdir():
        p.unlink()
    probe.rmdir()
    if collides:
        pytest.skip(
            "needs a case-sensitive filesystem: on a case-insensitive one (macOS APFS by "
            "default) _locate's stat already finds the case-variant note, so #205 cannot "
            "be reproduced and this guard would pass without exercising anything. CI "
            "(ubuntu-latest) is case-sensitive and does run it."
        )


def test_a_re_scrape_under_different_company_casing_updates_rather_than_duplicates(tmp_path):
    """The defect, through the real write path: one role, two boards, one employer spelled
    two ways. `upsert` must reconcile them onto ONE note -- a second note is a second
    identity, and the two then hold divergent status, so a dismissal recorded under one
    spelling does not stop the role returning as `new` under the other."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    first = v.upsert(_lead("Example Co"))
    second = v.upsert(_lead("EXAMPLE CO", url="https://ex.invalid/2"))

    assert first.outcome == "created"
    assert second.outcome != "created", (
        f"a case-variant company minted a second note: {_notes(v)}")
    assert len(_notes(v)) == 1, f"case-variant duplicate: {_notes(v)}"


def test_lowercase_and_mixed_case_company_are_one_identity(tmp_path):
    """The second reported pair -- an all-lowercase board spelling against a mixed-case one.
    Kept separate from the all-caps pair above because the two are NOT equivalent under
    every candidate fix: an acronym-safe title-caser converges this pair and leaves the
    all-caps pair apart (measured, 2026-09-03), so a fix that only passes this one has not
    closed #205."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    v.upsert(_lead("Example Co", title="Head of Data & AI", search="Head of Data & AI"))
    second = v.upsert(_lead("example co", title="head of data & ai",
                            search="head of data & ai", url="https://ex.invalid/2"))

    assert second.outcome != "created", (
        f"a case-variant company minted a second note: {_notes(v)}")
    assert len(_notes(v)) == 1, f"case-variant duplicate: {_notes(v)}"


def test_a_note_already_seated_at_a_variant_casing_is_found_not_duplicated(tmp_path):
    """The MIGRATION direction, and the one a name-canonicalising fix gets wrong on its own.
    Every store predating the fix holds notes at board-verbatim names. If a fix canonicalises
    the name it derives but leaves resolution case-sensitive, the very first re-scrape of an
    existing note derives a name the walk cannot find and CREATES the duplicate the fix was
    written to prevent -- so the store is worse, not better, and only after upgrading."""
    _require_case_sensitive_fs(tmp_path)
    v = Vault(str(tmp_path / "vault"))

    # Seat the note the way a pre-fix store holds it: board-verbatim, shouty.
    seeded = v.upsert(_lead("EXAMPLE CO"))
    assert seeded.outcome == "created"
    before = _notes(v)
    assert len(before) == 1, before

    again = v.upsert(_lead("Example Co", url="https://ex.invalid/2"))

    assert again.outcome != "created", (
        f"re-scraping an existing note under a different casing duplicated it: {_notes(v)}")
    assert len(_notes(v)) == 1, f"case-variant duplicate on re-scrape: {_notes(v)}"
