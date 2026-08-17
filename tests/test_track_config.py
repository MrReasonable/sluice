import pytest
import textwrap
from sluice.track.config import TrackConfig, load_track_config


def test_defaults():
    c = TrackConfig()
    # token_path was asserted here as "./google_token.json". #80 made it a path field
    # that ships BLANK -- a non-empty default is always truthy and short-circuits
    # per-system resolution -- so the assertion MOVED rather than vanished: both the
    # blank default and where the loader lands it live in tests/test_config_paths.py.
    # Restating the blank here would pin the same fact in two places; restating the old
    # literal would pin the bug.
    assert c.calendar_match_minutes == 30
    assert c.auto_reject_min == 0.9
    # Asserted as a SHAPE, not by naming a vendor: these two are safety denylists whose
    # only load-bearing property is that they ship non-empty (emptying one widens the
    # proof tier) and are keyed by host strings, which `receipt._suffix_match` iterates.
    # Naming a real ATS or board here would pin a brand into a fixture for no extra
    # coverage -- `test_job_board_defaults_cover_every_shipped_source_host` already
    # verifies the board list covers the shipped ingest sources, derived from the
    # registry rather than hand-listed.
    for denylist in (c.ats_relay_domains, c.job_board_domains):
        assert denylist, "a safety denylist must ship non-empty"
        assert all(isinstance(k, str) and k.strip() and v for k, v in denylist.items())


def test_load_overlays_track_block(monkeypatch, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        track:
          auto_reject_min: 0.95
    """))
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    c = load_track_config()
    assert c.auto_reject_min == 0.95
    assert c.gmail_lookback_days == 2  # untouched default


def test_load_defaults_when_no_config(monkeypatch):
    # Retargeted off token_path by #80 (see test_defaults). What this row is actually
    # for survives unchanged: a loader with no config file at all must return the
    # shipped defaults rather than raising. The path half is in test_config_paths.py,
    # where the expected value can be derived from the pinned state root instead of
    # being a literal that is different on every machine.
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_track_config().auto_reject_min == 0.9


def test_config_exposes_backend_selectors():
    # track had no selectors while its backend was hardcoded; config-driven
    # construction needs them, and they must match the other two sub-apps
    # (triage, cv). Carried over from the retired test_cli_backend_selection.py.
    c = TrackConfig()
    assert c.primary_backend == "claude-max"
    assert c.fallback_backend == "deepseek"


def test_safety_denylist_overrides_merge_over_shipped_defaults(tmp_path):
    # A plain setattr REPLACED these dicts: adding one in-house ATS left exactly that one
    # entry and dropped the shipped eight, which makes the PROOF tier MORE permissive --
    # the opposite of what someone adding an entry to a safety denylist intends, and the
    # opposite of what sluice.yaml.example promises. Merge, so no user block can DROP a
    # shipped entry; a user key still wins on collision (relabelling is fine).
    shipped = TrackConfig()
    # The colliding key is DERIVED from the shipped default rather than typed in: the
    # property under test is "a user key wins on collision", which has nothing to do with
    # which vendor happens to sit at that key, and hard-coding one puts a brand in a
    # fixture that the shipped list already carries.
    collide = sorted(shipped.ats_relay_domains)[0]
    p = tmp_path / "s.yaml"
    p.write_text("track:\n"
                 "  ats_relay_domains:\n"
                 "    ats.example.invalid: in-house\n"
                 f'    "{collide}": relabelled\n'
                 "  job_board_domains:\n"
                 "    board.example.invalid: example-board\n")
    c = load_track_config(str(p))
    assert c.ats_relay_domains["ats.example.invalid"] == "in-house"
    assert set(shipped.ats_relay_domains) <= set(c.ats_relay_domains)   # none dropped
    assert c.ats_relay_domains[collide] == "relabelled"                 # user wins on collision
    assert c.job_board_domains["board.example.invalid"] == "example-board"
    assert set(shipped.job_board_domains) <= set(c.job_board_domains)


@pytest.mark.parametrize("block, needle", [
    # An empty LIST is the dangerous one: it read as "not a dict", skipped the merge and
    # took the plain-setattr branch, so the safety denylist ended up EMPTY -- every ATS
    # relay then reads as a single-employer host and can prove which employer a receipt
    # concerns. `[]` for job_board_domains does the same for the boards sluice scrapes.
    ("  ats_relay_domains: []\n", "ats_relay_domains"),
    ("  job_board_domains: []\n", "job_board_domains"),
    # A bare string was worse than empty: it replaced the denylist with a value whose
    # "keys" are its characters, so a nonsense denylist of single letters silently took
    # over from the shipped hosts.
    ("  ats_relay_domains: 'oops'\n", "ats_relay_domains"),
    # A mapping with a non-string key merges fine and then raises TypeError from
    # `host.endswith("." + k)` at MATCH time -- inside engine.run's per-message except,
    # which skips seen.add, so that message re-fails on every future run forever.
    ("  ats_relay_domains:\n    1234: numeric\n", "1234"),
])
def test_invalid_denylist_override_raises_rather_than_emptying(tmp_path, block, needle):
    p = tmp_path / "s.yaml"
    p.write_text("track:\n" + block)
    with pytest.raises(ValueError) as e:
        load_track_config(str(p))
    # The message must name the offending key and show a valid value -- this repo's rule
    # is that an invalid config says what is valid rather than falling through.
    assert needle in str(e.value) and "host.example.invalid" in str(e.value)


def test_valid_one_entry_denylist_still_merges_over_the_defaults(tmp_path):
    # The other half of the same guard: validation must not have turned into a refusal of
    # the ordinary case the config example documents.
    p = tmp_path / "s.yaml"
    p.write_text("track:\n  ats_relay_domains:\n    ats.example.invalid: in-house\n")
    c = load_track_config(str(p))
    assert c.ats_relay_domains["ats.example.invalid"] == "in-house"
    assert set(TrackConfig().ats_relay_domains) <= set(c.ats_relay_domains)


def test_scalar_overrides_still_replace_rather_than_merge(tmp_path):
    # The merge is scoped to the two safety-denylist dict keys. Everything else keeps
    # plain last-wins overlay -- a knob that silently merged would be its own surprise.
    p = tmp_path / "s.yaml"
    p.write_text("track:\n  gmail_extra_query: 'label:jobs'\n  auto_reject_min: 0.5\n")
    c = load_track_config(str(p))
    assert c.gmail_extra_query == "label:jobs" and c.auto_reject_min == 0.5


def test_auto_apply_min_default_and_override(tmp_path):
    from sluice.track.config import TrackConfig, load_track_config
    assert TrackConfig().auto_apply_min == 0.75
    cfg_file = tmp_path / "s.yaml"
    cfg_file.write_text("track:\n  auto_apply_min: 0.9\n")
    assert load_track_config(str(cfg_file)).auto_apply_min == 0.9


# ---- a bare `no` must not silently disable a run ------------------------------------------

_POSITIVE_INT_CONFIG_KEYS = [
    "gmail_max_messages", "calendar_max_events",
    "calendar_lookahead_days", "gmail_lookback_days", "calendar_match_minutes",
]


@pytest.mark.parametrize("key", _POSITIVE_INT_CONFIG_KEYS)
@pytest.mark.parametrize("bad,why", [
    ("no", "PyYAML reads a bare no/off/false as a BOOLEAN, and bool subclasses int"),
    ("0", "a zero bound makes the thing it bounds read or match nothing"),
    ("-5", "a negative bound is nonsense the callers do not check for"),
    ("abc", "a non-integer"),
])
def test_a_nonsense_integer_key_is_REFUSED(tmp_path, monkeypatch, key, bad, why):
    """Swept over every integer key, not just the two this PR added.

    The caps were the reported case -- `gmail_max_messages: no` -> `False` ->
    `min(False, 500)` == 0, so the run reads no mail and reports an ordinary empty run. But
    `calendar_lookahead_days: no` is the same one-word typo with a worse outcome:
    `timedelta(days=False)` is a ZERO-LENGTH search window, so `_find_ours` finds nothing,
    every interview is double-booked and every cancellation left in the calendar. That one
    predates this PR, which is why the guard covers the pre-existing keys too.
    """
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"track:\n  seen_db: {tmp_path}/s.db\n  {key}: {bad}\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfg))
    with pytest.raises(ValueError) as e:
        load_track_config(str(cfg))
    assert key in str(e.value), f"the error must name the key ({why})"


@pytest.mark.parametrize("key", _POSITIVE_INT_CONFIG_KEYS)
def test_an_ordinary_positive_value_is_still_accepted(tmp_path, monkeypatch, key):
    # The refusal must be narrow, or a legitimate config stops loading.
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"track:\n  seen_db: {tmp_path}/s.db\n  {key}: 7\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(cfg))
    assert getattr(load_track_config(str(cfg)), key) == 7


def test_the_swept_key_list_matches_the_module(tmp_path):
    """ENUMERATED against the source, so a new integer key added without validation fails
    here rather than shipping a config that silently disables a run."""
    from sluice.track.config import _POSITIVE_INT_KEYS

    assert set(_POSITIVE_INT_CONFIG_KEYS) == set(_POSITIVE_INT_KEYS), (
        "this test's key list has drifted from sluice/track/config.py")


def test_every_shipped_relay_key_is_a_real_multi_label_host():
    """Both safety denylists (`ats_relay_domains`, `job_board_domains`) are dot-anchored
    suffix matchers (`receipt._suffix_match`: `host == k or host.endswith("." + k)`), so a
    key with fewer than two dot-separated labels -- a bare TLD like "com", or nothing at
    all -- would swallow every host under that TLD, not just the intended vendor. And a key
    that is itself a dot-separated SUFFIX of another key in the same dict makes the longer
    one redundant (the shorter one already matches every host the longer one would), which
    is a sign the roster was assembled by appending rather than by checking against what is
    already there.

    Read from `TrackConfig()`'s actual fields, not the module's `_ATS_RELAY_DOMAINS` /
    `_JOB_BOARD_DOMAINS` constants directly -- the property under test is what a FRESH
    INSTALL gets, and re-importing the same module constants the dataclass fields are built
    from would only prove the constants agree with themselves.
    """
    cfg = TrackConfig()
    for denylist in (cfg.ats_relay_domains, cfg.job_board_domains):
        # Assert the SCOPE before the property: `all()` over an empty collection is
        # vacuously True, so a denylist that silently emptied out would make every
        # assertion below pass for the wrong reason -- CLAUDE.md's "Guard tests fail
        # open" section names this exact shape.
        assert denylist, "a shipped safety denylist must be non-empty"
        for key in denylist:
            labels = key.split(".")
            assert len(labels) >= 2, (
                f"{key!r} has fewer than two dot-separated labels -- a bare TLD or "
                "single label as a suffix-match key would swallow every host under it")
            others = [other for other in denylist if other != key]
            assert not any(key == other or key.endswith("." + other) for other in others), (
                f"{key!r} is a dot-separated suffix of another key in the same denylist, "
                "making the longer entry redundant")
