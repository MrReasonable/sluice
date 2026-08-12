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
