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


_GOOD = ('company: "Northwind"\nrole: "Banker"\nstatus: shortlist\n'
         'url: "https://northwind.example/careers/em"\ntailored_cv: CV_deadbeef.pdf (2026-07-09)')


def test_eligibility_all_conditions_pass():
    v, cfg = _vault([("Northwind - Analyst.md", _GOOD)])
    note = v.read_leads({"shortlist"})[0]
    assert select.eligibility(note, cfg) == (True, "")


def test_eligibility_rejects_non_shortlist():
    v, cfg = _vault([("Applied.md", _GOOD.replace("status: shortlist", "status: applied"))])
    note = [n for n in v.read_leads() if n.fm["company"] == "Northwind"][0]
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
    v, cfg = _vault([("Northwind - Analyst.md", _GOOD)])
    note, reason = select.select_one(v, "northwind", cfg)
    assert reason == "" and note is not None


def test_select_one_refuses_ambiguous_shortlist_match():
    v, cfg = _vault([
        ("flowline - Analyst.md", _GOOD.replace("Northwind", "flowline")),
        ("flowlineRemote in London - Analyst.md", _GOOD.replace("Northwind", "flowlineRemote in London")),
    ])
    note, reason = select.select_one(v, "flowline", cfg)
    assert note is None and reason.startswith("ambiguous")


def test_select_one_no_match():
    v, cfg = _vault([("Northwind - Analyst.md", _GOOD)])
    note, reason = select.select_one(v, "zzz", cfg)
    assert note is None and reason == "no_match"


def test_select_all_partitions_eligible_and_skipped():
    v, cfg = _vault([
        ("Northwind - Analyst.md", _GOOD),
        ("Legacy.md", 'company: "B"\nrole: "Analyst"\nstatus: shortlist\nurl: "https://x/y"\ntailored_cv: "My CV/CV_B.pdf"'),
    ])
    eligible, skipped = select.select_all(v, cfg)
    assert [n.fm["company"] for n in eligible] == ["Northwind"]
    assert [(n.fm["company"], r) for n, r in skipped] == [("B", "no_artifact")]
