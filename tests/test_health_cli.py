"""`job-sluice health` at the CLI layer: `cmd_health`'s printed output must be
byte-identical before and after the #105 refactor to call `Sluice.health_report()`.
`tests/test_health.py` tests `HealthStore` (and, after #105, `Sluice.health_report()`)
directly and never exercised `cmd_health` itself -- this is that CLI-level regression
test, matching this repo's `test_<command>_cli.py` convention
(`test_apply_record_cli.py`, `test_leads_dedupe_cli.py`)."""
from sluice.cli import _build_parser, cmd_health
from sluice.core.config import Config
from sluice.core.health import HealthStore
from sluice.ingest import sources as registry


def test_cmd_health_prints_one_line_per_source_with_baseline_and_recent(capsys):
    ids = sorted(s.id for s in registry.all_sources())
    assert len(ids) >= 2, "the real source registry enumerated fewer than two sources"
    first, second = ids[0], ids[-1]

    h = HealthStore()  # sandboxed into tmp_path by the autouse _pin_paths fixture
    h.record(first, 5)
    h.record(second, 0)
    h.record(second, 0)
    h.record(second, 0)  # three zero runs -> RETIRE

    args = _build_parser().parse_args(["health"])
    assert cmd_health(args, Config()) == 0

    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines() if ln.split()}
    # Exact-line equality for byte-identical output validation: check the primary source
    # with the exact format {id:16} baseline={baseline:.0f} recent={recent}{flag}
    assert lines[first] == f"{first:16} baseline=5 recent=[5]"
    # Substring check for RETIRE flag presence/absence: this is meaningfully falsifiable
    # as a presence/absence check even though it's not exact-line equality
    assert "RETIRE" not in lines[first]
    assert "RETIRE" in lines[second]


def test_cmd_health_leads_flag_reports_the_unjudgeable_rate(tmp_path, capsys):
    """`--leads` is the opt-in for `health_report`'s vault walk (#169 §2). The test above
    pins that OMITTING the flag stays byte-identical to before; this pins what changes
    when a caller does ask: the source's line grows an `unjudgeable=<N>/<M>` fragment."""
    ids = sorted(s.id for s in registry.all_sources())
    assert len(ids) >= 1, "the real source registry enumerated no sources"
    source = ids[0]

    leads = tmp_path / "vault" / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Alpha - Analyst.md").write_text(
        f'---\ncompany: "Alpha"\nrole: "Analyst"\nstatus: new\nsource: {source}\n---\n\nbody\n')
    (leads / "Beta - Analyst.md").write_text(
        f'---\ncompany: "Beta"\nrole: "Analyst"\nstatus: unjudgeable\nsource: {source}\n'
        "---\n\nbody\n")

    args = _build_parser().parse_args(["health", "--leads"])
    assert cmd_health(args, Config()) == 0

    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines() if ln.split()}
    assert "unjudgeable=1/2" in lines[source]


def test_cmd_health_without_leads_flag_never_shows_the_unjudgeable_fragment(tmp_path, capsys):
    """Without `--leads`, every `SourceHealth` reads the dataclass default `unjudgeable=0
    selected=0` -- printing that unconditionally would misrepresent "not measured" as
    "measured, and clean". Same fixture as the test above, but no `--leads`: the fragment
    must be absent entirely, not merely zero."""
    ids = sorted(s.id for s in registry.all_sources())
    source = ids[0]

    leads = tmp_path / "vault" / "Job Applications" / "Job Leads"
    leads.mkdir(parents=True)
    (leads / "Beta - Analyst.md").write_text(
        f'---\ncompany: "Beta"\nrole: "Analyst"\nstatus: unjudgeable\nsource: {source}\n'
        "---\n\nbody\n")

    args = _build_parser().parse_args(["health"])
    assert cmd_health(args, Config()) == 0

    lines = {ln.split()[0]: ln for ln in capsys.readouterr().out.splitlines() if ln.split()}
    assert "unjudgeable=" not in lines[source]
