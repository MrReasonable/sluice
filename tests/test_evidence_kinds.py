"""Tests for evidence kind definitions."""
from sluice.core.protocols import EVIDENCE_KINDS
from sluice.core.vault import Vault

_FM = ("---\nCompany: {company}\nCategory: \nBest For: \nMetrics: \n{skills}"
       "verified: 2026-08-01\n---\nBody.\n")


def test_experience_declares_skills_and_reads_blank_or_absent_as_empty(tmp_path):
    """`Skills` is the association #168 stores on the entry, and it must read as `""` in
    BOTH shapes a real vault produces -- they come from different code and cover different
    populations:

      * ABSENT (`gamma`) is the upgrade shape. Every Experience Library note that already
        exists carries no `Skills:` line at all, so the `""` comes from
        `_evidence_entries`' `fm.get(k, "")` DEFAULT. Without this arm that default is
        untested: mutating it to `{k: fm[k] for k in spec.fields if k in fm}` was measured
        to leave the whole suite green.
      * BLANK (`alpha`) is the new-note shape. `_render_evidence_note` writes
        `{k: str(fields.get(k, "")) for k in spec.fields}`, so every note created from now
        on carries an empty `Skills:` and the `""` comes from `_parse_fm_spaced` instead.

    Both are the DEFAULT state rather than an edge case, which is why the gate work treats
    blank as absent everywhere.
    """
    assert "Skills" in EVIDENCE_KINDS["experience"].fields

    v = Vault(str(tmp_path))
    d = tmp_path / "Job Applications" / "Experience Library"
    d.mkdir(parents=True)
    (d / "alpha.md").write_text(_FM.format(company="Example Alpha", skills="Skills: \n"))
    (d / "beta.md").write_text(_FM.format(
        company="Example Beta", skills="Skills: Example Query, Example Framework\n"))
    # No `Skills:` line at all -- the shape of every note written before #168.
    (d / "gamma.md").write_text(_FM.format(company="Example Alpha", skills=""))

    by_title = {e["title"]: e for e in v.read_evidence("experience", verified_only=False)}
    assert by_title["alpha"]["fields"]["Skills"] == ""
    assert by_title["gamma"]["fields"]["Skills"] == ""
    assert by_title["beta"]["fields"]["Skills"] == "Example Query, Example Framework"


def test_experience_add_round_trips_a_skills_value(tmp_path, monkeypatch):
    """The `--skills` flag is GENERATED from `spec.fields` by `cli.py`'s registry loop, so
    nothing hand-written asserts it exists or that a value passed to it survives the
    write/read round trip. The existing per-field-flag test hand-lists the skills kind's
    four flags and never passes `--skills` at all.

    Driven through the real `main()` argv, because the GENERATED flag is the thing under
    test: calling the handler with a hand-built args object would assert nothing about
    whether `cli.py` derives the flag from `spec.fields`.

    Pins the COMMA-STRING spelling specifically. `_parse_fm_spaced` joins a YAML block list
    to the identical comma string, and every later part of the gate work assumes one
    comma-separated value rather than a list.
    """
    from sluice.cli import main

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["experience", "add", "--name", "delta",
                 "--company", "Example Alpha",
                 "--skills", "Example Query, Example Framework"]) == 0

    v = Vault(str(tmp_path))
    entries = {e["title"]: e for e in v.read_pending_evidence("experience")}
    assert entries["delta"]["fields"]["Skills"] == "Example Query, Example Framework"
