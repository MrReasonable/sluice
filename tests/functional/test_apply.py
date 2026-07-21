"""apply handlers through the real main(argv). Re-homed from tests/test_apply_cli.py.

The four parser assertions are kept (identity values re-expressed to neutral
conventions -- `--ats greenhouse` -> `example-ats`, a real ATS name per #55). The six
command tests move from direct cmd_*(SimpleNamespace, None) calls to real dispatch,
including the load-bearing wording pin: prep --dry-run must print literal "dry-run" on
stderr and NEVER leak Sluice.prep's internal "previewed" status.
"""
import os

import pytest

from sluice.cli import _build_parser


# ── parser wiring (kept from test_apply_cli.py) ──────────────────────────────
def test_apply_prep_parses_lead_and_flags():
    args = _build_parser().parse_args(
        ["apply", "prep", "--lead", "example-systems", "--json", "--dry-run"])
    assert args.group == "apply" and args.cmd == "prep"
    assert args.lead == "example-systems" and args.json and args.dry_run


def test_apply_prep_parses_all_shortlist():
    args = _build_parser().parse_args(["apply", "prep", "--all-shortlist", "--limit", "5"])
    assert args.all_shortlist and args.limit == 5


def test_apply_prep_requires_lead_or_all_shortlist():
    with pytest.raises(SystemExit):        # the mutually-exclusive group is required
        _build_parser().parse_args(["apply", "prep"])


def test_apply_record_parses_args():
    args = _build_parser().parse_args(
        ["apply", "record", "--lead", "example-systems",
         "--ats", "example-ats", "--url", "https://example.invalid/apply"])
    assert args.group == "apply" and args.cmd == "record"
    assert args.lead == "example-systems"
    assert args.ats == "example-ats" and args.url == "https://example.invalid/apply"


# ── the handlers, over the harness vault ─────────────────────────────────────
def _seed_ready_lead(h):
    """A shortlisted lead with a staged tailored_cv sitting in the served dir, ready
    for `apply prep` to stage into the upload dir."""
    served = h.paths["cv_served"]
    os.makedirs(served, exist_ok=True)
    os.makedirs(h.paths["apply_upload"], exist_ok=True)
    with open(os.path.join(served, "CV_deadbeef.pdf"), "wb") as f:
        f.write(b"%PDF-1.4\nx")
    leads = os.path.join(h.paths["vault"], "Job Applications", "Job Leads")
    os.makedirs(leads, exist_ok=True)
    fm = ('company: "Example Systems"\nrole: "Analyst"\nstatus: shortlist\n'
          'url: "https://example.invalid/x"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')
    with open(os.path.join(leads, "Example Systems - Analyst.md"), "w", encoding="utf-8") as f:
        f.write("---\n" + fm + "\n---\n\nBODY\n")


def _uploaded(h):
    return os.path.exists(os.path.join(h.paths["apply_upload"], "CV.pdf"))


def test_apply_prep_lead_stages_and_prints(cli):
    h, run = cli()
    _seed_ready_lead(h)
    rc, out, _err = run(["apply", "prep", "--lead", "example-systems"])
    assert rc == 0
    assert _uploaded(h)                        # the tailored CV was staged for upload
    assert "APPLICATION PACKET" in out


def test_apply_prep_all_shortlist_stages_no_cv(cli):
    h, run = cli()
    _seed_ready_lead(h)
    rc, _out, _err = run(["apply", "prep", "--all-shortlist"])
    assert rc == 0
    assert not _uploaded(h)                     # preview mode stages nothing


def test_apply_record_flips_status(cli):
    h, run = cli()
    _seed_ready_lead(h)
    rc, _out, _err = run(["apply", "record", "--lead", "example-systems", "--ats", "example-ats"])
    assert rc == 0
    note = os.path.join(h.paths["vault"], "Job Applications", "Job Leads",
                        "Example Systems - Analyst.md")
    with open(note, encoding="utf-8") as f:
        assert "status: applied" in f.read()    # shortlist -> applied, the one apply transition


def test_apply_prep_dry_run_touches_no_filesystem(cli):
    h, run = cli()
    _seed_ready_lead(h)
    rc, out, err = run(["apply", "prep", "--lead", "example-systems", "--dry-run"])
    assert rc == 0
    assert not _uploaded(h)                     # dry-run stages no CV
    assert "APPLICATION PACKET" in out
    # THE wording pin: Sluice.prep(dry_run=True) returns status "previewed", but the
    # CLI's dry-run wording predates that API and must stay literal "dry-run".
    assert "apply-prep: example-systems dry-run" in err
    assert "previewed" not in err


def test_apply_prep_no_match_returns_1(cli):
    h, run = cli()
    _seed_ready_lead(h)
    rc, _out, err = run(["apply", "prep", "--lead", "zzz-nomatch"])
    assert rc == 1
    assert "skipped" in err                     # the no-match digest goes to stderr


def test_apply_record_no_match_returns_1(cli):
    h, run = cli()
    _seed_ready_lead(h)
    rc, _out, err = run(["apply", "record", "--lead", "zzz-nomatch"])
    assert rc == 1
    assert "refused" in err
