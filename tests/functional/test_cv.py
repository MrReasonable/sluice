"""cv handler through the real main(argv). Re-homed from tests/test_cv_cli.py.

The two parser assertions are kept verbatim; added is a real compose through the
scripted backend and the no-match rc-1 branch -- cmd_cv_run's own handler logic
(rendered-path stderr, the "no shortlist lead" refusal) is exercised by nothing
else (e2e drives Sluice.compose_cv directly, never cli.py).
"""
import os

from sluice.cli import _build_parser
from tests.harness import PASSING_CV, ScriptedBackend


def test_cv_run_parses_lead_and_flags():
    args = _build_parser().parse_args(
        ["cv", "run", "--lead", "acme-em", "--dry-run", "--backend", "deepseek"])
    assert args.group == "cv" and args.cmd == "run"
    assert args.lead == "acme-em" and args.dry_run and args.backend == "deepseek"


def test_cv_run_parses_all_shortlist():
    args = _build_parser().parse_args(["cv", "run", "--all-shortlist", "--limit", "3"])
    assert args.all_shortlist and args.limit == 3


def _seed_shortlist_lead(vault_dir, company, role):
    leads = os.path.join(vault_dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    fm = [f'company: "{company}"', f'role: "{role}"', "status: shortlist",
          'url: "https://example.invalid/jobs/1"', "score: 80", 'relevance_notes: ""']
    with open(os.path.join(leads, f"{company} - {role}.md"), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm) + "\n---\n# body\n")


def test_cv_run_composes_and_renders(cli):
    # Company "Example Foundry" -> [EF1] via the harness prefix_map; PASSING_CV cites
    # [EF1] and its numbers appear in the seeded Experience entry, so the gate passes.
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_CV})
    h, run = cli(backend=backend)
    _seed_shortlist_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    rc, _out, err = run(["cv", "run", "--lead", "example-foundry"])
    assert rc == 0
    assert "cv:" in err and "rendered" in err              # cmd_cv_run's stderr line
    assert h.recorder.rendered == [PASSING_CV]             # the gate passed, it rendered


def test_cv_run_no_matching_lead_returns_1(cli):
    # A never-called backend, only so compose_cv's up-front backend build (before the
    # empty-notes check, app.py:351) does not hit real construction.
    h, run = cli(backend=ScriptedBackend())
    rc, _out, err = run(["cv", "run", "--lead", "no-such-lead"])
    assert rc == 1
    assert "no shortlist lead matching" in err


def test_cv_run_shipped_default_name_returns_1(cli):
    """#99: the harness pins `cv.name: "Jane Roe"` by default (see harness/config.py's
    own neutrality note) precisely so every OTHER functional test in this file composes
    for real -- this is the one test that deliberately reverts that override back to the
    shipped placeholder, through the real CLI, to prove cmd_cv_run's own exit-code
    handling for it (compose_cv's refusal is covered at the engine level already;
    this covers the handler wiring on top of it, the same split test_cv_run_ambiguous_
    lead_composes_for_neither_and_returns_1 draws for the ambiguous case).
    """
    from sluice.cv.config import CvConfig
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_CV})
    h, run = cli(backend=backend, cv_name=CvConfig().name)
    _seed_shortlist_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    rc, _out, err = run(["cv", "run", "--lead", "example-foundry"])
    assert rc == 1
    assert "cv.name" in err and "Your Name" in err
    assert h.recorder.rendered == []           # nothing composed, nothing rendered
    assert backend.prompts == []               # and the refusal cost no LLM call


def test_cv_run_ambiguous_lead_composes_for_neither_and_returns_1(cli):
    """A `--lead` fragment matching TWO shortlist notes must refuse, not compose for
    whichever the store listed first (#1).

    Asserted through the CLI's EXIT CODE, not just the returned list: `compose_cv` could
    refuse correctly and `cmd_cv_run` still print the skip row among the ordinary ones and
    exit 0, which is what `leads expire` shipped with when it gained its own `ambiguous`
    outcome. Both halves have to hold, so both are asserted here.

    The two notes carry DIFFERENT slugs -- `slug_matches` is a substring match, so the
    typed fragment is what is ambiguous, not the slug. This is therefore reachable on a
    flat store too; the recursive scan only widens it.

    The second half is the mirror-harm check, and it is run against the SAME two-note
    vault: naming one of the twins precisely still composes and renders. A guard that
    refused whenever a vault held look-alike leads would silently block real work.
    """
    backend = ScriptedBackend(cv_by_company={"Example Foundry": PASSING_CV})
    h, run = cli(backend=backend)
    _seed_shortlist_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    _seed_shortlist_lead(h.paths["vault"], "Example Foundry", "Senior Engineer")

    rc, _out, err = run(["cv", "run", "--lead", "example-foundry"])
    assert rc == 1
    assert "ambiguous" in err
    # BOTH candidates named, so the user can retype a longer fragment -- one ref would
    # name only the twin the tool happened to pick, which is the guess being refused.
    assert "Staff Engineer" in err and "Senior Engineer" in err
    assert h.recorder.rendered == []          # nothing composed, nothing rendered
    assert backend.prompts == []              # and the refusal cost no LLM call
    for role in ("Staff Engineer", "Senior Engineer"):
        assert "tailored_cv:" not in _lead_text(h.paths["vault"], "Example Foundry", role)

    # Mirror harm: an unambiguous fragment still composes, twins present or not.
    rc, _out, err = run(["cv", "run", "--lead", "staff-engineer"])
    assert rc == 0 and "rendered" in err
    assert h.recorder.rendered == [PASSING_CV]
    assert "tailored_cv:" in _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    # ...and only the named twin was touched.
    assert "tailored_cv:" not in _lead_text(h.paths["vault"], "Example Foundry",
                                            "Senior Engineer")


# --- #60 `sluice cv signoff` -------------------------------------------------

def _seed_pending_lead(vault_dir, company, role, *, pending="CV_ab12.pdf (2026-07-24)"):
    leads = os.path.join(vault_dir, "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    fm = [f'company: "{company}"', f'role: "{role}"', "status: shortlist",
          'url: "https://example.invalid/jobs/1"',
          f"pending_cv: {pending}",
          'needs_signoff: ["unsupported\\tMotivated by placeholder\\tNONE"]']
    with open(os.path.join(leads, f"{company} - {role}.md"), "w", encoding="utf-8") as f:
        f.write("---\n" + "\n".join(fm) + "\n---\n# body\n")


def _lead_text(vault_dir, company, role):
    with open(os.path.join(vault_dir, "Job Applications", "Job Leads",
                           f"{company} - {role}.md"), encoding="utf-8") as f:
        return f.read()


def test_cv_signoff_parses_lead_discard_yes():
    args = _build_parser().parse_args(
        ["cv", "signoff", "--lead", "acme-em", "--discard", "--yes"])
    assert args.group == "cv" and args.cmd == "signoff"
    assert args.lead == "acme-em" and args.discard and args.yes


def test_cv_signoff_promotes_with_yes(cli):
    # signoff needs no backend: it resolves the lead and calls store.sign_off. --yes
    # skips the interactive confirm.
    h, run = cli(backend=ScriptedBackend())
    _seed_pending_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry", "--yes"])
    assert rc == 0 and "promoted" in err
    text = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "tailored_cv: CV_ab12.pdf (2026-07-24)" in text   # promoted -> apply can see it
    assert "pending_cv:" not in text and "needs_signoff:" not in text


def test_cv_signoff_discard_clears_without_promoting(cli):
    h, run = cli(backend=ScriptedBackend())
    _seed_pending_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry", "--discard"])
    assert rc == 0 and "discarded" in err
    text = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "tailored_cv:" not in text                        # discard never promotes
    assert "pending_cv:" not in text and "needs_signoff:" not in text


def test_cv_signoff_nothing_pending(cli):
    """`nothing` exits 1: the named lead exists but holds no pending_cv, so no write
    happened -- `cmd_leads_expire`'s `_FAILED` rule, which is keyed on exactly that.

    The exit code is the assertion that matters. `sign_off_cv` returning `nothing` while
    `cmd_cv_signoff` printed it and exited 0 is the shape `leads expire` shipped with, and
    the harm here is concrete: the seeded note carries NO `tailored_cv` either, so an exit 0
    tells `sluice cv signoff --lead X && sluice apply prep --lead X` that a CV is send-ready
    when none exists.
    """
    h, run = cli(backend=ScriptedBackend())
    _seed_shortlist_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")  # no pending_cv
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry", "--yes"])
    assert rc == 1 and "nothing" in err.lower()
    assert "tailored_cv:" not in _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")


def test_cv_signoff_collision_exits_zero_because_the_write_happened(cli):
    """`collision` exits 0: classified on whether a write happened, not on the word.

    A `tailored_cv` appeared while the hold stood (a direct `set_tailored_cv`), so
    `Vault.sign_off` keeps that pointer and clears the markers -- changed text, so
    `_cas_write` commits. Post-state: hold gone, lead send-ready. That is the postcondition
    a caller gates on, and `nothing` (rc 1 above) is the case that fails to establish it, so
    the two exit codes differ for a reason a caller can act on rather than by vocabulary.
    """
    h, run = cli(backend=ScriptedBackend())
    leads = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, "Example Foundry - Staff Engineer.md"), "w",
              encoding="utf-8") as f:
        f.write('---\ncompany: "Example Foundry"\nrole: "Staff Engineer"\nstatus: shortlist\n'
                'url: "https://example.invalid/1"\npending_cv: CV_pending.pdf (2026-07-24)\n'
                'needs_signoff: ["unsupported\\tMotivated by placeholder\\tNONE"]\n'
                'tailored_cv: CV_already.pdf (2026-07-25)\n---\n# body\n')
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry", "--yes"])
    assert rc == 0 and "collision" in err
    text = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "tailored_cv: CV_already.pdf (2026-07-25)" in text   # kept, never clobbered
    assert "CV_pending.pdf" not in text                         # and not promoted over it
    assert "pending_cv:" not in text and "needs_signoff:" not in text   # the write: hold gone


def test_cv_signoff_conflict_returns_1(cli, monkeypatch):
    """`conflict` exits 1: a sustained write race (#16) means the user asked for a write and
    did not get one, and the #60 hold is still held.

    Forced at the store method rather than by racing a real vault, so the test is
    deterministic; `sign_off_cv`'s `except VaultConflict` arm and this classification are
    what it exercises. The note is asserted UNCHANGED afterwards, which is what makes the
    non-zero right: the lead is not send-ready and a script must not proceed to `apply`.
    """
    from sluice.core.protocols import VaultConflict
    from sluice.core.vault import Vault

    h, run = cli(backend=ScriptedBackend())
    _seed_pending_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")

    def _always_conflicts(self, ref, *, accept=True):
        raise VaultConflict(ref)

    monkeypatch.setattr(Vault, "sign_off", _always_conflicts)
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry", "--yes"])
    assert rc == 1 and "conflict" in err
    text = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "pending_cv:" in text and "tailored_cv:" not in text   # hold intact, nothing sent


def test_cv_signoff_no_match_returns_1(cli):
    h, run = cli(backend=ScriptedBackend())
    rc, _out, err = run(["cv", "signoff", "--lead", "no-such-lead", "--yes"])
    assert rc == 1 and "no shortlist lead matching" in err


def test_cv_signoff_ambiguous_lead_refuses_and_returns_1(cli):
    """A `--lead` fragment matching TWO held leads must refuse, not release the #60 hold on
    whichever the store listed first -- that would promote a CV no human reviewed onto a
    lead the user did not name, and `apply prep` reads that pointer.

    Exit code asserted, not just the (slug, outcome) tuple: `sign_off_cv` returning
    `ambiguous` while `cmd_cv_signoff` printed it and exited 0 would tell a script the CV
    is send-ready. Asserting the tuple alone is exactly what let that gap through on
    `leads expire`.

    The second half is the load-bearing one for #60: the latch must stay RESOLVABLE.
    `cv signoff` is the sanctioned way out of a `pending_cv` hold, so a refusal that
    survived a longer fragment would strand both CVs with no route out but hand-editing
    frontmatter.
    """
    h, run = cli(backend=ScriptedBackend())
    _seed_pending_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    _seed_pending_lead(h.paths["vault"], "Example Foundry", "Senior Engineer")

    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry", "--yes"])
    assert rc == 1
    assert "ambiguous" in err
    assert "Staff Engineer" in err and "Senior Engineer" in err   # both candidates named
    for role in ("Staff Engineer", "Senior Engineer"):
        text = _lead_text(h.paths["vault"], "Example Foundry", role)
        assert "pending_cv:" in text and "tailored_cv:" not in text   # both holds intact

    # Mirror harm / #60 latch: naming one twin precisely still promotes it, and only it.
    rc, _out, err = run(["cv", "signoff", "--lead", "staff-engineer", "--yes"])
    assert rc == 0 and "promoted" in err
    promoted = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "tailored_cv:" in promoted and "pending_cv:" not in promoted
    held = _lead_text(h.paths["vault"], "Example Foundry", "Senior Engineer")
    assert "pending_cv:" in held and "tailored_cv:" not in held


def test_cv_signoff_shows_raw_claim_when_needs_signoff_is_not_json(cli, monkeypatch):
    # A malformed needs_signoff (not JSON) must not crash the review: peek_signoff falls
    # back to the raw string so the candidate still sees something to sign off on.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    h, run = cli(backend=ScriptedBackend())
    leads = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    with open(os.path.join(leads, "Example Foundry - Staff Engineer.md"), "w", encoding="utf-8") as f:
        f.write('---\ncompany: "Example Foundry"\nrole: "Staff Engineer"\nstatus: shortlist\n'
                'url: "https://example.invalid/1"\npending_cv: CV_x.pdf (2026-07-24)\n'
                'needs_signoff: not valid json\n---\n# body\n')
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry"])
    assert rc == 0 and "not valid json" in err   # raw fallback shown, no crash
    # ...and the "n" abort wrote nothing: the hold is intact, no tailored_cv promoted.
    text = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "pending_cv: CV_x.pdf (2026-07-24)" in text and "tailored_cv:" not in text


def test_cv_signoff_prompt_aborts_on_no(cli, monkeypatch):
    # Without --yes an accept must review + confirm; a "no" leaves the hold intact.
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    h, run = cli(backend=ScriptedBackend())
    _seed_pending_lead(h.paths["vault"], "Example Foundry", "Staff Engineer")
    rc, _out, err = run(["cv", "signoff", "--lead", "example-foundry"])
    assert rc == 0 and "abort" in err.lower()
    text = _lead_text(h.paths["vault"], "Example Foundry", "Staff Engineer")
    assert "pending_cv:" in text and "tailored_cv:" not in text   # unchanged
    assert "Motivated by placeholder" in err   # the flagged claim was shown for review
