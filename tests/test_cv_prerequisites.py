"""#242, carried into #364/#365/#368: cv's config-level preconditions, refused once and
before any spend (#364 spec §9.1). Each is equally true of every lead in a run, so none is a
per-lead fact.

The ORDER is load-bearing and asserted directly: the check precedes the RENDERER, the
backend and the dossier fetch -- on a bare install the renderer raises first, so a check
placed after it never runs for the newcomer it exists to help.
"""
import os

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.core.protocols import CV_LAYOUT_RELPATH
from sluice.cv.engine import missing_prerequisites
from tests.conftest import UNREADABLE_DIR, layout_yaml

ALPHA = (("alpha", {"Company": "Example Foundry"}),)


def _vault(tmp_path, *, layout="default", entries=ALPHA):
    """A real vault: a CV Layout note (none when `layout` is None) and verified experience
    entries proposed and verified through the store -- the route a user's entries take."""
    from sluice.core.vault import Vault

    v = Vault(str(tmp_path / "vault"))
    if layout is not None:
        path = os.path.join(v.dir, CV_LAYOUT_RELPATH)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(layout_yaml() if layout == "default" else layout)
    for name, fields in entries:
        v.propose_evidence("experience", name=name, fields=fields)
        pending = {e["title"]: e for e in v.read_pending_evidence("experience")}
        with open(pending[name]["path"], encoding="utf-8") as fh:
            raw = fh.read()
        assert v.verify_evidence("experience", name, today="2026-09-03", reviewed=raw), name
    return v


def _layout_path(v):
    path = os.path.join(v.dir, CV_LAYOUT_RELPATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _seed_shortlist_leads(vault_dir, n=1):
    """Shortlist leads, so `run_one` is genuinely reachable and the dossier path is live: an
    ordering test with no selectable lead cannot fail on a dossier regression."""
    from sluice.core.leads import Lead
    from sluice.core.vault import Vault

    v = Vault(str(vault_dir))
    for i in range(n):
        v.upsert(Lead(source="manual", search="", title=f"SYNTHETIC-ROLE-{i}",
                      company="Example Foundry", location="", salary="",
                      url=f"https://example.invalid/jobs/{i}", job_type="",
                      job_type_source="", first_seen="", last_seen=""))
    for note in v.read_leads():
        v.update_fields(note.ref, {"status": "shortlist"})
    return v


def _forbid_spend(monkeypatch):
    for name in ("renderer", "backend", "dossier_cache"):
        monkeypatch.setattr(
            Sluice, name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f"{_n} ran")))


# --- the CV Layout ------------------------------------------------------------------

def test_a_composable_vault_has_no_missing_prerequisite(tmp_path):
    assert missing_prerequisites(_vault(tmp_path)) == []


def test_an_absent_layout_is_reported_not_raised(tmp_path):
    [msg] = missing_prerequisites(_vault(tmp_path, layout=None))
    assert msg.startswith("no CV Layout note at") and CV_LAYOUT_RELPATH in msg


def test_a_malformed_layout_reports_every_problem(tmp_path):
    [msg] = missing_prerequisites(
        _vault(tmp_path, layout="---\nroles:\n  - heading: Example Foundry\n---\n"))
    assert "is malformed" in msg
    assert "roles[0].from: required" in msg and "roles[0].to: required" in msg


def test_an_undecodable_layout_is_unreadable_not_absent(tmp_path):
    # UnicodeDecodeError descends from ValueError, not OSError: an `except OSError` alone
    # would let a layout saved as UTF-16 escape as a traceback.
    v = _vault(tmp_path, layout=None)
    with open(_layout_path(v), "wb") as fh:
        fh.write(b"\xff\xfe\x00 not utf-8")
    [msg] = missing_prerequisites(v)
    assert msg.startswith("cannot read your CV Layout note")


def test_a_symlinked_layout_is_unreadable_not_absent(tmp_path):
    v = _vault(tmp_path, layout=None)
    outside = tmp_path / "outside.md"
    outside.write_text(layout_yaml(), encoding="utf-8")
    os.symlink(outside, _layout_path(v))
    [msg] = missing_prerequisites(v)
    assert msg.startswith("cannot read your CV Layout note") and "symlink" in msg


@UNREADABLE_DIR
def test_an_unreadable_layout_is_not_reported_as_absent(tmp_path):
    v = _vault(tmp_path)
    path = _layout_path(v)
    os.chmod(path, 0o000)
    try:
        [msg] = missing_prerequisites(v)
    finally:
        os.chmod(path, 0o644)
    assert msg.startswith("cannot read your CV Layout note")
    assert "Permission denied" in msg or "Errno 13" in msg


# --- the experience entries ----------------------------------------------------------

def test_an_empty_citable_corpus_is_reported(tmp_path):
    [msg] = missing_prerequisites(_vault(tmp_path, entries=()))
    assert msg.startswith("no verified experience entries")


def test_empty_skills_and_stories_do_not_block_a_run(tmp_path):
    v = _vault(tmp_path)
    assert v.read_evidence("skills") == [] and v.read_evidence("stories") == []
    assert missing_prerequisites(v) == []


def test_an_unverified_entry_does_not_satisfy_the_corpus(tmp_path):
    v = _vault(tmp_path, entries=())
    d = os.path.join(v.dir, "Job Applications", "Experience Library")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write('---\nCompany: "Example Foundry"\n---\n\n# alpha\n')
    assert v.read_evidence("experience", verified_only=False), "precondition: it exists"
    assert v.read_evidence("experience") == [], "premise: the default read is verified-only"
    assert any("no verified experience" in m for m in missing_prerequisites(v))


def test_an_unreadable_corpus_does_not_tell_you_to_add_an_entry():
    class _Unreadable:
        def read_cv_layout(self):
            return None

        def read_evidence(self, kind, verified_only=True):
            raise OSError("refusing to read through it: symlinked evidence directory")

    missing = missing_prerequisites(_Unreadable())
    assert any("cannot read your experience entries" in m for m in missing), missing
    assert all("experience add" not in m for m in missing), missing


def test_a_tools_item_the_gate_cannot_use_is_named_with_its_entry(tmp_path):
    v = _vault(tmp_path, entries=(
        ("alpha", {"Company": "Example Foundry", "Tools": "Examplelang, 9001 Examplestandard"}),))
    [msg] = missing_prerequisites(v)
    assert msg.startswith("your experience entry 'alpha':") and "9001" in msg


def test_no_slot_that_can_cite_anything_is_reported_naming_doctor(tmp_path):
    [msg] = missing_prerequisites(
        _vault(tmp_path, entries=(("alpha", {"Company": "Example Robotics"}),)))
    assert msg.startswith("no role in your CV Layout can cite any verified experience entry")
    assert "job-sluice doctor" in msg


def test_a_layout_of_zero_budgets_needs_no_verified_entry(tmp_path):
    # Every role at bullets_max 0 is a headings-only CV (#364 spec §5.2, D10): it cites nothing.
    zero = layout_yaml([{"heading": "Example Foundry", "from": "02/2023", "to": "present",
                         "bullets_max": 0}])
    assert missing_prerequisites(_vault(tmp_path, layout=zero, entries=())) == []


def test_one_role_asking_for_bullets_still_needs_a_verified_entry(tmp_path):
    # The control: the same refusal, while any role's budget is non-zero (absent = no cap).
    mixed = layout_yaml([{"heading": "Example Foundry", "from": "02/2023", "to": "present",
                          "bullets_max": 0},
                         {"heading": "Example Robotics", "from": "01/2020", "to": "01/2023"}])
    [msg] = missing_prerequisites(_vault(tmp_path, layout=mixed, entries=()))
    assert msg.startswith("no verified experience entries")


def test_a_layout_of_zero_budgets_is_a_choice_not_a_misconfiguration(tmp_path):
    zero = layout_yaml([{"heading": "Example Foundry", "from": "02/2023", "to": "present",
                         "bullets_max": 0}])
    v = _vault(tmp_path, layout=zero, entries=(("alpha", {"Company": "Example Robotics"}),))
    assert missing_prerequisites(v) == []


# --- before any spend, through the facade ---------------------------------------------

def test_the_refusal_precedes_the_renderer_and_the_backend(tmp_path, monkeypatch):
    sl = Sluice(Config(vault_dir=str(tmp_path / "vault")))
    _seed_shortlist_leads(tmp_path / "vault")
    _forbid_spend(monkeypatch)
    with pytest.raises(ValueError) as exc:
        sl.compose_cv(all_shortlist=True)
    assert "not set up to compose yet" in str(exc.value)
    assert "CV Layout" in str(exc.value)


def test_a_dry_run_is_refused_too(tmp_path, monkeypatch):
    sl = Sluice(Config(vault_dir=str(tmp_path / "vault")))
    _seed_shortlist_leads(tmp_path / "vault")
    _forbid_spend(monkeypatch)
    with pytest.raises(ValueError, match="not set up to compose yet"):
        sl.compose_cv(all_shortlist=True, dry_run=True)


@pytest.mark.parametrize("entries,expected", [
    ((("alpha", {"Company": "Example Foundry", "Tools": "9001 Examplestandard"}),),
     "your experience entry 'alpha':"),
    ((("alpha", {"Company": "Example Robotics"}),),
     "no role in your CV Layout can cite any verified experience entry"),
])
def test_a_run_over_two_leads_is_refused_once_with_no_fetch_and_no_compose(
        tmp_path, monkeypatch, entries, expected):
    v = _vault(tmp_path, entries=entries)
    _seed_shortlist_leads(v.dir, n=2)
    _forbid_spend(monkeypatch)
    with pytest.raises(ValueError) as exc:
        Sluice(Config(vault_dir=v.dir)).compose_cv(all_shortlist=True)
    assert str(exc.value).count(expected) == 1, str(exc.value)


def test_the_refusal_reaches_the_cli_as_exit_2(tmp_path, monkeypatch, capsys):
    from sluice.cli import main

    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    rc = main(["cv", "run", "--all-shortlist"])
    out = capsys.readouterr()
    assert rc == 2, f"documented exit 2, got {rc}"
    assert "not set up to compose yet" in (out.out + out.err)
