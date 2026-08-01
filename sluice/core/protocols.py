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

# Where the judge's criteria live inside a store. Here, in the contract module, because it IS
# part of the Store contract -- the document `read_criteria` serves. It was previously two
# independent literals (`core/vault.py`, `triage/prompt.py`); `sluice init` would have made three,
# and a divergence means init writes a profile the judge never reads, silently, because a missing
# profile falls back to the shipped default rather than raising.
#
# A non-filesystem store treats this as an opaque DOCUMENT KEY, not a path -- and it is spelled with
# a literal "/" rather than os.path.join for exactly that reason. os.path.join makes the SEPARATOR
# platform-dependent, so the "opaque key" would silently be backslash-separated on Windows and two
# stores would disagree about the same document. Translating the key to a filesystem path is the
# FILESYSTEM store's job (see Vault._doc_path), not the contract's.
CRITERIA_RELPATH = "Job Applications/Judging Profile.md"


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


class MalformedNoteField(Exception):
    """A store-managed field's on-disk content does not parse into the shape the store's
    own writers expect (e.g. `alt_urls` should be a JSON list[str]).

    A modify-write that finds the FRESH value malformed must raise this rather than
    reset/discard it: silently replacing a possibly-human-edited value is exactly the
    clobber never-clobber exists to prevent (#23). Distinct from VaultConflict -- this is
    not a concurrency race to retry, it is a genuinely malformed value a human must look
    at, so the whole write it was part of (e.g. a cluster merge) is aborted with nothing
    written rather than papered over.
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
        one, so it writes nothing -- because no identity distinguishes it from a note proven
        different, because one identity resolves to SEVERAL stored notes so there is no way to
        tell which lead this is, or because a concurrent writer keeps winning the create race.
        The causes are distinguished only in the log, and that list is the vault's rather than
        an exhaustive one: what the outcome PROMISES a caller is only that nothing was written).

        Two more (#81), both MAY-return: "merged_away" and "merged_away_unproven" -- the
        lead was already merged away by merge_cluster, so nothing is written. They differ
        only in evidence strength, and the caller uses that: the ingest sink records the
        PROVEN one in its dedup store and must never record the other. "merged_away"
        therefore requires the store to have PROVED identity -- for the vault, a matching
        non-empty url on both sides. A match resting on anything weaker (the vault's
        location-token overlap, or an inconclusive comparison) is "merged_away_unproven":
        it still suppresses, but it re-surfaces every run until a human acts, because the
        dedup store has no removal path and a same-company/title/location RE-POST carrying
        a brand-new url is a real job. "Until a human acts" is a real obligation on the
        store, not a figure of speech: a store returning this outcome MUST leave the human
        a route back to an identified state, or the lead re-reports forever with nothing
        anyone can do about it. For the vault that route is moving the archived note back
        out of `_merged/`, after which the next scrape reconciles against it as an ordinary
        note and reports "updated" -- an outcome the sink DOES record, which is what makes
        the re-reporting stop. A store with no archive concept never returns either.

        On "updated" and "merged" ONLY `last_seen` may change -- never status, enrichment,
        or body -- and it may only move FORWARD: a re-scrape carrying an older date leaves
        the newer stored value untouched (`last_seen` is monotonic). This is never-clobber,
        and it is the reason sluice exists.

        "created"/"updated" are MUST-support. "merged"/"refused" are MAY-return: a store
        keyed on synthetic ids never merges-on-uncertainty and never hits a naming
        collision, so it need only ever create or update. See #5.

        MUST-honour for any store implementing merge_cluster: a merged-away loser MUST
        remain discoverable by `upsert` through THE IDENTITY THE STORE RECORDED AT MERGE
        TIME, and MUST NOT be re-created when that identity is presented again. That is a
        safety property in the never-clobber family -- it protects a human's decision from
        being silently undone, and re-creating the lead can mean a second application
        under the user's name.

        Stated that way on purpose: the absolute form ("never re-created") is not what any
        store can deliver, and claiming it would hide the residual instead of bounding it.
        A re-scrape whose identity has DRIFTED beyond what the store recorded is OUTSIDE
        the guarantee -- for the vault the recorded identity is the note NAME the loser was
        seated at, so a re-scrape whose title has drifted past every name candidate is
        created, a visible duplicate a human can merge again. The conformance suite
        exercises only the location-split shape, so it does not police that residual; the
        contract does, by naming it. See tests/conformance/test_store_contract.py."""
        ...

    def update_fields(self, ref, fields: dict, *, append_note=None, note_tag=None,
                      require_status: frozenset | None = None) -> bool:
        """Set exactly the named frontmatter keys, leaving the body byte-for-byte intact.
        This is the sanctioned write path for triage, cv, apply and track. MAY raise
        VaultConflict if the note changed under a sustained concurrent edit and the store
        could not re-apply without clobbering (see VaultConflict; #16). Callers treat that
        as non-fatal. Returns whether a write happened.

        `require_status`, when given, is re-read from the FRESH stored note and the write
        is abstained -- nothing written, returns False -- if the status is not in that
        set. Two semantics an implementation MUST honour, both pinned by the conformance
        suite: the comparison is against the NORMALIZED status (`core.status.normalize`),
        because real vaults carry drift like `Shortlist`/`dismissed`/`needs review` and a
        raw comparison would abstain on those forever -- reporting the lead stale on every
        run and never writing it; and the returned bool reports whether the stored record
        CHANGED, so a write of a value the note already holds returns False.

        This CANNOT be delegated to the caller, which is why it is on the contract
        rather than in `leads expire`: a caller-side check reads a snapshot taken before
        the write and cannot see a concurrent entry into the application lifecycle (via
        `apply record` or a #10 receipt). A store that ignored it would silently write a
        triage status over `applied` -- never-regress, and irreversible in practice
        because the audit note would claim a prior status that was no longer true (#9)."""
        ...

    def merge_cluster(self, survivor_ref, loser_refs, *, alt_urls, first_seen, last_seen) -> list:
        """Merge a human-vetted duplicate cluster (#23): union `alt_urls` onto the
        survivor WITHOUT touching its status/scores/enrichment/body (never-clobber),
        with `last_seen` advanced and `first_seen` minimised -- both RE-DERIVED against
        the FRESH survivor, so a caller's stale min/max can never regress them. The
        survivor write happens BEFORE any loser is removed, so a VaultConflict on the
        survivor removes nothing. If the survivor's EXISTING `alt_urls` is present but
        not a JSON list of strings, MAY raise MalformedNoteField instead of silently
        resetting it -- never-clobber forbids discarding a possibly-human-edited value,
        so the whole merge is aborted with nothing written and no loser touched. Each
        loser is then removed/archived independently; a per-loser removal failure is
        isolated to that loser (it stays in the active view and is never counted as
        merged) rather than aborting the whole cluster.

        A removed loser MUST remain invisible to `read_leads` and discoverable by `upsert`
        through the identity recorded here, so a later re-scrape PRESENTING THAT IDENTITY is
        not re-created (#81; see `upsert` for the bound on that obligation and what falls
        outside it). The vault keeps the whole note under `_merged/` and stamps the name it
        was seated at INTO it; a natural-key tombstone satisfies the contract equally --
        retention of the note itself is this store's mechanism, not the requirement, but
        recording SOME identity is. The returned handles are whatever identifies the removed
        records to this store; a tombstone id is a handle."""
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

    def write_document(self, rel: str, text: str, *, only_if_absent: bool = False) -> str:
        """Write a store-managed document (the rejected-leads digest) and return an
        opaque handle.

        `only_if_absent=True` writes NOTHING and returns `""` when the document already
        exists. This is the never-clobber primitive `sluice init` scaffolds the Judging
        Profile through, and it belongs on the contract rather than on one store: the
        document it protects is the one a human hand-edits, and a store that overwrote it
        would discard the criteria the judge scores every lead against. Implementations
        must make it a property of the CREATE itself (an exclusive open), not an
        exists()-then-write pair -- the racer is a human in Obsidian, who takes no lock
        (#16).

        With `only_if_absent=False` -- the DEFAULT -- the write must REPLACE any existing
        document at `rel`. That arm is not a nicety: `triage/audit.py` regenerates the
        rejected-leads digest through it on every run, so a store implementing
        create-exclusive as its primitive would silently freeze that digest at its first
        version and nothing would report it.

        `rel` must also stay INSIDE the store: an absolute path, or one that RESOLVES
        outside the store root, raises ValueError rather than writing. An interior `..`
        that stays inside (`a/../b.md`) is accepted -- the rule is containment of the
        resolved path, not a ban on the characters, and a second store that rejected the
        characters would disagree with this one on the same key. This is the one wholesale-write primitive on
        a never-clobber contract, so an escape would let it scribble over `My CV/CV.md`,
        the fabrication gate's ground truth.

        The complementary requirement, because callers distinguish the two outcomes by
        TRUTHINESS: a successful write must return a NON-EMPTY handle. A store returning
        `""` after creating the document would make `sluice init` report "exists (left
        alone)" for a file it had just written."""
        ...

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

    One CONTRACT note that the signatures do not carry: `evaluate(tab,
    "location.href")` is no longer only a health signal. The dossier fetcher (#18)
    uses it to decide whether a response body may be read, so an implementation that
    reports a url the tab did not actually land on defeats an SSRF guard. Report the
    tab's real current url, or return a non-string so the caller fails closed.
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
