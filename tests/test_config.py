import importlib
import textwrap

import pytest

from sluice.core.config import load_config


def test_defaults_when_no_file(monkeypatch):
    # SLUICE_LOCATIONS is still cleared, but for the opposite reason to before: the key
    # is retired (#8) and an exported value now RAISES, so a developer with one set
    # would fail here rather than silently override.
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    cfg = load_config(None)
    assert cfg.source("anything").enabled is True
    assert cfg.source("anything").tuning == {}


def test_yaml_disables_a_source(tmp_path):
    p = tmp_path / "sluice.yaml"
    p.write_text("sources:\n  cord:\n    enabled: false\n")
    cfg = load_config(str(p))
    assert cfg.source("cord").enabled is False
    assert cfg.source("wttj").enabled is True  # unlisted → default enabled


def test_yaml_tuning(tmp_path):
    # The root `locations` key this test also covered is retired (#8): it was read by
    # nothing, and setting it now raises. Its refusal -- in both the file and the
    # `$SLUICE_LOCATIONS` spellings -- lives in tests/test_config_retired_locations.py.
    p = tmp_path / "sluice.yaml"
    p.write_text(textwrap.dedent("""
        sources:
          jobserve:
            enabled: true
            tuning:
              wait: 8
    """))
    cfg = load_config(str(p))
    assert cfg.source("jobserve").tuning["wait"] == 8


def test_env_telegram_populates_notify(monkeypatch):
    monkeypatch.setenv("SLUICE_TELEGRAM_TOKEN", "t0k")
    monkeypatch.setenv("SLUICE_TELEGRAM_CHAT", "42")
    cfg = load_config(None)
    assert cfg.notify["telegram"] == {"token": "t0k", "chat_id": "42"}


def test_source_searches_default_empty(monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    cfg = load_config(None)
    assert cfg.source("linkedin").searches == []  # no override → use built-in


def test_yaml_source_searches_override(tmp_path):
    p = tmp_path / "sluice.yaml"
    p.write_text(textwrap.dedent("""
        sources:
          linkedin:
            searches:
              - ["My Search Palmerburgh", "https://example.com/em", {"job_type": "perm"}]
              - ["My SM Remote", "https://example.com/sm"]
    """))
    cfg = load_config(str(p))
    got = cfg.source("linkedin").searches
    assert got == [
        ["My Search Palmerburgh", "https://example.com/em", {"job_type": "perm"}],
        ["My SM Remote", "https://example.com/sm"],
    ]
    assert cfg.source("reed").searches == []  # unlisted → no override


def test_load_config_reads_location_noise_words(tmp_path, monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    p = tmp_path / "s.yaml"
    p.write_text("location_noise_words:\n  - remote\n  - hybrid\n")
    assert load_config(str(p)).location_noise_words == ["remote", "hybrid"]


def test_location_noise_words_rejects_a_scalar(tmp_path, monkeypatch):
    import pytest

    from sluice.core.config import load_config
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    p = tmp_path / "s.yaml"
    p.write_text("location_noise_words: remote\n")   # a scalar, not a list
    with pytest.raises(ValueError, match="location_noise_words"):
        load_config(str(p))


def test_location_noise_words_rejects_non_string_entries(tmp_path, monkeypatch):
    import pytest

    from sluice.core.config import load_config
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    p = tmp_path / "s.yaml"
    p.write_text("location_noise_words:\n  - 42\n")   # a non-string entry
    with pytest.raises(ValueError, match="location_noise_words"):
        load_config(str(p))


def test_path_keys_round_trip_through_load_config(tmp_path, monkeypatch):
    # #80. A dataclass field ALONE is dead here: load_config names every field
    # explicitly (no splat, no loop, unlike the four sub-app loaders' hasattr+setattr
    # loops), so a field it does not name stays at its default no matter what the YAML
    # says. That is the pre-existing shape of triage/config.py's two dead keys -- the
    # user sets them, nothing happens, and nothing errors -- and it is why this row
    # asserts the LOADER rather than the dataclass.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    vault, dossiers = tmp_path / "v", tmp_path / "d"
    p = tmp_path / "s.yaml"
    p.write_text(f'vault_dir: "{vault}"\ndossier_dir: "{dossiers}"\n', encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.vault_dir == str(vault)
    assert cfg.dossier_dir == str(dossiers)


# ── a malformed sub-app block is a ValueError, in every loader ────────────────

_SUB_APP_LOADERS = {
    "cv": "sluice.cv.config:load_cv_config",
    "triage": "sluice.triage.config:load_triage_config",
    "apply": "sluice.apply.config:load_apply_config",
    "track": "sluice.track.config:load_track_config",
}

# Every spelling of "the user did not write a mapping under this key". Measured before
# the fix, the four failed in THREE different ways, none of them ValueError -- a str or
# a list gave `AttributeError: 'str' object has no attribute 'get'`, a scalar gave
# `TypeError: argument of type 'int' is not a container`, and the last row gave a
# ValueError that was WORSE than either: `"name" in data` is a SUBSTRING test on a str,
# so `cv:`'s #133/#107 migration guard fired and told the user to migrate a `cv.name`
# key they never set.
_MALFORMED = ['"hello"', "5", "true", "\n  - a\n  - b", "my name is here"]


@pytest.mark.parametrize("block", sorted(_SUB_APP_LOADERS))
@pytest.mark.parametrize("body", _MALFORMED)
def test_a_non_mapping_sub_app_block_raises_value_error(block, body, tmp_path):
    """Enumerated over the loader REGISTRY above rather than hand-listed per loader:
    all four share the identical `(yaml.safe_load(f) or {}).get("<block>") or {}` read,
    so a guard applied to one leaves the same trap armed in the other three -- and a
    half-applied defensive pattern is the shape this repo treats as worse than none.

    ValueError specifically, not merely "raises": `doctor` guards `load_cv_config()`
    with `except ValueError` precisely so a bad `cv:` block becomes a DEAD row instead
    of a traceback, and the two exception types this used to raise walked straight
    through that handler. The type IS the contract here, so `pytest.raises(ValueError)`
    is the assertion and a bare `Exception` would certify nothing.
    """
    mod, fn = _SUB_APP_LOADERS[block].split(":")
    loader = getattr(importlib.import_module(mod), fn)
    p = tmp_path / "s.yaml"
    p.write_text(f"{block}: {body}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=rf"`{block}:` block"):
        loader(str(p))


@pytest.mark.parametrize("block", sorted(_SUB_APP_LOADERS))
def test_an_absent_or_empty_sub_app_block_still_loads_defaults(block, tmp_path):
    """The anti-vacuity half. A guard that rejected the ABSENT and EMPTY spellings too
    would pass the test above while breaking every install that simply does not
    configure that sub-app -- and `sluice.yaml.example` ships `cv:` entirely commented,
    so "the key is not there" is the COMMON case, not an edge one.

    Asserts EQUALITY with the no-config baseline, not merely that the call returned
    something (CodeRabbit, PR #161): `is not None` proves only that the loader did not
    raise, and a normaliser that quietly dropped or altered defaults on its way through
    would satisfy it. What must hold is that these three spellings are all
    indistinguishable from having no config file at all -- which is the actual promise,
    and the thing the guard above could break.
    """
    mod, fn = _SUB_APP_LOADERS[block].split(":")
    loader = getattr(importlib.import_module(mod), fn)
    baseline = loader(str(tmp_path / "no-such-config.yaml"))
    for body in ("", f"{block}:\n", f"{block}: {{}}\n"):
        p = tmp_path / "s.yaml"
        p.write_text(body, encoding="utf-8")
        assert loader(str(p)) == baseline, (
            f"{block}: {body!r} must be indistinguishable from no config file at all")


def test_the_migration_guard_no_longer_misreads_a_prose_cv_block(tmp_path):
    """The specific wrong-diagnosis regression, pinned on its own rather than left to
    the parametrised sweep above -- that sweep only asserts the message names the
    block, which a substring-matched `cv.name has moved to the vault` would ALSO have
    to fail, but only by accident of wording. This asserts the wrong message is gone.

    A user who typed prose under `cv:` was told to move a `cv.name` key that is not in
    their file. Sending someone to edit a key that does not exist is worse than a raw
    traceback: the traceback at least does not lie about the cause.
    """
    from sluice.cv.config import load_cv_config

    p = tmp_path / "s.yaml"
    p.write_text("cv: my name is here\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_cv_config(str(p))
    assert "has moved to the vault" not in str(exc.value), (
        "a prose `cv:` block must not be diagnosed as a legacy cv.name key")
