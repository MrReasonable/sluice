"""#181: renaming a slop stem must not break a working config.

`cv.slop_allow` is validated by membership against `slop._PHRASES` and RAISES on a miss.
That is right -- a typo'd entry is otherwise SILENTLY inert, and the style hold it was
meant to suppress recurs forever with nothing pointing at the entry -- but it quietly
made `_PHRASES` a config compatibility surface. The day a stem is renamed, a working
`sluice.yaml` stops loading and every `cv` command dies, over a lint heuristic the user
has no stake in.

`_RETIRED_PHRASES` is the graveyard that prevents it, and it deliberately does NOT copy
`plugins._RETIRED`'s semantics. That table still RAISES, because accepting a retired
ADAPTER name would run an implementation the user did not select -- no substitution is
safe. A retired STEM has an exact one: the same suppression under a new spelling. So a
retired stem is MIGRATED and warned about; only a genuine typo, matching neither table,
still refuses.

The tests patch `sluice.cv.config._RETIRED_PHRASES` rather than `slop`'s, because
`cv/config.py` binds the name at import (`from sluice.cv.slop import ... _RETIRED_PHRASES`)
and patching the source module would leave that binding untouched -- the same
import-alias hazard this repo's guard-test rules already warn about, in reverse.
"""
import pytest

import sluice.cv.config as cvconfig
from sluice.cv.config import load_cv_config
from sluice.cv.slop import _PHRASES, _RETIRED_PHRASES


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


# ── a renamed stem migrates, loudly, instead of breaking the run ─────────────────

def test_a_renamed_stem_is_migrated_and_warned_about(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cvconfig, "_RETIRED_PHRASES", {"utilise": "leverage"})
    path = _write(tmp_path, "cv:\n  slop_allow: [utilise]\n")

    with caplog.at_level("WARNING"):
        cfg = load_cv_config(path)

    assert cfg.slop_allow == ["leverage"], "the retired stem was not migrated"
    # getMessage() renders the lazy %-args the logger was called with; reading `.message`
    # gives the unformatted template, so the old stem would not appear in it.
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "utilise" in warned and "leverage" in warned, "the migration happened silently"


def test_a_stem_retired_outright_is_dropped_and_warned_about(tmp_path, monkeypatch, caplog):
    # "" means the phrase is no longer checked at all, so allowing it cannot mean
    # anything. Dropping it is the honest outcome; keeping it would leave a config entry
    # that looks live and is not -- the silently-inert shape the raise exists to prevent.
    monkeypatch.setattr(cvconfig, "_RETIRED_PHRASES", {"utilise": ""})
    path = _write(tmp_path, "cv:\n  slop_allow: [utilise, leverage]\n")

    with caplog.at_level("WARNING"):
        cfg = load_cv_config(path)

    assert cfg.slop_allow == ["leverage"]
    assert caplog.records, "a dropped entry must not be silent"


# ── a genuine typo still refuses ────────────────────────────────────────────────

def test_an_unknown_stem_still_raises(tmp_path, monkeypatch):
    # The typo catcher must survive the migration path. `leveraged` is an INFLECTION,
    # never a stem, so it matches neither table -- exactly the entry most likely to slip
    # past a silent check, per the guard's original reasoning.
    monkeypatch.setattr(cvconfig, "_RETIRED_PHRASES", {"utilise": "leverage"})
    path = _write(tmp_path, "cv:\n  slop_allow: [leveraged]\n")
    with pytest.raises(ValueError) as e:
        load_cv_config(path)
    assert "leveraged" in str(e.value)


def test_the_refusal_does_not_cite_an_internal_symbol(tmp_path):
    """An error naming `slop._PHRASES` tells a user where the maintainer keeps something.

    It is module-private: they cannot open it, look it up, or act on it. The message
    should name the offending entries and the valid stems, which it does.
    """
    path = _write(tmp_path, "cv:\n  slop_allow: [leveraged]\n")
    with pytest.raises(ValueError) as e:
        load_cv_config(path)
    msg = str(e.value)
    assert "_PHRASES" not in msg, "the refusal cited a module-private symbol"
    assert "leverage" in msg, "the valid stems must still be listed"


# ── the graveyard's own invariants ──────────────────────────────────────────────

def test_a_retired_stem_is_actually_gone_and_its_replacement_actually_exists():
    """The table must describe reality, or it is a map to nowhere.

    A key still present in `_PHRASES` is not retired at all, and the migration would
    rewrite a live stem into something else. A non-empty value absent from `_PHRASES`
    migrates a user onto a stem that does not exist, which the very next load refuses --
    turning a helpful migration into a delayed break.
    """
    for old, new in _RETIRED_PHRASES.items():
        assert old not in _PHRASES, f"{old!r} is retired but still a live stem"
        if new:
            assert new in _PHRASES, f"{old!r} migrates to {new!r}, which is not a stem"


# ── the ratchet that makes the graveyard get used ───────────────────────────────

# Every stem shipped when #181 landed. This is a RATCHET, not a duplicate of the list:
# its job is to make a REMOVAL impossible to land silently, because a removal is exactly
# what breaks a user's config and exactly what the graveyard above exists to absorb.
_STEMS_AT_181 = frozenset({
    'at the end of the day', 'best-in-class', 'boasts', 'cutting-edge', 'delve',
    'detail-oriented', 'dive into', 'drove', 'elevate', 'embark', 'empower', 'foster',
    'furthermore', 'game-chang', 'holistic', 'in order to', "it's worth noting",
    'leverage', 'meticulous', 'moreover', 'myriad', 'needle-mov', 'not just',
    'passionate about', 'pivotal', 'plethora', 'proven track record', 'realm',
    'results-driven', 'seamless', 'showcasing', 'spearhead', 'streamline', 'synergy',
    'tapestry', 'team player', 'testament to', 'underscore', 'unlock',
    'wealth of experience', 'world-class',
})


def test_no_stem_disappears_without_a_graveyard_entry():
    """ADDING a stem is free. REMOVING or RENAMING one breaks a config that allowed it.

    `_PHRASES` must stay tunable -- it tracks model-output drift, and #167 added `drove`
    in its own PR -- so this does not freeze the list. It asks one question of a removal:
    did you put it in `_RETIRED_PHRASES` first?
    """
    live = set(_PHRASES)
    vanished = _STEMS_AT_181 - live - set(_RETIRED_PHRASES)
    assert not vanished, (
        f"these stems left _PHRASES with no _RETIRED_PHRASES entry: {sorted(vanished)}.\n"
        "A config allowing one of them now fails to load. Add it to _RETIRED_PHRASES -- "
        "mapped to its replacement, or to \"\" if the phrase is no longer checked at all "
        "-- and the migration path will absorb it.\n"
        "If you ADDED stems instead, nothing is wrong: extend _STEMS_AT_181 to match.")
    assert _STEMS_AT_181 <= live | set(_RETIRED_PHRASES), "unreachable given the above"


def test_the_ratchet_is_not_vacuous():
    # A frozenset that drifted empty, or a _PHRASES that stopped importing, would make
    # the sweep above pass over nothing at all.
    assert len(_STEMS_AT_181) >= 40, "the ratchet's baseline has shrunk; it is now weaker"
    assert len(_PHRASES) >= 40, "_PHRASES has collapsed -- the style tier is near-inert"
