"""Whether the misattributed-tool check ran is REPORTED, never left to infer (#364 spec §6.6,
§12.1): four vaults read through the real Vault -- never a hand-built entry dict -- each
asserting the flag on CvResult, run.json and the MCP result, and that `cv run` logs no
attribution WARNING on any of them (doctor's verbose NOTICE is where a vault declaring `Skills`
items but no tools hears it). D13's end-to-end row lives here too, since it needs the same
real-store fixture."""
import json

import pytest

from sluice.core.app import Sluice
from sluice.core.config import Config
from tests.structured_cv import Cache, RecordingRenderer, ReplyBackend, reply
from tests.test_cv_prerequisites import _seed_shortlist_leads, _vault

# "Example Foundry" codes to EX (cv/bundle.py::_prefix's fallback); the entry has no body,
# so the bullet carries no figure.
REPLY = reply(roles={"R1": [{"text": "Shipped the platform", "cites": ["EX1"]}]}, skills=())


def _vault_with(tmp_path, *, tools="", skills="", unverified_tools=""):
    import os

    from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH
    v = _vault(tmp_path, entries=())
    # A declared identity, so each lead reaches composition: `run_one` refuses a blank
    # Candidate Profile (`skipped-config`) before any spend.
    profile = os.path.join(v.dir, CANDIDATE_PROFILE_RELPATH)
    os.makedirs(os.path.dirname(profile), exist_ok=True)
    with open(profile, "w", encoding="utf-8") as fh:
        fh.write("---\nforenames: Jane\nsurname: Roe\nmobile: +1 555 0100\n---\n")
    # `Tools` as a keyword: in a dict literal keyed by the field name, with a variable as
    # its value, the fixture-name sweep reads the variable's NAME as a tool.
    # `Skills` likewise: a declared experience field again (owner decision 2026-10-06), so it
    # is written the way `experience add --skills` writes it.
    fields = dict({"Company": "Example Foundry"}, **(dict(Tools=tools) if tools else {}),
                  **(dict(Skills=skills) if skills else {}))
    v.propose_evidence("experience", name="alpha", fields=fields)
    [pending] = v.read_pending_evidence("experience")
    with open(pending["path"], encoding="utf-8") as fh:
        raw = fh.read()
    assert v.verify_evidence("experience", "alpha", today="2026-09-03", reviewed=raw)
    if unverified_tools:
        v.propose_evidence("experience", name="beta",
                           fields=dict({"Company": "Example Foundry"}, Tools=unverified_tools))
    return v


def _app(v, tmp_path, monkeypatch, replies):
    monkeypatch.chdir(tmp_path)        # cv.output_dir and served_dir default to ./cv-*
    be, rend = ReplyBackend(replies), RecordingRenderer()
    monkeypatch.setattr(Sluice, "backend", lambda self, **kw: be)
    monkeypatch.setattr(Sluice, "renderer", lambda self, cvcfg: rend)
    monkeypatch.setattr(Sluice, "dossier_cache", lambda self, *a, **k: Cache())
    return Sluice(Config(vault_dir=v.dir))


@pytest.mark.parametrize("vault_kw,off", [
    ({"skills": "Examplelang"}, True),             # a Skills line, no Tools
    ({}, True),                                    # neither: an unconfigured install
    ({"tools": "Examplelang"}, False),             # a verified Tools line turns it on
    ({"unverified_tools": "Examplelang"}, True),   # an unverified one does not
], ids=["skills-only", "neither", "verified-tools", "unverified-tools"])
def test_the_attribution_flag_is_reported_on_every_surface(
        tmp_path, monkeypatch, caplog, vault_kw, off):
    from sluice.mcpserver import cv_run
    v = _vault_with(tmp_path, **vault_kw)
    _seed_shortlist_leads(v.dir, n=2)
    app = _app(v, tmp_path, monkeypatch, [REPLY, REPLY, REPLY])
    with caplog.at_level("WARNING"):
        results = app.compose_cv(all_shortlist=True)
    assert [(r.status, r.attribution_check_off) for r in results] == [("rendered", off)] * 2
    # No run-time WARNING on any vault (the owner's model: Skills without Tools is a
    # legitimate shape, so doctor's verbose NOTICE is the one place it is said).
    said = [r for r in caplog.records if "attribution check" in r.getMessage()]
    assert said == []
    for i in range(2):
        run = json.loads((tmp_path / "cv-output" / f"example-foundry-synthetic-role-{i}"
                          / "run.json").read_text(encoding="utf-8"))
        assert run["attribution_check_off"] is off
    assert cv_run(app, lead="SYNTHETIC-ROLE-0")["attribution_check_off"] is off


def test_a_skill_name_the_slug_destroys_reaches_the_cv_through_its_label(tmp_path, monkeypatch):
    """#364 spec §12.1 (D13), through the real store: a skill proposed under a name its filename
    slug destroys, verified, then picked -- the document lists the TYPED spelling, which
    only Label: kept."""
    v = _vault_with(tmp_path)
    v.propose_evidence("skills", name="Examplelang#", fields={"Label": "Examplelang#"})
    [pending] = v.read_pending_evidence("skills")
    assert pending["title"] != "Examplelang#", "premise: the slug changed the typed name"
    with open(pending["path"], encoding="utf-8") as fh:
        raw = fh.read()
    assert v.verify_evidence("skills", pending["title"], today="2026-09-03", reviewed=raw)
    _seed_shortlist_leads(v.dir, n=1)
    picked = reply(roles={"R1": [{"text": "Shipped the platform", "cites": ["EX1"]}]},
                   skills=["Examplelang#"])
    app = _app(v, tmp_path, monkeypatch, [picked])
    [result] = app.compose_cv(all_shortlist=True)
    assert result.status == "rendered"
    [(document, _out)] = app.renderer(None).rendered     # _app's renderer ignores its argument
    assert list(document.skills) == ["Examplelang#"]


def test_unverified_names_and_tools_never_reach_the_pool(tmp_path, monkeypatch):
    """#364 spec §12.1 (Skills pool), through the real store: only VERIFIED entries feed the pool.
    A pick of an unverified skill note's name, or of an unverified entry's tool, is dropped
    and reported, never listed. The verified entry's own tool keeps the pool non-empty, so
    skills ARE requested and the two picks are refused for what they are."""
    v = _vault_with(tmp_path, tools="Examplelang", unverified_tools="Examplelangscript")
    v.propose_evidence("skills", name="Example Query", fields={})      # never verified
    _seed_shortlist_leads(v.dir, n=1)
    picked = reply(roles={"R1": [{"text": "Shipped the platform", "cites": ["EX1"]}]},
                   skills=["Example Query", "Examplelangscript"])
    app = _app(v, tmp_path, monkeypatch, [picked])
    [result] = app.compose_cv(all_shortlist=True)
    [(document, _out)] = app.renderer(None).rendered
    assert list(document.skills) == []
    for name in ("Example Query", "Examplelangscript"):
        assert any(name in dropped for dropped in result.skills_dropped), name


def test_a_verified_skills_item_reaches_the_compose_prompt_only_as_a_pool_candidate(
        tmp_path, monkeypatch):
    """Engine level, through the real Vault: a vault whose ONLY annotation is a verified
    `Skills` item puts that item in the compose prompt's SKILLS pool -- and nowhere else in
    the prompt, since the owner's model ties `Skills` to no job (cv/bundle.py never shows it
    inside an entry)."""
    from sluice.cv.compose import _SKILLS_POOL_PROMPT_HEADER
    v = _vault_with(tmp_path, skills="examplecoach")
    _seed_shortlist_leads(v.dir, n=1)
    be = ReplyBackend([REPLY])
    app = _app(v, tmp_path, monkeypatch, [])
    monkeypatch.setattr(Sluice, "backend", lambda self, **kw: be)
    [result] = app.compose_cv(all_shortlist=True)
    assert result.status == "rendered"
    [prompt] = be.compose_prompts
    pool = prompt.split(_SKILLS_POOL_PROMPT_HEADER, 1)[1].split("\n\n", 1)[0]
    assert "- examplecoach" in pool.splitlines()
    assert prompt.count("examplecoach") == 1, "the item must appear in the pool alone"
