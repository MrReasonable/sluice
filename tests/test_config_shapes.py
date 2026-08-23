"""#176: a YAML SCALAR given for a container-typed config field must be REFUSED.

The bug class, measured on the pre-fix tree rather than reasoned about:

  * `relevance_drop: senior` (root) loaded as `['s','e','n','i','o','r']` and
    `is_relevant` then returned False for EVERY title tried -- the whole scrape
    binned, at ingest, before dedup and before any note exists to notice.
  * `triage.target_locations: remote` loaded as the STRING `"remote"`, and `classify`
    then kept Remote AND London AND Berlin -- byte-identical to the unconfigured
    abstain, so a geography filter the user believes they configured does nothing.
  * `cv.fabrication_decoys: Acme` made the CV gate emit `FABRICATED: contains 'A'`,
    `'c'`, `'m'`, `'e'` and hard-block every CV.

All three are 672ad2a -- a preference gate reading as something the user did not
choose -- reached through a YAML typo instead of a shipped default.

RAISING rather than coercing is deliberate and is the decision this repo had already
made and half-applied: `core/config.py`'s `_str_list` names this exact scalar case and
says "a clear error at construction is the house style". Coercion cannot be made safe
here, because the likeliest scalar is the COMMA-SEPARATED one -- `init` asks
"comma-separated?", and a user hand-editing YAML repeats that phrasing.
`target_locations: Example City, Example Region` coerces to ONE token matching nothing,
so every located lead is rejected. Coercion converts "the gate abstains" into "the gate matches
nothing", which is the same bug class one step further from view.
"""
import pytest

from sluice.core.config import load_config
from sluice.cv.config import load_cv_config
from sluice.triage.config import load_triage_config


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ── the three loaders, one representative field each ─────────────────────────────

def test_a_root_list_field_refuses_a_scalar(tmp_path):
    # The severe one: this runs at ingest, before dedup and before any LLM call.
    path = _write(tmp_path, "root.yaml", "vault_dir: ./v\nrelevance_drop: senior\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    assert "relevance_drop" in str(e.value)


def test_a_triage_list_field_refuses_a_scalar(tmp_path):
    path = _write(tmp_path, "triage.yaml", "triage:\n  target_locations: remote\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    assert "target_locations" in str(e.value)


def test_a_cv_list_field_refuses_a_scalar(tmp_path):
    path = _write(tmp_path, "cv.yaml", "cv:\n  fabrication_decoys: Acme\n")
    with pytest.raises(ValueError) as e:
        load_cv_config(path)
    assert "fabrication_decoys" in str(e.value)


# ── the comma-separated case, which is why coercion was rejected ─────────────────

def test_a_comma_separated_scalar_is_refused_rather_than_silently_one_token(tmp_path):
    # Coerced, this becomes ONE token that matches nothing, and every located lead is
    # rejected with nothing said. It is the likeliest scalar a user writes, because
    # `job-sluice init` asks for these answers comma-separated.
    # Synthetic places, matching `sluice.yaml.example`'s own `[Antarctica]` for this
    # key. The property under test is the COMMA, not the cities -- every other
    # London/Berlin in tests/ is an IANA timezone under the standing exemption, and
    # this is the first in a geography-PREFERENCE position.
    path = _write(tmp_path, "csv.yaml",
                  "triage:\n  target_locations: Example City, Example Region\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    assert "target_locations" in str(e.value)


# ── the message contract ─────────────────────────────────────────────────────────

def test_the_refusal_names_the_key_the_type_and_both_correct_spellings(tmp_path):
    path = _write(tmp_path, "msg.yaml", "triage:\n  reject_companies: AcmeCorp\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    msg = str(e.value)
    assert "triage.reject_companies" in msg, "the block-qualified key must be named"
    assert "str" in msg, "the type actually found must be named"
    assert "[" in msg and "- " in msg, "both YAML list spellings must be shown"


@pytest.mark.parametrize("body,loader", [
    ("triage:\n  reject_companies: {v}\n", load_triage_config),
    ("cv:\n  employers: {v}\n", load_cv_config),
    ("vault_dir: ./v\nrelevance_drop: {v}\n", load_config),
    ("vault_dir: ./v\nlocation_noise_words: {v}\n", load_config),
])
def test_the_refusal_never_echoes_the_VALUE(tmp_path, body, loader):
    """`reject_companies`, `employers`, `relevance_drop` and the noise words are all
    personal -- an employer, a job title, a place.

    `load_config` already declines `_str_list` for `dossier_allow_hosts` on exactly this
    ground: "a config file is one of the few places a user's real private hostnames
    legitimately live", and `urlguard.parse_allow_hosts` prints
    `type(entries).__name__` instead. `_str_list` itself echoed `got {value!r}`, which
    made that choice inconsistent with its own reasoning; both now name the TYPE.

    All four loaders' paths are covered because the property is the same one and the
    implementations are not: two go through `refuse_wrong_container`, two through
    `_str_list`.
    """
    secret = "SomeRealEmployerLtd"
    path = _write(tmp_path, "leak.yaml", body.format(v=secret))
    with pytest.raises(ValueError) as e:
        loader(path)
    assert secret not in str(e.value), "the refusal echoed the user's private value"


# ── the other direction: correct config must still load ──────────────────────────

@pytest.mark.parametrize("body,loader,attr,expected", [
    ("vault_dir: ./v\nrelevance_drop: [senior]\n", load_config, "relevance_drop", ["senior"]),
    ("triage:\n  target_locations: [remote]\n", load_triage_config, "target_locations", ["remote"]),
    ("cv:\n  fabrication_decoys: [Acme]\n", load_cv_config, "fabrication_decoys", ["Acme"]),
])
def test_a_real_list_still_loads(tmp_path, body, loader, attr, expected):
    # Without this, refusing EVERY value would satisfy the tests above while making
    # the fields unusable.
    path = _write(tmp_path, "ok.yaml", body)
    assert getattr(loader(path), attr) == expected


@pytest.mark.parametrize("body,loader,attr", [
    ("vault_dir: ./v\n", load_config, "relevance_drop"),
    ("triage:\n  company_resolve_llm: false\n", load_triage_config, "target_locations"),
    ("cv:\n  voice_check: false\n", load_cv_config, "fabrication_decoys"),
])
def test_an_absent_key_still_abstains(tmp_path, body, loader, attr):
    # ABSENT is the abstain case, never an error: an unconfigured install must load and
    # gate nothing. Refusing here would be 672ad2a with a different trigger.
    path = _write(tmp_path, "absent.yaml", body)
    assert getattr(loader(path), attr) == []


# ── scope: the guard must reach every container field, not the ones I thought of ──

def test_every_container_field_in_every_loader_is_guarded(tmp_path):
    """The SCOPE assertion, without which this file is a hand-list that goes stale.

    A guard applied to the fields someone happened to enumerate is the half-applied
    defensive pattern this repo treats as worse than none -- and it is exactly how
    `_str_list` came to protect two root fields and none of the sub-app ones. This
    walks the real dataclasses, so a container field added later is covered the day it
    lands rather than the day someone remembers.
    """
    import dataclasses

    from sluice.apply.config import ApplyConfig, load_apply_config
    from sluice.core.config import Config
    from sluice.cv.config import CvConfig
    from sluice.track.config import TrackConfig, load_track_config
    from sluice.triage.config import TriageConfig

    # ALL FIVE loaders, not the three that happened to change. An earlier version walked
    # three and was named for five, which is the same half-application this sweep exists
    # to catch, one level up: `load_apply_config` is a bare `hasattr`+`setattr` loop, so a
    # container field added there later would be unguarded AND invisible here.
    cases = [("", Config, load_config, "vault_dir: ./v\n"),
             ("triage", TriageConfig, load_triage_config, ""),
             ("cv", CvConfig, load_cv_config, ""),
             ("apply", ApplyConfig, load_apply_config, ""),
             ("track", TrackConfig, load_track_config, "")]

    # PER-BLOCK counts, not one global floor. A single `>= N` is slack by however much the
    # largest block contributes -- measured, the old `>= 12` against a true 19 meant a
    # whole loader (triage's five preference gates) could drop out of discovery and the
    # sweep would still pass. A per-block floor cannot hide that.
    seen = {}
    for block, klass, loader, preamble in cases:
        for f in dataclasses.fields(klass):
            default = (f.default_factory() if f.default_factory is not dataclasses.MISSING
                       else f.default)
            if not isinstance(default, (list, dict)):
                continue
            # A scalar for this field must be refused by SOME guard -- the shared one, or
            # the field's own bespoke validator (`dossier_allow_hosts`, `slop_allow`,
            # track's `_merge_denylist`), whichever gives the more specific message.
            body = (f"{preamble}{block}:\n  {f.name}: scalar\n" if block
                    else f"{preamble}{f.name}: scalar\n")
            path = _write(tmp_path, f"scope-{block or 'root'}-{f.name}.yaml", body)
            with pytest.raises(ValueError):
                loader(path)
            seen[block or "root"] = seen.get(block or "root", 0) + 1

    # `apply` is absent on purpose: it has NO container fields today. Asserting a floor of
    # 0 for it would be indistinguishable from discovery breaking, so its coverage is the
    # assertion below instead -- if it ever gains one, this dict grows a key and the
    # per-block floors here stop describing reality.
    expected = {"root": 7, "triage": 5, "cv": 5, "track": 2}
    assert seen == expected, (
        f"container-field discovery changed: {seen} != {expected}.\n"
        "If a field was ADDED, update this map. If a block VANISHED, discovery has broken "
        "and the sweep is now passing over nothing -- which is the failure this test "
        "exists to make impossible.")


def test_apply_has_no_container_field_and_would_be_guarded_if_it_gained_one():
    """`apply` is the loader with nothing to sweep, so it needs its own assertion.

    A zero count is indistinguishable from broken discovery, so the sweep above cannot
    carry it. This pins the FACT (zero container fields today) and the CONSEQUENCE (its
    loop calls the shared guard, so the day it gains one it is covered) separately.
    """
    import dataclasses
    import inspect

    from sluice.apply.config import ApplyConfig, load_apply_config

    containers = [f.name for f in dataclasses.fields(ApplyConfig)
                  if isinstance((f.default_factory() if f.default_factory
                                 is not dataclasses.MISSING else f.default), (list, dict))]
    assert containers == [], (
        f"apply gained container fields {containers}; add 'apply' to the sweep's expected "
        "map above, which currently records that it has none.")
    assert "refuse_wrong_container" in inspect.getsource(load_apply_config), (
        "apply's loader no longer calls the shared container guard, so the field it "
        "gains next will be unprotected")


# ── the refusal must never INSTRUCT the bug it refuses (review round 1, High) ────

def test_the_searches_refusal_teaches_a_spelling_that_actually_works(tmp_path):
    """A refusal must be answerable without making things worse.

    `sources.<id>.searches` entries are themselves lists (`[label, url, {params}?]`), so
    the generic "write it as `[first, second]`" advice was actively harmful here.
    Measured before the fix: following it produced a FLAT two-string list, each string was
    then indexed `spec[0], spec[1]`, and "My search" became label='M', url='y' -- the
    per-character explosion the guard exists to refuse, arrived at BY OBEYING the refusal.

    This repo already learned the lesson from the CV parser's LOCATION field, where the
    only actionable reading of the message was to invent a city.
    """
    path = _write(tmp_path, "s.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches: my search\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    msg = str(e.value)
    assert "[[" in msg, "the refusal must show the NESTED shape, not a flat list"

    # And the spelling it teaches must load into the shape the consumer expects: ONE
    # entry that is itself a list, not two bare strings.
    import re as _re
    example = _re.search(r"`searches: (\[\[.*?\]\])`", msg)
    assert example, f"could not find the taught spelling in: {msg}"
    good = _write(tmp_path, "good.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n"
                  f"    searches: {example.group(1)}\n")
    loaded = load_config(good).source("reed").searches
    assert len(loaded) == 1 and isinstance(loaded[0], list), (
        f"following the refusal produced {loaded!r}, which is the flat shape that "
        "explodes per character")


def test_a_non_string_scalar_is_not_told_it_is_a_string(tmp_path):
    # "sluice would read it one CHARACTER at a time" is true of a str and false of an int
    # or a bool. Asserting a mechanism that does not happen is the stale-prose class this
    # repo keeps finding in its own comments.
    path = _write(tmp_path, "n.yaml", "triage:\n  target_locations: 5\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    msg = str(e.value)
    assert "int" in msg
    assert "CHARACTER" not in msg, "an int was told it would be read character by character"


def test_a_bad_ELEMENT_names_the_index_and_type_not_the_value(tmp_path):
    """The element arm must not be answered with the container arm's advice.

    Collapsed into one message, `relevance_keep: [2024]` said "must be a YAML list of
    strings, but got a list" -- naming the wrong problem and instructing the user to write
    what they had already written. Dropping the value echo was right; dropping it without
    splitting the arms took the information away with it.
    """
    path = _write(tmp_path, "el.yaml", "vault_dir: ./v\nrelevance_keep: [2024]\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    msg = str(e.value)
    assert "index 0" in msg, "the offending POSITION must be named"
    assert "int" in msg, "the offending element's TYPE must be named"
    assert "2024" not in msg, "the element's VALUE must not be echoed"
