import tempfile, pathlib
from sluice.core.vault import Vault
from sluice.apply.config import ApplyConfig
from sluice.apply import select


def _vault(notes):
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads"); leads.mkdir(parents=True)
    served = pathlib.Path(root, "documents"); served.mkdir()
    for fname, fm in notes:
        (leads / fname).write_text("---\n" + fm + "\n---\n\nBODY\n")
    cfg = ApplyConfig(served_dir=str(served), camofox_upload_dir=str(pathlib.Path(root, "up")))
    # a real served PDF for the "good" artifact
    (served / "CV_deadbeef.pdf").write_bytes(b"%PDF-1.4\nx")
    return Vault(root), cfg


_GOOD = ('company: "Example Northgate"\nrole: "Banker"\nstatus: shortlist\n'
         'url: "https://example-northgate.invalid/careers/em"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')


def test_eligibility_all_conditions_pass():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg) == (True, "")


def test_eligibility_rejects_non_shortlist():
    v, cfg = _vault([("Applied.md", _GOOD.replace("status: shortlist", "status: applied"))])
    note = [n for n in v.read_leads() if n.fm["company"] == "Example Northgate"][0]
    assert select.eligibility(note, cfg) == (False, "not_shortlist")


def test_eligibility_reasons():
    v, cfg = _vault([
        ("NoUrl.md", 'company: "A"\nrole: "Analyst"\nstatus: shortlist\ntailored_cv: CV_deadbeef.pdf (2026-07-09)'),
        ("Legacy.md", 'company: "B"\nrole: "Analyst"\nstatus: shortlist\nurl: "https://x/y"\ntailored_cv: "My CV/CV_B.pdf"'),
        ("Skipped.md", 'company: "C"\nrole: "Analyst"\nstatus: shortlist\nurl: "https://x/y"\ntailored_cv: "SKIPPED - too senior"'),
        ("Missing.md", 'company: "D"\nrole: "Analyst"\nstatus: shortlist\nurl: "https://x/y"\ntailored_cv: CV_facef00d.pdf (2026-07-09)'),
    ])
    by = {p.slug + ".md": select.eligibility(p, cfg) for p in v.read_leads({"shortlist"})}
    assert by["NoUrl.md"] == (False, "no_url")
    assert by["Legacy.md"] == (False, "no_artifact")
    assert by["Skipped.md"] == (False, "no_artifact")
    assert by["Missing.md"] == (False, "missing_file")


def test_select_one_resolves_single_eligible():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    note, reason = select.select_one(v, "northgate", cfg)
    assert reason == "" and note is not None


def test_select_one_refuses_ambiguous_shortlist_match():
    v, cfg = _vault([
        ("Example Meridian - Analyst.md", _GOOD.replace("Example Northgate", "Example Meridian")),
        ("Example MeridianRemote - Analyst.md", _GOOD.replace("Example Northgate", "Example MeridianRemote")),
    ])
    note, reason = select.select_one(v, "meridian", cfg)
    assert note is None and reason.startswith("ambiguous")


def test_select_one_no_match():
    v, cfg = _vault([("Example Northgate - Analyst.md", _GOOD)])
    note, reason = select.select_one(v, "zzz", cfg)
    assert note is None and reason == "no_match"


def test_select_all_partitions_eligible_and_skipped():
    v, cfg = _vault([
        ("Example Northgate - Analyst.md", _GOOD),
        ("Legacy.md", 'company: "B"\nrole: "Analyst"\nstatus: shortlist\nurl: "https://x/y"\ntailored_cv: "My CV/CV_B.pdf"'),
    ])
    eligible, skipped = select.select_all(v, cfg)
    assert [n.fm["company"] for n in eligible] == ["Example Northgate"]
    assert [(n.fm["company"], r) for n, r in skipped] == [("B", "no_artifact")]


# ── #9: the staleness gate ───────────────────────────────────────────────────
from sluice.core.leads import StalenessPolicy   # noqa: E402

_POLICY = StalenessPolicy(ttl_days=90, today="2026-07-27")
_STALE = _GOOD + "\nlast_seen: 2026-01-01"
_FRESH = _GOOD + "\nlast_seen: 2026-07-20"


def test_eligibility_refuses_a_stale_lead():
    v, cfg = _vault([("Example Northgate - Analyst.md", _STALE)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg, _POLICY) == (False, "stale")


def test_eligibility_reports_stale_rather_than_no_artifact():
    # A stale lead must not be reported as `no_artifact`: that sends the user to run
    # `cv run`, which would itself refuse it, for a reason the message never mentioned.
    no_cv = _STALE.replace("tailored_cv: CV_deadbeef.pdf (2026-07-09)", "")
    v, cfg = _vault([("Example Northgate - Analyst.md", no_cv)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg, _POLICY) == (False, "stale")


def test_eligibility_default_policy_abstains():
    v, cfg = _vault([("Example Northgate - Analyst.md", _STALE)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg) == (True, "")


def test_eligibility_include_stale_passes_a_stale_lead():
    p = StalenessPolicy(ttl_days=90, today="2026-07-27", include_stale=True)
    v, cfg = _vault([("Example Northgate - Analyst.md", _STALE)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg, p) == (True, "")


def test_fresh_lead_is_unaffected():
    v, cfg = _vault([("Example Northgate - Analyst.md", _FRESH)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg, _POLICY) == (True, "")


def test_select_one_reports_stale():
    v, cfg = _vault([("Example Northgate - Analyst.md", _STALE)])
    assert select.select_one(v, "Example Northgate", cfg, _POLICY) == (None, "stale")


def test_select_all_reports_stale():
    v, cfg = _vault([("Example Northgate - Analyst.md", _STALE)])
    eligible, skipped = select.select_all(v, cfg, _POLICY)
    assert eligible == []
    assert [reason for _, reason in skipped] == ["stale"]


# ── #1: the batch path and a slug two notes claim ────────────────────────────
def _vault_subfolders(notes):
    """Like _vault, but each entry is (subdir, filename, fm) so two notes can claim ONE
    slug. The recursive scan (#1) admits that state; a flat store could not, since
    `_slug_for` is the basename and one directory cannot hold two files at one name."""
    root = tempfile.mkdtemp()
    leads = pathlib.Path(root, "Job Applications", "Job Leads")
    served = pathlib.Path(root, "documents"); served.mkdir(parents=True)
    for sub, fname, fm in notes:
        d = leads / sub if sub else leads
        d.mkdir(parents=True, exist_ok=True)
        (d / fname).write_text("---\n" + fm + "\n---\n\nBODY\n")
    cfg = ApplyConfig(served_dir=str(served), camofox_upload_dir=str(pathlib.Path(root, "up")))
    # A REAL served PDF. Without it both twins fail eligibility at `missing_file` and never
    # reach the ambiguity check at all, so the test would pass with the guard deleted --
    # the exact false negative this fixture exists to rule out.
    (served / "CV_deadbeef.pdf").write_bytes(b"%PDF-1.4\nx")
    return Vault(root), cfg


_TWIN = ('company: "Example"\nrole: "Analyst"\nstatus: shortlist\n'
         'url: "https://example.invalid/careers/1"\n'
         'tailored_cv: CV_deadbeef.pdf (2026-07-09)')


def test_select_all_refuses_a_slug_two_notes_claim():
    """The batch path must not list one job twice. select_one already refuses this state;
    select_all iterates notes rather than keying on slug, so the slug-keyed fixes in track
    and `leads expire` did not reach it -- both twins were eligible, and select_all's one
    caller (`engine.preview_all`, behind `apply prep --all-shortlist`) printed the same job
    twice in the ready queue a human works down. That path stages nothing, so this is a
    report defect rather than a duplicate send.

    Witnessed by deleting the `if n.slug in ambiguous:` arm from select_all: eligible comes
    back with BOTH twins and this goes red on the eligible assertion."""
    v, cfg = _vault_subfolders([
        ("Active", "Example - Analyst.md", _TWIN),
        ("Archive", "Example - Analyst.md", _TWIN),
    ])
    # The state is real: both twins ARE eligible on every other axis, so nothing but the
    # ambiguity guard is keeping them out.
    assert [select.eligibility(n, cfg) for n in v.read_leads({"shortlist"})] \
        == [(True, ""), (True, "")]
    eligible, skipped = select.select_all(v, cfg)
    assert eligible == []
    assert len(skipped) == 2
    assert all(r.startswith("ambiguous: ") for _, r in skipped)
    # The reason NAMES both colliding notes, which is the only actionable content: the
    # slug alone is already the lead label preview_all prints.
    assert all("Active" in r and "Archive" in r for _, r in skipped)


def test_select_all_still_sends_an_unambiguous_lead():
    """The mirror harm. A guard that refuses too broadly silently suppresses a real
    application, so pin that one unambiguous shortlist lead with a resolvable CV is still
    eligible -- including alongside a colliding pair, which must not take it down with
    them."""
    v, cfg = _vault_subfolders([
        ("Active", "Example - Analyst.md", _TWIN),
        ("Archive", "Example - Analyst.md", _TWIN),
        ("Active", "Example - Engineer.md", _TWIN.replace("Analyst", "Engineer")),
    ])
    eligible, skipped = select.select_all(v, cfg)
    assert [n.slug for n in eligible] == ["Example - Engineer"]
    assert sorted(n.slug for n, _ in skipped) == ["Example - Analyst", "Example - Analyst"]
