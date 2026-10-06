"""Tests for evidence kind definitions."""
import pytest

from sluice.core.protocols import EVIDENCE_KINDS, EvidenceKind
from sluice.core.vault import Vault

_FM = ("---\nCompany: {company}\nCategory: \nBest For: \nMetrics: \n{extra}"
       "verified: 2026-08-01\n---\nBody.\n")


def test_experience_declares_tools_and_reads_blank_or_absent_as_empty(tmp_path):
    """`Tools` is the attribution index the entry carries (#364/#365/#368, spec §4.2, in
    place of #168's `Skills`), and it must read as `""` in BOTH shapes a real vault
    produces -- they come from different code and cover different populations:

      * ABSENT (`gamma`) is the upgrade shape. Every Experience Library note that already
        exists carries no `Tools:` line at all, so the `""` comes from
        `_evidence_entries`' `fm.get(k, "")` DEFAULT. Without this arm that default is
        untested: mutating it to `{k: fm[k] for k in spec.fields if k in fm}` was measured
        to leave the whole suite green.
      * BLANK (`alpha`) is the new-note shape. `_render_evidence_note` writes
        `{k: str(fields.get(k, "")) for k in spec.fields}`, so every note created from now
        on carries an empty `Tools:` and the `""` comes from `_parse_fm_spaced` instead.

    Both are the DEFAULT state rather than an edge case, which is why the gate work treats
    blank as absent everywhere.
    """
    assert "Tools" in EVIDENCE_KINDS["experience"].fields

    v = Vault(str(tmp_path))
    d = tmp_path / "Job Applications" / "Experience Library"
    d.mkdir(parents=True)
    (d / "alpha.md").write_text(_FM.format(company="Example Alpha", extra="Tools: \n"))
    (d / "beta.md").write_text(_FM.format(
        company="Example Beta", extra="Tools: Example Query, Example Framework\n"))
    # No `Tools:` line at all -- the shape of every note written before #364.
    (d / "gamma.md").write_text(_FM.format(company="Example Alpha", extra=""))

    by_title = {e["title"]: e for e in v.read_evidence("experience", verified_only=False)}
    assert by_title["alpha"]["fields"]["Tools"] == ""
    assert by_title["gamma"]["fields"]["Tools"] == ""
    assert by_title["beta"]["fields"]["Tools"] == "Example Query, Example Framework"


def test_experience_add_round_trips_a_tools_value(tmp_path, monkeypatch):
    """The `--tools` flag is GENERATED from `spec.fields` by `cli.py`'s registry loop, so
    nothing hand-written asserts it exists or that a value passed to it survives the
    write/read round trip.

    Driven through the real `main()` argv, because the GENERATED flag is the thing under
    test: calling the handler with a hand-built args object would assert nothing about
    whether `cli.py` derives the flag from `spec.fields`.

    Pins the COMMA-STRING spelling specifically. `_parse_fm_spaced` joins a YAML block list
    to the identical comma string, and `core/tokens.py::tool_items` splits one
    comma-separated value.
    """
    from sluice.cli import main

    monkeypatch.setenv("VAULT_DIR", str(tmp_path))
    assert main(["experience", "add", "--name", "delta",
                 "--company", "Example Alpha",
                 "--tools", "Example Query, Example Framework"]) == 0

    v = Vault(str(tmp_path))
    entries = {e["title"]: e for e in v.read_pending_evidence("experience")}
    assert entries["delta"]["fields"]["Tools"] == "Example Query, Example Framework"


def test_only_the_skills_kind_lists_its_names_in_the_skills_pool():
    assert {k for k, s in EVIDENCE_KINDS.items() if s.names_in_skills_pool} == {"skills"}


def test_the_skills_kind_declares_a_label():
    assert "Label" in EVIDENCE_KINDS["skills"].fields


def test_a_legacy_field_may_not_also_be_a_declared_field():
    with pytest.raises(ValueError, match="legacy"):
        EvidenceKind("Job Applications/Example", ("Company",), legacy_fields=("Company",))
