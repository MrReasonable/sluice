"""Config factory and synthetic fixture set for the e2e tier.

`build_harness` writes a `sluice.yaml` under `tmp_path`, points `SLUICE_CONFIG`
at it, and pins every cwd-relative path INTO `tmp_path` so a run writes nothing
into the repo (the run then asserts the repo root is untouched). It also seeds a
synthetic vault -- a baseline CV and verified Experience Library entries -- so the
CV hop composes against real store reads.

Neutrality: every identity-shaped value is synthetic and reuses conventions
already vetted in this repo. Companies are the `Example ...` family (the set
`test_cv_engine.py`'s CLEAN_CV already uses); the CV author is the `Jane Roe`
placeholder CLEAN_CV also uses; URLs and emails use the reserved `example.invalid`
/ `*.example` domains. Locations use the neutral `Remote` work-arrangement token
(as `test_triage_engine.py` does), and job titles are generic role names -- both
are job-posting CONTENT, not preference lists: the harness ships `accept_titles`
and `reject_titles` empty, so it carries no title taste of its own (conftest's
seeded-faker title fixtures exist for THOSE preference lists, which these
scenarios deliberately leave unset). Pay floors are the one deliberate
exception -- synthetic round numbers chosen for the scenario -- because a
currency-denominated personal number is exactly what must not be copied from real
config.
"""
import json
import os
from dataclasses import dataclass, field

from sluice.core.config import load_config
from sluice.core.vault import Vault

from tests.harness.browser import ScriptedBrowserClient, install_scripted_fetcher
from tests.harness.renderer import Recorder, install_recording_renderer

# ── canned composed CVs ──────────────────────────────────────────────────────
# A fully-cited, slop-free, reverse-chronological CV that PASSES cv/validate.py
# against a bundle whose single verified entry (Example Foundry) codes to [EF1].
# En-dash date ranges (U+2013) are fine; only em-dash / "--" are slop errors.
PASSING_CV = "\n".join([
    "JANE ROE",
    "",
    "WORK EXPERIENCE",
    "",
    "Example Systems",
    "02/2023–present | Remote | Staff Engineer",
    "- Shipped the billing service to production [EF1]",
    "",
    "Example Analytics",
    "06/2020–01/2023 | Remote | Senior Engineer",
    "- Grew the team from 3 to 8 engineers [EF1]",
    "",
    "CERTIFICATES",
    "- CSM",
    "EDUCATION",
    "- Example University, 2015 | BSc",
])

# The SAME CV under a drifted header. cv/engine.py's structural guard fires when
# the exact "WORK EXPERIENCE" line is absent, so the citation gate "did not run"
# and rendering is HARD-blocked -- the proven `test_drifted_work_header` failure
# mode, reached here through the full composition root.
GATE_FAILING_CV = PASSING_CV.replace("WORK EXPERIENCE", "PROFESSIONAL EXPERIENCE")

# One verified Experience Library entry. company -> [EF1] via the prefix_map
# below; its metrics/body carry the 3 and 8 the passing CV's bullet cites.
DEFAULT_EXPERIENCE = [
    {"title": "Grew the delivery team", "company": "Example Foundry",
     "category": "people", "best_for": "delivery", "metrics": "3 8",
     "verified": "true", "body": "Grew the team from 3 to 8 engineers."},
]

DEFAULT_PREFIX_MAP = {"Example Foundry": "EF"}


@dataclass
class Harness:
    config: object                  # root Config (fetcher=scripted, sources override, ...)
    vault: Vault                    # a Vault over the seeded tmp vault, for assertions
    recorder: Recorder             # what the recording renderer captured
    browser: ScriptedBrowserClient
    paths: dict = field(default_factory=dict)

    def sluice(self, backend, *, today=None, sleep=None):
        """A Sluice wired to this harness: the scripted fetcher and recording
        renderer via the config seams, `backend` via the per-instance override,
        and a no-op sleep so the browser's page-settle waits cost nothing."""
        from sluice.core.app import Sluice
        return Sluice(self.config, backend=backend, today=today,
                      sleep=sleep if sleep is not None else (lambda *a, **k: None))


def _seed_vault(vault_dir, *, baseline, experience):
    os.makedirs(os.path.join(vault_dir, "My CV"), exist_ok=True)
    with open(os.path.join(vault_dir, "My CV", "CV.md"), "w", encoding="utf-8") as f:
        f.write(baseline)
    exp_dir = os.path.join(vault_dir, "Job Applications", "Experience Library")
    os.makedirs(exp_dir, exist_ok=True)
    for e in experience:
        fm = "\n".join([
            f'Company: "{e["company"]}"',
            f'Category: "{e.get("category", "")}"',
            f'Best For: "{e.get("best_for", "")}"',
            f'Metrics: "{e.get("metrics", "")}"',
            f'verified: {e.get("verified", "true")}',
        ])
        with open(os.path.join(exp_dir, f"{e['title']}.md"), "w", encoding="utf-8") as f:
            f.write(f"---\n{fm}\n---\n{e.get('body', '')}\n")


def build_harness(tmp_path, monkeypatch, *, board_url, rows,
                  source_id="remoteok",
                  jd_text="Synthetic job description for the harness.",
                  accept_titles=(), target_locations=("remote",),
                  perm_floor_gbp=50000, contract_floor_gbp_day=400,
                  cv_name="Jane Roe", prefix_map=None,
                  experience=None, baseline="Synthetic baseline CV for the harness."):
    """Scaffold a hermetic e2e run and return a Harness. The caller supplies the
    scripted backend (with its per-company CVs / track response) and drives the
    pipeline through `harness.sluice(backend, today=...)`."""
    prefix_map = DEFAULT_PREFIX_MAP if prefix_map is None else prefix_map
    experience = DEFAULT_EXPERIENCE if experience is None else experience

    p = {
        "vault": str(tmp_path / "vault"),
        "seen_db": str(tmp_path / "seen.db"),
        "health": str(tmp_path / "health.json"),
        "triage_dossiers": str(tmp_path / "dossiers-triage"),
        "triage_audit": str(tmp_path / "triage-audit.jsonl"),
        "cv_output": str(tmp_path / "cv-output"),
        "cv_served": str(tmp_path / "cv-served"),
        "cv_dossiers": str(tmp_path / "dossiers-cv"),
        "cv_home": str(tmp_path / "cv-home"),
        "apply_upload": str(tmp_path / "cv-host"),
        "track_seen_db": str(tmp_path / "track-seen.db"),
        "track_token": str(tmp_path / "google_token.json"),
    }

    # Every cwd-relative default a run could write to is pinned, across two
    # mechanisms: the *Config loaders read their block of $SLUICE_CONFIG, but
    # Sluice.triage reads its dossier dir and audit path straight from env
    # (DOSSIER_DIR / TRIAGE_AUDIT), and the ingest SeenDb / HealthStore honour
    # SEEN_DB / SLUICE_HEALTH. (The spec enumerates nine at-risk defaults; the
    # dict below also pins vault/health/upload, which are write targets too.)
    cfg = {
        "fetcher": "scripted",
        "sources": {source_id: {"searches": [["harness", board_url]]}},
        "triage": {
            "accept_titles": list(accept_titles),
            "target_locations": list(target_locations),
            "perm_floor_gbp": perm_floor_gbp,
            "contract_floor_gbp_day": contract_floor_gbp_day,
        },
        "cv": {
            "renderer": "recording",
            "name": cv_name,
            "prefix_map": prefix_map,
            "output_dir": p["cv_output"],
            "served_dir": p["cv_served"],
            "dossier_dir": p["cv_dossiers"],
            "render_home": p["cv_home"],
        },
        "apply": {
            "served_dir": p["cv_served"],       # must match cv.served_dir
            "camofox_upload_dir": p["apply_upload"],
        },
        "track": {
            "seen_db": p["track_seen_db"],
            "token_path": p["track_token"],
        },
    }
    # json.dumps is valid YAML (flow style) and quotes the spaces/paths safely.
    config_path = tmp_path / "sluice.yaml"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")

    monkeypatch.setenv("SLUICE_CONFIG", str(config_path))
    monkeypatch.setenv("VAULT_DIR", p["vault"])
    monkeypatch.setenv("SEEN_DB", p["seen_db"])
    monkeypatch.setenv("SLUICE_HEALTH", p["health"])
    monkeypatch.setenv("DOSSIER_DIR", p["triage_dossiers"])
    monkeypatch.setenv("TRIAGE_AUDIT", p["triage_audit"])

    _seed_vault(p["vault"], baseline=baseline, experience=experience)

    browser = install_scripted_fetcher(
        ScriptedBrowserClient({board_url: rows}, jd_text=jd_text))
    recorder = install_recording_renderer(Recorder())

    return Harness(config=load_config(), vault=Vault(p["vault"]),
                   recorder=recorder, browser=browser, paths=p)
