"""The adapter contracts: Store, Fetcher, Renderer.

These are what an implementation must satisfy to be registered under a seam, and what
`tests/conformance/` asserts against every registered implementation.

The PROTOCOLS carry no logic -- every method body below is `...`, and that stays true:
a default implementation here would be a behaviour a store inherits without passing the
conformance suite for it. What this module does own alongside them is the shared,
implementation-independent DATA every store is written against, and that is not inert:
`EvidenceKind.__post_init__` validates a `floor_map` and raises, `floor_sources()` merges
one over `FLOOR_FIELD_SOURCES`, and the three `EVIDENCE_KINDS` entries run that validator
at import time -- deliberately, so a bad registry edit fails the build rather than
producing empty floor keys at runtime. (This docstring used to say "interface only, no
logic", which stopped being true when that validator landed.)

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

CANDIDATE_PROFILE_RELPATH = "Job Applications/Candidate Profile.md"
"""The candidate's own identity and application-form data. Like CRITERIA_RELPATH
this is an opaque DOCUMENT KEY, not a path -- nothing here may assume a filesystem."""


FLOOR_FIELD_SOURCES = {
    "company": "Company",
    "category": "Category",
    "best_for": "Best For",
    "metrics": "Metrics",
}
"""Which frontmatter key fills each of `read_evidence`'s four TEXT floor keys, by
default: an identity mapping on the title-cased name. A kind whose own field names
differ overrides individual entries through `EvidenceKind.floor_map`.

`title`, `verified` and `body` are not here -- none of them comes from a user-named
frontmatter field (the first is the entry's identity, the second is store-managed, the
third is everything after the fence)."""


@dataclass(frozen=True)
class EvidenceKind:
    """One evidence store: where it lives, and the frontmatter fields a USER supplies.

    THREE of the four attributes bind a store: `relpath` is the document key its entries
    live under, `fields` is the set it must accept and no more, and `floor_map` decides
    which of those fills each text floor key `read_evidence` promises. `cited_by_gate`
    binds NO store at all -- it is a fact about `cv/engine.py`, published here because
    this is the one registry every user-facing message keys its wording on, and a store
    implementer may (and should) ignore it entirely. It is stated rather than left to be
    inferred because it sits on the Store contract's own data with nothing else marking
    it off-limits.

    `fields` is deliberately the user-facing set only. The store-managed `verified`
    key is NOT here: `cli.py` derives `add`'s flags from this tuple, so listing it
    would generate a `--verified` flag, and a flag that grants citability is exactly
    what an agent shelling out to the CLI would pass. See the spec's decision 2.

    `cited_by_gate` says whether the CV fabrication gate actually READS this corpus
    today. It exists because `doctor` and the `add` handler both told a user that
    verifying an entry of ANY kind made it "citable by the CV fabrication gate", while
    `cv/engine.py` reads `experience` alone -- `skills` and `stories` wait on #165
    (#164 review, M2). Over-claiming here is the worst direction to be wrong in: a user
    reads it as "my skills are feeding my CVs" and stops looking. #165 flips a boolean
    rather than editing prose in three places, and
    `test_cited_by_gate_names_exactly_the_kinds_the_cv_engine_reads` derives the true
    set from `cv/engine.py`'s own source, so the flag cannot silently go stale in either
    direction.

    `floor_map` overrides, per floor key, which of THIS kind's frontmatter keys fills
    it -- `(floor_key, frontmatter_key)` pairs, merged over `FLOOR_FIELD_SOURCES`.
    A tuple rather than a dict so `frozen=True` keeps giving these a working `__hash__`.

    It exists because the floor is otherwise an identity mapping on title-cased names,
    and `skills`' four fields collide with NONE of them: measured (#164 review, M3), a
    skills entry whose own `Domain` was `platform` scored ZERO in `cv/bundle.py`'s
    `rank()` against the keyword `platform`, because `rank` reads `best_for`/`category`/
    `title` and the first two were empty strings. That is rework #165 walks straight
    into, and it is cheap to fix now, while no user has a vault to migrate.
    """
    relpath: str
    fields: tuple
    cited_by_gate: bool = False
    floor_map: tuple = ()

    def __post_init__(self):
        """Refuse a `floor_map` entry naming a floor key that is not a floor key, or a
        frontmatter key this kind does not declare. Fail loudly at construction, the rule
        `_select_backend` and `Vault._kind` already follow.

        Both halves close a class rather than an enumerated vector, and neither is
        reachable from a config file today -- every `EvidenceKind` is shipped code -- so
        this is a guard against the NEXT edit to that literal, in the shape this codebase
        uses for a quiet wrong default.

        The floor-key half: `Vault._evidence_entries` builds each entry dict by spreading
        `floor_sources()` in among literal `path`/`title`/`verified`/`body` keys. Measured
        against the real store, a `floor_map` naming `title` or `path` OVERWROTE them, and
        `verified` -- the key that decides citability -- survived only because the spread
        happens to sit ABOVE it in that dict literal, so a tidy-up reorder would have let a
        user-supplied frontmatter value grant citability. Restricting the floor key to
        `FLOOR_FIELD_SOURCES`' own four names makes the floor disjoint from every literal
        key, so the literal's ORDER stops being load-bearing at all.

        The frontmatter-key half: `floor_sources()` feeds `fm.get(key, "")`, so a typo'd
        key yields `""` for every entry with nothing red anywhere -- exactly the shape of
        the zero-score bug (#164 review, M3) the `floor_map` was added to fix, silently
        re-opened. `fields` is the kind's own declared set, so it is the only honest
        spelling to check against.
        """
        for floor, key in self.floor_map:
            if floor not in FLOOR_FIELD_SOURCES:
                raise ValueError(
                    f"floor_map key {floor!r} is not a text floor key; valid floor keys "
                    f"are {', '.join(sorted(FLOOR_FIELD_SOURCES))}")
            if key not in self.fields:
                raise ValueError(
                    f"floor_map maps {floor!r} onto frontmatter key {key!r}, which this "
                    f"kind does not declare; its fields are {', '.join(self.fields)}")

    def floor_sources(self) -> dict:
        """`FLOOR_FIELD_SOURCES` with this kind's own overrides applied. One place, so
        a store cannot spell the merge differently from the next one."""
        return {**FLOOR_FIELD_SOURCES, **dict(self.floor_map)}


EVIDENCE_KINDS = {
    # The one kind cv/engine.py reads today (`read_experience_entries`), hence the only
    # one anything may call citable. `skills`/`stories` default to False until #165.
    "experience": EvidenceKind("Job Applications/Experience Library",
                               ("Company", "Category", "Best For", "Metrics"),
                               cited_by_gate=True),
    # `Domain` IS this kind's keyword axis -- what `Best For` is for the other two, and
    # exactly what `cv/bundle.py`'s rank() scores on. Without the mapping the floor's
    # `best_for` was the empty string for every skill, so a skills entry in domain
    # `platform` scored ZERO against the JD keyword `platform` (#164 review, M3).
    #
    # ONLY `best_for` is mapped, deliberately. `Proficiency` is a LEVEL, not a
    # classification, so filling `category` with it would make a JD's ordinary
    # vocabulary rank skills by how good the user says they are at them. `Evidence` and
    # `Signal Value` are prose, not figures, and `metrics` feeds the gate's numeric
    # allowlist. And nothing fills `company`: it is rendered to the composer as
    # `(<company>)`, so putting a domain there would show a technology in the slot
    # labelled employer -- fabrication pressure aimed at the gate that exists to prevent
    # it. A companyless entry takes `_prefix`'s documented `XX` fallback and is still
    # uniquely sequenced (XX1, XX2, ...), which is that fallback working as designed.
    # The three unmapped fields stay reachable by name in the entry's `fields` dict.
    "skills": EvidenceKind("Job Applications/Skills Inventory",
                           ("Proficiency", "Domain", "Evidence", "Signal Value"),
                           floor_map=(("best_for", "Domain"),)),
    # STAR reuses `Best For` rather than inventing a keyword field: cv/bundle.py's
    # rank() scores on best_for/category/title, so #165 gets that ranker unchanged.
    # Situation/Task/Action/Result live in the BODY -- _parse_fm_spaced is line-based,
    # so a multi-line frontmatter value does not round-trip (its continuation lines
    # are re-read as further keys).
    "stories": EvidenceKind("Job Applications/STAR Stories",
                            ("Company", "Best For")),
}


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


class RenderError(RuntimeError):
    """A renderer could not produce a PDF, or could not be CONSTRUCTED to try.

    The Renderer seam's error type, and it lives here for the same reason `VaultConflict`
    does: it is a property of the CONTRACT, not of any one implementation. It used to be
    defined in `renderers/script.py` and imported from there by `renderers/template.py`
    (under a comment reading "one error type for the whole seam", naming its own problem)
    and by `core/app.py`'s dry-run construction guard. Measured with an AST sweep of
    `core/`: that guard held the only import anywhere in `core/` that reached INSIDE an
    implementation package for a NAME. The five others the sweep finds are package-level
    `import sluice.<pkg>` autoloads in `_import_plugins` and `backends.py`, which exist
    solely to trigger plugin self-registration and are the seam working as designed --
    they bind no symbol from any implementation module. An orchestrator reaching into one
    adapter to catch an error the OTHER adapter also raises is the seam inverted.

    Raised at CONSTRUCTION wherever the failure is knowable there -- a missing template or
    render script, an uninstalled `job-sluice[render]`, a template that is not valid Jinja2.
    That is the whole point of the type: `cv/engine.py` reaches a renderer only after a
    composition and a fabrication-gate pass, so a failure that waits until `render()` has
    already cost the LLM spend and arrives with no recovery. Callers that can proceed
    without a renderer (`compose_cv`'s dry run) catch it and say what was lost; callers
    that cannot let it propagate.

    `renderers/script.py` re-exports it, so the historical import path still resolves.
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

    A store must issue a NON-EMPTY slug for every note it returns, and must issue the SAME
    slug for the same note across reads. Uniqueness across the returned list is bounded
    rather than absolute, in the same shape `upsert`/`merge_cluster` state the merged-away
    obligation: a store must not itself CREATE two notes at one slug, and the vault does not
    -- `_resolve_path` refuses an ambiguous candidate rather than writing a second. What it
    cannot promise is that no two notes ever arrive at one slug, because its slug is the note
    FILENAME and a human with a filesystem can seat that name in two directories (the flat
    store made this impossible by construction; a recursive scan, #1, does not). Two notes at
    one slug are therefore returned BOTH, and loudly -- dropping one would take a lead out of
    the read AND out of the write path's lookup, which re-creates it.

    The obligation that falls on the CALLER follows from that: never index a returned list by
    slug with a bare dict comprehension, which silently keeps the last twin. `core/leads.py:
    index_by_slug` drops both and RETURNS them for the caller to report, which is what
    `track` and `leads expire` use. Stated obligations are only as good as what checks them,
    and this one was violated at all four sites that existed when it was written, so
    `tests/test_slug_indexing_discipline.py` sweeps `sluice/` for the hand-rolled shapes --
    per-site regression tests say nothing about a FIFTH consumer.
    The obligation is not discharged by INDEXING carefully, though -- a caller that walks the
    list without keying on slug at all is bound just as hard, and is the shape a fix aimed at
    the dicts misses: `apply`'s batch path (`select_all`, whose one caller is `preview_all`
    behind `apply prep --all-shortlist`) iterated the shortlist directly and carried both
    twins through, which for that caller means one job listed TWICE in the ready queue it
    prints -- a report defect, not a write: that path stages nothing and no sluice command
    submits an application. It takes the ambiguous SET from the same helper and skips them.
    The obligation does not scale with a caller's blast radius, though: `apply`'s cost is a
    report defect, `track`'s is a wrong `applied` that no forward-only status move can undo.
    A store whose ids are synthetic (a row id) satisfies the bound trivially and needs no
    such care -- but the CONTRACT is what callers are written against, so the weaker
    guarantee is the one stated here.
    """
    ref: object
    slug: str
    fm: dict
    body: str
    status: str


@dataclass
class UpsertResult:
    """Vault.upsert's own report of what it just did (#131 post-final-review fix).
    `outcome` is the existing six-member vocabulary, unchanged in wording or
    meaning. `slug` is populated ONLY for "created"/"updated"/"merged" -- the three
    outcomes where a note now exists that this call itself put there or resolved
    to: "created" seats a genuinely NEW note; "updated"/"merged" identify an
    EXISTING note as this call's own resolution decided (same posting, or
    inconclusive evidence, respectively) -- last_seen is the only field either may
    change, and even that is not guaranteed: it is monotonic, so a re-upsert
    carrying a stamp no newer than what is already stored resolves to (and
    correctly reports the slug of) the same note while writing nothing at all.
    `slug` is "" for "refused"/"merged_away"/"merged_away_unproven", none of which
    write into (or match) any note this call itself now owns.

    This is the single source of truth for "which note did THIS call actually
    touch." A caller that instead re-derives the answer post-hoc (e.g. re-reading
    every note matching the incoming lead's company+title) is reconstructing
    information the store already had and discarded -- and can get it wrong: two
    notes can legitimately share company+title (a proven-different location seats a
    second note at that identity), and a filter applied AFTER the write cannot
    always tell which of them THIS write actually resolved to, because the store's
    own resolution walks candidate NAMES in a specific order and stops at the first
    non-advance verdict -- a property no post-hoc filter over the finished set can
    reconstruct in general. See Sluice.create_lead's own history (#131) for the
    concrete reproduction that motivated this fix: three separate "guess after the
    fact" strategies (location-only, a flat url-or-location filter, and a two-tier
    url-then-location priority) each returned a real but WRONG note's slug in some
    reachable scenario."""
    outcome: str
    slug: str = ""


@dataclass
class CandidateProfile:
    """Every field is a plain `str` defaulting to "" -- no bool fields, deliberately.

    `core/vault.py`'s `_fm_dict` is a regex line-scanner, not a YAML loader, so
    `right_to_work_uk: true` and `disability: No` both arrive as the literal
    strings "true" and "No". Forcing a Python bool would buy nothing (nothing
    downstream needs boolean logic beyond the one
    `how_heard_detail_from_lead_source` check, which is an explicit string
    comparison) and would risk the bool-subclasses-int / PyYAML-coerces-`yes`
    trap this codebase is already careful about for fields that DO go through a
    real YAML loader.

    "" means UNDECLARED, and an undeclared field is never inferred, defaulted or
    guessed -- see the spec's "Presence semantics". The all-blank default is what
    makes an unconfigured install abstain rather than assert.

    No `__post_init__` type guard: adding one would change the dataclass
    contract the reader below and nine further tasks build on. `full_name`,
    `contact_block` and `has_any_declared` (core/candidate.py) all call `.strip()`
    or `.split()` and so raise `AttributeError` at a distance on a non-`str`
    field -- accepted, because the only producer is
    `Vault.read_candidate_profile()` (core/vault.py). It is built on
    `core/vault.py`'s `_fm_dict`, a regex line-scanner that already yields `str`
    or nothing for every other note field it reads today; a direct
    `CandidateProfile(**d)` from any other source is that caller's obligation to
    type.
    `age_from_dob`'s explicit guard on its `today` argument is not a
    counterexample: `today` is not a dataclass field here, it is a
    caller-supplied argument with no producer to trust, which is why it gets a
    harder check than anything on this class.
    """
    # Identity & contact -- feeds cv, via full_name()/contact_block()
    forenames: str = ""
    surname: str = ""
    email: str = ""
    mobile: str = ""
    linkedin: str = ""
    # Address -- feeds apply, one packet key per field
    address_line1: str = ""
    address_line2: str = ""
    town: str = ""
    county: str = ""
    postcode: str = ""
    country: str = ""
    # Right to work & employment history -- feeds apply
    requires_uk_work_permit: str = ""
    right_to_work_uk: str = ""
    currently_employed_by_them: str = ""
    previously_employed_by_them: str = ""
    referred_by_current_employee: str = ""
    # How you heard about the role -- feeds apply
    how_heard_default: str = ""
    how_heard_detail_from_lead_source: str = ""
    # Equal-opportunities monitoring -- feeds apply, special-category data
    gender_identity: str = ""
    identifies_as_trans: str = ""
    ethnicity: str = ""
    religion: str = ""
    sexual_orientation: str = ""
    preferred_pronouns: str = ""
    disability: str = ""
    neurodivergent: str = ""
    open_about_orientation_at_work: str = ""
    # Other -- feeds apply
    date_of_birth: str = ""
    honorific: str = ""  # Mr/Ms/Dr -- NOT a job title, which is what `title` means
    # everywhere else in this codebase (Lead.title, dedup_key, accept_titles,
    # core/vault.py's own module docstring). Named `honorific` rather than a `title`-
    # bearing compound (`name_title`) deliberately: a compound still contains the
    # colliding token, so a reader skimming the field list -- or an ATS-filling agent
    # matching packet keys to form fields by name, per the packet's own RULES block --
    # still has to disambiguate. `honorific` removes the token outright.
    marital_status: str = ""
    nationality: str = ""
    dual_nationality: str = ""
    first_language: str = ""
    served_armed_forces: str = ""
    caring_responsibility: str = ""
    worked_in_construction: str = ""


class Store(Protocol):
    """The lead/experience store. See tests/conformance/test_store_contract.py -- an
    implementation that does not pass that suite is not a Store, whatever it claims.

    OPTIONAL MEMBER -- `preflight() -> dict`. Not declared below, for the identical
    reason `Renderer.precheck` is not: a Protocol member is a REQUIRED member, and the
    whole point of this hook is that a store may omit it. `sluice doctor` (core/app.py)
    reaches it via `getattr(store, "preflight", None)` and reports nothing for that
    component when it is absent, rather than treating an unimplemented hook as a
    failure -- the same shape `cv/engine.py` already gives the renderer seam's optional
    `precheck`.

    A store implements `preflight` to answer "can a run actually use me right now?"
    with facts doctor cannot get any other way -- for the vault: does the configured
    directory exist, is the baseline CV readable, is a Judging Profile present, how
    many Experience Library entries are verified, and (#133/#107) is a candidate name
    declared and is a contact block declared -- the two facts `cv/engine.py`'s
    `skipped-config` refusal already gates a real compose on. It returns FACTS, not
    verdicts: classification is `core/doctor.py`'s job, kept pure there the same way
    backend classification is kept separate from `Sluice.doctor`'s credential
    resolution.

    MUST NOT create or open anything that does not already exist, and MUST NOT read a
    store file that could disarm a later relocation notice -- see #81's warning at
    `core/paths.py`: `sqlite3.connect` creates a 0-byte file merely by OPENING one, and
    the relocation notice on a dedup store is keyed on the resolved path NOT existing,
    so a "harmless" preflight probe would silently disable it for every later run this
    process makes. `Vault.preflight` therefore only `stat`s paths and reads documents
    through the store's own existing read methods (`read_baseline`, `read_criteria`,
    `read_evidence`/`read_pending_evidence` per kind, `read_candidate_profile` -- it does
    NOT go through `read_experience_entries`, whose name is experience-specific), never opens a store's OWN
    internal state file (a SQLite-backed store's preflight must not connect to its
    database), and never walks the full lead scan set -- doctor is a preflight users
    run often and cheaply, not a second `leads` pass."""

    def read_leads(self, statuses: set | None = None) -> list:
        """Every stored lead as a LeadNote, filtered to `statuses` when given.

        A store decides for itself what counts as a lead. The filesystem store shares its
        directory with whatever else the user keeps there, so it returns only files whose
        frontmatter carries a company or a role; a store with its own table has this by
        construction rather than by a filter it must apply.

        A merged-away loser is NOT returned (see upsert). For the vault that exclusion is by
        NAME -- the archive directory is pruned from the scan -- rather than a side effect of
        a flat listing, because the scan is recursive.

        A store MAY raise rather than return a partial list: the filesystem one propagates
        the OSError from an unreadable directory in its scan set, since a subtree silently
        read as empty drops every lead in it from BOTH this read and the write path's
        lookup, and the next scrape re-creates all of them. Permitted, not required -- no
        obligation is placed on an implementation here.
        """
        ...

    def upsert(self, lead) -> "UpsertResult":
        """Reconcile an incoming lead against the stored notes. Returns an
        UpsertResult whose `outcome` is one of:
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
        contract does, by naming it. See tests/conformance/test_store_contract.py.

        `result.slug` is the slug of the note this call resolved to -- populated for
        "created"/"updated"/"merged", empty for "refused"/"merged_away"/
        "merged_away_unproven" (the latter two are a MATCH against an archived note,
        never a write into one this call now owns, so they carry no slug either --
        same rule as "refused"). For "created"/"updated"/"merged" a store MUST
        report the slug of the EXACT note whose content this call's write decided --
        never a different note that merely happens to share the same company+title
        identity. See UpsertResult's own docstring for why this matters."""
        ...

    def update_fields(self, ref, fields: dict, *, append_note=None, note_tag=None,
                      require_status: frozenset | None = None,
                      require_blank: frozenset | None = None,
                      blank_values: frozenset | None = None) -> bool:
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
        because the audit note would claim a prior status that was no longer true (#9).

        `require_blank` (#109) carries the identical obligation for the NAMED NON-STATUS
        keys: re-read each from the FRESH stored note and abstain -- nothing written,
        returns False -- unless every one of them is empty. Never-clobber, in the same
        family: #109's blank-company resolution decides the field is safe to fill from a
        snapshot and then spends SECONDS on a page fetch before writing, so a human's own
        edit landing in that window is precisely what it protects. An implementation MUST
        refuse on PRESENCE rather than on inequality -- a value DIFFERING from the one
        offered is the harmful case, and it is the one a store comparing values would
        wave through. Same delegation argument as above: a caller-side blankness check
        reads the pre-fetch snapshot and is byte-identical to no check at all.

        `blank_values`, when given alongside `require_blank`, names the stored values
        that count as BLANK for that guard in addition to empty/whitespace-only. Only the
        FRESH STORED side is normalised, through `core.leads.fold_company_answer` (strip,
        drop a trailing `.`/`!`, casefold) -- the identical asymmetry `require_status`
        already has with `core.status.normalize`, which folds the stored status but takes
        `require_status` itself as already-canonical. `blank_values` members MUST already
        be folded by the caller (`core.leads.NON_ANSWER_COMPANIES` is built that way for
        exactly this reason); an unfolded member silently never matches, the same failure
        mode an unnormalized `require_status` set would have. It widens exactly one thing:
        a value in the given set now counts as blank for the presence check. Every other
        non-blank value is still refused, including one that merely *differs* from the
        value being written -- never-clobber holds for anything not named here.
        `blank_values` given without `require_blank` is inert and must never become a
        guard of its own."""
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

    def sign_off(self, ref, *, accept: bool = True,
                 require_pending: str | None = None) -> str:
        """Resolve a #60 profile-audit hold, reporting the OUTCOME on FRESH content:
        'promoted' (accept, no existing pointer -> pending_cv becomes tailored_cv,
        markers cleared), 'discarded' (accept=False -> markers cleared, no pointer),
        'collision' (accept but a tailored_cv already exists -> that pointer is left
        intact, stale markers cleared), 'nothing' (no pending_cv -> no write), or
        'stale' (#131: `require_pending` given and it does not match the FRESH
        pending_cv -> no write). The outcome is the store's own verdict, like
        upsert's, so a caller never reconstructs it from a stale snapshot. MAY raise
        VaultConflict (#16)."""
        ...

    def read_evidence(self, kind: str, verified_only: bool = True) -> list:
        """Entries for one EVIDENCE_KINDS kind. Raises ValueError on an unknown kind,
        naming the valid ones -- never a quiet [], which the caller cannot distinguish
        from an empty store and which the fabrication gate reports as `skipped-gate`.

        Returns dicts carrying at least `title`, `company`, `category`, `best_for`,
        `metrics`, `verified`, `body` (the floor cv/bundle.py's ranker needs on every
        kind) plus `fields`, the kind's own frontmatter under its own names. Which of a
        kind's fields fills each of the four TEXT floor keys is `FLOOR_FIELD_SOURCES`
        merged with that kind's `floor_map` -- not an identity mapping the store invents
        for itself, and not every field: one with no floor analogue is reachable only
        through `fields`.

        A filesystem `path` is deliberately NOT among them. It used to be, and the
        facade opened it (#164 review, H3) -- a store-agnostic caller reaching through
        the seam at a filesystem, the exact inversion `read_criteria` was introduced to
        remove, and a key a SQL- or API-backed store has nothing to put in. Everything
        this returns is an opaque handle; `read_pending_evidence_text` below is how a
        caller gets bytes. A store MAY still carry extra keys of its own (the vault
        does carry `path`), but no contract-bound caller may read one."""
        ...

    def read_pending_evidence(self, kind: str) -> list:
        """Everything in the pending set. Same dict shape as read_evidence.

        These are NEVER citable: the fabrication gate reads read_evidence only, and a
        store must keep the two sets disjoint rather than filtering one out of the other.

        Which is why this returns the pending set WHOLE and must not filter it on the
        citability key. An entry that carries that key while still being pending is
        reachable -- for the vault, by a human placing one there, which this tool treats
        as a first-class workflow -- and it is exactly the entry a human needs to see: it
        is inert, it is not citable, and the ONLY places that could report it are this
        reader's three consumers (`<kind> list --pending`, the queue `verify` offers, and
        doctor's pending count). Filtering here hides it from all three at once."""
        ...

    def read_pending_evidence_text(self, kind: str, name) -> str:
        """The exact stored text of ONE pending entry, freshly read.

        The READ side of the currency `verify_evidence(..., reviewed=)` already spends:
        a human is shown these bytes and approves them, and the promotion compares
        against them. Freshness is load-bearing, not incidental -- the value must be
        read at review time, never carried over from the listing that built the queue,
        or the compare-and-set would compare against a snapshot that is stale by
        construction and abstain on nothing.

        `name` is the entry's OWN identity as read_pending_evidence reports it (its
        `title`), on the same terms as verify_evidence's: a store must refuse a `name`
        that is not a bare identifier in its own namespace. Raises when there is no such
        pending entry -- never a quiet "", which a caller cannot tell from an entry that
        is genuinely empty and would hand a human nothing to review."""
        ...

    def propose_evidence(self, kind: str, *, name, fields, body: str = "") -> str:
        """Record a PROPOSED entry, returning an opaque handle to it.

        Never citable, and the OBLIGATION is on the store rather than on the signature:
        `fields` is a caller-supplied mapping, so a store MUST reject a key it does not
        declare BY NAME -- `verified` among them -- rather than passing the mapping
        through to whatever it writes. (This paragraph used to say the signature "has no
        parameter that could carry it", which is simply false: `fields` is exactly such a
        parameter, and a store could satisfy that sentence to the letter while writing
        `verified` straight into its record. `Vault` implements the real rule in
        `_render_evidence_note`; `tests/conformance/test_store_contract.py`'s
        `test_a_caller_cannot_supply_the_citability_key_by_any_route` is what binds it.)
        A store must also write the entry somewhere `read_evidence` cannot see it, so a
        proposal is invisible to the fabrication gate until `verify_evidence` promotes it.

        Refuses rather than overwrites when the name is already proposed, and refuses a
        name already taken in the VERIFIED set: the clash is the same one
        verify_evidence would hit, and refusing it at propose time is where a human can
        still pick a different name. Both refusals are FileExistsError carrying a
        message a caller may print verbatim, never a bare errno. Raises on a name that
        does not reduce to a usable identifier, on a field key the kind does not
        declare, and on content that would not survive being read back.

        The handle is OPAQUE, on the same terms as `write_document`'s: a caller may show
        it to a user (`job-sluice <kind> add` prints it) and may test it for truthiness,
        and may do nothing else with it -- in particular it is not promised to be a
        filesystem path, exactly as `read_evidence`'s dicts no longer promise a `path`
        key. The vault's handle IS a path; a SQL- or API-backed store has none to give.
        The complementary requirement, because a successful propose must be
        distinguishable from an abstain the way `write_document`'s is: a store that
        recorded the entry must return a NON-EMPTY handle."""
        ...

    def verify_evidence(self, kind: str, name, *, today: str, reviewed: str) -> bool:
        """Promote a proposed entry to citable, stamping it as verified.

        `name` is the entry's OWN identity as read_pending_evidence reports it (its
        `title`), NOT the raw name propose_evidence was called with. A store reduces a
        user-supplied name at PROPOSE time, so re-deriving that reduction here could
        only disagree with what the store actually filed -- measured, it made an entry
        whose identity did not survive the round trip (one added by hand, which this
        tool treats as a first-class workflow) listable and permanently unverifiable.
        A store must still refuse a `name` that is not a bare identifier in its own
        namespace, so no caller can reach outside the pending set.

        The ONLY way an entry becomes citable by the CV fabrication gate. Returns
        False, writing nothing, when the entry changed since `reviewed` was shown to
        a human -- promoting an edit made after approval would make unreviewed
        content citable. Raises when the name is already taken in the verified set,
        before mutating anything."""
        ...

    def read_experience_entries(self, verified_only: bool = True) -> list:
        """`read_evidence("experience")` under a second, required name.

        EXPIRES AT #165. It predates the kind registry and survives only because
        `cv/engine.py` still calls it; #165 rewrites that caller to read the corpora it
        composes from by kind. When it does, DELETE this member rather than inheriting
        it -- a Protocol member is a REQUIRED member, so every future store has to
        implement a second spelling of a call it already implements, for one caller that
        will no longer exist. Its conformance row and its two hand-listed test literals go
        with it. Nothing in the contract depends on the name; `Vault` implements it as a
        one-line delegate precisely so there is nothing to migrate."""
        ...

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

    def read_candidate_profile(self) -> CandidateProfile:
        """The candidate's own identity and application-form data.

        MUST-support, like read_baseline/read_criteria -- NOT optional like
        preflight/precheck. An optional member would push a `getattr` None-branch
        into four callers and hand cv a "the store cannot say" case with no safe
        answer: composing without a name is the fabrication risk #99 exists to
        stop, and refusing on a store that merely did not implement the hook
        would be a silent feature-off.

        A store with no such document returns an all-blank CandidateProfile --
        abstain, not raise, the same shape read_criteria already has.
        """
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

    FAILURE MODE -- `RenderError` (defined above). A renderer signals every failure with
    it, and raises at CONSTRUCTION for anything knowable there rather than at `render()`,
    because by render time a composition and a gate pass have already been spent. This
    contract went undocumented while the type itself lived in `renderers/script.py`, so
    the seam declared no failure mode at all and its two implementations agreed on one
    only by importing from each other. The Store seam does the same thing correctly with
    `VaultConflict`, which is the shape copied here.

    OPTIONAL SECOND METHOD -- `precheck(cv_text) -> list[str]`. Not declared below,
    because a Protocol member is a REQUIRED member and the whole point of this hook is
    that a renderer may omit it; `cv/engine.py` reaches it via
    `getattr(renderer, "precheck", None)` and skips the call when it is absent.

    A renderer implements `precheck` when it needs the composed CV to satisfy a GRAMMAR
    of its own -- something the fabrication gate does not model and cannot be extended to
    model (the gate is out of scope, and a second gate beside it would be a way around
    the real one). The engine calls it INSIDE its compose/gate retry loop and folds the
    returned strings in with the gate's violations, so a renderer-specific formatting
    complaint reaches the model's one retry rather than arriving after the LLM spend with
    no recovery. Return `[]` for "nothing to say"; the strings are prompt text, so they
    must name what is wrong and what was expected.

    It is a per-RENDERER obligation and must not be hoisted into the engine. Measured
    2026-08-06 on a genuinely gate-clean CV carrying a PUBLICATIONS section: with the
    `template` renderer's grammar applied unconditionally, `cv.renderer: script` reported
    `skipped-gate` and rendered nothing, although the operator's own script would have
    laid that section out fine. `script` shells out to arbitrary user code and has no
    grammar to impose, so it deliberately does NOT implement this -- one seam member
    imposing another's requirements is the inversion this hook exists to undo.
    """

    def render(self, cv_text: str, out_dir: str, *, neutral_name: str = "CV.pdf") -> str: ...
