"""Store-facing rows for the triage framing keys (#329): what a note carries, and what a triage
write may and may not do to a value a human typed into it."""
from sluice.core.leads import Lead
from sluice.core.vault import Vault


def _frontmatter_lines(path):
    text = open(path, encoding="utf-8").read()
    return text.split("---\n")[1].splitlines()


def test_a_new_note_carries_a_blank_triage_concerns_key(tmp_path):
    # Written blank at creation, directly after `culture_flags`, so a note's schema does not
    # depend on whether triage has run yet.
    v = Vault(str(tmp_path))
    v.upsert(Lead(source="s", search="q", title="Analyst", company="Example Foundry",
                  url="https://example.invalid/1"))
    lines = _frontmatter_lines(v.read_leads()[0].ref)
    assert 'triage_concerns: ""' in lines
    assert lines.index('triage_concerns: ""') == lines.index('culture_flags: ""') + 1
