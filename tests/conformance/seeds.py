"""Per-store seeding for the conformance suite.

The first two versions of the contract suite had tests that passed VACUOUSLY: they
asserted on data that was never created. `test_read_experience_entries_honours_verified_only`
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
from sluice.core.protocols import CANDIDATE_PROFILE_RELPATH


def _seed_vault(store, *, experience=(), criteria="", conflicted_status=None, candidate=None):
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

    if candidate:
        # Flat frontmatter, one `key: value` line per given field -- the exact shape
        # `Vault.read_candidate_profile`'s `_fm_dict` parses. Writing through
        # `write_document` rather than the filesystem directly keeps this seeder honest
        # to the Store contract: it only ever does what a real Store write can do.
        lines = "\n".join(f"{k}: {v}" for k, v in candidate.items())
        store.write_document(CANDIDATE_PROFILE_RELPATH, f"---\n{lines}\n---\n")


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
