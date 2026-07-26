"""Reusable e2e-tier harness: fakes at the true I/O boundaries (browser client,
renderer, backend, Google client) and a config factory, so the real `Sluice`
composition root runs `ingest -> triage -> cv -> apply -> track` through the real
`Vault` on `tmp_path`. Consumed by `tests/e2e/` and, later, the functional and
acceptance tiers -- which is why it lives here, not under one layer's directory.
"""
from tests.harness.backend import ScriptedBackend
from tests.harness.browser import ScriptedBrowserClient, install_scripted_fetcher
from tests.harness.config import (
    FIXTURE_ADDR,
    GATE_FAILING_CV,
    PASSING_CV,
    Harness,
    build_harness,
)
from tests.harness.google import FakeGoogleClient
from tests.harness.notes import seed_lead_note
from tests.harness.renderer import Recorder, RecordingRenderer, install_recording_renderer

__all__ = [
    "ScriptedBackend",
    "ScriptedBrowserClient",
    "install_scripted_fetcher",
    "Recorder",
    "RecordingRenderer",
    "install_recording_renderer",
    "FakeGoogleClient",
    "Harness",
    "build_harness",
    "PASSING_CV",
    "GATE_FAILING_CV",
    "FIXTURE_ADDR",
    "seed_lead_note",
]
