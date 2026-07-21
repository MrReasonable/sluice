"""The cli driver itself: it runs main(argv) against the harness and captures
output, and its Sluice patch is a subclass (so classmethods survive)."""
import sluice.core.app as app_mod


def test_driver_runs_main_and_captures(cli):
    _harness, run = cli()
    rc, out, err = run(["ingest", "list-sources"])
    assert rc == 0
    assert "cord" in out          # a shipped source id, printed to stdout


def test_patch_is_a_subclass_so_classmethods_survive(cli):
    # arc-002: the patch MUST be a subclass, not a proxy -- Sluice's own methods
    # resolve the patched module global at call time (doctor() self-references
    # Sluice.available("backend")), so a bare callable would lose the staticmethod.
    real = app_mod.Sluice
    cli()  # applies the patch to app_mod.Sluice
    patched = app_mod.Sluice
    assert patched is not real
    assert issubclass(patched, real)
    assert patched.available is real.available   # inherited, not shadowed
