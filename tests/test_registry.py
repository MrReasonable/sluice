import pytest

from sluice.ingest import sources


class _Dummy:
    id = "dummy"
    enabled = True
    kind = "test"


def test_register_and_get():
    # try/finally, matching tests/test_plugins.py's own registry-cleanup pattern:
    # `_REGISTRY` is a module-level global shared by the whole suite, and an
    # uncleaned `_Dummy` -- lacking `.searches()` -- used to crash any later test
    # that swept `registry.all_sources()` and called it, order-dependently.
    try:
        sources.register(_Dummy())
        assert sources.get("dummy").id == "dummy"
        assert any(s.id == "dummy" for s in sources.all_sources())
    finally:
        sources._REGISTRY.pop("dummy", None)


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        sources.get("nope-not-registered")


def test_register_returns_the_source():
    d = _Dummy()
    try:
        assert sources.register(d) is d
    finally:
        sources._REGISTRY.pop("dummy", None)
