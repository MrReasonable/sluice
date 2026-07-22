"""An unconfigured sluice bins nothing.

Empty-config-abstains, end to end: with every preference gate explicitly empty,
a lead that a *configured* gate would reject is NOT dismissed. The trap this is
built to avoid is `build_harness`'s default `target_locations=("remote",)` -- the
literal 672ad2a bug value -- so both arms set the gates explicitly. The anti-
vacuity proof is the attribution arm: the SAME board-ingested lead, run once more
with `target_locations=("Alfa",)` (a neutral conftest token that does NOT match
"Remote"), comes back `dismiss`. That proves the location gate would bin this
exact lead, so the empty-gate pass is abstention -- not a bland lead. Witness and
attribution are aligned on the location gate; the lead carries "Remote" via the
source's parse-time `extra`, so deleting the guard reddens the abstain arm.
"""
from sluice.ingest import sources as _sources

from tests.harness import ScriptedBackend, build_harness

BOARD_URL = "https://remoteok.example/harness"
ROWS = [{"title": "Staff Engineer", "company": "Example Foundry",
         "link": "https://remoteok.example/jobs/1", "salary": ""}]


def _ingest_and_triage(h):
    app = h.sluice(ScriptedBackend(default_verdict="shortlist"))
    app.ingest([_sources.get("remoteok")])
    app.triage(statuses=("new",))
    return h.vault.read_leads()[0]


def test_an_empty_config_bins_nothing(tmp_path, monkeypatch):
    # ── Arm 1: every gate empty -> the located lead is NOT dismissed (abstain) ──
    abstain_dir = tmp_path / "abstain"
    abstain_dir.mkdir()
    h = build_harness(abstain_dir, monkeypatch, board_url=BOARD_URL, rows=ROWS,
                      target_locations=(), accept_titles=(), reject_titles=(),
                      perm_floor_gbp=0, contract_floor_gbp_day=0)
    lead = _ingest_and_triage(h)
    assert lead.fm.get("location") == "Remote"   # precondition: lead is located
    assert lead.status != "dismiss"              # the empty location gate abstained

    # ── Arm 2 (attribution): same lead, a non-matching neutral target -> dismiss.
    # Proves the location gate WOULD bin it, so Arm 1 is abstention, not a bland lead.
    attrib_dir = tmp_path / "attrib"
    attrib_dir.mkdir()
    h2 = build_harness(attrib_dir, monkeypatch, board_url=BOARD_URL, rows=ROWS,
                       target_locations=("Alfa",), accept_titles=(), reject_titles=(),
                       perm_floor_gbp=0, contract_floor_gbp_day=0)
    assert _ingest_and_triage(h2).status == "dismiss"
