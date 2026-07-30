"""e2e-tier fixtures shared by the full-pipeline, re-scrape, and never-regress runs.

The adapter-registry isolation is shared with the functional tier, so it lives in
`tests/harness/registry.py`; importing it here re-exports it as an autouse fixture
for `tests/e2e/`.
"""
from tests.harness.initdriver import run_init  # noqa: F401  (fixture, requested by name)
from tests.harness.registry import isolate_plugin_registry  # noqa: F401  (autouse fixture)
