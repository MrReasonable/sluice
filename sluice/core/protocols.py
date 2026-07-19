"""The adapter contracts: Store, Fetcher, Renderer.

Interface only, no logic. These are what an implementation must satisfy to be
registered under a seam, and what `tests/conformance/` asserts against every
registered implementation.

The important one is `Store`. Never-clobber and never-regress used to be properties of
`core/vault.py` -- of one implementation. Once the store is pluggable they cannot live
there, because a second store would ship without them. They are properties of *being a
store*, pinned by the conformance suite, and that is the whole point of writing this
contract down.
"""
from dataclasses import dataclass
from typing import Protocol


@dataclass
class LeadNote:
    """One lead read back from the store.

    `ref` is an OPAQUE store handle. Only the store that issued it may interpret it.
    It is a filesystem path for VaultStore and would be a row id for a SQLite store;
    callers pass it back to the store's write methods and never parse it. The previous
    contract passed `path: str`, which is what actually pinned the store to a
    filesystem.

    `slug` is the lead's stable identity, ISSUED BY THE STORE. It used to be re-derived
    from the markdown filename in four separate modules
    (`os.path.basename(note.path)[:-3]` in apply/select, apply/engine, track/classify,
    track/engine), which is the same leak wearing a different hat.
    """
    ref: object
    slug: str
    fm: dict
    body: str
    status: str


class Store(Protocol):
    """The lead/experience store. See tests/conformance/test_store_contract.py -- an
    implementation that does not pass that suite is not a Store, whatever it claims."""

    def read_leads(self, statuses: set | None = None) -> list: ...

    def upsert(self, lead) -> str:
        """Reconcile an incoming lead against the stored notes. Returns one of:
        "created" (a genuinely new note), "updated" (an existing note identified as the
        same opportunity), "merged" (an existing note we could not prove same-or-different
        from), or "refused" (no identity distinguishes this lead from a note proven
        different, so nothing is written). On "updated" and "merged" ONLY `last_seen` may
        change -- never status, enrichment, or body -- and it may only move FORWARD: a
        re-scrape carrying an older date leaves the newer stored value untouched
        (`last_seen` is monotonic). This is never-clobber, and it is the reason sluice
        exists.

        "created"/"updated" are MUST-support. "merged"/"refused" are MAY-return: a store
        keyed on synthetic ids never merges-on-uncertainty and never hits a naming
        collision, so it need only ever create or update. See #5."""
        ...

    def update_fields(self, ref, fields: dict, *, append_note=None, note_tag=None) -> None:
        """Set exactly the named frontmatter keys, leaving the body byte-for-byte
        intact. This is the sanctioned write path for triage, cv, apply and track."""
        ...

    def append_body_section(self, ref, tag: str, section_md: str) -> bool: ...

    def set_tailored_cv(self, ref, value: str) -> None: ...

    def read_experience_entries(self, verified_only: bool = True) -> list: ...

    def read_baseline(self) -> str:
        """The baseline CV. Where it lives is the store's business, configured on the
        store -- not a path passed in by a caller who should not know paths exist."""
        ...

    def read_criteria(self) -> str:
        """The user's judging criteria -- who they are, what they want, what they refuse.
        Returns "" when unset, and the caller then falls back to the shipped default,
        which states only that nothing is configured and declines to invent an opinion.

        On the judge's critical path, so a store that gets this wrong changes which jobs
        the user is shown."""
        ...

    def write_document(self, rel: str, text: str) -> str:
        """Write a store-managed document (the rejected-leads digest) and return an
        opaque handle."""
        ...

    def existing_keys(self) -> set: ...

    def normalize_all_statuses(self, dry_run: bool = False) -> dict: ...


class Fetcher(Protocol):
    """The impure I/O boundary an ingest source drives a tab through. Today: Camofox.

    `Source.fetch` receives one of these on the Ctx and `Source.parse` never sees it --
    that split is what makes parsers testable offline against golden fixtures.
    """

    def create_tab(self, url: str) -> str | None: ...

    def evaluate(self, tab: str, js: str) -> dict: ...

    def scroll(self, tab: str, amount: int) -> None: ...

    def close_tab(self, tab: str) -> None: ...


class Renderer(Protocol):
    """Turn composed CV text into a PDF, and return the path written.

    A renderer is only ever reached AFTER the fabrication gate has passed. It must not
    be given the power to bypass it: no renderer validates, and no renderer is called
    with outstanding violations.
    """

    def render(self, cv_text: str, out_dir: str, *, neutral_name: str = "CV.pdf") -> str: ...
