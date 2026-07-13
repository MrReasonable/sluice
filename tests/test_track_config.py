import textwrap
from sluice.track.config import TrackConfig, load_track_config


def test_defaults():
    c = TrackConfig()
    assert c.token_path == "./google_token.json"
    assert c.calendar_match_minutes == 30
    assert c.auto_reject_min == 0.9
    assert "greenhouse.io" in c.ats_relay_domains


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
