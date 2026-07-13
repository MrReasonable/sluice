import pytest

from sluice.ingest import sources


class _Dummy:
    id = "dummy"
    enabled = True
    kind = "test"


def test_register_and_get():
    sources.register(_Dummy())
    assert sources.get("dummy").id == "dummy"
    assert any(s.id == "dummy" for s in sources.all_sources())


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        sources.get("nope-not-registered")


def test_register_returns_the_source():
    d = _Dummy()
    assert sources.register(d) is d
