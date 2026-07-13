import textwrap
from sluice.apply.config import ApplyConfig, load_apply_config


def test_defaults():
    c = ApplyConfig()
    assert c.served_dir == "./cv-served"
    assert c.camofox_upload_dir == "./cv-host"
    assert c.neutral_name == "CV.pdf"


def test_load_overlays_apply_block(monkeypatch, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        apply:
          camofox_upload_dir: /tmp/uploads
    """))
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    c = load_apply_config()
    assert c.camofox_upload_dir == "/tmp/uploads"
    assert c.served_dir == "./cv-served"  # untouched default


def test_load_defaults_when_no_config(monkeypatch):
    monkeypatch.delenv("SLUICE_CONFIG", raising=False)
    c = load_apply_config()
    assert c.served_dir == "./cv-served"
    assert c.camofox_upload_dir == "./cv-host"
    assert c.neutral_name == "CV.pdf"


def test_load_ignores_yaml_without_apply_block(monkeypatch, tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("cv:\n  ttl_days: 3\n")
    monkeypatch.setenv("SLUICE_CONFIG", str(p))
    c = load_apply_config()
    assert c.camofox_upload_dir == "./cv-host"  # untouched by a cv-only config
