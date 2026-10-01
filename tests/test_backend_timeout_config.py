"""`backend_timeout`: the root knob sizing every backend call that is not a CV composition.

Since #337 the HTTP backends treat a timeout as a TOTAL deadline rather than a per-read one, so
a provider that queues a request and then answers slowly can fail where it used to finish late.
Before this key, triage and track had no way to raise that bound short of editing code --
`cv.compose_timeout` covered composition only. These rows pin the loader's contract and, from
each stage's own entry point, that the value is what reaches backend construction.
"""
import pytest

from sluice.core.app import Sluice
from sluice.core.backends import DEFAULT_TIMEOUT
from sluice.core.config import Config, load_config


def _cfg(tmp_path, monkeypatch, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    return load_config(None)


# ── the loader ────────────────────────────────────────────────────────────────


def test_the_default_is_the_shipped_timeout(monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert Config().backend_timeout == DEFAULT_TIMEOUT
    assert load_config(None).backend_timeout == DEFAULT_TIMEOUT


def test_a_configured_value_round_trips(tmp_path, monkeypatch):
    # A distinct sentinel: a loader that ignored the key would still read 300 and pass a row
    # asserting the default alone.
    assert _cfg(tmp_path, monkeypatch, "backend_timeout: 1234\n").backend_timeout == 1234


def test_a_valueless_key_keeps_the_default(tmp_path, monkeypatch):
    # `backend_timeout:` with nothing after it parses as None, an ordinary half-edited line.
    assert _cfg(tmp_path, monkeypatch, "backend_timeout:\n").backend_timeout == DEFAULT_TIMEOUT


@pytest.mark.parametrize("value", ["yes", "on", "true", "True"])
def test_yaml_booleans_are_refused(tmp_path, monkeypatch, value):
    # bool subclasses int and PyYAML reads yes/on/true as True: without the bool check first,
    # `backend_timeout: yes` loads as a ONE-SECOND deadline and every backend call fails.
    with pytest.raises(ValueError, match="backend_timeout"):
        _cfg(tmp_path, monkeypatch, f"backend_timeout: {value}\n")


@pytest.mark.parametrize("value", ["0", "-5", "'300'", "1.5", "[300]"])
def test_non_positive_and_non_integer_values_are_refused(tmp_path, monkeypatch, value):
    # No "off": 0 would mean every call dies at once, not that the deadline is disabled.
    with pytest.raises(ValueError, match="backend_timeout must be a positive integer"):
        _cfg(tmp_path, monkeypatch, f"backend_timeout: {value}\n")


# ── the wiring, from each stage's own entry point ─────────────────────────────


class _Stop(Exception):
    pass


@pytest.fixture
def built(monkeypatch):
    """The timeout each backend was CONSTRUCTED with -- recorded at `_build_backend`, below
    `Sluice.backend()`, because the resolution under test happens inside that method and a
    spy above it would see only what the stage passed."""
    import sluice.core.app as A
    rec = []

    def spy(*a, timeout=None, **k):
        rec.append(timeout)
        raise _Stop()

    monkeypatch.setattr(A, "_build_backend", spy)
    return rec


def _run(fn):
    try:
        fn()
    except _Stop:
        pass


def test_triage_builds_its_backend_with_backend_timeout(built, tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    _run(lambda: Sluice(Config(backend_timeout=1234)).triage())
    assert built == [1234]


def test_track_builds_its_backend_with_backend_timeout(built):
    _run(lambda: Sluice(Config(backend_timeout=1234)).track(client=object()))
    assert built == [1234]


def test_cv_keeps_its_own_compose_timeout(built, tmp_path, monkeypatch):
    """Composition has its own, larger job and its own key; the root knob must not override it."""
    import dataclasses

    from sluice.core.vault import Vault
    from sluice.cv.config import load_cv_config
    from tests.conftest import make_composable
    cvc = dataclasses.replace(load_cv_config(), compose_timeout=4321,
                              backend="claude-max", model="m")
    monkeypatch.setattr("sluice.cv.config.load_cv_config", lambda: cvc)
    make_composable(Vault(str(tmp_path / "vault")))

    _run(lambda: Sluice(Config(backend_timeout=1234)).compose_cv(all_shortlist=True,
                                                                 dry_run=True))
    assert built == [4321]


def test_an_explicit_timeout_still_wins():
    """`Sluice.backend(timeout=...)` named by a caller is taken as given; only an omitted one
    falls back to the root knob."""
    def b(**kw):
        return Sluice(Config(backend_timeout=1234)).backend(
            provider="claude-max", model="m", effort="max", host="", claude_path="claude",
            **kw)

    assert b().inner.timeout == 1234
    assert b(timeout=900).inner.timeout == 900


# ── importing the CLI must not load a backend ─────────────────────────────────


def test_importing_the_cli_loads_no_backend_module():
    """`cli.py` imports heavy modules inside command functions so an offline command never
    touches a backend -- but it imports `load_config` at module scope, so anything
    `core/config.py` imports loads on EVERY invocation. Reading the default from
    `core/backends.py` there did exactly that, pulling the backend module and `subprocess` in
    with it. Measured in a fresh interpreter, since this process has long since imported both."""
    import importlib.util
    import subprocess
    import sys
    watched = ("sluice.core.backends", "sluice.backends")
    # Both names must still resolve, or a rename leaves this row asserting the absence of a
    # module that no longer exists anywhere.
    assert all(importlib.util.find_spec(m) for m in watched), watched
    probe = ("import sys, sluice.cli; "
             "print(sorted(m for m in ('sluice.core.backends', 'sluice.backends') "
             "if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == "[]", f"importing sluice.cli loaded {out}"


# ── every backend a stage builds, not only the first ──────────────────────────


def test_an_override_provider_gets_backend_timeout(monkeypatch):
    """A one-run `--backend` provider is a different construction from the configured one,
    and must be sized by the same knob rather than the module constant."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    be = Sluice(Config(backend_timeout=1234)).backend(
        provider="claude-max", model="m", effort="max", host="", claude_path="claude",
        override="deepseek")
    assert be.inner.timeout == 1234


def test_every_backend_triage_builds_gets_backend_timeout(monkeypatch, tmp_path):
    """The judge's AND tier-3 company resolution's, recorded from `triage()` itself without
    stopping at the first: a stage passing one of them its own timeout would leave the rows
    above green."""
    import dataclasses

    import sluice.core.app as A
    from sluice.triage.config import load_triage_config
    monkeypatch.setenv("TRIAGE_AUDIT", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "not-a-real-key")
    tcfg = dataclasses.replace(load_triage_config(), company_resolve_llm=True,
                               company_resolve_fetch=True, resolve_backend="deepseek")
    monkeypatch.setattr("sluice.triage.config.load_triage_config", lambda: tcfg)
    seen = []
    real = A._build_backend

    def spy(name, *a, timeout=None, **k):
        seen.append((name, timeout))
        return real(name, *a, timeout=timeout, **k)

    monkeypatch.setattr(A, "_build_backend", spy)

    Sluice(Config(backend_timeout=1234)).triage(dry_run=True)

    # Both constructions, distinguishable by provider: the judge's claude-max and tier 3's
    # deepseek. A `len` check alone would pass on two judge builds.
    assert sorted(n for n, _t in seen) == ["claude-max", "deepseek"], seen
    assert all(t == 1234 for _n, t in seen), seen
