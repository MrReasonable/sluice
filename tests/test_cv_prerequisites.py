"""#242: cv's two config-level preconditions, refused once and before any spend.

A missing baseline CV or an empty citable corpus is equally true of every lead in a run, so
neither is a per-lead fact. Before this they were checked nowhere: the run fetched the job
description and called the composer, then failed the fabrication gate or, under `--lead`,
raised out of `read_baseline`'s bare `open` as a traceback. It cost tokens to be told a file
was missing, and README's prerequisites table documented that as expected behaviour.

The ORDER is the load-bearing part and is asserted directly. The check has to precede the
RENDERER, not merely the dossier fetch: measured on a bare install the renderer raises first
(`No module named 'weasyprint'`), so a check placed after it never runs for exactly the
newcomer it exists to help.
"""

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from sluice.cv.engine import missing_prerequisites
from tests.conftest import UNREADABLE_DIR


def _seed_shortlist_lead(vault_dir):
    """One shortlist lead, so `run_one` is actually reached and the dossier path is live.

    An ordering test with no selectable lead cannot fail on a dossier regression: `run_batch`
    and the `--lead` path both return early, so `get_or_build` is never called however late the
    refusal is moved.
    """
    from sluice.core.leads import Lead
    from sluice.core.vault import Vault

    v = Vault(str(vault_dir))
    # Minimal identity on purpose: this test needs a SELECTABLE lead, nothing about who or
    # where. A location here would put a new value in front of
    # `test_fixture_name_neutrality`'s CV-fixture roster -- which it did, and the ratchet
    # caught it -- for a field the assertion never reads. Widening a neutrality roster to
    # carry a value the test does not need is the wrong direction.
    v.upsert(Lead(source="manual", search="", title="Senior Engineer",
                  company="Example Systems", location="", salary="",
                  url="https://example.invalid/jobs/1", job_type="", job_type_source="",
                  first_seen="", last_seen=""))
    for n in v.read_leads():
        v.update_fields(n.ref, {"status": "shortlist"})
    return v


def _vault(tmp_path, *, baseline=None, verified=()):
    from sluice.core.vault import Vault

    v = Vault(str(tmp_path / "vault"))
    if baseline is not None:
        import os
        os.makedirs(os.path.join(v.dir, "My CV"), exist_ok=True)
        with open(os.path.join(v.dir, v.baseline_rel), "w", encoding="utf-8") as fh:
            fh.write(baseline)
    for name in verified:
        v.propose_evidence("experience", name=name, fields={})
        # `reviewed` is compare-and-set against the WHOLE pending note (frontmatter and body,
        # `_read(src)`), not just the body -- it is the exact bytes a human was shown, which is
        # what makes an edit-after-approval abstain. Read it back through the store's own
        # listing rather than treating the propose handle as a path, since the Store contract
        # promises only an opaque handle.
        pending = {e["title"]: e for e in v.read_pending_evidence("experience")}
        with open(pending[name]["path"], encoding="utf-8") as fh:
            raw = fh.read()
        assert v.verify_evidence("experience", name, today="2026-09-03", reviewed=raw), name
    return v


def test_a_missing_baseline_is_reported_not_raised(tmp_path):
    """`read_baseline` goes through `_read`'s bare `open`, so the pre-fix path RAISED."""
    v = _vault(tmp_path)
    missing = missing_prerequisites(v)
    assert any("baseline CV" in m for m in missing), missing
    # The message must name the file, or the user cannot act on it.
    assert any(v.baseline_rel in m for m in missing), missing
    # ABSENT, not merely unreadable. Both messages contain "baseline CV", so without this the
    # `except FileNotFoundError: pass` arm can be deleted and the suite stays green while a
    # missing CV reports "cannot read ... [Errno 2]" -- an error text where an actionable
    # sentence belongs.
    assert all("cannot read" not in m for m in missing), missing
    assert any("no baseline CV at" in m for m in missing), missing


def test_an_empty_citable_corpus_is_reported(tmp_path):
    v = _vault(tmp_path, baseline="# CV\n")
    missing = missing_prerequisites(v)
    assert any("verified experience" in m for m in missing), missing
    # ...and it names the two commands that fix it.
    assert any("experience add" in m and "experience verify" in m for m in missing), missing


def test_empty_skills_and_stories_do_not_block_a_run(tmp_path):
    """Keyed on `cited_by_gate`, not on all three kinds. `skills` is read_by_composer only
    and `stories` is neither, so the gate licenses nothing from them and their emptiness
    cannot make a CV fail. Requiring them would refuse runs that compose perfectly well."""
    v = _vault(tmp_path, baseline="# CV\n", verified=("alpha",))
    assert v.read_evidence("skills") == [] and v.read_evidence("stories") == []
    assert missing_prerequisites(v) == [], (
        "an empty skills or stories corpus blocked a run the gate would have allowed")


def test_the_refusal_precedes_the_renderer_and_the_backend(tmp_path, monkeypatch):
    """ORDER, asserted by making both of them explode.

    A check placed after renderer construction is unreachable on a bare install, which is the
    install this exists for. Both are monkeypatched to raise: if either runs first the test
    sees ITS exception rather than the usage error, which is exactly the pre-fix behaviour."""
    cfg = Config(vault_dir=str(tmp_path / "vault"))
    sl = Sluice(cfg)
    # A SELECTABLE lead, and the dossier cache made to explode too. Without both, the test
    # cannot see a regression that moved the check past the dossier: with no shortlist lead
    # `run_one` never runs, so nothing reaches `get_or_build` and the fetch path is untested
    # however late the refusal lands. The dossier fetch drives a real browser, so it is the
    # spend this ordering most needs to precede.
    _seed_shortlist_lead(tmp_path / "vault")
    for name, what in (("renderer", "renderer"), ("backend", "backend"),
                       ("dossier_cache", "dossier cache")):
        monkeypatch.setattr(
            Sluice, name,
            lambda *a, _w=what, **k: (_ for _ in ()).throw(AssertionError(f"{_w} ran")))

    with pytest.raises(ValueError) as exc:
        sl.compose_cv(lead="anything")
    assert "not set up to compose yet" in str(exc.value)
    assert "baseline CV" in str(exc.value)


def test_a_dry_run_is_refused_too(tmp_path, monkeypatch):
    """Previewing a run that cannot possibly compose is the false green this removes."""
    sl = Sluice(Config(vault_dir=str(tmp_path / "vault")))
    # Same shape as the ordering test above, and for the same reason: a dry run still reaches
    # the dossier on the pre-fix path, so the lead and the exploding cache are what make this
    # cover it.
    _seed_shortlist_lead(tmp_path / "vault")
    for name in ("renderer", "backend", "dossier_cache"):
        monkeypatch.setattr(
            Sluice, name,
            lambda *a, _n=name, **k: (_ for _ in ()).throw(AssertionError(f"{_n} ran")))
    with pytest.raises(ValueError) as exc:
        sl.compose_cv(all_shortlist=True, dry_run=True)
    assert "not set up to compose yet" in str(exc.value)


def test_an_undecodable_baseline_is_reported_not_raised(tmp_path):
    """`read_baseline` opens with `encoding="utf-8"`, and `UnicodeDecodeError` descends from
    ValueError rather than OSError -- so an `except OSError` alone lets a baseline of arbitrary
    bytes escape as a raw traceback, which is precisely the defect #242 exists to remove.

    A real vault can reach this: a CV saved as UTF-16 or Latin-1 is an ordinary user mistake,
    and it should read as "your baseline is unreadable", not as a stack trace."""
    import os

    v = _vault(tmp_path)
    os.makedirs(os.path.join(v.dir, "My CV"), exist_ok=True)
    with open(os.path.join(v.dir, v.baseline_rel), "wb") as fh:
        fh.write(b"\xff\xfe\x00 not utf-8")

    missing = missing_prerequisites(v)          # must not raise
    # UNREADABLE, not absent -- and asserted in both directions for the same reason its sibling
    # `test_a_missing_baseline_is_reported_not_raised` is: both messages contain "baseline CV",
    # so `any("baseline CV" in m)` alone passes whichever arm fires. A file that exists but
    # cannot be decoded is not a file that is missing.
    assert any("cannot read the baseline CV" in m for m in missing), missing
    assert all("no baseline CV at" not in m for m in missing), missing


@UNREADABLE_DIR
def test_an_unreadable_vault_is_not_reported_as_an_empty_one(tmp_path):
    """UNREADABLE and EMPTY are different facts and get different sentences.

    `Vault.preflight` already ruled on this shape for the same corpora: a kind whose
    directories could not be read reports the error's own text "INSTEAD of that triple, never
    a zero count". Reporting a read failure as an absence is the quiet-wrong-default class,
    and here it also sent the user somewhere useless -- a corpus that cannot be read produced
    "no verified experience entries ... Add with `job-sluice experience add`", naming a command
    that reaches the corpus through the same resolver and so fails identically.
    """
    import os

    v = _vault(tmp_path)
    os.makedirs(os.path.join(v.dir, "My CV"), exist_ok=True)
    path = os.path.join(v.dir, v.baseline_rel)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# A REAL CV, with real content\n")
    os.chmod(path, 0o000)
    try:
        missing = missing_prerequisites(v)
    finally:
        os.chmod(path, 0o644)                      # so tmp_path cleanup can remove it

    baseline_msgs = [m for m in missing if "baseline CV" in m]
    assert baseline_msgs, missing
    assert all("no baseline CV at" not in m for m in baseline_msgs), (
        f"a present-but-unreadable baseline was reported as absent: {baseline_msgs}")
    assert any("cannot read" in m for m in baseline_msgs), baseline_msgs
    # The error's own text has to survive, or the user cannot tell permissions from a symlink.
    assert any("Permission denied" in m or "Errno 13" in m for m in baseline_msgs), baseline_msgs


def test_an_unreadable_corpus_does_not_tell_you_to_add_an_entry(tmp_path):
    """The command it used to name fails through the same resolver that just refused."""
    class _Unreadable:
        baseline_rel = "My CV/CV.md"

        def read_baseline(self):
            return "# CV\n"

        def read_evidence(self, kind, verified_only=True):
            raise OSError("refusing to write through it: symlinked evidence directory")

    missing = missing_prerequisites(_Unreadable())
    assert any("cannot read your experience entries" in m for m in missing), missing
    assert all("experience add" not in m for m in missing), (
        f"pointed the user at a command that fails the same way: {missing}")


def test_an_unverified_entry_does_not_satisfy_the_corpus(tmp_path):
    """`read_evidence` defaults to verified_only=True, and that default is the check.

    Measured non-equivalent with a HAND-PLACED entry, which is what the body sets up: with
    `verified_only=False` such a note counts here, the run proceeds, and it then fails the
    fabrication gate -- the exact token cost #242 removes. A PROPOSED entry witnesses nothing,
    at either setting: `propose_evidence` lands under `_inbox/`, which `read_evidence` cannot
    see at all. `_vault()` only ever creates VERIFIED entries, so nothing else here sees it.
    """
    import os

    v = _vault(tmp_path, baseline="# CV\n")
    # HAND-PLACED, not proposed: `propose_evidence` lands under `_inbox/`, which
    # `read_evidence` cannot see at all, so a pending entry is invisible either way and could
    # not witness this. A note the user wrote into the kind's own directory without a
    # `verified:` key is the case that separates the two -- and it is a first-class workflow,
    # since hand-editing the vault is supported.
    d = os.path.join(v.dir, "Job Applications", "Experience Library")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "alpha.md"), "w", encoding="utf-8") as fh:
        fh.write("---\ncompany: \"\"\n---\n\n# alpha\n")

    assert v.read_evidence("experience") == [], "an unverified entry is not citable"
    assert v.read_evidence("experience", verified_only=False), "precondition: it exists"

    assert any("verified experience" in m for m in missing_prerequisites(v)), (
        "a pending entry satisfied the corpus check, so an unverified vault would compose and "
        "then fail the gate")


def test_a_whitespace_only_baseline_is_not_a_baseline(tmp_path):
    """`.strip()` is doing work: a file of blank lines is a file, but not a CV to tailor."""
    v = _vault(tmp_path, baseline="\n   \n\t\n")
    assert any("no baseline CV at" in m for m in missing_prerequisites(v)), (
        "a whitespace-only baseline passed as real content")


def test_preflight_and_the_refusal_agree_about_a_blank_baseline(tmp_path):
    """The two halves of #242 must not disagree about the same vault.

    `missing_prerequisites` refuses on `not baseline.strip()`. `Vault.preflight`'s
    `baseline_exists` was existence-only, so a whitespace-only `My CV/CV.md` reported
    `baseline_rel  ok  found` while the very next `cv run` refused that vault -- the exact
    doctor-says-fine-while-cv-refuses contradiction the doctor commit exists to remove,
    surviving on the other half of the feature. Nothing bound them; this does.
    """
    v = _vault(tmp_path, baseline="\n   \n\t\n")
    assert v.preflight()["baseline_exists"] is False, (
        "preflight calls a whitespace-only baseline present while the refusal calls it absent")
    assert any("no baseline CV at" in m for m in missing_prerequisites(v))


def test_the_refusal_reaches_the_cli_as_exit_2(tmp_path, monkeypatch, capsys):
    """END TO END, because every doc that documents this documents the CODE.

    docs/USAGE.md and docs/AI-SETUP.md both state exit 2. Measured: a `cmd_cv_run` that
    caught the ValueError and returned 1 survived the entire suite while making both false --
    nothing asserted the number a user or an agent actually sees.
    """
    from sluice.cli import main

    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    rc = main(["cv", "run", "--all-shortlist"])
    out = capsys.readouterr()
    assert rc == 2, f"documented exit 2, got {rc}"
    assert "not set up to compose yet" in (out.out + out.err)
