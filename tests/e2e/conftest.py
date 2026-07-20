"""e2e-tier fixtures shared by the full-pipeline, re-scrape, and never-regress runs."""
import pytest

from sluice.core import plugins
from sluice.core.app import Sluice


@pytest.fixture(autouse=True)
def _isolate_plugin_registry():
    """Snapshot the adapter registry before each e2e test and restore it after.

    The harness registers its `scripted` fetcher and `recording` renderer through
    the real `plugins.register()` API (to exercise the config-key wiring). Those
    registrations are process-global and last-wins, so without a teardown they
    leak past the test -- re-registering (a WARNING) on every subsequent one, and,
    once a second PRODUCTION fetcher/renderer lands and its registry grows an
    exact-membership guard (mirroring store/backend), becoming an order hazard.

    Force the production plugins to autoload BEFORE snapshotting: `autoload` runs
    once per module import, so a snapshot taken before it and restored afterward
    would drop camofox/script/... permanently (the cached import never re-runs
    autoload). After the force, the snapshot holds every production impl, and
    restore removes only what a test added. Mirrors the copy-the-inner-dicts
    rollback `plugins.autoload` itself uses.
    """
    for seam in ("store", "fetcher", "renderer", "backend"):
        Sluice.available(seam)  # triggers each seam's one-time autoload

    snapshot = {seam: dict(impls) for seam, impls in plugins._REGISTRY.items()}
    yield
    plugins._REGISTRY.clear()
    plugins._REGISTRY.update(snapshot)
