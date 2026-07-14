"""The adapter registry and the composition root.

The behaviour that matters here is the FAILURE behaviour. A quiet wrong default is the
bug class this codebase most consistently engineers out, and for the store seam it means
writing someone's leads to a place they did not ask for.
"""
import pytest

from sluice.core import plugins
from sluice.core.app import Sluice
from sluice.core.config import Config


def test_unknown_adapter_raises_and_lists_the_valid_names():
    # Never a silent fallback to a default. The message must name what IS available,
    # because the whole point is that the operator can fix it without reading source.
    with pytest.raises(plugins.UnknownAdapter) as e:
        Sluice(Config(store="sqlite")).store()
    msg = str(e.value)
    assert "sqlite" in msg
    assert "vault" in msg, "the error must list the names that ARE registered"


def test_shipped_defaults_resolve():
    cfg = Config()
    assert cfg.store == "vault"
    assert cfg.fetcher == "camofox"
    assert "vault" in Sluice.available("store")
    assert "camofox" in Sluice.available("fetcher")
    assert {"script", "weasyprint"} <= set(Sluice.available("renderer"))


def test_store_is_resolved_from_config_by_name(tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    store = Sluice(Config()).store()
    assert store.read_leads() == []          # a real, working vault store


def test_adapters_are_resolved_lazily(monkeypatch):
    """Constructing Sluice must not construct a browser, a store or a backend.

    This is the property cli.py's inside-the-function imports were protecting: an offline
    command (`ingest list-sources`, `triage run --no-llm`) must never touch Camofox. If
    the composition root resolved eagerly, every offline command would start opening a
    browser and the offline tests would start needing one.
    """
    built = []
    Sluice.available("fetcher")   # force the autoload FIRST: _resolve would otherwise
                                  # re-import the package and overwrite the patch below
                                  # with the real factory, making this test pass only
                                  # when some earlier test had already done the import.
    monkeypatch.setitem(plugins._REGISTRY["fetcher"], "camofox",
                        lambda cfg: built.append("camofox") or object())
    app = Sluice(Config())
    assert built == [], "constructing Sluice must not build any adapter"
    app.fetcher()
    assert built == ["camofox"], "the fetcher is built on first use"
    app.fetcher()
    assert built == ["camofox"], "and cached, not rebuilt per call"


def test_override_bypasses_the_registry():
    sentinel = object()
    assert Sluice(Config(), store=sentinel).store() is sentinel


def test_a_broken_plugin_is_skipped_but_never_silently_substituted(tmp_path, caplog):
    """One bad plugin module is logged and skipped, so it cannot sink the registry -- the
    rule the source registry already follows. But its NAME must then be absent, and asking
    for it must RAISE rather than quietly resolving to a different implementation.

    The previous version of this test broke no plugin at all: it just asserted `get` raised
    on a name nobody had registered, duplicating the test above and leaving autoload's
    except-branch with zero coverage.
    """
    import sys, types
    pkg = types.ModuleType("_probe_pkg")
    pkg.__path__ = [str(tmp_path)]
    sys.modules["_probe_pkg"] = pkg
    (tmp_path / "healthy.py").write_text(
        "from sluice.core import plugins\n"
        "plugins.register('probe', 'healthy', lambda cfg: 'ok')\n")
    (tmp_path / "broken.py").write_text("raise RuntimeError('this plugin is broken')\n")
    try:
        with caplog.at_level("WARNING"):
            plugins.autoload(pkg)
        assert plugins.available("probe") == ["healthy"], "a broken sibling sank the registry"
        assert "broken" in caplog.text, "the broken plugin was skipped SILENTLY"
        with pytest.raises(plugins.UnknownAdapter):
            plugins.get("probe", "broken")   # absent, never substituted
    finally:
        sys.modules.pop("_probe_pkg", None)
        plugins._REGISTRY.pop("probe", None)
