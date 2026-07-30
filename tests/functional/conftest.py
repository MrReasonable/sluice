"""Functional-tier driver: the real `main(argv)` against the shared harness.

The registry-isolation autouse fixture is shared with the e2e tier (imported from
`tests/harness/registry`). The `cli` fixture builds a hermetic harness and returns a
`(harness, run)` pair; `run(argv)` invokes the real CLI entrypoint -- parse, dispatch,
and the handler's own `Sluice(config)` -- capturing `(rc, out, err)`.

The one seam the CLI never exposes is the backend: handlers build `Sluice(config)`
bare and `main` passes no override, and registering a fake backend is barred
(`test_backend_registry` asserts set-equality). So the fixture patches
`sluice.core.app.Sluice` to a **subclass** whose `__init__` `setdefault`s the harness's
backend / a no-op sleep / an optional fixed clock, then calls `super()`. A subclass,
not a proxy, because `Sluice`'s own methods resolve the patched module global at call
time -- `doctor()` self-references `Sluice.available("backend")` -- so a bare callable
would lose the inherited staticmethod. It binds only composition-root seams (the
CLI-tier equivalent of `harness.sluice()`); all business logic stays real, and
backend-free handlers never touch the bound backend. It is deliberately NOT an
`app_factory=` param on `main` -- that would be the "capability reachable only from
tests" the #7 sweep exists to catch.
"""
import pytest

import sluice.core.app as app_mod
from sluice.cli import main
from tests.harness import build_harness
from tests.harness.config import harness_resolve
from tests.harness.initdriver import run_init  # noqa: F401  (fixture, requested by name)
from tests.harness.registry import isolate_plugin_registry  # noqa: F401  (autouse fixture)


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    """Factory: `cli(backend=..., **harness_kwargs) -> (harness, run)`.

    Pass a `ScriptedBackend` for handlers that reach an LLM (cv / triage / track);
    omit it for the offline handlers (it is filtered out as a `None` override). Any
    `build_harness` keyword (`target_locations`, `reject_titles`, `accept_titles`,
    pay floors, `board_url`/`rows`, ...) forwards through, so a test sets the gate
    config its scenario needs. `run(argv)` returns `(rc, out, err)`.
    """
    def _make(*, backend=None, today=None,
              board_url="https://harness.example.invalid/board", rows=None,
              **harness_kwargs):
        harness = build_harness(
            tmp_path, monkeypatch, board_url=board_url,
            rows=rows if rows is not None else [], **harness_kwargs)

        real_sluice = app_mod.Sluice

        class _HarnessSluice(real_sluice):
            def __init__(self, config=None, **kw):
                kw.setdefault("backend", backend)     # None is filtered by __init__
                kw.setdefault("sleep", lambda *a, **k: None)
                kw.setdefault("today", today)
                kw.setdefault("resolve_host", harness_resolve)
                super().__init__(config, **kw)

        monkeypatch.setattr(app_mod, "Sluice", _HarnessSluice)

        def run(argv):
            capsys.readouterr()  # drop anything captured before this call
            rc = main(argv)
            cap = capsys.readouterr()
            return rc, cap.out, cap.err

        return harness, run

    return _make
