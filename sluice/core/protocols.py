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


class VaultConflict(RuntimeError):
    """A modify-write refused because the stored note changed since it was read.

    The store re-derived its surgical edit from the moved content up to a bounded number
    of times; sustained flapping means it wrote nothing. This is never-clobber under
    filesystem concurrency (a human editing in Obsidian, Syncthing, or a second sluice
    process). Callers treat it as non-fatal: the lead is left in its prior state and
    re-attempted next run. `upsert` absorbs its own occurrence into the `refused` outcome
    rather than raising. The CAS *mechanism* is vault-specific, but this *outcome* is a
    store-agnostic contract property, the same altitude as last_seen-monotonicity. See #16.
    """


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
        from), or "refused" (the store cannot write this lead WITHOUT clobbering a different
        one, so it writes nothing -- either because no identity distinguishes it from a note
        proven different, or because a concurrent writer keeps winning the create race; the
        two causes are distinguished only in the log). On "updated" and "merged" ONLY `last_seen` may
        change -- never status, enrichment, or body -- and it may only move FORWARD: a
        re-scrape carrying an older date leaves the newer stored value untouched
        (`last_seen` is monotonic). This is never-clobber, and it is the reason sluice
        exists.

        "created"/"updated" are MUST-support. "merged"/"refused" are MAY-return: a store
        keyed on synthetic ids never merges-on-uncertainty and never hits a naming
        collision, so it need only ever create or update. See #5."""
        ...

    def update_fields(self, ref, fields: dict, *, append_note=None, note_tag=None) -> None:
        """Set exactly the named frontmatter keys, leaving the body byte-for-byte intact.
        This is the sanctioned write path for triage, cv, apply and track. MAY raise
        VaultConflict if the note changed under a sustained concurrent edit and the store
        could not re-apply without clobbering (see VaultConflict; #16). Callers treat that
        as non-fatal."""
        ...

    def append_body_section(self, ref, tag: str, section_md: str) -> bool:
        """Append a tagged section to the body, idempotently (returns False if `tag` is
        already present). MAY raise VaultConflict on sustained concurrent edit (#16)."""
        ...

    def set_tailored_cv(self, ref, value: str, *, only_if_absent: bool = False) -> bool:
        """Set the served-CV pointer. When `only_if_absent`, do not overwrite an existing
        one (returns False without writing). Returns whether a write happened. MAY raise
        VaultConflict on sustained concurrent edit (#16)."""
        ...

    def hold_for_signoff(self, ref, *, pending: str, claims: str) -> bool:
        """Stamp a #60 sign-off hold (pending_cv + needs_signoff) ONLY IF the note has no
        tailored_cv in FRESH content, mirroring set_tailored_cv(only_if_absent=...). Returns
        whether it stamped -- False means a real send-ready CV already exists, so the caller
        leaves the flagged CV inert rather than latching the lead behind a redundant hold.
        MAY raise VaultConflict (#16)."""
        ...

    def sign_off(self, ref, *, accept: bool = True) -> str:
        """Resolve a #60 profile-audit hold, reporting the OUTCOME on FRESH content:
        'promoted' (accept, no existing pointer -> pending_cv becomes tailored_cv,
        markers cleared), 'discarded' (accept=False -> markers cleared, no pointer),
        'collision' (accept but a tailored_cv already exists -> that pointer is left
        intact, stale markers cleared), or 'nothing' (no pending_cv -> no write). The
        outcome is the store's own verdict, like upsert's, so a caller never
        reconstructs it from a stale snapshot. MAY raise VaultConflict (#16)."""
        ...

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

    def normalize_all_statuses(self, dry_run: bool = False) -> dict:
        """Canonicalize every note's status vocabulary; return a `changed`/`unchanged`/
        `unknown`/`conflicts` summary. A note whose duplicate status lines disagree is
        left untouched and reported under `conflicts`, never auto-resolved. Unlike the
        other writers here, a sustained VaultConflict on one note is ABSORBED rather than
        raised -- that note is reported under `summary["skipped"]` instead -- so one
        conflicting note never aborts the sweep over the rest (#16). `conflicts` reports
        disagreements observed during the up-front scan; a disagreement introduced
        concurrently AFTER the scan instead makes the CAS transform abstain (a no-op),
        which is counted `unchanged`, not added to `conflicts`."""
        ...


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
