import textwrap

from sluice.core.config import load_config


def test_defaults_when_no_file(monkeypatch):
    monkeypatch.delenv("SLUICE_LOCATIONS", raising=False)
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    cfg = load_config(None)
    # Empty, not ["Remote"]: geography is a personal preference and none ships in
    # code. This assertion used to PIN the non-neutral default, so the next person to
    # neutralise it would have seen a red test and reverted the fix.
    assert cfg.locations == []
    assert cfg.source("anything").enabled is True
    assert cfg.source("anything").tuning == {}


def test_yaml_disables_a_source(tmp_path):
    p = tmp_path / "sluice.yaml"
    p.write_text("sources:\n  cord:\n    enabled: false\n")
    cfg = load_config(str(p))
    assert cfg.source("cord").enabled is False
    assert cfg.source("wttj").enabled is True  # unlisted → default enabled


def test_yaml_tuning_and_locations(tmp_path):
    p = tmp_path / "sluice.yaml"
    p.write_text(textwrap.dedent("""
        locations: [Clarkefurt]
        sources:
          jobserve:
            enabled: true
            tuning:
              wait: 8
    """))
    cfg = load_config(str(p))
    assert cfg.locations == ["Clarkefurt"]
    assert cfg.source("jobserve").tuning["wait"] == 8


def test_env_locations_override(tmp_path, monkeypatch):
    p = tmp_path / "sluice.yaml"
    p.write_text("locations: [Clarkefurt]\n")
    monkeypatch.setenv("SLUICE_LOCATIONS", "Palmerburgh, Remote")
    cfg = load_config(str(p))
    assert cfg.locations == ["Palmerburgh", "Remote"]


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
