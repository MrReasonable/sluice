"""#176: a YAML SCALAR given for a container-typed config field must be REFUSED.

The bug class, measured on the pre-fix tree rather than reasoned about:

  * `relevance_drop: senior` (root) loaded as `['s','e','n','i','o','r']` and
    `is_relevant` then returned False for EVERY title tried -- the whole scrape
    binned, at ingest, before dedup and before any note exists to notice.
  * `triage.target_locations: remote` loaded as the STRING `"remote"`, and `classify`
    then kept EVERY location tried, including ones sharing no word with the configured
    value -- byte-identical to the unconfigured abstain, so a geography filter the user
    believes they configured does nothing.
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

from sluice.core.config import load_config, validate_search_entry
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
    # key. The property under test is the COMMA, not the place names -- every other real
    # city in tests/ is an IANA timezone under the standing exemption, and a fixture for
    # `target_locations` is the one position where a bare city reads as a declared
    # geography preference rather than an illustration.
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
    # `sources.<id>.searches` itself is NOT a row here any more -- #212 round 3
    # (neu-r3-001/tst-r3-001): a single row can only ever reach ONE of
    # `validate_search_entry`'s three `raise` statements, whichever arm the planted
    # shape happens to hit, so this row certified nothing about the other two.
    # `test_every_malformed_search_entry_shape_never_echoes_the_VALUE` below replaces
    # it with a matrix driven through every arm.
])
def test_the_refusal_never_echoes_the_VALUE(tmp_path, body, loader):
    """`reject_companies`, `employers`, `relevance_drop` and the noise words are all
    personal -- an employer, a job title or a place.

    `load_config` already declines `_str_list` for `dossier_allow_hosts` on exactly this
    ground: "a config file is one of the few places a user's real private hostnames
    legitimately live", and `urlguard.parse_allow_hosts` prints
    `type(entries).__name__` instead. `_str_list` itself echoed `got {value!r}`, which
    made that choice inconsistent with its own reasoning; both now name the TYPE.

    These four loader paths are covered because the property is the same one and the
    implementations are not: two go through `refuse_wrong_container`, two through
    `_str_list`. `validate_search_entry`'s own never-echo property has its own dedicated
    matrix below, because one row cannot cover its three separate `raise` arms.
    """
    secret = "SomeRealEmployerLtd"
    path = _write(tmp_path, "leak.yaml", body.format(v=secret))
    with pytest.raises(ValueError) as e:
        loader(path)
    assert secret not in str(e.value), "the refusal echoed the user's private value"


def test_the_per_element_refusal_never_echoes_a_STRING_sibling(tmp_path):
    """`_str_list`'s per-ELEMENT arm (`core/config.py`'s `bad = [...]` branch) fires on a
    MIXED list -- one bad (non-string) element among good ones -- and is the worse of the
    two holes `neu-r3-001` measured as unreached by the never-echo guard above: it guards
    `relevance_keep`, `relevance_drop`, `location_noise_words` and
    `dedupe_title_noise_words`, the most preference-dense fields in the root config, and
    had never had its message checked for an echo. The message only names the bad
    element's INDEX and TYPE (never any value), so a real string sibling planted
    alongside the bad element must not leak either.
    """
    secret = "SomeRealEmployerLtd"
    for field in ("relevance_keep", "relevance_drop", "location_noise_words",
                  "dedupe_title_noise_words"):
        path = _write(tmp_path, f"element-{field}.yaml",
                      f"vault_dir: ./v\n{field}: [{secret}, 2024]\n")
        with pytest.raises(ValueError) as e:
            load_config(path)
        assert secret not in str(e.value), (
            f"{field}'s per-element refusal echoed a sibling value: {e.value}")


def _grammar_derived_malformed_search_entries(secret):
    """Every malformed `[label, url]`/`[label, url, {params}]` shape the grammar admits,
    generated from the SHAPE (arity x container-type x which position is wrong) rather
    than hand-picked, with `secret` planted in every position a real config VALUE could
    occupy. #212 round 3 (neu-r3-001/tst-r3-001): the prior single hand-picked row
    planted its secret only in the entry's third element, so it could only ever reach
    `validate_search_entry`'s third `raise`; mutating either of the other two (`got
    {entry!r}` restored at the length arm or the label/url arm) left the whole suite
    green. This drives the secret through all three arms, in every string-shaped
    position, so a value-echoing regression in ANY of them is caught.

    Four refusal arms, `sluice/core/config.py::validate_search_entry` (#212 round 4 split
    the old arm 3 in two -- see arc-r4-001):
      1. length/container -- not a list/tuple of length 2 or 3.
      2. label/url str -- position 0 or 1 is not a string.
      3. third element shape -- present and neither `None` nor a mapping.
      4. third element KEYS -- a mapping present, but a key collides with a `Lead`
         identity field (`_PARAMS_KEY_CLASH`); the message names the KEY, never the
         colliding VALUE.
    """
    cases = {}
    # Arm 1 -- wrong arity, as both list and tuple, secret in every slot present.
    for container in (list, tuple):
        name = container.__name__
        cases[f"empty-{name}"] = container()
        cases[f"one-{name}"] = container([secret])
        cases[f"four-{name}"] = container([secret, "url", "extra", secret])
    # Arm 1 -- a bare scalar entry; the secret IS the entry.
    cases["bare-scalar"] = secret
    # Arm 1 -- the natural YAML mapping spelling of an entry (validator docstring names
    # this as measured-real), secret as a VALUE and, separately, as the mapping's KEY.
    cases["mapping-value"] = {"label": secret, "url": "https://example.invalid/x"}
    cases["mapping-key"] = {secret: "https://example.invalid/x", "other": "y"}
    # Arm 2 -- label/url wrong type, secret in whichever position STAYS a string, so a
    # leak would reproduce the user's real text rather than the type mismatch.
    cases["url-wrong-type"] = [secret, 5]
    cases["label-wrong-type"] = [5, secret]
    # Arm 3 -- valid label/url (both the secret), invalid third element.
    cases["params-wrong-type"] = [secret, secret, "scalar-params"]
    # Arm 3, #212 round 4 (tst-r4-001/neu-r4-003): the round-3 refactor deleted the ONLY
    # row that planted the sentinel in the entry's THIRD element -- restoring an echo in
    # arm 3's message (`got a {type(entry[2]).__name__}: {entry[2]!r}`) survived the whole
    # suite with no row above catching it, because every row so far puts the secret in
    # slot 0 or 1, never slot 2. Valid label/url this time so the entry actually REACHES
    # arm 3, with the secret occupying the one position the earlier rows never touch.
    cases["params-scalar-is-the-secret"] = [
        "Label", "https://example.invalid/x", secret]
    cases["params-list-carries-the-secret"] = [
        "Label", "https://example.invalid/x", [secret]]
    # Arm 3's second raise (#212 round 4, arc-r4-001): a `{params}` KEY colliding with a
    # `Lead` identity field. The message names the KEY ("url"), never the VALUE, so
    # planting the secret as a colliding key's VALUE proves that raise does not echo it
    # either.
    cases["params-key-clash-value-is-the-secret"] = [
        "Label", "https://example.invalid/x", {"url": secret}]
    return cases


def test_every_malformed_search_entry_shape_never_echoes_the_VALUE():
    """Grammar-derived replacement for the single hand-picked `sources.<id>.searches` row
    that used to live in `test_the_refusal_never_echoes_the_VALUE` above. Drives
    `validate_search_entry` directly -- rather than round-tripping through YAML, which
    cannot express a `tuple` or a non-string mapping key -- so coverage tracks the
    GRAMMAR (arity x position x wrong type) rather than the one shape someone thought of.
    """
    secret = "SomeRealEmployerLtd"
    for shape_id, entry in _grammar_derived_malformed_search_entries(secret).items():
        with pytest.raises(ValueError) as e:
            validate_search_entry("sources.reed.searches", 0, entry)
        assert secret not in str(e.value), f"shape {shape_id!r} leaked the value: {e.value}"


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

    Also asserts NEVER-ECHO over the same derived population (#212 round 3): the planted
    body's value IS the sentinel, so one extra assertion below covers every container
    field without a second hand-list -- see `test_every_malformed_search_entry_shape_
    never_echoes_the_VALUE` for the population this sweep structurally cannot reach
    (`sources.<id>.searches` is a per-ENTRY validator, not a dataclass field).

    `lead_ttl_days`, `lead_layout` and `min_jd_chars` never appear here: they are
    `int`/`str` fields, not `list`/`dict`, so the `isinstance(default, (list, dict))`
    filter below excludes them by construction. That is NOT the same claim as "safe to
    skip" -- #212 round 4 (neu-r4-001) measured that each of their raises interpolates the
    raw value with `!r}`, and that raise fires whenever the value ISN'T the TTL/layout/
    count it is supposed to be, which includes a misindented YAML block landing there
    carrying a SIBLING key's real content (a `target_locations`/`reject_companies` list,
    say) -- at which point `!r}` reproduces that sibling's content in one copy-pasteable
    exception, on three root keys this test's own docstring used to certify safe. Fixed by
    reporting the TYPE rather than the value whenever the value is a list/dict
    (`core/config.py`'s `_safe_scalar_repr`); a genuine scalar typo (`lead_ttl_days: yes`)
    still gets the diagnostic repr these raises exist to give.
    `test_lead_ttl_days_lead_layout_and_min_jd_chars_never_echo_a_CONTAINER_value` below is
    the check that replaces this claim -- read it, not this paragraph, for what is
    actually verified.
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
            with pytest.raises(ValueError) as e:
                loader(path)
            # Assert the KEY, not just the exception TYPE. A guard that raises the
            # same type as the unguarded path it precedes cannot be witnessed by the
            # type alone -- and `notify` proved it: unguarded, `dict("scalar")`
            # raises ValueError by itself, so deleting the notify guard left this
            # sweep, and the whole suite, green.
            assert f.name in str(e.value), (
                f"{block or 'root'}.{f.name} raised, but the message does not name "
                f"the key -- so this row cannot tell a real guard from an incidental "
                f"ValueError from the unguarded path: {e.value}")
            # #212 round 3 (neu-r3-001/tst-r3-001): the planted body's own value IS the
            # sentinel ("scalar"), so this one extra assertion covers never-echo over the
            # SAME derived population the sweep already walks -- no second hand-list, and
            # every future container field is covered the day it lands rather than the day
            # someone remembers to add a row for it.
            assert "scalar" not in str(e.value), (
                f"{block or 'root'}.{f.name}'s refusal echoed the planted value: {e.value}")
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


# ── the three int/str root fields the sweep above cannot reach ──────────────────────

def test_lead_ttl_days_lead_layout_and_min_jd_chars_never_echo_a_CONTAINER_value(tmp_path):
    """#212 round 4 (neu-r4-001). `lead_ttl_days`/`lead_layout`/`min_jd_chars` are
    excluded from the sweep above by TYPE (they default to `int`/`str`, never `list`/
    `dict`), and the sweep above's own docstring used to read that exclusion as "safe"
    because "a TTL, a layout name and a character count are not personal". That is true of
    the value each field is SUPPOSED to hold and false of the value its raise actually
    sees on a misindented config: reproduced here with the exact shape measured -- a
    nested block that was meant to sit under a SIBLING key lands as THIS key's value
    instead, a dict carrying the sibling's real content, and the un-fixed `!r}` echoed the
    whole thing in one copy-pasteable `ValueError`.

    A genuine SCALAR typo must still get the diagnostic repr these raises exist to give
    (`test_a_genuine_scalar_typo_still_gets_the_diagnostic_repr` below is the other half).
    """
    secret = "SomeRealTargetLocation"
    for key, body in [
        ("lead_ttl_days",
         f"vault_dir: ./v\nlead_ttl_days:\n  nested: [{secret}]\n  other: {secret}\n"),
        ("lead_layout",
         f"vault_dir: ./v\nlead_layout:\n  nested: [{secret}]\n  other: {secret}\n"),
        ("min_jd_chars",
         f"vault_dir: ./v\nmin_jd_chars:\n  nested: [{secret}]\n  other: {secret}\n"),
    ]:
        path = _write(tmp_path, f"misindent-{key}.yaml", body)
        with pytest.raises(ValueError) as e:
            load_config(path)
        assert secret not in str(e.value), (
            f"{key}'s refusal echoed a misindented sibling's value: {e.value}")
        assert key in str(e.value), f"{key}'s own refusal did not name the key: {e.value}"


def test_lead_ttl_days_lead_layout_and_min_jd_chars_never_echo_a_BYTES_value(tmp_path):
    """Sibling gap to the CONTAINER test above, closed one revision later than the
    `list`/`dict` case: PyYAML's SafeLoader resolves a `!!binary` scalar to `bytes`, not
    to `list`/`dict`, so the un-fixed `_safe_scalar_repr`'s `isinstance(value, (list,
    dict))` check missed it and fell through to `repr(value)` -- reproducing the decoded
    content in full, the identical harm the CONTAINER test guards against one type over.
    This is the SAME gap `validate_search_entry` closed for `sources.<id>.searches`
    entries one commit earlier (`c5aa57e3`); `_safe_scalar_repr` was its sibling on the
    three root-key raises and shipped the gap a second time in the same wave.

    Witnessed by mutation: removing `bytes` from `_safe_scalar_repr`'s isinstance tuple
    reddens every row here on the `secret_text not in msg` assertion.
    """
    import base64
    secret_text = "ZZ-a-private-search-string-ZZ"
    b64 = base64.b64encode(secret_text.encode()).decode()
    for key in ("lead_ttl_days", "lead_layout", "min_jd_chars"):
        body = f"vault_dir: ./v\n{key}: !!binary |\n  {b64}\n"
        path = _write(tmp_path, f"binary-{key}.yaml", body)
        with pytest.raises(ValueError) as e:
            load_config(path)
        msg = str(e.value)
        assert secret_text not in msg, (
            f"{key}'s refusal echoed a !!binary value's decoded content: {msg}")
        assert "bytes" in msg, f"{key}'s refusal did not report the type name: {msg}"
        assert key in msg, f"{key}'s own refusal did not name the key: {msg}"


def test_a_genuine_scalar_typo_still_gets_the_diagnostic_repr(tmp_path):
    """The other half of the neu-r4-001 fix: `_safe_scalar_repr` must not over-correct
    into hiding a genuine scalar mistype, which is the common case these three raises were
    written to diagnose (`lead_ttl_days: yes` is the natural spelling to turn the feature
    ON, and PyYAML resolves it to `True`)."""
    path = _write(tmp_path, "scalar-typo.yaml", "vault_dir: ./v\nlead_ttl_days: yes\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    assert "True" in str(e.value), (
        f"a genuine scalar typo lost its diagnostic repr: {e.value}")


def test_apply_has_no_container_field_and_would_be_guarded_if_it_gained_one(
        tmp_path, monkeypatch):
    """`apply` is the loader with nothing to sweep, so it needs its own assertion.

    A zero count is indistinguishable from broken discovery, so the sweep above cannot
    carry it. This pins the FACT (zero container fields today) and the CONSEQUENCE (its
    loop calls the shared guard, so the day it gains one it is covered) separately.
    """
    import dataclasses

    from sluice.apply.config import ApplyConfig, load_apply_config

    containers = [f.name for f in dataclasses.fields(ApplyConfig)
                  if isinstance((f.default_factory() if f.default_factory
                                 is not dataclasses.MISSING else f.default), (list, dict))]
    assert containers == [], (
        f"apply gained container fields {containers}; add 'apply' to the sweep's expected "
        "map above, which currently records that it has none.")
    # BEHAVIOURAL, not a source-text match. The previous version asserted
    # `"refuse_wrong_container" in inspect.getsource(...)`, which was wrong in BOTH
    # directions: commenting the call out left it GREEN (the string survives in the
    # comment), and aliasing the import turned it RED with behaviour unchanged.
    #
    # ApplyConfig has no container field to test with, so give it one: a subclass
    # with a list default, swapped in for the duration. That exercises the loop's
    # guard exactly as a real future field would.
    import dataclasses as _dc

    @_dc.dataclass
    class _WithContainer(ApplyConfig):
        future_list: list = _dc.field(default_factory=list)

    monkeypatch.setattr("sluice.apply.config.ApplyConfig", _WithContainer)
    path = tmp_path / "apply.yaml"
    path.write_text("apply:\n  future_list: scalar\n", encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_apply_config(str(path))
    assert "future_list" in str(e.value), (
        "apply's loader does not guard a container field, so the one it gains next "
        f"would be silently unprotected: {e.value}")


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


# ── the NESTED call sites, which the dataclass walk cannot reach ─────────────────
#
# `sources.<id>` and `sources.<id>.tuning` live inside a mapping, not on a top-level
# dataclass field, so the sweep above never visits them. Measured before these rows
# existed: deleting either guard left the entire suite green. `searches` had its own row
# already and survived only because of it.

@pytest.mark.parametrize("body,expect_key", [
    ("vault_dir: ./v\nsources: reed\n", "sources"),
    ("vault_dir: ./v\nsources:\n  reed: enabled\n", "reed"),
    ("vault_dir: ./v\nsources:\n  reed:\n    tuning: fast\n", "tuning"),
    ("vault_dir: ./v\nsources:\n  reed:\n    searches: my search\n", "searches"),
    ("vault_dir: ./v\nnotify: telegram\n", "notify"),
])
def test_a_nested_container_refuses_a_scalar_and_names_its_key(tmp_path, body, expect_key):
    path = _write(tmp_path, f"nested-{expect_key}.yaml", body)
    with pytest.raises(ValueError) as e:
        load_config(path)
    # The KEY, deliberately, not just the type. `notify: telegram` reaches `dict(...)`,
    # which raises ValueError unaided -- so a type-only assertion here would pass with the
    # guard deleted, which is exactly how this hole stayed open.
    assert expect_key in str(e.value), (
        f"the refusal for {expect_key!r} does not name it, so this row cannot distinguish "
        f"a real guard from an incidental ValueError: {e.value}")


# ── a `sources.<id>.searches` ENTRY's own shape, one level below the container check ────
#
# `refuse_wrong_container` above only checks that `searches` itself is a list; it says
# nothing about what each ELEMENT looks like. #212 review: four reviewers independently
# reproduced `ingest list-sources` crashing on a malformed entry -- `IndexError` for a
# too-short entry, `KeyError(0)` for the natural YAML mapping spelling -- because
# `_mk_search` (`ingest/base.py`) indexes `spec[0], spec[1]` with no shape check, and
# `main()` catches `ValueError` only, so neither exception produced a clean exit.

def test_a_too_short_search_entry_names_the_source_and_index(tmp_path):
    path = _write(tmp_path, "short.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - ["OnlyLabel"]\n')
    with pytest.raises(ValueError) as e:
        load_config(path)
    msg = str(e.value)
    assert "reed" in msg, f"the source id must be named: {msg}"
    assert "searches[0]" in msg, f"the offending entry's INDEX must be named: {msg}"
    assert "[label, url]" in msg, f"the expected shape must be taught: {msg}"


def test_the_second_entry_being_malformed_names_index_1_not_0(tmp_path):
    # A hand-listed "index 0" in the guard would pass every test above while reporting the
    # wrong entry for every source with more than one search.
    path = _write(tmp_path, "second.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - ["Good", "https://example.invalid/a"]\n'
                  '      - ["OnlyLabel"]\n')
    with pytest.raises(ValueError, match=r"searches\[1\]"):
        load_config(path)


def test_a_mapping_shaped_search_entry_is_refused(tmp_path):
    """The natural YAML spelling of `{label: x, url: y}` -- what a user reaches for
    without knowing the entry must be a flat two-element list. Parses to a dict, which
    `_mk_search`'s old `spec[0], spec[1]` indexed as `KeyError(0)`."""
    path = _write(tmp_path, "mapping.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  "      - label: x\n"
                  "        url: y\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    msg = str(e.value)
    assert "reed" in msg
    assert "searches[0]" in msg


def test_a_search_entry_with_a_non_string_label_is_refused(tmp_path):
    # Pinned on the POSITION named, not the shared "strings" prefix both messages carry
    # -- #212 round 3 (tst-r3-002): a hand-coded `bad, bad_type = ("label", ...)` survives
    # `match="strings"` while reporting the WRONG position for every url-side failure.
    path = _write(tmp_path, "nonstring.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - [5, "https://example.invalid/a"]\n')
    with pytest.raises(ValueError, match=r"the label is a int"):
        load_config(path)


def test_a_search_entry_with_a_non_string_url_is_refused(tmp_path):
    # The label/url check picks out WHICH position is wrong -- a hand-listed "label" in
    # the message would pass the row above while silently never checking url at all, and
    # `match="strings"` alone could not tell the two rows apart (#212 round 3: tst-r3-002).
    path = _write(tmp_path, "nonstring-url.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - ["Label", 5]\n')
    with pytest.raises(ValueError, match=r"the url is a int"):
        load_config(path)


def test_a_search_entry_with_a_non_mapping_third_element_is_refused(tmp_path):
    # #212 round 2 (arc-r2-001/inv-r2-002): element 2 was advertised in the shape
    # message and never checked, so `["L", "u", "perm"]` -- what a user reaches for
    # un-braced -- passed `load_config` cleanly and only exploded inside
    # `_row_to_lead`'s `{**extra, **search.params}` merge, naming no key or index.
    path = _write(tmp_path, "badparams.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - ["Label", "https://example.invalid/a", "perm"]\n')
    with pytest.raises(ValueError, match="mapping"):
        load_config(path)


def test_a_search_entry_with_a_null_third_element_still_loads(tmp_path):
    # An explicit YAML `null` third element is the same as omitting it -- `_mk_search`
    # already treats `len(spec) > 2` as the only params-vs-none distinction, and a
    # too-strict rewrite of the third-element check could reject it.
    path = _write(tmp_path, "nullparams.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - ["Label", "https://example.invalid/a", null]\n')
    got = load_config(path).source("reed").searches
    assert got == [["Label", "https://example.invalid/a", None]]


@pytest.mark.parametrize("literal,typename", [("5", "int"), ("null", "NoneType"),
                                              ("true", "bool")])
def test_an_unsized_search_entry_is_refused_as_a_ValueError(tmp_path, literal, typename):
    # #212 round 3 (tst-r3-003): `len()` raises `TypeError` on these, and `cli.main()`
    # catches `ValueError` only -- the round-1 raw-traceback bug class, on a shape
    # `refuse_wrong_container` cannot see (it only confirms `searches` itself is a list,
    # never what an individual entry looks like). `- 5` / `- null` / `- true` are all
    # ordinary YAML a user can type under `searches:`.
    path = _write(tmp_path, f"unsized-{typename}.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  f"      - {literal}\n")
    with pytest.raises(ValueError, match=f"got a {typename}"):
        load_config(path)


def test_a_bare_string_search_entry_does_not_leak_its_length(tmp_path):
    # #212 round 3 (tst-r3-006): a bare-string entry (`searches:` with `- MyRealSearch`)
    # hits the SAME length-arm branch as the shapes above, but unlike a list/tuple/dict's
    # length, a STRING's length is derived from the user's own real search text -- so it
    # is withheld too, not just the text itself.
    secret = "SomeRealEmployerLtd"
    path = _write(tmp_path, "barestring.yaml",
                  f"vault_dir: ./v\nsources:\n  reed:\n    searches:\n      - {secret}\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    msg = str(e.value)
    assert secret not in msg, f"the bare-string entry's value leaked: {msg}"
    assert "length" not in msg, f"the bare-string entry's length leaked: {msg}"


def test_a_well_formed_search_entry_still_loads(tmp_path):
    # The two- and three-element shapes both go through the entry-shape guard unharmed --
    # this is the regression guard the four rows above would not, by themselves, catch a
    # too-strict rewrite of the check breaking.
    path = _write(tmp_path, "good.yaml",
                  "vault_dir: ./v\nsources:\n  reed:\n    searches:\n"
                  '      - ["Two", "https://example.invalid/a"]\n'
                  '      - ["Three", "https://example.invalid/b", {"job_type": "perm"}]\n')
    got = load_config(path).source("reed").searches
    assert len(got) == 2


@pytest.mark.parametrize("entry", ["a-search-of-known-length", b"a-search-of-known-length"])
def test_a_scalar_entrys_LENGTH_is_withheld_because_the_scalar_IS_the_search(entry):
    """A bare scalar under `searches:` IS the user's search text, so its LENGTH is content.

    Every other shape reports `got a <type> of length <n>`, and that is safe: the length of
    a list, tuple or dict says nothing about what is in it. A `str` or `bytes` is the
    exception, and both spellings are reachable -- PyYAML's SafeLoader resolves a plain
    scalar to `str` and `!!binary` to `bytes`, so `- !!binary <base64 of the search>` lands
    here with its length intact. The `bytes` arm shipped unguarded once (round-4
    inv-r4-003) precisely because the `str` carve-out had no test to extend.
    """
    with pytest.raises(ValueError) as e:
        validate_search_entry("sources.demo.searches", 0, entry)
    msg = str(e.value)
    assert f"got a {type(entry).__name__}" in msg, msg
    assert "length" not in msg, f"the scalar's length leaks its search text: {msg}"


def test_safe_scalar_repr_reproduces_ONLY_the_closed_vocabulary_types():
    """The property is the ALLOW-LIST, not a roster of containers to redact.

    The deny-list spelling was written three times and wrong three times: `(list, dict)`
    missed `bytes`, `(list, dict, bytes)` missed `set` and `tuple`. Each revision closed
    the type that had just been reported and left the class open, so the next reviewer
    found the next member. Asserting "only these five reproduce" cannot rot that way -- a
    type added to Python, or reachable through a YAML tag nobody here has thought of, is
    redacted by default rather than by having been enumerated.

    `str` is in the allow-list deliberately: a `str` under these three keys is a scalar
    typed on that key's own line, and reproducing it is the diagnostic these raises exist
    to give (`lead_layout: flatt`). A misindented SIBLING block -- the leak neu-r4-001
    measured -- arrives as a `list` or `dict`, never as a `str`.
    """
    from sluice.core.config import _safe_scalar_repr

    secret = "ZZ-a-private-value-ZZ"
    for value in (7, 1.5, True, None, secret):
        assert _safe_scalar_repr(value) == repr(value), (
            f"{type(value).__name__} must keep its diagnostic repr")

    # Everything else, whether or not a YAML tag reaches it today.
    redacted = [secret.encode(), [secret], {"k": secret}, {secret}, (secret,),
                frozenset({secret})]
    assert len(redacted) >= 6, "the redacted population went empty -- this asserts nothing"
    for value in redacted:
        got = _safe_scalar_repr(value)
        assert got == f"a {type(value).__name__}", f"{type(value).__name__}: {got}"
        assert secret not in got, f"{type(value).__name__} reproduced its contents: {got}"


def test_a_yaml_set_under_the_three_root_keys_never_echoes_its_members(tmp_path):
    """`!!set` is plain SafeLoader, so this reaches `load_config` end to end.

    The unit test above pins the shape; this pins that a real config file carrying the
    shape actually traverses the guarded path, which a direct call cannot show.
    """
    secret = "ZZ-a-private-member-ZZ"
    for key in ("lead_ttl_days", "lead_layout", "min_jd_chars"):
        body = f"vault_dir: ./v\n{key}: !!set\n  ? {secret}\n  ? other\n"
        path = _write(tmp_path, f"set-{key}.yaml", body)
        with pytest.raises(ValueError) as e:
            load_config(path)
        assert secret not in str(e.value), f"{key} echoed a set member: {e.value}"
        assert key in str(e.value), f"{key}'s refusal did not name the key: {e.value}"
