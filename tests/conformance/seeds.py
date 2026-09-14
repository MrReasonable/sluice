"""Per-store seeding for the conformance suite.

The first two versions of the contract suite had tests that passed VACUOUSLY: they
asserted on data that was never created. `test_read_evidence_honours_verified_only`
ran against a store with no experience entries at all, so `verified == []` and
`every == []`, and `all([])` is True. A store that ignored `verified_only` entirely --
feeding unverified, agent-authored material into the bundle the fabrication gate validates
against -- passed all thirteen tests.

The root cause was structural: the `Store` protocol has no portable way to CREATE an
experience entry or a conflicted note, so the tests were written to whatever could be
asserted without one, which was nothing. Adding write primitives to the contract just to
serve the tests would be the tail wagging the dog.

So each store ships a SEEDER instead. A store with no seeder cannot be conformance-tested,
and that is a hard failure rather than a silent skip -- the whole lesson of this suite is
that a green tick over an untested store is worse than no tick at all.
"""
import re

from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH


def _seed_vault(store, *, experience=(), criteria="", conflicted_status=None,
                candidate=None, evidence=(), multi_line_key=None):
    """Seed the markdown vault by writing the files it reads.

    This knows the vault's layout, which is fine: it is the VAULT's seeder. The contract
    tests themselves never learn that a lead is a file.
    """
    for entry in experience:
        body = (
            "---\n"
            f"id: {entry['id']}\n"
            f"Company: {entry.get('employer', 'Example Foundry')}\n"
            f"verified: {'true' if entry.get('verified') else ''}\n"
            "---\n"
            f"{entry.get('body', 'Did a thing.')}\n"
        )
        store.write_document(f"Job Applications/Experience Library/{entry['id']}.md", body)

    if criteria:
        store.write_document("Job Applications/Judging Profile.md", criteria)

    if conflicted_status:
        # Two DISAGREEING status lines: the legacy-writer drift a store must REPORT for a
        # human rather than auto-resolve. Guessing here silently drags a shortlisted lead
        # into `dismiss`, where it vanishes from triage with no error.
        a, b = conflicted_status
        store.write_document(
            "Job Applications/Job Leads/Conflicted - Analyst.md",
            f"---\ncompany: Conflicted\nrole: Analyst\nstatus: {a}\nstatus: {b}\n"
            f"url: https://example.invalid/jobs/9\n---\nbody\n",
        )

    if multi_line_key:
        # #329: a lead whose `multi_line_key` holds a hand-typed block list, the shape Obsidian
        # writes for a List property. Through `write_document`, like the conflicted note above,
        # so the contract rows never pass YAML structure through `update_fields`.
        store.write_document(
            "Job Applications/Job Leads/Example Foundry - Analyst.md",
            f"---\ncompany: Example Foundry\nrole: Analyst\nstatus: new\n"
            f"{multi_line_key}:\n  - KEPT-ONE\n  - KEPT-TWO\n"
            f"url: https://example.invalid/jobs/8\n---\nbody\n",
        )

    if candidate:
        # Flat frontmatter, one `key: value` line per given field -- the exact shape
        # `Vault.read_candidate_profile`'s `_fm_dict` parses. Writing through
        # `write_document` rather than the filesystem directly keeps this seeder honest
        # to the Store contract: it only ever does what a real Store write can do.
        lines = "\n".join(f"{k}: {v}" for k, v in candidate.items())
        store.write_document(CANDIDATE_PROFILE_RELPATH, f"---\n{lines}\n---\n")

    for item in evidence:
        # Every WRITE goes through the store's own propose_evidence/verify_evidence,
        # not through writing files directly: a seeder that knows the layout can drift
        # from the reader, which is exactly how the `Employer:` vs `Company:` mismatch
        # above went unnoticed for so long. It is also the ONLY way to reach the
        # verified set at all -- there is no write primitive that skips the inbox.
        store.propose_evidence(item["kind"], name=item["name"],
                               fields=item.get("fields", {}), body=item.get("body", ""))
        if not item.get("verified"):
            continue
        # verify_evidence is compare-and-set: it promotes only the EXACT bytes a human
        # is shown, so the seeder must read back what propose_evidence actually wrote
        # rather than reconstruct it. Matched by `title`, not by re-deriving the slug
        # here -- a second spelling of _evidence_slug's reduction would drift from the
        # real one exactly the way the layout-knowledge this docstring warns about
        # already has. This assumes `item["name"]` already IS its own slug (e.g.
        # "alpha"); a seeded name needing real reduction (spaces, mixed case) would not
        # be found by this match and is not something today's callers need.
        #
        # The read-back is a STORE call now. It used to reach through the entry's `path`
        # key with a raw filesystem `open()`, which worked only because the sole
        # registered store backs `path` with a real file -- a seeder in the
        # store-AGNOSTIC suite quietly requiring a filesystem of every store it
        # certifies. `read_pending_evidence_text` is the contract member for exactly
        # this, and `path` is no longer a required key at all (#164 review, H3).
        pending = store.read_pending_evidence(item["kind"])
        [proposed] = [p for p in pending if p["title"] == item["name"]]
        reviewed = store.read_pending_evidence_text(item["kind"], proposed["title"])
        store.verify_evidence(item["kind"], item["name"], today="2026-01-01",
                              reviewed=reviewed)


SEEDERS = {
    "vault": _seed_vault,
}


def seed(store_name, store, **kw):
    """Seed `store` for a conformance test, or fail loudly if it has no seeder."""
    if store_name not in SEEDERS:
        raise AssertionError(
            f"store '{store_name}' ships no conformance seeder (add one to "
            f"tests/conformance/seeds.py). Without it the contract tests would assert on "
            f"empty data and pass vacuously, which is how a store that feeds unverified "
            f"material to the fabrication gate slipped through review."
        )
    return SEEDERS[store_name](store, **kw)


def _witness_vault(store, key):
    """`key`'s raw on-disk representation in the seeded lead note: the key's own line, plus
    every following line that belongs to its value (indented deeper than the key, or a `-`
    item line at the key's own indentation), up to the first line that is neither and is not
    blank or comment-only either.

    Neither a blank line nor a comment-only line, at any indentation, ends the value on its own
    (#329): YAML continues a block collection past either kind, so a broken store that appended a
    corrupted item AFTER either one, following the real block, is still holding one value, and
    the witness has to keep scanning past that line to see the item -- a scan that stopped
    there let exactly that corruption through the contract rows undetected. Such a line is
    included in the captured raw text only when a later qualifying line follows it (it was
    interior to the value); any after the value's last qualifying line are not part of it and
    are dropped.

    Reads the note's RAW frontmatter straight off disk, the way `_seed_vault`'s
    `multi_line_key` note was written -- never through `read_leads`, whose `fm` collapses a
    block list to `""` and so cannot see a store that deleted or rewrote the block's ITEMS
    while leaving the key's own line untouched (#329). Written independently of
    `sluice.core.vault._holds_multiline_value`, on purpose: a bug in that helper must not be
    able to hide inside the witness meant to catch it."""
    path = store.read_leads()[0].ref
    inner = open(path, encoding="utf-8").read().split("---\n", 2)[1]
    lines = inner.split("\n")
    pat = re.compile(rf"^(\s*){re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        m = pat.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        block = [line]
        pending = []
        for later in lines[i + 1:]:
            if not later.strip() or later.lstrip().startswith("#"):
                pending.append(later)
                continue
            later_indent = len(later) - len(later.lstrip())
            if later_indent > indent or (later_indent == indent and later.lstrip().startswith("-")):
                block.extend(pending)
                block.append(later)
                pending = []
                continue
            break
        return "\n".join(block)
    return ""


WITNESSES = {
    "vault": _witness_vault,
}


def witness(store_name, store, key):
    """`key`'s raw on-disk representation for `store`, or fail loudly if it has no witness --
    see `seed`'s docstring for why silence here is worse than an error. A conformance row
    compares this before and after a write BESIDE the portable `fm`/`wrote` assertions, because
    `fm` alone cannot see a store that corrupted a preserved key's block while leaving its
    read-back value unchanged."""
    if store_name not in WITNESSES:
        raise AssertionError(
            f"store '{store_name}' ships no conformance witness (add one to "
            f"tests/conformance/seeds.py). Without it a store that deletes or rewrites a "
            f"preserved key's block ITEMS, while leaving the key's own line untouched, would "
            f"still pass a `preserve_block_values` row."
        )
    return WITNESSES[store_name](store, key)
