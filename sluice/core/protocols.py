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
import hashlib
import json
import dataclasses
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

# The coach's researched notes on the role the user chose (in-session setup). Read ONLY by the
# setup tools and the coach -- never by triage or cv, so model-researched text cannot reach a
# scoring or composing decision. tests/test_role_brief_unread.py pins that.
ROLE_BRIEF_RELPATH = "Job Applications/Role Brief.md"


LEADS_VIEW_RELPATH = "Job Applications/Job Leads/Job Leads.base"
"""The Obsidian Bases view over the lead notes (#240). Another opaque DOCUMENT KEY.

Every lead note `core/vault.py` writes carries `base: "[[Job Leads.base]]"`, and every
triage audit note does too; that key is the view's own membership predicate, which is
why the notes have carried it since long before anything created the file it names.
Until #240 nothing did, so each note shipped an unresolved link and the user never got
the table the link exists to open.

The path is INSIDE the leads directory rather than beside it, which is not arbitrary.
An Obsidian wikilink resolves by name from anywhere in the vault, so both locations
satisfy the notes; what does not survive the choice is never-overwrite. A user who
already hand-built this view has it here, and writing to any other path would hand them
a second, competing one instead of finding theirs and standing down. The lead scan is
unaffected either way: its consumers admit only names ending `.md`.

KNOWN GAP, stated rather than implied: the view's membership predicate is that `base:` key
alone, and `triage/audit.py` stamps the SAME key into the Rejected Leads Audit note, which
carries no company, role, status or score. So the unfiltered "All leads" tab gains one blank
row after the first non-dry-run triage that has audit entries; the three status-filtered tabs
exclude it already, because it has no `status`. `generated: true` on that note is the
available discriminator, and using it needs a filter form verified against a live Obsidian
rather than guessed at -- every construct the shipped view uses today is one observed
working, and a filter that fails to parse renders an EMPTY table, which would hide every
lead rather than surface one extra row.

That is not the only lever, and the other needs no Bases syntax at all: `triage/audit.py`
could stop stamping this key into a note that is not a lead. So this is deferred rather than
unavailable, and whichever lever is taken, this paragraph is what should stop being true.
"""

# The vault notes in-session setup reads and writes, by artefact name.
SETUP_NOTES = {"profile": CRITERIA_RELPATH, "candidate": CANDIDATE_PROFILE_RELPATH,
               "brief": ROLE_BRIEF_RELPATH, "view": LEADS_VIEW_RELPATH}


def document_sha(text: str) -> str:
    """The sha a review form records for a document as shown, and the one
    `Store.write_document(expect_sha=...)` compares against: SHA-256 over the text encoded as
    UTF-8. Read with `newline=""`, that is the document's raw bytes, so a CRLF note compares
    truly."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

    `relpath` is the document key its entries live under, `fields` is the set a store
    must accept and no more, and `floor_map` decides which of those fills each text
    floor key `read_evidence` promises. `cited_by_gate` binds NO store at all -- it is a
    fact about `cv/engine.py`, published here because
    this is the one registry every user-facing message keys its wording on, and a store
    implementer may (and should) ignore it entirely. It is stated rather than left to be
    inferred because it sits on the Store contract's own data with nothing else marking
    it off-limits.

    `fields` is deliberately the user-facing set only. The store-managed `verified`
    key is NOT here: `cli.py` derives `add`'s flags from this tuple, so listing it
    would generate a `--verified` flag, and a flag that grants citability is exactly
    what an agent shelling out to the CLI would pass. See the spec's decision 2.

    `cited_by_gate` says whether the CV fabrication gate may LICENSE this corpus's content;
    `read_by_composer` says whether the composer is handed it at all. It exists because
    `doctor` and the `add` handler both told a user that verifying an entry of ANY kind
    made it "citable by the CV fabrication gate", while `cv/engine.py` read `experience`
    alone (#164 review, M2). Over-claiming here is the worst direction to be wrong in: a
    user reads it as "my skills are feeding my CVs" and stops looking.

    #164 wrote ONE flag because the two questions then had one answer. #165 made them
    different questions: `skills` reaches the prompt as framing and is licensed nowhere.
    Each is derived rather than hand-asserted, and DIFFERENTLY, because only one of them
    can be answered by reading source. `read_by_composer` is derived by grepping
    `cv/engine.py` for `read_evidence("<kind>")`. `cited_by_gate` cannot be -- citability
    is decided by `cv/validate.py::entry_facts`, which walks `bundle["entries"]` and knows
    nothing about kinds -- so it is derived by EXECUTION: build a bundle carrying one entry
    per kind with a distinct sentinel digit and ask which sentinels were licensed. See
    tests/test_evidence_store.py.

    `floor_map` overrides, per floor key, which of THIS kind's frontmatter keys fills
    it -- `(floor_key, frontmatter_key)` pairs, merged over `FLOOR_FIELD_SOURCES`.
    A tuple rather than a dict so `frozen=True` keeps giving these a working `__hash__`.

    It exists because the floor is otherwise an identity mapping on title-cased names,
    and `skills`' four fields collide with NONE of them: measured (#164 review, M3), a
    skills entry whose own `Domain` was `platform` scored ZERO in `cv/bundle.py`'s
    `rank()` against the keyword `platform`, because `rank` reads `best_for`/`category`/
    `title` and the first two were empty strings. #165 is what made that reachable, and
    the mapping was already in place when it landed.
    """
    relpath: str
    fields: tuple
    cited_by_gate: bool = False
    # Whether cv/engine.py puts this corpus in the COMPOSER's bundle at all. SPLIT from
    # `cited_by_gate` at #165, which made the two non-equivalent for the first time:
    # `skills` reaches the prompt as a FRAMING section whose digits `entry_facts`
    # licenses nowhere, and which the #60 ADVISORY audit is deliberately not shown either
    # (cv/bundle.py's `render_audit_bundle`). #164 wrote ONE flag because "read" and "cited" then
    # coincided; collapsing them again would make `doctor` tell a user their skills are
    # citable, which is the over-claim `cited_by_gate` was introduced to prevent.
    read_by_composer: bool = False
    floor_map: tuple = ()
    # A verified entry's CV NAME (its `Label:`, else its title) may be listed in a CV's
    # SKILLS section (#364 D12). The NAMES route only: an experience entry's `Tools:` and
    # `Skills:` reach the pool through core/tokens.py::tool_items and ::skill_items whatever
    # this flag says.
    names_in_skills_pool: bool = False

    def __post_init__(self):
        """Refuse a `floor_map` entry naming a floor key that is not a floor key, or a
        frontmatter key this kind does not declare. Fail loudly at construction, the rule
        `_select_backend` and `Vault._kind` already follow.

        All three close a class rather than an enumerated vector, and none is
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
        # Fail loudly at construction, this module's house rule. The gate can only license
        # content the composer actually put in the bundle, so the reverse combination is
        # incoherent rather than merely unused.
        if self.cited_by_gate and not self.read_by_composer:
            raise ValueError(
                "cited_by_gate=True requires read_by_composer=True: the fabrication gate "
                "cannot license a corpus the composer never emits into the bundle")
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


# (#243) The pipeline sub-apps, in pipeline order, with the phrase a user would recognise.
#
# HERE rather than in `core/doctor.py`, where it was written and where its only consumer
# lives, for one measured reason: `cli.py` needs the names at PARSER-BUILD time, to list
# them in `doctor --require`'s help, and `_build_parser()` runs on every invocation.
# Importing `core.doctor` there costs ~36ms and drags in `core.backends` -- one of the
# three families `cli.py` deliberately keeps off the critical path. This module was
# already imported at `cli.py`'s module scope and costs ~3ms, and it is where the rest of
# the cross-module vocabulary lives (`EVIDENCE_KINDS` below is the same shape). The
# alternative -- hand-listing the names in a help string -- is the stale-roster trap this
# repo keeps walking into.
# This is the ONE hand-written roster in the verdict path, and it is hand-written because a
# label is not derivable from anything executable -- "cv" is the package name, "tailored CVs"
# is what the user came for. Nothing in `sluice/` enumerates the sub-apps, so there is no
# second list to derive these keys from.
#
# Its COMPLETENESS is enforced at RUNTIME, by `ComponentCheck.__post_init__` above, rather
# than by sweeping this module's source: rows are minted in `core/app.py` too, so a
# source-text sweep keyed on this file alone certified only some of them, and a `blocks=`
# spelled as a name rather than a literal is invisible to it either way. Construction-time
# validation catches every row wherever it is built, which is the same reason unknown
# backend and adapter names raise instead of falling through to a default.
CAPABILITIES = (
    ("ingest", "scrape job boards"),
    ("triage", "triage leads"),
    ("cv", "tailored CVs"),
    ("apply", "send applications"),
    ("track", "track replies"),
)


# Derived, never hand-listed. Two rows mean "this stops the entire pipeline", and both used
# to spell the five sub-apps out; a sixth capability would have left them silently naming
# four fifths of the pipeline while reading as complete.
ALL_CAPABILITIES = tuple(name for name, _ in CAPABILITIES)

# The four buckets `verdict()` sorts capabilities into. Deliberately NOT the row states:
# a row is `ok`/`degraded`/`dead`/`setup`/`notice`, a CAPABILITY is one of these, and the
# two vocabularies answer different questions -- several rows in different states can bear
# on one capability. `doctor --require` compares against READY and nothing else.
READY = "ready"
NEEDS_SETUP = "needs setup"
DEGRADED_CAP = "degraded"
BROKEN = "broken"
CAPABILITY_BUCKETS = (READY, NEEDS_SETUP, DEGRADED_CAP, BROKEN)


EVIDENCE_KINDS = {
    # The one kind the fabrication gate licenses. Since #165 `read_by_composer` and
    # `cited_by_gate` are separate flags: the first says the corpus reaches the prompt, the
    # second that the gate may license its content. They coincide here and diverge for
    # `skills`.
    #
    # `Tools` (#364/#365/#368, spec §4.2) is the attribution index: the specific tools and
    # hard skills, tied to the job, that an entry declares it used. It took over that job
    # from #168's `Skills`, and lives here rather than on a skill note for #168's reason --
    # it is where the gate already reads, so a per-entry set slots in beside the entry's
    # figures with no name join. `Skills` (the owner's model, 2026-10-06) holds general
    # soft skills tied to NO job: they feed a CV's SKILLS pick list and nothing else --
    # never shown inside the entry, never vault vocabulary, never attribution-checked.
    # Neither has a `floor_map` entry: no floor analogue, exactly like the skills kind's
    # own Proficiency/Evidence/Signal Value.
    #
    # DECLARING a field makes it live immediately across several independent readers of
    # `spec.fields` -- an argparse flag builder (`cli.py`), an interactive prompt
    # builder (the evidence wizard), a note serializer (`_render_evidence_note`'s
    # unknown-field refusal and its blank-line default), and a note materializer
    # (`_evidence_entries`' per-entry `fields` dict, which `mcpserver.py` passes
    # through whole) -- rather than a fixed NUMBER of sites, which drifts the moment any
    # one of them changes: grep `spec.fields`/`EvidenceKind.fields` across `sluice/` for
    # the current set rather than trusting a count typed here.
    #
    # The BUNDLE, the GATE, the skills pool and doctor consume it through ONE parser,
    # `core/tokens.py::tool_items`, which splits an entry's `Tools:` on commas and refuses
    # an item the gate cannot match as a whole term (one that begins with a digit, for
    # instance) -- so doctor's row, the run's refusal and the gate cannot disagree about
    # what an item is. `cv/validate.py::entry_facts` carries each entry's items into the
    # MISATTRIBUTED TOOL check (a bullet naming a tool no entry it cites declares), and
    # `cv/selection.py::build_pool` offers them as skill picks.
    #
    # A tool's digits are never licensed as a metric: `tool_items` values are matched as
    # terms, never added to an entry's figures, so the `3` in "Examplelang3" is not a
    # citable number. `Skills` items reach neither the figures, the matcher nor the
    # composer's entry text (core/tokens.py::skill_items says why they skip the token rule).
    "experience": EvidenceKind("Job Applications/Experience Library",
                               ("Company", "Category", "Best For", "Metrics", "Skills",
                                "Tools"),
                               cited_by_gate=True, read_by_composer=True),
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
                           ("Proficiency", "Domain", "Evidence", "Signal Value", "Label"),
                           read_by_composer=True, names_in_skills_pool=True,
                           floor_map=(("best_for", "Domain"),)),
    # STAR reuses `Best For` rather than inventing a keyword field: cv/bundle.py's
    # rank() scores on best_for/category/title, so a future consumer gets that ranker
    # unchanged (#195).
    # Situation/Task/Action/Result live in the BODY -- _parse_fm_spaced is line-based,
    # so a multi-line frontmatter value does not round-trip (its continuation lines
    # are re-read as further keys).
    "stories": EvidenceKind("Job Applications/STAR Stories",
                            ("Company", "Best For")),
}



def verify_outcome(spec, subject: str = "it") -> str:
    """What `verify` actually BUYS for this kind, as a verb phrase -- the one place, so no
    message can over-claim on its own (#164 review, M2). Keyed on the kind's flags, never its
    name: `cited_by_gate` first (the gate may license its content), then
    `names_in_skills_pool` (#364 D12: a verified note's name may appear in a CV's skills list).
    Here rather than in sluice/evidence/ so core/doctor.py and core/app.py can say the same
    thing; `subject` lets the init wizard's plural summary share the sentence."""
    if spec.cited_by_gate:
        return f"make {subject} citable"
    if spec.names_in_skills_pool:
        return f"make {subject} available to a CV's skills list"
    return f"mark {subject} reviewed"

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


class RenderDependencyError(RenderError):
    """A renderer could not be CONSTRUCTED because something it needs is not installed.

    A `RenderError` subclass, so every existing `except RenderError` keeps catching it and
    no caller has to learn a second type. It exists so a renderer can DECLARE the one
    distinction `job-sluice doctor` cannot infer (#243): an uninstalled dependency is a
    setup step the user has not taken, while a `cv.template` that is not a file, a template
    that is not valid Jinja2, or a `cv.render_script` that does not exist are all things the
    user DID configure and that do not work. doctor exits 0 on the first and 1 on the
    second, so the difference is the exit code.

    This is a seam member and not an implementation detail on purpose. The first cut of
    #243 asked `isinstance(e.__cause__, ImportError)` in `core/app.py`, which read
    `renderers/template.py`'s `except (ImportError, OSError)` tuple through a keyhole:
    that renderer changing what it catches would silently change doctor's verdict, and a
    THIRD renderer raising `RenderError` from inside an `except` WITHOUT a `from` clause
    gets `__cause__ = None` (implicit chaining sets `__context__`, not `__cause__`) and
    would be classified broken however plainly its message said "not installed". Neither
    shipped renderer is written that way today -- this is a hazard the seam declines to
    hand a future one, not a bug being fixed. The same
    argument `RenderError`'s own docstring makes above -- an orchestrator reaching inside
    one adapter for a name is the seam inverted -- applies to reaching inside it for an
    exception's cause.

    Raise it ONLY for a genuinely absent dependency. A renderer that cannot tell should
    raise plain `RenderError`, which is the louder, safer reading.
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

    OPTIONAL ATTRIBUTE -- `dir`. Not declared below either, and for the same reason as
    `preflight`: a store with no directory is still a store. `Sluice._reverdict_scope`
    reads it via `getattr` to key #223's one-shot re-verdict acknowledgement to ONE store.
    When it is absent the key is built from the configured store name plus `VAULT_DIR`, or
    `vault_dir` when that is unset, and nothing else: that value has a leading `~` expanded
    and is resolved with `realpath`, and the marker holding the key is one file per user.
    So a store whose location is NOT fully determined by that name and that value, read
    that way -- one located relative to the working directory, one that does not expand a
    `~` in that value, or one located by a setting of its own such as a database path --
    MUST expose that location as `dir`: the filesystem path the store itself opens, with
    any `~` already expanded wherever the store expands it. The key resolves `dir` with
    `realpath` exactly as given, so a `~` left in it names a directory literally called `~`
    under the working directory. Without it, every copy of the store that differs only in
    that location resolves to one key, and the first to acknowledge silences the notice
    for the rest, whose leads are then dismissed unannounced.

    OPTIONAL MEMBER -- `preflight() -> dict`. Not declared below, because a Protocol
    member is a REQUIRED member, and the whole point of this hook is that a store may omit
    it. `sluice doctor` (core/app.py) reaches it via `getattr(store, "preflight", None)`
    and reports nothing for that component when it is absent, rather than treating an
    unimplemented hook as a failure.

    A store implements `preflight` to answer "can a run actually use me right now?"
    with facts doctor cannot get any other way -- for the vault: does the configured
    directory exist, is a Judging Profile present, how
    many Experience Library entries are verified, and (#133/#107) is a candidate name
    declared and is a contact block declared -- the two facts `cv/engine.py`'s
    `skipped-config` refusal already gates a real compose on. It returns FACTS, not
    verdicts: classification is `core/doctor.py`'s job, kept pure there the same way
    backend classification is kept separate from `Sluice.doctor`'s credential
    resolution.

    One of those facts is about the store's own CONFIGURATION rather than its contents,
    and it is worth naming because it is the shape a second store would miss (#243). A
    store that cannot be reached should say whether the location it tried was one the
    USER named or the one it ships as a default: "no store configured yet" is an
    unfinished setup step, while "the store I was pointed at is gone" is a fault that
    stops every command, and on disk the two are indistinguishable. `Vault` answers with
    `vault_dir_is_default`, recorded at construction because that is the last moment the
    distinction still exists. Reporting NOTHING is allowed and is not a defect -- a store
    with no notion of a default location has nothing to say -- but `core/doctor.py` then
    takes the louder reading, so a store that CAN tell them apart and stays silent will
    have its unconfigured state reported as broken.

    MUST NOT create or open anything that does not already exist, and MUST NOT read a
    store file that could disarm a later relocation notice -- see #81's warning at
    `core/paths.py`: `sqlite3.connect` creates a 0-byte file merely by OPENING one, and
    the relocation notice on a dedup store is keyed on the resolved path NOT existing,
    so a "harmless" preflight probe would silently disable it for every later run this
    process makes. `Vault.preflight` therefore only `stat`s paths and reads documents
    through the store's own existing read methods (`read_criteria`,
    `read_evidence`/`read_pending_evidence` per kind, `read_candidate_profile` -- it does
    NOT go through a kind-specific spelling), never opens a store's OWN
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
        and it is the reason sluice exists. A stored `last_seen` spread over several lines
        is a hand edit the stamp would corrupt, so it is left as it is (#329).

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

        A store MAY match that recorded identity up to an equivalence of its own, and it
        MUST then apply the SAME equivalence on every path that resolves a lead -- the
        create walk as well as the archive probe. The vault matches note names up to CASE
        and to CANONICAL EQUIVALENCE (#205 and #299, `fold_note_name`), because a board
        renders one employer several ways and two boards may publish the same accented name
        in different composition forms. It stops at compatibility NORMALIZATION: a store MUST
        NOT apply NFKC/NFKD, which would call a superscript and its digit, or a full-width
        letter and its ASCII form, one job. Stated as "no compatibility EQUIVALENCE" this
        would be breached by the reference implementation itself -- full case folding merges
        the fi/ff/ffi ligatures on its own, with no NFKD involved -- so the ceiling is on the
        normalization applied, not on the equivalence that results.
        Applying it in one place and not the other is not a partial improvement, it is a
        RESURRECTION: measured on the vault before the fold reached the archive probe, a
        `EXAMPLE CO` re-scrape of a lead merged away as `Example Co` returned "created"
        while the exact-casing control suppressed. Widening the equivalence can only
        suppress more, never resurrect more, so the direction is safe -- but it MUST NOT
        widen what enters the dedup store, which for the vault stays gated on a matching
        non-empty url that no name equivalence can manufacture. A match reached only by
        the equivalence, without that proof, is "merged_away_unproven".

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
                      blank_values: frozenset | None = None,
                      preserve_block_values: frozenset | None = None) -> bool:
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
        reads the pre-fetch snapshot and is byte-identical to no check at all. A value
        spread over several lines (for a markdown store, a hand-typed block list under a
        key whose own line is blank) is PRESENT, never blank (#329): filling it would
        write over what a person typed.

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
        guard of its own.

        `preserve_block_values` (#329): each named key that is also in `fields` MUST be left
        unwritten when its FRESH stored value spans several lines (a block list, nested mapping
        or block scalar) -- for a markdown store, what a person editing the note by hand may
        type -- while every other field still lands and the returned bool still reports whether
        the record changed. The key then reads back exactly as it did before the write. A value
        that fits on one line, a flow list or flow mapping included, is written normally, though a
        store MAY also leave it unwritten when it cannot tell the value from one continued on a
        later line (a deeper-indented comment under it, say), or when writing it would change how
        the record's other keys read -- the safe direction. Decided
        against the fresh record, before any named field is written, for the delegation reason
        given above. It stays opt-in per key rather than applying to every key: a caller's status
        or score write MUST never be silently skipped just because some unrelated field on the
        same note happens to span several lines.

        `append_note` (#329) carries the same obligation for its own write path, which is not
        a `fields` key and so is not covered by `preserve_block_values`: when the FRESH stored
        `relevance_notes` spans several lines (a block list, nested mapping or block scalar),
        the append MUST be left undone -- the key read back exactly as it was, never corrupted
        -- while every other named field still lands, exactly as an unsafe-for-frontmatter
        append already abstains rather than mangling the note. A `relevance_notes` that fits on
        one line, a flow list or flow mapping included, gets the append normally, with the same
        allowances."""
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
        so the whole merge is aborted with nothing written and no loser touched; an
        `alt_urls` spread over several lines is such a value too, and a `first_seen` or
        `last_seen` spread over several lines is left as it is (#329). Each
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
        one (returns False without writing); a value spread over several lines counts as an
        existing one (#329). Otherwise a value spread over several lines raises
        MalformedNoteField rather than being overwritten with its items orphaned. Returns whether a
        write happened. MAY raise VaultConflict on sustained concurrent edit (#16)."""
        ...

    def hold_for_signoff(self, ref, *, pending: str, claims: str) -> bool:
        """Stamp a #60 sign-off hold (pending_cv + needs_signoff) ONLY IF the note has no
        tailored_cv in FRESH content, mirroring set_tailored_cv(only_if_absent=...). Returns
        whether it stamped -- False means a real send-ready CV already exists, so the caller
        leaves the flagged CV inert rather than latching the lead behind a redundant hold. A
        `pending_cv` or `needs_signoff` spread over several lines raises MalformedNoteField
        rather than being stamped over (#329). MAY raise VaultConflict (#16)."""
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
        upsert's, so a caller never reconstructs it from a stale snapshot. A tailored_cv
        spread over several lines counts as existing ('collision'), and a pending_cv or
        needs_signoff spread over several lines is left as it is and reported 'nothing',
        since clearing it would corrupt what a person typed (#329). MAY raise
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
        does carry `path`), but no contract-bound caller may read one.

        ABSENT and UNREADABLE are different outcomes, and callers discriminate on it. A
        corpus that simply does not exist yet returns `[]` -- the abstain case, and the
        state of every install before its first `job-sluice <kind> add`. A corpus that
        exists and cannot be READ (permissions, a symlink out of the store, an entry whose
        bytes are not valid UTF-8) RAISES. `cv/engine.py` relies on exactly this: it
        catches `(OSError, ValueError)` around the `skills` read, composes without the
        framing section, and stamps `CvResult.skills_unreadable` -- so a store that raised
        for an absent corpus would tell a user with no Skills Inventory yet that their
        corpus is unreadable, on every lead of every run. `experience` is deliberately NOT
        wrapped there, being the gate's only citable evidence.

        `tests/conformance/test_store_contract.py::test_an_absent_corpus_reads_as_empty`
        binds this per kind, because a docstring alone is what the vault happened to do
        rather than what the seam requires."""
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
        """Promote a proposed entry into the VERIFIED corpus, stamping it as verified.

        `name` is the entry's OWN identity as read_pending_evidence reports it (its
        `title`), NOT the raw name propose_evidence was called with. A store reduces a
        user-supplied name at PROPOSE time, so re-deriving that reduction here could
        only disagree with what the store actually filed -- measured, it made an entry
        whose identity did not survive the round trip (one added by hand, which this
        tool treats as a first-class workflow) listable and permanently unverifiable.
        A store must still refuse a `name` that is not a bare identifier in its own
        namespace, so no caller can reach outside the pending set.

        Verification is NECESSARY for citability and not SUFFICIENT for it, and the
        difference is per kind. This call is the only way an entry becomes verified, and
        for a `cited_by_gate` kind that is also the only way it becomes citable by the CV
        fabrication gate. For a kind that is NOT `cited_by_gate` -- `skills` since #165,
        which the composer is shown as framing and the gate licenses nothing from --
        verifying an entry never makes it citable at all. A store implementer reading
        "promote to citable" would be entitled to treat a verified skill as
        citation-authorised evidence, which is the #164 M2 over-claim restated as a
        contract.

        Returns False, writing nothing, when the entry changed since `reviewed` was shown
        to a human -- promoting an edit made after approval would make unreviewed content
        verified, and for a citable kind that means citable. Raises when the name is
        already taken in the verified set, before mutating anything."""
        ...

    def read_criteria(self) -> str:
        """The user's judging criteria -- who they are, what they want, what they refuse.
        Returns "" when unset, and the caller then falls back to the shipped default,
        which states only that nothing is configured and declines to invent an opinion.

        On the judge's critical path, so a store that gets this wrong changes which jobs
        the user is shown."""
        ...

    def read_document(self, rel: str) -> str | None:
        """A store-managed document's text, decoded as UTF-8 with line endings untouched, or
        None when it does not exist. Reading creates nothing. An unreadable or undecodable
        document RAISES rather than reading as empty: shown as absent, it would be offered a
        create the exclusive open then refuses -- or, through a store whose create is not
        exclusive, overwritten. `rel` must stay inside the store, as for `write_document`."""
        ...

    def read_cv_layout(self) -> "CvLayout | None":
        """The user's CV Layout (CV_LAYOUT_RELPATH): which roles a CV shows and how.

        MUST-support, like read_candidate_profile. Three outcomes, kept apart: None when
        the note is absent; LayoutError when it is malformed (any core/layout.py rule, or
        YAML the store cannot read as YAML); OSError/ValueError when it cannot be read at
        all (a symlink out of the store, a permission error, a non-UTF-8 file). An
        unreadable note must never read as absent (#242)."""
        ...

    def read_candidate_profile(self) -> CandidateProfile:
        """The candidate's own identity and application-form data.

        MUST-support, like read_criteria -- NOT optional like
        `Store.preflight`. An optional member would push a `getattr` None-branch
        into four callers and hand cv a "the store cannot say" case with no safe
        answer: composing without a name is the fabrication risk #99 exists to
        stop, and refusing on a store that merely did not implement the hook
        would be a silent feature-off.

        A store with no such document returns an all-blank CandidateProfile --
        abstain, not raise, the same shape read_criteria already has.
        """
        ...

    def write_document(
        self, rel: str, text: str, *, only_if_absent: bool = False, expect_sha: str | None = None,
    ) -> str:
        """Write a store-managed document and return an opaque handle, or "" when the write
        abstained. Callers: the rejected-leads digest (a bare replace), `sluice init` (creates),
        and in-session setup (creates and updates).

        `expect_sha=` (in-session setup's update arm): replace the document ONLY when its
        current text hashes to `expect_sha` (`document_sha`); otherwise -- including when it
        does not exist -- write nothing and return "". It is the human-was-shown-these-bytes
        check a review form needs, best-effort under the same compare-then-replace window
        `core/vault.py::_cas_write` documents, not a lock. The text is written with line
        endings untouched. Combining it with `only_if_absent` raises ValueError.

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
        a never-clobber contract, so an escape would let it scribble over a verified
        evidence entry, which is what the fabrication gate's truth is made of.

        The complementary requirement, because callers distinguish the two outcomes by
        TRUTHINESS: a successful write must return a NON-EMPTY handle. A store returning
        `""` after creating the document would make `sluice init` report "exists (left
        alone)" for a file it had just written."""
        ...

    def keep_document_copy(self, rel: str, expect_sha: str) -> str:
        """Keep a durable copy of a store-managed document's current text, before in-session
        setup replaces it, and return the copy's own document key -- which `read_document`
        reads back as the exact prior text -- or "" when the document is absent or no longer
        hashes to `expect_sha` (`document_sha`), keeping nothing. The copy stands for the text
        the user was shown being replaced, so a document edited since is not copied.

        Every copy is NEW: a second copy of the same document gets a different key and never
        overwrites an earlier one, and none is pruned. A copy must never be read back as the
        document it copies, nor as a lead. A copy that cannot be kept RAISES -- OSError/ValueError
        (a symlinked folder on the way to the copy, a permission error, a `rel` outside the
        store) -- and the caller then does not replace the document: setup never replaces
        without a copy.

        The returned key is store-relative, never a filesystem path, because it is shown to
        the user (the setup_save report names where the copy went). `rel` must stay inside the
        store, as for `write_document`."""
        ...

    def normalize_all_statuses(self, dry_run: bool = False) -> dict:
        """Canonicalize every note's status vocabulary; return a `changed`/`unchanged`/
        `unknown`/`conflicts` summary. A note whose duplicate status lines disagree, or
        whose status is spread over several lines (#329), is left untouched and reported
        under `conflicts`, never auto-resolved. Unlike the
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

    CONCURRENCY (#309) -- every method must be safe to call from several threads at
    once, on ONE shared instance. Triage MAY fetch dossiers over a pool: the fetch closure
    builds a single Fetcher and shares it across those workers, so a second tab is opened
    while the first is still being read. The obligation is unconditional even though the
    shipped default is not -- `dossier_concurrency` defaults to 1, at which
    `_prefetch_dossiers` builds no pool at all, so a Fetcher that quietly assumed one
    caller would pass every default install and fail the first operator who raises the
    knob. Two specific obligations, because both have already been got wrong once here:

    - `create_tab` must hand every caller a DISTINCT tab id, and any per-instance
      bookkeeping behind it must be synchronized. A read-modify-write counter is not
      atomic; two threads taking the same id then read each other's page.
    - a tab id must be an INDEPENDENT handle. Nothing an implementation does for one tab
      may disturb another -- no "current tab" held on the instance, and no shared cursor
      that `evaluate`/`scroll` resolve against.

    Camofox's CLIENT satisfies this by holding only immutable config and building a fresh
    request per call (`sluice/core/camofox.py`) -- which is the whole of what this repo
    can vouch for. The two obligations above are then discharged by the browser SERVER,
    which this repo does not bundle, so they are stated as requirements on an
    implementation rather than as something verified here. An implementation that keeps a
    live session or a connection pool must add its own locking. This is a real obligation, not a note: it
    is the seam that makes `dossier_concurrency > 1` safe, and nothing in the signatures
    can enforce it.
    """

    def create_tab(self, url: str) -> str | None: ...

    def evaluate(self, tab: str, js: str) -> dict: ...

    def scroll(self, tab: str, amount: int) -> None: ...

    def close_tab(self, tab: str) -> None: ...


class RateSource(Protocol):
    """Where exchange rates come from. Today: `frankfurter`.

    ONE method, and the whole contract is in what it must RETURN: a mapping of ISO 4217
    code to GBP per one unit of that currency -- already normalised, whatever the provider
    quoted against. The base a service answers in, and the direction it expresses a rate,
    are properties OF THAT SERVICE, so converting them is the implementation's job and not
    the caller's. A provider that returned its own units-per-base and expected `core/fx.py`
    to invert would put a per-provider fact in shared code, which is precisely how a second
    provider silently gets every rate reciprocal-of-the-wrong-thing.

    FAILURE MODE -- `None`, never an exception. This is unlike the Store and Renderer
    seams, which raise, and the difference is deliberate: rates are an OPTIMISATION over
    the table pinned in `core/fx.py`, so a provider that cannot answer must leave the
    caller exactly as it was rather than take a triage run down. An offline machine, a DNS
    failure, a changed response shape, a service answering in an unexpected base -- all of
    them are `None`. An empty dict is NOT the same thing and must not be returned for a
    failure: it would overwrite a good cache with nothing.

    ONE LIMIT a second implementer has to know, because it is not visible from this
    signature: a code you return that `core/fx.py`'s PINNED table does not carry will be
    cached and valued by `fx.rate()`, but `triage/classify.py` will not read it as MONEY.
    The parser's alphabet is derived from the pinned table at import, deliberately, so the
    same advert cannot parse differently on two machines depending on what each has
    cached. Returning a currency outside that table is therefore harmless but inert; the
    way to make a new currency readable is to re-pin the table in a release.

    A provider must also VALIDATE before it normalises. `fetch` returning a plausible table
    built from a response it did not check is worse than returning `None`, because the
    result is persisted and then used to compute pay-floor REJECTS -- a lead binned on a
    bad feed is one the user never sees. Reject the response instead.
    """

    def fetch(self, timeout: int) -> dict | None: ...


class Renderer(Protocol):
    """Turn a CvDocument into a PDF, and return the path written.

    `render(document, out_dir, *, neutral_name="CV.pdf")` receives the CvDocument sluice
    ASSEMBLED from the vault and a checked reply (#364/#365/#368 spec section 7); nothing
    parses CV text, and a renderer that needs text writes
    `cv/document.py::to_text(document)`. Anything but a CvDocument raises `RenderError`
    naming the renderer: coercing a wrong object would hand the template or the script
    junk, and a string is no longer a CV a renderer may be given.

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

    Raise `RenderDependencyError` (a `RenderError` subclass, defined above) instead when
    construction failed ONLY because something you need is not installed -- a Python
    package, or a native library no `pip install` can supply. `job-sluice doctor` reports
    that as a setup step the user has not taken yet and exits 0, rather than calling their
    install broken. Everything else -- a path they configured that is not a file, a
    template that will not parse, a binary that is not executable -- is plain
    `RenderError`, reported as a fault and exiting 1. If you cannot tell the two apart,
    raise plain `RenderError`: it is the louder reading, and being wrong in that direction
    costs a needless alarm rather than a silent "everything is fine" on an install that
    cannot render.

    NO GRAMMAR HOOK (#364/#365/#368, spec §7.2). The CV's structure is data sluice
    assembles, so there is no composed text for a renderer to parse and no grammar of its
    own that could refuse a gate-clean CV, and `cv/engine.py` asks a renderer for nothing
    but `render`. A renderer receives the document whole and lays it out.
    """

    def render(self, document: "CvDocument", out_dir: str, *, neutral_name: str = "CV.pdf") -> str:
        """Lay the whole `CvDocument` out as a PDF under `out_dir` and return its path.
        Failures raise `RenderError`."""
        ...


@dataclass
class Role:
    """One WORK EXPERIENCE entry. Field names are the PUBLIC CONTRACT a user's Jinja2
    template writes against (`sluice/templates/cv_plain.html.j2` already depends on this
    exact shape) -- renaming a field is a breaking change for every user template."""
    company: str
    dates: str
    location: str
    title: str
    bullets: list[str]


@dataclass
class CvDocument:
    """The whole parsed CV. Same public-contract rule as `Role` above."""
    name: str
    contact: str
    profile: str
    work: list[Role]
    skills: list[str]
    certificates: list[str]
    education: list[str]


# The CV Layout note (#364/#365/#368): the vault's one record of which roles a CV shows and
# how. Beside the Candidate Profile, which supplies the name and contact.
CV_LAYOUT_RELPATH = "Job Applications/CV Layout.md"

# The canonical CV text's section headings, in the order `cv/document.py::to_text` writes
# them. Here rather than in cv/document.py because core/layout.py must refuse a layout
# heading equal to one and core/ may not import a sub-app; cv/document.py re-exports it.
SECTION_HEADINGS = ("PROFILE", "WORK EXPERIENCE", "CERTIFICATES", "EDUCATION", "SKILLS")


class LayoutError(ValueError):
    """The CV Layout note is malformed. Carries EVERY problem found, each naming its path,
    so one edit fixes them all. A ValueError so the existing `(OSError, ValueError)`
    catches keep holding; a caller that tells malformed from unreadable catches this
    first."""

    def __init__(self, problems):
        self.problems = tuple(problems)
        super().__init__("the CV Layout note is malformed:\n  - " + "\n  - ".join(self.problems))


@dataclass(frozen=True)
class LayoutRole:
    """One heading on the CV. `start`/`end` are the note's `from`/`to` (`from` is a Python
    keyword); `end` is `MM/YYYY` or `present`. `employers` defaults to `(heading,)`;
    `bullets_max` is None for no cap, 0 for none."""
    heading: str
    start: str
    end: str
    location: str = ""
    title: str = ""
    employers: tuple = ()
    bullets_max: int | None = None


@dataclass(frozen=True)
class CvLayout:
    roles: tuple
    skills_max: int | None = None
    certificates: tuple = ()
    education: tuple = ()
    any_role: tuple = ()
    omitted: tuple = ()


@dataclass(frozen=True)
class SetupSnapshot:
    """What in-session setup reads before proposing or writing (Sluice.setup_snapshot)."""
    config_text: str | None
    notes: dict            # artefact -> text | None  (keys: SETUP_NOTES)
    unreadable: dict       # artefact ("config" included) -> reason, no path
    vault_from_env: bool   # $VAULT_DIR decides the vault
    vault_is_default: bool # the store fell back to the cwd-relative default
    settings: dict         # "block.field" / "field" -> loaded value, from every loader
    defaults: dict         # the same keys, loaded from an empty config
    source_ids: tuple
    searches: dict         # source id -> [[label, url], ...] currently configured
    # A newly created source block's settings other than its searches ("enabled", "tuning")
    # at their loaded defaults: a search that creates the block declares them (the config check).
    source_defaults: dict
    # A digest of the vault the store resolved, never the path: it goes into `version`, so a
    # vault that moved between setup_status and setup_save is caught without any response
    # carrying a discovered path. None for a store with no directory to name.
    vault_digest: str | None = None
    # Registered sources whose searches would never run, by why: "shipped" (the source module
    # registers it disabled -- a retired board), "config" (`sources.<id>.enabled: false`) or
    # "overlay" (`job-sluice ingest disable`): ingest/enabled.py::off_reason's answer.
    # Offered for no search (spec 2026-10-08, Retired boards are not offered).
    disabled_sources: dict = dataclasses.field(default_factory=dict)

    @property
    def config_exists(self) -> bool:
        return self.config_text is not None

    @property
    def version(self) -> str:
        """One token for the exact state read: the config text or its absence, each setup
        note's text or its absence, and which vault is in use. setup_status returns it and
        setup_save refuses a save whose token no longer matches (spec 2026-10-08, The tools).

        An UNREADABLE artefact is its own state, never "absent": a note that could not be read
        and then became readable has changed under the coach just as an edit would. Hashed, so
        no text and no path travels in it."""
        def part(art, text):
            # Both halves, never one for the other: a config whose text was read but which a
            # loader refused is unreadable AND has text, and two different refused texts must
            # not share a token.
            return [None if text is None else document_sha(text), self.unreadable.get(art)]
        state = {"config": part("config", self.config_text),
                 "notes": {art: part(art, self.notes.get(art)) for art in sorted(self.notes)},
                 "vault": self.vault_digest}
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode("utf-8")).hexdigest()

    def sha_for(self, artefact: str) -> str | None:
        text = self.config_text if artefact == "config" else self.notes.get(artefact)
        return None if text is None else document_sha(text)


@dataclass(frozen=True)
class ArtefactWrite:
    artefact: str              # "config" or a SETUP_NOTES key
    text: str
    expect_sha: str | None     # None: create exclusively
    settings: tuple = ()       # config only: the settings this write may change
    expect: tuple = ()         # config only: ((setting, value), ...) each must read afterwards


@dataclass(frozen=True)
class ArtefactOutcome:
    status: str                # "written" | "conflict" | "failed" | "set_aside"
    reason: str = ""
    # A "written" REPLACE only: where the prior text was kept, relative to the store (a note,
    # Store.keep_document_copy's key) or the config copy's file name in sluice's state folder
    # (`core/config.py::config_copy_dir`). "" for a create, which replaced nothing.
    kept: str = ""
