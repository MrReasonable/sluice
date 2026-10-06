"""tests/harness/registry.py's snapshot reaches every seam (#364 spec §12.2)."""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_the_registry_snapshot_holds_every_seams_production_plugins():
    """In a FRESH interpreter, because that is the only place the ordering shows: inside
    the suite some earlier test has usually autoloaded every seam already, so a snapshot
    that forced only four would look complete here."""
    code = ("from sluice.core.app import _SEAMS\n"
            "from tests.harness.registry import _snapshot\n"
            "snap = _snapshot()\n"
            "missing = [s for s in _SEAMS if not snap.get(s)]\n"
            "assert not missing, missing\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
