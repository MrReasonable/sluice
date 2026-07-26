import textwrap
from sluice.track.config import TrackConfig, load_track_config


def test_defaults():
    c = TrackConfig()
    assert c.token_path == "./google_token.json"
    assert c.calendar_match_minutes == 30
    assert c.auto_reject_min == 0.9
    assert "greenhouse.io" in c.ats_relay_domains
    assert "linkedin.com" in c.job_board_domains


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
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    assert load_track_config().token_path == "./google_token.json"


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
    p = tmp_path / "s.yaml"
    p.write_text("track:\n"
                 "  ats_relay_domains:\n"
                 "    ats.example.invalid: in-house\n"
                 "    greenhouse.io: relabelled\n"
                 "  job_board_domains:\n"
                 "    board.example.invalid: example-board\n")
    c = load_track_config(str(p))
    shipped = TrackConfig()
    assert c.ats_relay_domains["ats.example.invalid"] == "in-house"
    assert set(shipped.ats_relay_domains) <= set(c.ats_relay_domains)   # none dropped
    assert c.ats_relay_domains["greenhouse.io"] == "relabelled"         # user wins on collision
    assert c.job_board_domains["board.example.invalid"] == "example-board"
    assert set(shipped.job_board_domains) <= set(c.job_board_domains)


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
