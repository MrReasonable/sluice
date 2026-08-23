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
from sluice.cv.slop import _PHRASES, _RETIRED_PHRASES, _stem_pattern


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _said_by_the_loader(caplog) -> str:
    """Every WARNING `cv/config.py` itself emitted, rendered, joined.

    Filtered by LOGGER NAME rather than taken from `caplog.records` whole: an assertion
    over every record in the run is satisfied by a warning from any other module the load
    touches, so it would go green on a load that said nothing about the config at all.

    `getMessage()` renders the lazy %-args the logger was called with. (`record.msg` is the
    unformatted template; `record.message` is set by caplog and IS rendered -- an earlier
    version of this comment had those two the wrong way round.)

    On `propagate`: `get_logger` sets it False, which used to mean a non-propagating
    logger's records never reached caplog's root handler. It does not any more --
    pytest's `catching_logs` attaches its handler to every non-propagating logger too --
    and this file relies on that rather than passing `logger=`. Measured under the pinned
    pytest: one record, `name == "sluice.cv.config"`. What `propagate=False` still costs
    is the LEVEL, which `catching_logs` raises on the root logger only; `tests/conftest.py`
    resets every `sluice.*` logger to INFO for exactly that reason (#144). If a future
    pytest drops the attach, these assertions go RED rather than quiet: each one requires
    named content, so an empty capture fails them.
    """
    return " ".join(r.getMessage() for r in caplog.records if r.name == "sluice.cv.config")


# ── a renamed stem migrates, loudly, instead of breaking the run ─────────────────

def test_a_renamed_stem_is_migrated_and_warned_about(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cvconfig, "_RETIRED_PHRASES", {"utilise": "leverage"})
    path = _write(tmp_path, "cv:\n  slop_allow: [utilise]\n")

    with caplog.at_level("WARNING"):
        cfg = load_cv_config(path)

    assert cfg.slop_allow == ["leverage"], "the retired stem was not migrated"
    warned = _said_by_the_loader(caplog)
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
    # NOT a bare `assert caplog.records`. That is satisfied by ANY warning from anywhere in
    # the load -- including one about an unrelated key -- so the single shape it exists to
    # exclude, the drop going unmentioned while something else speaks, passes it. Pin the
    # entry name and the CLAIM, which is also what separates this arm from the rename arm
    # above: swapping the two branches leaves a record, names 'utilise', and still lies.
    warned = _said_by_the_loader(caplog)
    assert "utilise" in warned, "the dropped entry was not named"
    assert "no effect" in warned, "the drop was reported as something other than a drop"


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


def test_the_refusal_reads_as_a_sentence_on_both_the_one_and_many_paths(tmp_path):
    """A refusal that garbles itself is read as a bug in sluice, not in the config.

    It shipped garbled: the helper returned the connective along with the verb, the raise
    wrote a second one, and the noun never followed the verb into the plural -- so the two
    real renderings were `which which is not a phrase` and `which which are not a phrase`.
    Nothing caught it, because the only assertion on this message checked that the
    offending ENTRY appeared in it, which every mangling of the surrounding prose survives.

    So assert the whole clause, on both arms. A negative (`"which which" not in msg`) is
    not enough on its own -- it is the fail-open shape, green on a message that lost the
    clause entirely -- and it is subsumed by pinning the text that must be there instead.
    """
    def refusal(entries):
        path = _write(tmp_path, f"cv:\n  slop_allow: {entries}\n")
        with pytest.raises(ValueError) as e:
            load_cv_config(path)
        return str(e.value)

    assert "'leveraged', which is not a phrase sluice checks for." in refusal("[leveraged]")
    assert ("'leveraged', 'utilised', which are not phrases sluice checks for."
            in refusal("[leveraged, utilised]"))


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
    # EVERY stem, not one -- and not by a bare substring. `assert "leverage" in msg` was
    # inert: the message's own prose named 'leverage' as an example, so deleting the
    # entire valid-stems list from the raise left the whole suite green with the message
    # ending in no valid name at all.
    missing = [stem for stem in _PHRASES if repr(stem) not in msg]
    assert not missing, f"the refusal no longer lists these valid stems: {missing}"


# ── the graveyard's own invariants ──────────────────────────────────────────────

def test_the_retirement_invariants_are_shaped_for_a_table_that_has_rows():
    """The invariant test below iterates an EMPTY table today, so it asserts nothing.

    That is correct for a graveyard nobody has needed yet, but it means the test cannot
    report its own health. This pins the shape it will check the day a row lands, so a
    later edit cannot quietly drop one of the three conditions while the loop is still
    empty and green.
    """
    import inspect
    src = inspect.getsource(test_a_retired_stem_is_actually_gone_and_its_replacement_actually_exists)
    for required in ("old == old.lower()", "old not in _PHRASES",
                     "new in _PHRASES", "_stem_pattern(new).search(old)"):
        assert required in src, f"the retirement invariant no longer checks {required!r}"


def test_a_retired_stem_is_actually_gone_and_its_replacement_actually_exists():
    """The table must describe reality, or it is a map to nowhere.

    A key still present in `_PHRASES` is not retired at all, and the migration would
    rewrite a live stem into something else. A non-empty value absent from `_PHRASES`
    migrates a user onto a stem that does not exist, which the very next load refuses --
    turning a helpful migration into a delayed break.
    """
    for old, new in _RETIRED_PHRASES.items():
        assert old == old.lower(), (
            f"{old!r} must be lower-case: both lookups use `p.lower()`, so a key with "
            "capitals is unreachable -- migration silently does nothing and the user "
            "gets the raise this table exists to prevent")
        assert old not in _PHRASES, f"{old!r} is retired but still a live stem"
        if new:
            assert new in _PHRASES, f"{old!r} migrates to {new!r}, which is not a stem"
            # The LOAD-BEARING one. Migrating rather than raising is justified only
            # because the substitution is exact -- the replacement suppresses the text the
            # old stem suppressed. Membership alone does not check that: measured,
            # `delve -> dive into` and `spearhead -> foster` are both real stems and both
            # pass `new in _PHRASES` while changing what the user's CV is checked for. If
            # a replacement does not cover the old stem, RAISING is the honest outcome and
            # this table is the wrong tool.
            assert _stem_pattern(new).search(old), (
                f"{old!r} migrates to {new!r}, which does not match it -- the "
                "substitution is not lossless, so migrating silently changes what this "
                "user's CV is checked for. Retire it to \"\" instead, or let it raise.")


# ── the ratchet's LOGIC, testable without a stem that does not exist yet ─────────
#
# The two assertions in the ratchet above are vacuously true today: `_PHRASES` and
# `_STEMS_AT_181` are equal, so `set(_PHRASES) - _STEMS_AT_181` is empty whatever the
# expression says. Measured -- replacing it with `set()` SURVIVED a mutation run. The
# comparison itself is therefore exercised here against SIMULATED sets, so the rule is
# pinned even while the real sets cannot distinguish it.

def _vanished(baseline, live, retired):
    """A stem that left `_PHRASES` with no graveyard entry -- the ratchet's first rule."""
    return baseline - set(live) - set(retired)


def _unratcheted(baseline, live):
    """A stem present in `_PHRASES` that the baseline does not cover -- the second."""
    return set(live) - baseline


def test_the_ratchet_rules_catch_what_they_are_written_for():
    base = frozenset({"alpha", "beta"})

    # 1. A stem removed with no graveyard entry: caught.
    assert _vanished(base, ["alpha"], {}) == {"beta"}
    # 2. ...and recorded, so not caught.
    assert _vanished(base, ["alpha"], {"beta": "alpha"}) == set()
    # 3. A stem ADDED after the baseline was frozen: caught at ADD time, which is what
    #    stops its later removal going unratcheted. Before this rule existed, add-then-
    #    remove was green and a config allowing the new stem broke with nothing red.
    assert _unratcheted(base, ["alpha", "beta", "gamma"]) == {"gamma"}
    # 4. The steady state: nothing added, nothing missing.
    assert _unratcheted(base, ["alpha", "beta"]) == set()


def test_the_shipped_sets_satisfy_both_ratchet_rules():
    # The real sets, through the same two helpers the simulated test above pins.
    assert _vanished(_STEMS_AT_181, _PHRASES, _RETIRED_PHRASES) == set()
    assert _unratcheted(_STEMS_AT_181, _PHRASES) == set()


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
    vanished = _vanished(_STEMS_AT_181, _PHRASES, _RETIRED_PHRASES)
    assert not vanished, (
        f"these stems left _PHRASES with no _RETIRED_PHRASES entry: {sorted(vanished)}.\n"
        "A config allowing one of them now fails to load. Add it to _RETIRED_PHRASES -- "
        "mapped to its replacement, or to \"\" if the phrase is no longer checked at all "
        "-- and the migration path will absorb it.\n"
        "If you ADDED stems instead, nothing is wrong: extend _STEMS_AT_181 to match.")
    # Forces the BASELINE to grow when `_PHRASES` does. Without it the ratchet decays:
    # a stem added after #181 never enters `_STEMS_AT_181`, so its later removal is
    # unratcheted and a config allowing it breaks with nothing red. Measured -- add then
    # remove was green before this line. It also makes `docs/CONFIGURATION.md`'s promise
    # true for stems added later, not only for the 41 frozen here.
    unratcheted = _unratcheted(_STEMS_AT_181, _PHRASES)
    assert not unratcheted, (
        f"these stems are not in the ratchet's baseline: {sorted(unratcheted)}.\n"
        "Add them to _STEMS_AT_181 -- otherwise removing one later breaks a user's config "
        "with nothing going red, which is the whole failure this ratchet exists to catch.")


def test_the_ratchet_is_not_vacuous():
    # A frozenset that drifted empty, or a _PHRASES that stopped importing, would make
    # the sweep above pass over nothing at all.
    # EXACT, not `>= 40`. The loose floor let a stem and its baseline entry be removed
    # together (41 -> 40) with nothing red -- which is precisely the co-ordinated edit the
    # ratchet is supposed to make impossible.
    assert len(_STEMS_AT_181) == 41, (
        "the ratchet's baseline changed size. Growing it is correct when stems are ADDED "
        "(update this number). SHRINKING it means a baseline entry was deleted, which "
        "silently un-ratchets whatever it covered.")
    assert len(_PHRASES) >= 41, "_PHRASES has shrunk -- see the graveyard rule above"


# ── the loader's precedence, for a table shape the invariant test forbids ────────

def test_a_graveyard_key_that_is_still_live_is_not_silently_substituted(tmp_path,
                                                                       monkeypatch):
    """Defence in depth for a state `_RETIRED_PHRASES`' own invariant already forbids.

    The classification loop skips a LIVE stem before classifying, so it is never migrated
    and never warned about. An earlier version recomputed the write-back from `raw`,
    consulting the graveyard for every entry including live ones -- so the two disagreed:
    measured, `slop_allow: [leverage]` with `{"leverage": "foster"}` loaded as
    `['foster']` with ZERO warnings. Only the invariant test forbade that table; the
    loader itself was inconsistent. Deriving the write-back from `migrated` makes the two
    agree by construction, and this pins that they do.
    """
    # TWO entries, deliberately. `utilise` is genuinely retired, so `migrated` is
    # non-empty and the write-back actually runs; `leverage` is a LIVE stem that the
    # graveyard also names. With one entry only, `migrated` is empty, the `if migrated:`
    # guard skips the block, and the mutation is unreachable -- the first version of this
    # test could not distinguish the two implementations at all.
    monkeypatch.setattr(cvconfig, "_RETIRED_PHRASES",
                        {"utilise": "leverage", "leverage": "foster"})
    path = _write(tmp_path, "cv:\n  slop_allow: [utilise, leverage]\n")
    cfg = load_cv_config(path)
    assert cfg.slop_allow == ["leverage", "leverage"], (
        f"got {cfg.slop_allow!r}: the live stem was substituted from the graveyard "
        "without being classified as migrated -- the loop and the write-back disagree "
        "about precedence")
