import json
import os
import pathlib, tempfile

import pytest

from sluice.core.vault import Vault, _fm_dict, _split_frontmatter
from tests.conftest import UNREADABLE_DIR as _UNREADABLE_DIR


def _lead_note(fm_lines, body="BODY TEXT\n"):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    note = leads / "Example Foundry - Analyst.md"
    note.write_text("---\n" + fm_lines + "\n---\n\n" + body)
    return Vault(root), str(note), note

def _vault_with(entries, baseline="BASELINE"):
    root = tempfile.mkdtemp()
    exp = pathlib.Path(root, "Job Applications", "Experience Library")
    (exp / "_inbox").mkdir(parents=True)
    for name, fm, body in entries:
        (exp / f"{name}.md").write_text("---\n" + fm + "\n---\n\n" + body)
    (exp / "_inbox" / "draft.md").write_text("---\nCompany: X\nverified: 2026-01-01\n---\nbody")
    mycv = pathlib.Path(root, "My CV"); mycv.mkdir(parents=True)
    (mycv / "CV.md").write_text(baseline)
    return Vault(root), root

def test_read_experience_verified_only_skips_unverified_and_inbox():
    v, _ = _vault_with([
        ("good", 'Company: "Example Foundry"\nBest For: "leadership"\nMetrics: "3 8"\nverified: 2026-07-01', "Grew team 3 to 8."),
        ("bad", 'Company: "Example Systems"\nBest For: "leadership"', "130-person programme."),
    ])
    entries = v.read_experience_entries(verified_only=True)
    titles = [e["title"] for e in entries]
    assert titles == ["good"]
    assert entries[0]["company"] == "Example Foundry"
    assert entries[0]["best_for"] == "leadership"
    assert entries[0]["metrics"] == "3 8"
    assert entries[0]["body"] == "Grew team 3 to 8."

def test_read_experience_parses_block_list_category():
    v, _ = _vault_with([
        ("blocklist", 'Company: "Example Foundry"\nCategory:\n  - Process\n  - Leadership\nverified: 2026-07-01', "Body."),
    ])
    e = v.read_experience_entries(verified_only=True)[0]
    assert e["category"] and "Process" in e["category"] and "Leadership" in e["category"]

@_UNREADABLE_DIR
def test_read_experience_does_not_read_an_unstatable_library_as_empty():
    """`_is_dir`, not os.path.isdir. These entries are the only citable evidence the hard
    fabrication gate recognises, so an empty read leaves a bundle with no ids, every WORK
    bullet fails BAD CITATION, and a permissions problem is reported to the user as
    `skipped-gate` -- a fabrication verdict against their composer -- after a dossier fetch
    and a full compose have already been paid for.

    The VAULT ROOT is what loses permission, not the library: `os.stat(<root>/Experience
    Library)` is then the call that fails. With the library ITSELF at 000 `os.listdir` raises
    already, so that case was never the silent one and asserting on it would witness nothing.

    Witnessed by restoring `os.path.isdir`: this returns [] and goes green-to-red here."""
    v, root = _vault_with([
        ("good", 'Company: "Example Foundry"\nverified: 2026-07-01', "Grew team 3 to 8."),
    ])
    assert len(v.read_experience_entries()) == 1        # mirror harm: the readable case
    os.chmod(pathlib.Path(root, "Job Applications"), 0o000)
    try:
        with pytest.raises(OSError):
            v.read_experience_entries()
    finally:
        os.chmod(pathlib.Path(root, "Job Applications"), 0o755)


def test_read_experience_reads_a_vault_with_no_library_as_empty():
    """The mirror harm of the guard above, and the reason only FileNotFoundError may answer
    absent: an install before the user has written a single entry is the common case, not an
    error, and `cv run` must not raise on it."""
    root = tempfile.mkdtemp()
    assert Vault(root).read_experience_entries() == []


def test_read_baseline():
    v, _ = _vault_with([], baseline="Phone number: +44\nJANE ROE")
    assert "JANE ROE" in v.read_baseline()

def test_set_tailored_cv_is_additive_and_preserves_body():
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    note = leads / "Example Foundry - Analyst.md"
    note.write_text('---\ncompany: "Example Foundry"\nstatus: shortlist\n---\n\nBODY TEXT\n')
    v = Vault(root)
    v.set_tailored_cv(str(note), "Jane_Roe_CV_ab12cd34.pdf (2026-07-08)")
    text = note.read_text()
    assert "tailored_cv: Jane_Roe_CV_ab12cd34.pdf (2026-07-08)" in text
    assert "status: shortlist" in text  # untouched
    assert "BODY TEXT" in text          # body preserved


# --- #60 sign-off gate: the sign_off Store method (outcome string, never-clobber) ---

def test_sign_off_promotes_pending_and_clears_markers_body_intact():
    v, ref, note = _lead_note(
        'company: "Acme"\nstatus: shortlist\n'
        'pending_cv: CV_ab12.pdf (2026-07-24)\nneeds_signoff: ["unsupported\\tMotivated by placeholder\\tNONE"]')
    assert v.sign_off(ref) == "promoted"
    text = note.read_text()
    assert "tailored_cv: CV_ab12.pdf (2026-07-24)" in text
    assert "pending_cv:" not in text and "needs_signoff:" not in text
    assert "status: shortlist" in text and "BODY TEXT" in text  # body + other keys intact


def test_sign_off_discard_clears_markers_without_promoting():
    v, ref, note = _lead_note(
        'status: shortlist\npending_cv: CV_ab12.pdf (2026-07-24)\nneeds_signoff: ["x"]')
    assert v.sign_off(ref, accept=False) == "discarded"
    text = note.read_text()
    assert "pending_cv:" not in text and "needs_signoff:" not in text
    assert "tailored_cv:" not in text  # discard never promotes


def test_sign_off_collision_leaves_existing_tailored_cv_unchanged():
    # A real CV appeared since the flagged compose (a direct `sluice cv --lead X` after
    # discard+recompose). Accept must NOT clobber it: clear the stale markers, report
    # collision, mirror set_tailored_cv(only_if_absent=...). Asserting the VALUE (not
    # merely that markers cleared) is what catches a naive promote (arch-004 / W3).
    v, ref, note = _lead_note(
        'status: shortlist\ntailored_cv: CV_REAL.pdf (2026-07-24)\n'
        'pending_cv: CV_STALE.pdf (2026-07-24)\nneeds_signoff: ["x"]')
    assert v.sign_off(ref) == "collision"
    text = note.read_text()
    assert "tailored_cv: CV_REAL.pdf (2026-07-24)" in text  # UNCHANGED
    assert "CV_STALE" not in text                            # stale pending not promoted
    assert "pending_cv:" not in text and "needs_signoff:" not in text


def test_sign_off_nothing_when_no_pending_is_a_noop():
    v, ref, note = _lead_note('status: shortlist\ncompany: "Acme"')
    before = note.read_text()
    assert v.sign_off(ref) == "nothing"
    assert note.read_text() == before  # no write at all


def test_needs_signoff_json_scalar_survives_quote_and_colon():
    # needs_signoff is a single-line JSON scalar so a claim carrying a quote or colon
    # cannot corrupt the flat frontmatter. Round-trips update_fields -> _fm_dict -> json.
    claim = 'unsupported\tMotivated by "impact": scale\tNONE'
    v, ref, note = _lead_note('status: shortlist\ncompany: "Acme"')
    v.update_fields(ref, {"needs_signoff": json.dumps([claim])})
    inner, _ = _split_frontmatter(note.read_text())
    assert json.loads(_fm_dict(inner)["needs_signoff"]) == [claim]
    assert "status: shortlist" in note.read_text()  # other keys + body untouched
