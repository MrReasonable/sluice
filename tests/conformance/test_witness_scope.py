"""The vault's conformance witness, checked on its own (#329).

`tests/conformance/seeds.py::_witness_vault` is what lets the `preserve_block_values` and
`append_note` contract rows see a store that corrupts a block's ITEMS while leaving the key's own
line in place. Those rows compare the witness before and after a write, so a witness that
captured too little would leave both of them green against exactly that store. These rows pin
what the witness captures, independently of any store write.
"""
import pytest

from sluice.core.vault import Vault
from tests.conformance.seeds import witness


@pytest.mark.parametrize("interruption", ["", "# typed by hand"], ids=["blank-line",
                                                                        "comment-line"])
def test_the_vault_witness_captures_items_past_a_blank_or_comment_line(tmp_path, interruption):
    # YAML continues a block list past a blank or comment-only line, so an item after one is
    # still part of the value, and a broken store that appended a corrupted item there has to
    # show up in the witness.
    store = Vault(str(tmp_path))
    store.write_document(
        "Job Applications/Job Leads/Example Foundry - Analyst.md",
        "---\ncompany: Example Foundry\nrole: Analyst\nstatus: new\n"
        f"triage_concerns:\n  - KEPT-ONE\n{interruption}\n  - CORRUPT\n"
        "next_key: KEPT-AFTER\nurl: https://example.invalid/jobs/8\n---\nbody\n")
    captured = witness("vault", store, "triage_concerns")
    assert "KEPT-ONE" in captured
    assert "CORRUPT" in captured, "the witness stopped at the interruption, before the item"
    assert "next_key" not in captured, "the witness ran on into the following top-level key"
