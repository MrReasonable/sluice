import json
from types import SimpleNamespace
from sluice.apply.config import ApplyConfig
from sluice.apply import packet


def _note(**fm):
    return SimpleNamespace(fm=fm, path="/v/Job Leads/Northwind - Analyst.md")


def test_listing_host_table():
    assert packet.listing_host("https://uk.linkedin.com/jobs/view/123") == "linkedin"
    assert packet.listing_host("https://uk.indeed.com/rc/clk?jk=1") == "indeed"
    assert packet.listing_host("https://job-boards.greenhouse.io/x/jobs/9") == "greenhouse"
    assert packet.listing_host("https://jobs.ashbyhq.com/x/abc") == "ashby"
    assert packet.listing_host("https://jobs.lever.co/x/abc") == "lever"
    assert packet.listing_host("https://apply.workable.com/x/") == "workable"
    assert packet.listing_host("https://careers.icims.com/x") == "icims"
    assert packet.listing_host("https://x.teamtailor.com/jobs/9") == "teamtailor"
    assert packet.listing_host("https://northwind.example/careers/em") == "other"


def test_build_packet_cv_path_only_when_staged():
    cfg = ApplyConfig()
    n = _note(company="Northwind", role="Analyst", location="Edinburgh", salary="", url="https://northwind.example/x")
    staged = packet.build_packet(n, cfg, cv_staged=True)
    assert staged["cv_path"] == "./cv-uploads/CV.pdf"
    preview = packet.build_packet(n, cfg, cv_staged=False)
    assert preview["cv_path"] is None
    assert preview["listing_host"] == "other"


def test_render_text_has_rules_and_no_em_dash():
    cfg = ApplyConfig()
    n = _note(company="Northwind", role="Analyst", location="Edinburgh", salary="", url="https://northwind.example/x")
    text = packet.render_text(packet.build_packet(n, cfg, cv_staged=True))
    assert "\u2014" not in text and "--" not in text
    assert "never" in text.lower() and "one-click" in text.lower()
    assert "first name" in text.lower()
    assert "submit" in text.lower()
    assert "job-application-workflow" in text


def test_render_text_preview_mode_no_dashes():
    cfg = ApplyConfig()
    n = _note(company="Northwind", role="Analyst", location="", salary="", url="https://northwind.example/x")
    text = packet.render_text(packet.build_packet(n, cfg, cv_staged=False))
    assert "\u2014" not in text and "--" not in text
    assert "stag" in text.lower()  # still tells the user to stage the CV first


def test_render_json_roundtrips():
    cfg = ApplyConfig()
    n = _note(company="Northwind", role="Analyst", location="", salary="", url="https://northwind.example/x")
    d = json.loads(packet.render_json(packet.build_packet(n, cfg, cv_staged=False)))
    assert d["company"] == "Northwind" and d["cv_path"] is None
