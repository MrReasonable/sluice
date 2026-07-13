# Sluice

Sluice is an engineered, config-driven job-hunting pipeline. It scans job
boards into a lead store, triages leads with deterministic rules plus an LLM
judge, composes a fabrication-gated CV tailored to each shortlisted role,
preps and records applications, and reconciles the funnel from email and
calendar signals. Every stage is config-first: sane defaults ship in code,
a single YAML file overrides them, and secrets come from the environment.

## Pipeline

```
ingest -> triage -> cv -> apply -> track
```

- **ingest**: scan job boards (via declarative sources) into the lead store, deduping and gating for relevance as it goes.
- **triage**: deterministic classification resolves obvious cases for free; ambiguous leads go to an LLM judge, and verdicts are written back without touching any lead already in the application lifecycle.
- **cv**: select verified source material, compose a tailored CV against a closed bundle, gate it for fabricated claims, render, and serve.
- **apply**: select eligible leads, stage the CV and a prep packet; the actual ATS form-fill is human-driven, this sub-app prepares the material.
- **track**: reconcile the application funnel from email and calendar signals, never regressing a lead's status.

`core/` underlies all five: layered config, the lead/experience store, LLM
backend clients, the shared status vocabulary, the dedup database, and the
resilience helpers (retry, timeout, rate-limit) that every stage wraps its
I/O in.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for more detail.

## Status: work in progress

Sluice currently assumes:

- an Obsidian-style markdown vault as the lead and experience store
- a Claude CLI backend (run locally or shelled out over SSH) for the LLM
  judge and composer, with a cheaper per-token backend as fallback
- an external WeasyPrint render script you supply, for turning a composed
  CV into a PDF
- a browser for ATS forms: an automated browser for ingest sourcing, and a
  human at the keyboard for filling in application forms
- a Google OAuth token for track's Gmail and Calendar access

Each of those is a seam meant to become a pluggable adapter. The roadmap:

- **SP2**: LLM API backend adapter (replace the CLI shell-out with a direct API client)
- **SP3**: bundled renderer (ship a renderer instead of depending on an external script)
- **SP4**: store adapter (a pluggable store behind the Obsidian vault)
- **SP5**: fetch/browser adapter (a pluggable browser automation layer)
- **SP6**: docs and CI

## Quickstart

```bash
pip install -e .
cp sluice.yaml.example sluice.local.yaml
export SLUICE_CONFIG=$(pwd)/sluice.local.yaml
sluice ingest run --help
sluice triage run --help
```

`sluice.local.yaml` is git-ignored, so personal config (locations, employer
lists, contact details, hosts) never lands in the repo.

## Configuration

Every config key is optional and falls back to a code default. See
[`sluice.yaml.example`](sluice.yaml.example) for the full set of knobs
across ingest, triage, cv, and apply, with comments on what each one does.

## License

MIT. See [`LICENSE`](LICENSE).
