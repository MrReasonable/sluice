from types import SimpleNamespace

import pytest

from sluice.ingest.base import (
    BrowserListSource,
    Ctx,
    Search,
    searches_for,
)


class _FakeConfig:
    """Minimal config stand-in: maps source id -> object with a `.searches` list."""

    def __init__(self, overrides):
        self._o = overrides

    def source(self, id):
        return SimpleNamespace(searches=self._o.get(id, []))


def _demo_browser(**kw):
    return BrowserListSource(id="demo", searches_spec=[("Analyst", "http://x")],
                             extractor_js="JS", **kw)


def test_browserlist_parse_maps_rows_to_leads():
    src = _demo_browser()
    raw = {"result": [{"title": "Analyst", "company": "Acme", "link": "http://x/1"}],
           "landed": "http://x"}
    leads = src.parse(raw, Search("Analyst", "http://x"))
    assert leads[0].title == "Analyst"
    assert leads[0].url == "http://x/1"
    assert leads[0].source == "demo"
    assert leads[0].search == "Analyst"


def test_browserlist_parse_skips_titleless_rows():
    src = _demo_browser()
    raw = {"result": [{"title": "", "link": "http://x/1"},
                      {"title": "Analyst", "link": "http://x/2"}]}
    leads = src.parse(raw, Search("Analyst"))
    assert [l.url for l in leads] == ["http://x/2"]


def test_browserlist_extra_overrides_applied():
    src = _demo_browser(extra={"job_type": "contract"})
    leads = src.parse({"result": [{"title": "Analyst", "link": "http://x/1"}]}, Search("Analyst"))
    assert leads[0].job_type == "contract"


def test_searches_builds_search_objects():
    src = _demo_browser()
    s = src.searches()
    assert [x.label for x in s] == ["Analyst"]
    assert s[0].url == "http://x"


def test_searches_for_uses_builtin_without_config():
    src = _demo_browser()
    s = searches_for(src, None)
    assert [(x.label, x.url) for x in s] == [("Analyst", "http://x")]


def test_searches_for_config_override_replaces_builtin():
    src = _demo_browser()
    cfg = _FakeConfig({"demo": [["Mine", "http://y", {"job_type": "perm"}]]})
    s = searches_for(src, cfg)
    assert [(x.label, x.url, x.params) for x in s] == [
        ("Mine", "http://y", {"job_type": "perm"})
    ]


def test_searches_for_empty_override_falls_back_to_builtin():
    src = _demo_browser()
    cfg = _FakeConfig({"demo": []})  # present but empty → built-in wins
    s = searches_for(src, cfg)
    assert [(x.label, x.url) for x in s] == [("Analyst", "http://x")]


def test_per_search_params_override_source_extra():
    # one engine, perm + contract by parameter: a perm search on a contract-default
    # source still tags the lead job_type=perm.
    src = BrowserListSource(id="demo", extractor_js="JS", extra={"job_type": "contract"},
                            searches_spec=[("Contract", "http://x"),
                                           ("Perm", "http://y", {"job_type": "perm"})])
    contract, perm = src.searches()
    c = src.parse({"result": [{"title": "Analyst", "link": "http://x/1"}]}, contract)
    p = src.parse({"result": [{"title": "Analyst", "link": "http://y/1"}]}, perm)
    assert c[0].job_type == "contract"   # source default
    assert p[0].job_type == "perm"       # per-search override


def test_health_hint_reports_count_and_hosts():
    src = _demo_browser()
    raw = {"result": [{"title": "A", "link": "u"}],
           "landed": "https://x.com/a", "requested": "https://x.com/s"}
    hint = src.health_hint(raw)
    assert hint["count"] == 1
    assert hint["landed_host"] == "x.com"
    assert hint["requested_host"] == "x.com"
    assert hint["landed_path"] == "/a"
    assert hint["requested_path"] == "/s"
    assert "degraded" not in hint, "nothing stamped a row -- there is nothing to promote"


def test_health_hint_reports_empty_paths_on_an_empty_url():
    # Unconditional "", mirroring landed_host/requested_host -- a path is a measurement
    # that always exists, not an event that "fired".
    src = _demo_browser()
    hint = src.health_hint({"result": [], "landed": "", "requested": ""})
    assert hint["landed_path"] == "" and hint["requested_path"] == ""


def test_health_hint_paths_carry_no_query_string():
    # #156's `login` drift reason deliberately does not match on query tokens -- two real
    # false positives were measured against it (an ordinary `?q=account+manager` search;
    # a healthy redirect merely gaining `session_id=`). This is the producer half of that
    # decision: `urlparse(...).path` already excludes the query, so a query token can never
    # reach `login_wall` in the real pipeline, not merely "chosen not to match".
    src = _demo_browser()
    raw = {"result": [],
           "landed": "https://example.invalid/jobs?q=account+manager&session_id=abc123",
           "requested": "https://example.invalid/jobs?q=account+manager"}
    hint = src.health_hint(raw)
    assert hint["landed_path"] == "/jobs" and hint["requested_path"] == "/jobs"


def test_health_hint_promotes_a_degraded_row_marker_from_a_browserlist_source():
    # #156: a row the extractor's own fallback stamped is direct evidence of degradation,
    # promoted so `detect_drift` can report `fallback` instead of a silently healthy count.
    src = _demo_browser()
    raw = {"result": [{"title": "A", "link": "u", "degraded": "anchor-fallback"}]}
    assert src.health_hint(raw)["degraded"] == "anchor-fallback"


def test_health_hint_promotes_the_FIRST_degraded_marker_only():
    src = _demo_browser()
    raw = {"result": [{"title": "A", "link": "u1", "degraded": "anchor-fallback"},
                      {"title": "B", "link": "u2", "degraded": "link-fallback"}]}
    assert src.health_hint(raw)["degraded"] == "anchor-fallback"


def test_browserlist_fetch_drives_camofox_with_fake():
    calls = []

    class FakeCam:
        def create_tab(self, url=""):
            calls.append(("create_tab", url))
            return "t1"

        def evaluate(self, tid, expr):
            calls.append(("evaluate", expr))
            if expr == "location.href":
                return {"result": "http://x/landed"}
            return {"result": [{"title": "Analyst", "link": "http://x/1"}]}

        def scroll(self, tid, amount):
            calls.append(("scroll", amount))
            return {}

        def close_tab(self, tid):
            calls.append(("close_tab", tid))
            return {}

    ctx = Ctx(camofox=FakeCam(), config=None, sleep=lambda *_: None)
    raw = _demo_browser(scrolls=2).fetch(ctx, Search("Analyst", "http://x"))
    assert raw["result"] == [{"title": "Analyst", "link": "http://x/1"}]
    assert raw["landed"] == "http://x/landed"
    assert raw["requested"] == "http://x"
    assert ("create_tab", "http://x") in calls
    assert calls.count(("scroll", 800)) == 2
    assert ("close_tab", "t1") in calls


def test_browserlist_fetch_returns_empty_when_no_tab():
    class NoTabCam:
        def create_tab(self, url=""):
            return None

    ctx = Ctx(camofox=NoTabCam(), sleep=lambda *_: None)
    raw = _demo_browser().fetch(ctx, Search("Analyst", "http://x"))
    assert raw["result"] == [] and raw["error"] == "no-tab"


def test_a_builtin_example_search_is_not_marked_configured():
    src = _demo_browser()
    assert [s.configured for s in searches_for(src, None)] == [False]


def test_a_config_override_search_is_marked_configured():
    src = _demo_browser()
    cfg = _FakeConfig({"demo": [["Mine", "https://example.invalid/q", {"job_type": "perm"}]]})
    assert [s.configured for s in searches_for(src, cfg)] == [True]


def test_an_empty_override_falls_back_and_is_not_marked_configured():
    # An override that is present but empty falls back to the built-in, so the
    # fallback must not inherit the override branch's provenance.
    src = _demo_browser()
    cfg = _FakeConfig({"demo": []})
    assert [s.configured for s in searches_for(src, cfg)] == [False]


def test_every_registered_source_ships_exactly_one_unconfigured_example():
    """SCOPE assertion, not a behaviour one: this sweep is what stops the three tests
    above certifying an empty set. A source with no built-in searches would make
    `configured is False` vacuously true for it, and #212 is precisely about the
    built-in set being non-empty and invisible.

    EXACTLY one, not merely non-empty (#212 round 3, neu-r3-002): the 2026-08-21 ruling
    ("each source ships exactly ONE neutral example search") is asserted in PROSE in
    three places this branch touches (`cli.py`, `ingest/engine.py`,
    `test_engine.py::test_source_result_counts_the_example_searches_it_ran`'s docstring),
    each using it to justify the `(n/m)` fraction over a bare flag -- and a source gaining
    a SECOND shipped search would keep the whole suite green while making all three
    comments false and adding a second shipped opinion about what to search for, the one
    place sluice expresses a preference at all. This turns that prose claim into a check;
    a board that genuinely needs two searches should fail here and record why.

    Filtered to `sluice.` classes, same as `_every_registered_source()`
    (test_source_auth_probe.py) and the URL-vocabulary sweep in test_health_wrong_page.py
    (`test_no_shipped_source_search_url_already_matches_the_vocabulary`): the registry is
    a global any test could register into, and "shipped sources" is the population this
    sweep is about regardless of what any one test happens to register."""
    from sluice.ingest import sources as registry
    sources = [s for s in registry.all_sources()
               if type(s).__module__.startswith("sluice.")]
    assert len(sources) >= 10, f"registry enumerated only {len(sources)} sources"
    for src in sources:
        builtin = searches_for(src, None)
        assert len(builtin) == 1, (
            f"{src.id} ships {len(builtin)} example searches -- the 2026-08-21 ruling is one")
        assert all(s.configured is False for s in builtin), f"{src.id} marks a built-in configured"


# ── `_mk_search`'s own defence-in-depth guard (#212 review) ──────────────────────────
#
# `core/config.py`'s `validate_search_entry` is the ONE grammar behind all three rungs
# (#212 round 2); these tests drive `_mk_search` directly, the path a config-load check
# cannot reach -- a test, a future caller, or a source's own `searches_spec` literal.
# `searches_for`'s try/except covers only the `config.source(...)` lookup, never the list
# comprehension that calls `_mk_search`, so a malformed override entry reaches the raise
# below unguarded by anything else in `searches_for` itself.

def test_mk_search_refuses_a_too_short_spec():
    from sluice.ingest.base import _mk_search
    with pytest.raises(ValueError, match=r"\[label, url\]"):
        _mk_search(["OnlyLabel"])


def test_mk_search_refuses_a_mapping_shaped_spec():
    # The natural YAML mapping spelling of an entry parses to a dict -- what a config-load
    # bypass (a hand-built override, a test) might also pass. `spec[0]` on a dict raised a
    # bare `KeyError(0)` before this guard existed.
    from sluice.ingest.base import _mk_search
    with pytest.raises(ValueError, match=r"\[label, url\]"):
        _mk_search({"label": "x", "url": "y"})


def test_mk_search_refuses_a_non_string_label_or_url():
    from sluice.ingest.base import _mk_search
    with pytest.raises(ValueError, match="strings"):
        _mk_search([5, "https://example.invalid/x"])


def test_mk_search_refuses_a_non_mapping_third_element():
    # #212 round 2 (arc-r2-001/inv-r2-002): the third element was advertised in the
    # shape message and never checked -- a scalar `params` passed this rung cleanly and
    # only exploded inside `_row_to_lead`'s `{**extra, **search.params}` merge.
    from sluice.ingest.base import _mk_search
    with pytest.raises(ValueError, match="mapping"):
        _mk_search(["Label", "https://example.invalid/x", "perm"])


def test_mk_search_still_accepts_two_and_three_element_specs():
    from sluice.ingest.base import _mk_search
    two = _mk_search(["Label", "https://example.invalid/a"])
    assert (two.label, two.url, two.params) == ("Label", "https://example.invalid/a", None)
    three = _mk_search(["Label", "https://example.invalid/a", {"job_type": "perm"}])
    assert three.params == {"job_type": "perm"}
    null_params = _mk_search(["Label", "https://example.invalid/a", None])
    assert null_params.params is None


# ── #212 round 4 (arc-r4-001): a `{params}` key colliding with a `Lead` identity field
# must never reach `_row_to_lead`'s verbatim `setattr` loop ─────────────────────────────

def test_mk_search_refuses_a_params_key_that_collides_with_a_lead_identity_field():
    # Measured before this guard: a `url` params key silently REPLACED the scraped url --
    # `_row_to_lead`'s `setattr` loop applies every params key verbatim, with no field
    # allowlist of its own, and `url` is what the vault's non-resurrection match records
    # into `seen.db`, which has no removal path.
    from sluice.ingest.base import _mk_search
    with pytest.raises(ValueError, match="url"):
        _mk_search(["Label", "https://example.invalid/x",
                     {"job_typ": "perm", "url": "PWN"}])


def test_mk_search_refusal_names_the_colliding_key_never_the_value():
    # Never-echo holds for the NEW arm too: the key is structural sluice vocabulary and
    # may be named; "PWN" (the value that would have poisoned lead.url) must not appear.
    from sluice.ingest.base import _mk_search
    with pytest.raises(ValueError) as e:
        _mk_search(["Label", "https://example.invalid/x", {"url": "PWN"}])
    assert "PWN" not in str(e.value), str(e.value)


def test_a_non_colliding_params_key_is_still_applied_verbatim():
    """The narrow half of #212 round 4's ruling (arc-r4-001), pinned so #223's
    implementer inherits a known baseline rather than a surprise: only a KEY that
    collides with a declared `Lead` field is refused. A typo'd or otherwise unrecognised
    key -- `job_typ`, one character short of the real `job_type` -- is NOT in
    `_PARAMS_KEY_CLASH` (it names no `Lead` field at all) and is still set verbatim by
    `_row_to_lead`'s `setattr` loop, exactly as before this fix. #223 is what is expected
    to narrow this further with a real allowlist; this test only pins that round 4 did not
    silently do so already.
    """
    src = BrowserListSource(
        id="demo", extractor_js="JS",
        searches_spec=[("Label", "http://x", {"job_typ": "perm"})])
    search = src.searches()[0]
    leads = src.parse({"result": [{"title": "Analyst", "link": "http://x/1"}]}, search)
    assert leads[0].job_typ == "perm"   # the typo'd attribute really was set, verbatim
    assert leads[0].job_type == ""      # ...and the REAL field was untouched by it


def test_mk_search_still_permits_the_sanctioned_job_type_override():
    # `job_type` is the ONE exclusion from `_PARAMS_KEY_CLASH`: it is the override
    # `_row_to_lead`'s own docstring documents ("a perm search on a contract-default
    # source still tags the lead job_type=perm"), and several shipped sources rely on it.
    from sluice.ingest.base import _mk_search
    search = _mk_search(["Label", "https://example.invalid/x", {"job_type": "perm"}])
    assert search.params == {"job_type": "perm"}


def test_searches_for_propagates_mk_searchs_refusal_for_a_malformed_override():
    # `searches_for`'s try/except wraps only the config LOOKUP -- confirming the
    # list-comprehension call to `_mk_search` is genuinely outside it, not merely
    # documented as such.
    src = _demo_browser()
    cfg = _FakeConfig({"demo": [["OnlyLabel"]]})
    with pytest.raises(ValueError, match=r"\[label, url\]"):
        searches_for(src, cfg)


# ── `BrowserListSource.__post_init__`'s eager check over the whole `searches_spec`
# (#212 round 2, arc-r2-002) ──────────────────────────────────────────────────────────
#
# Before this, a malformed `searches_spec` literal constructed the source fine and only
# failed later, inside `_row_to_lead`, naming no source, key or index -- and the
# registry's per-plugin isolation ("a broken plugin must not sink the rest") never got a
# chance to run, because nothing raised at the point a broken plugin is imported.

def test_a_malformed_searches_spec_raises_at_construction():
    with pytest.raises(ValueError) as e:
        BrowserListSource(id="demo", searches_spec=[("OnlyLabel",)], extractor_js="JS")
    msg = str(e.value)
    assert r"[label, url]" in msg, msg
    # #212 round 3 (tst-r3-005): the shape substring alone is satisfied even by a
    # hand-coded owner string that drops `self.id` -- the whole reason rung 3 exists is
    # that the OLD failure "named no source, key or index".
    assert "source demo.searches_spec" in msg, msg


def test_a_malformed_searches_spec_names_the_source_and_the_entry_index():
    # #212 round 3 (tst-r3-004): the `index` parameter threaded through this round is
    # unpinned without this -- a hard-coded 0, or an owner that drops `self.id`, survives
    # the test above (which only checks the FIRST entry) while reporting the wrong entry
    # for every source with more than one search.
    with pytest.raises(ValueError) as e:
        BrowserListSource(id="demo", searches_spec=[
            ("Good", "https://example.invalid/a"),
            ("OnlyLabel",),
        ], extractor_js="JS")
    msg = str(e.value)
    assert "source demo.searches_spec" in msg, msg
    assert "[1]" in msg, msg


def test_a_malformed_override_entry_names_its_own_index():
    # #212 round 3 (tst-r3-004): the mirror case at rung 2 (`searches_for` -> `_mk_search`)
    # -- a config override, not a shipped `searches_spec` literal.
    src = _demo_browser()
    cfg = _FakeConfig({"demo": [["Good", "https://example.invalid/a"], ["OnlyLabel"]]})
    with pytest.raises(ValueError, match=r"\[1\]"):
        searches_for(src, cfg)


def test_a_none_searches_spec_is_refused_rather_than_crashing():
    # CodeRabbit, #212 round 3: `searches_spec=None` (or any other non-iterable) reached
    # `enumerate(...)` before this guard existed and raised a raw `TypeError` naming no
    # source at all -- uncaught by `cli.main()`'s `ValueError`-only except.
    with pytest.raises(ValueError, match="source demo.searches_spec"):
        BrowserListSource(id="demo", searches_spec=None, extractor_js="JS")


@pytest.mark.parametrize("yaml_body", [
    "vault_dir: ./v\n",                                            # no sources: block at all
    "vault_dir: ./v\nsources:\n  reed:\n    searches: null\n",
    "vault_dir: ./v\nsources:\n  reed:\n    searches: []\n",
])
def test_absent_null_and_empty_searches_all_fall_back_end_to_end(tmp_path, yaml_body):
    # #212 round 3 (tst-r3-008): the empty-override fallback pinned above
    # (`test_an_empty_override_falls_back_and_is_not_marked_configured`) goes through a
    # `_FakeConfig`, never `load_config` -- so a `load_config` change that turned an
    # absent/null `searches` into something TRUTHY would not be caught there. This drives
    # all three spellings through a real `load_config` and a real registered source.
    from sluice.core.config import load_config
    from sluice.ingest import sources as registry

    path = tmp_path / "abstain.yaml"
    path.write_text(yaml_body, encoding="utf-8")
    cfg = load_config(str(path))
    src = registry.get("reed")
    searches = searches_for(src, cfg)
    assert [s.configured for s in searches] == [False]
    assert [s.label for s in searches] == [s.label for s in src.searches()]

