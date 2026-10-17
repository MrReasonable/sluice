# Interview prep briefs from STAR stories (#195, part 1 of 2)

## Problem

The `stories` evidence kind has a store, a CLI (`job-sluice stories add|list|verify`) and a
verify step, and nothing reads it. `core/protocols.py`'s `EVIDENCE_KINDS["stories"]` reuses
`Best For` "so a future consumer gets that ranker unchanged (#195)", and `job-sluice doctor`
reports the corpus with "nothing reads this corpus yet" (`core/doctor.py::classify_store`). A user
who records their STAR stories gets nothing for it.

#195 asks for two consumers: interview prep briefs and cover notes. This spec covers the first.
**Cover notes get a later, separate spec** (owner, 2026-10-16): a cover note is SENT and needs its
own gate design, as the issue itself says. So both PRs below reference #195 with `refs #195`,
never a closing keyword, and #195 stays open until the cover-notes work ships.

## Decisions

### The owner's (in chat, 2026-10-16 and 2026-10-17)

1. **Interview prep comes first and cover notes later**, in two specs. This spec covers two PRs: a
   deterministic core and CLI (PR 1), then an MCP coach prompt over the same core (PR 2).
2. **The brief uses every entry in the kind's own folder.** That means verified entries plus any
   note the user placed there by hand, which is labelled unverified (2026-10-16). Entries still in
   `_inbox/` are LEFT OUT and only COUNTED, with a pointer to `<kind> verify` (2026-10-17, revising
   "all stories").
   - Why: an inbox note records nothing about its origin. `stories add`, the `init` wizard and MCP
     `propose_evidence` all produce the same file, so a brief could show assistant-worded text
     beside the user's real stories and invite the user to rehearse it as fact.
   - Leaving the inbox out keeps the store's standing rule that a proposal is inert until a human
     promotes it.
   - The accepted cost: a story drafted with `stories add` needs a verify before it appears.
3. **`leads prep` prints by default.** `--write` appends the brief to the note through
   `Store.append_body_section`, under its own tag, idempotently, and never overwrites a section
   that is already there. `--json` is offered.
4. **It works at any lead status.**

### Decided while writing this spec (delegated; each reason stated so it can be challenged)

5. **There is no LLM in PR 1.** Nothing is composed and no model text reaches the note, so the CV
   fabrication gate has nothing to check and is neither used nor weakened. The issue asks the
   design to "say so rather than inheriting CV-grade machinery by default". So, stated plainly: a
   brief reuses the CV ranker and nothing else from cv, and a Testing row pins it.
6. **Each note gets one brief, written once, under a fixed tag** (§4). Rerunning `--write` reports
   `already_written` and writes nothing. To refresh, the user deletes the section and writes again;
   its heading is visibly sluice's (§4). Two alternatives were rejected:
   - A content-hashed tag (`core/followups.py::answer_tag`). It appends a new brief on every
     change, and since nothing removes body sections, the briefs pile up. That is right for dated
     follow-up answers and wrong for a brief.
   - A status-keyed tag. The deterministic brief does not depend on the stage (§4), so a second
     stage's section would only duplicate the first.
7. **`--write` refuses a brief that write-once would lock in DEGRADED** (outcome `not_writable`,
   §5). Under Decision 6 a degraded brief is permanent: once the user adds stories or pastes the
   posting, their next `--write` reports `already_written`, and the brief's own advice has led into
   a dead end.
   `prepbrief.degradations(...)` computes the degradations BEFORE the brief is built, on every run,
   with or without `--write`. Each degradation becomes a fixed sentence in the brief that names a way
   out, so a printed or `--json` brief states exactly what a write refusal would say. The codes are
   a contract (they reach `--json` and MCP):
   - `no_entries`: some `read_by_prep` kind has no entry. Way out: `<kind> add` or `<kind> verify`.
   - `no_jd`: `jd_source` is `none`, for any reason, an unreadable cache included. Way out: paste
     the posting under `## Job description`.
   - `jd_below_floor`, checked ONLY WHEN A JD EXISTS: `jd_arrived` refuses it. This is the one rule
     triage and cv apply (`len(text) >= min_jd_chars`), called through a cache built with the
     configured floor (§3), never reimplemented, so a JD of exactly `min_jd_chars` passes here
     exactly as it passes there. The user set that floor as their own test of a real posting. Way
     out: paste the full posting.
   - `jd_no_keyword`, checked ONLY WHEN A JD EXISTS: the JD adds no keyword beyond the role's,
     meaning `jd_keywords(role, jd) == jd_keywords(role, "")`. This catches a section reading "TBD"
     at the shipped `min_jd_chars: 0` without shipping a threshold. Way out: paste the full posting.
   - `jd_rejected_page`: the JD is a CACHED copy, and either `note.status` (the store-normalised
     value, never `note.fm["status"]`) is `unjudgeable` or `core/leads.py::blocked_stamp(note.fm)`
     is set. Either marker means triage would not judge that lead on the JD it last saw: the judge
     rejected it, or that JD was missing or below the floor while an older, possibly good, copy
     stayed cached (`get_or_build` never persists a failed refetch, and the pre-judge gate then
     marks the lead). #302 records an `unjudgeable` verdict on a `research`
     lead as the stamp rather than the status, and a verdict refused that way never evicts the
     cache. #402's eviction removes a page only when `unjudgeable_give_up_after > 0` and a verdict
     lands, which is off by default. Prep cannot tell the two producers apart without parsing
     triage's prose, so it refuses all of them. Way out: paste the posting under
     `## Job description`. If the posting has since been taken down and you know the cached copy is
     the real posting, the sentence names the route that fits the lead's state, with its slug
     filled in:
     - a `research` lead: `job-sluice leads unpark --apply "<slug>"`. It clears the stamp AND
       moves the lead to `needs_review`, and the sentence says so. A bare `leads unpark` is a
       preview that writes nothing, and the command never reaches a lead awaiting a follow-up
       reply, so for one of those the sentence names `leads answer` instead;
     - a stamp at ANY other status (it survives a hand move to `shortlist` or later, which is
       where prep is used): delete the `triage_blocked` value by hand;
     - a status of `unjudgeable`: move it off `unjudgeable` by hand.

     The residual below treats each as your call.
   - `note_open_fence`: the note's body ENDS inside an unclosed code fence. That is easy to cause
     with a hand-pasted posting, and the brief would be appended inside the fence. Measured: the
     `## Job description` section then reads as the posting PLUS the whole brief, story text
     included, and triage and cv would take that as the JD. The new read-only
     `jdsection.open_fence(body)` reports it from `_scan_fences`' open opener. Way out: close the
     fence.

   The two "only when a JD exists" guards mean a missing JD reports `no_jd` alone.
8. **The write flag is `--write`, not `--apply`.** `--apply` is the `leads` precedent for a write
   the TOOL computed (`reconcile`, `rename`, `unpark`), and a brief is tool-computed. But `apply` is
   also the sub-app that SENDS applications, so `job-sluice leads prep --apply` reads as "apply to
   this job". The report-by-default half of the convention is kept: without the flag, nothing is
   written.
9. **The lead is named by `--lead SLUG` and matched EXACTLY**, as `leads dismiss`, `mark-asked` and
   `answer` do: `required=True`, `metavar="SLUG"`, byte for byte via `Sluice._resolve_exact`. No
   `leads` subcommand takes a positional, and exactness keeps a write off the wrong note.
10. **A slug that matches nothing gets bounded suggestions.** No CLI listing prints the slug of a
    lead at `applied` or later: `leads followups` lists `research` only, and there is no `leads
    list`. So `not_found` reports the slugs `core/leads.py::slug_matches` finds for the given text,
    over EVERY status, sorted. That is the substring matcher `cv run --lead` uses.
    - The list is capped at `SUGGESTION_LIMIT = 10`, plus a count of the rest. Nothing is dropped
      silently, and a one-letter or empty fragment cannot return the whole backlog. Bounding the
      reply is the reason `list_leads` has a `limit`.
    - Suggestions are only that: nothing is read or written through a substring match.
11. **No usable JD means no ranking.** `build_brief` orders entries by title exactly when the codes
    include `no_jd` or `jd_no_keyword`, and never recomputes that test itself. The brief says which
    case applies (§4). There is no role-only fallback, because a ranking on the role alone would
    claim a relevance the brief cannot back.
12. **The brief's TEXT never embeds the job description.** It names the JD's source and length.
    Copying a cached, scraped page into the vault would be a new path for untrusted text into the
    user's notes. The facade's result still CARRIES the JD text, because PR 2's coach needs it to
    probe for gaps (§9).
13. **Placement follows `docs/ARCHITECTURE.md`'s `core/followups.py` bullet** (#378): a
    facade-driven feature with a body-section grammar that mcpserver reads. All of it lives in ONE
    core module, `core/prepbrief.py`, and there is no `sluice/prep/` package. A command package sits
    ABOVE core (imported by `cli.py`, importing the facade). This code sits the other way round:
    the facade calls it, mcpserver reads `TAGGED_HEADING` through the allow-list, and `cli.py`'s
    `leads prep` handler imports `HEADING` lazily so its stderr lines name the real heading. No
    sub-app imports it.
14. **Naming.** `apply prep` (`Sluice.prep`, `apply/engine.py::PrepResult`, `apply/packet.py`)
    already means staging a CV and an application packet. So no new name is bare "prep":
    - the facade method is `Sluice.prep_brief`;
    - its result type is `BriefResult`;
    - the module is `core/prepbrief.py`;
    - the section heading is `Interview prep brief (job-sluice)`;
    - the help text says "interview prep brief".

## Design

### 1. Modules

- **`core/prepbrief.py`, new.** It holds:
  - `HEADING = "## Interview prep brief (job-sluice)"`, `TAG = "<!-- sluice:interview-prep -->"`,
    `TAGGED_HEADING = f"{HEADING} {TAG}"` (the exact line it writes) and `SUGGESTION_LIMIT`;
  - the `Brief`, `BriefEntry` and `Degradation` dataclasses, and the degradation codes (Decision 7);
  - the pure `degradations(...)`, `build_brief(...)`, `render_markdown(brief) -> str` and
    `to_json(brief) -> dict`;
  - `written_section(body, rendered) -> bool` and `tagged_section_exists(body) -> bool` (§5);
  - the brief's fixed sentence templates.

  It imports only `core/rank.py` and `core/jdsection.py`, which keeps it inside
  `tests/test_core_layering.py`'s P1/P2 checks. The facade computes the floor check, the
  rejected-page check and the open-fence check and passes them in as bools (§4), so this module
  needs neither `core/status`, `core/leads` nor `core/dossier`. It joins
  `tests/test_mcpserver.py::_ISOLATION_ALLOWED_MODULES` by name, with a "pure section grammar and
  renderer; no write path" comment, exactly as `sluice.core.followups` did.
- **`core/rank.py`, new.** `cv/bundle.py::rank` and `cv/engine.py::_jd_keywords` move here as
  `rank` and `jd_keywords`, because core may not import a sub-app. The precedents are
  `core/jdsection.py` and `core/protocols.py::cv_name`. The move changes no behaviour:
  - `cv/bundle.py` imports `rank` from core, so `from sluice.cv.bundle import rank` keeps working.
  - `cv/engine.py` binds `_jd_keywords = jd_keywords` at module scope, and its call site keeps
    calling `_jd_keywords(...)`. So `tests/test_dossier_guard.py`'s
    `monkeypatch.setattr(cvengine, "_jd_keywords", ...)` still governs cv. Its
    `test_the_cv_consumer_proceeds_with_an_empty_jd` is the witness: a call site that called
    `jd_keywords` directly would turn it red.
  - New: `matched_keywords(entry, keywords) -> list[str]`, the sorted keywords whose stem is in the
    entry's haystack. `rank`'s score and `matched_keywords` read ONE private haystack builder
    (`best_for`, `category`, `title`), so the terms a brief shows are the terms that ranked it. The
    score stays `len(wanted & hay)`, counted over stems.
  - The prose naming rank's old home is found by grepping for the CLAIM (`bundle.py:rank`,
    `rank()`, `ranker`, `_jd_keywords`) across `sluice/`, `tests/` and `docs/` (not
    `docs/superpowers/`). Each hit is repointed at `core/rank.py`, with no caller count.
    - Today's hits include: `rank`'s docstring ("Today's only production caller"), `core/stem.py`'s
      consumer list, `EVIDENCE_KINDS["stories"]` ("a future consumer"), `EvidenceKind`'s docstring
      and skills floor-map comment, `Store.read_evidence`, two comments in `core/vault.py`,
      `tests/test_cv_bundle.py` and `docs/ARCHITECTURE.md`'s skills-override paragraph.
    - Left alone: the `_jd_keywords` binding in `cv/engine.py` and the monkeypatch that targets it,
      which are CODE that must keep the name.
- **`core/jdsection.py`** gains one read-only helper, `open_fence(body) -> bool`. It is true when
  `_scan_fences(_lines(body))` ends with an open opener: one scanner, no second copy. Its consumer
  list in the module docstring gains prep. `sections` is unchanged.
  - Prep reads its own section as `jdsection.sections(body, TAGGED_HEADING)`, with no `tag`
    argument. `_is_heading` then compares the WHOLE heading line, right-stripped and casefolded. A
    heading without the tag never matches, and neither does one with different spacing between the
    heading and the tag. Probed: the tagged section is returned, and `[]` comes back for the
    untagged one.
  - A `require_tag` keyword was considered and dropped. With an empty `tag`, `endswith("")` is
    always true, so the keyword would silently do nothing.
- **`DossierCache.peek`** is new (§3).
- **`EvidenceKind.read_by_prep: bool = False`** is new and set on `stories` (§7).
- **`BriefResult`** is defined in `core/app.py`, beside `AnswerResult`.

### 2. Reading the entries

`Sluice.prep_brief` iterates `EVIDENCE_KINDS`, and for EACH kind with `read_by_prep` it calls the
store it resolved twice:
- `read_evidence(kind, verified_only=False)` gives the entries in the kind's folder. An entry is
  `verified` when its `verified` value is truthy. Otherwise it is a hand-placed note, labelled
  unverified.
- `read_pending_evidence(kind)` is used for its LENGTH only. No title, field or body text from an
  inbox entry enters the brief (Decision 2).

The brief is built per kind, and every noun and command is DERIVED from the kind key:
- the kind's section is headed `### <Kind>` (for example `### Stories`);
- the pending line reads "N entries are waiting in the inbox. Run `job-sluice <kind> verify`, then
  delete this section and run `leads prep --write` again, to include them" ("1 entry is ... to
  include it" at a count of one, since a written brief keeps the line for good; final review).
  The line is true in both the printed preview and the written note;
- the empty pointer reads `job-sluice <kind> add`.

No literal `stories` appears in `core/prepbrief.py`.

**Errors.**
- Either call raising `OSError` or `UnicodeDecodeError` gives `entries_unreadable`, naming the kind.
  Nothing is printed as a brief and nothing is written. That exception pair is the one
  `triage/engine.py` catches.
- The reason comes from `core/vault.py::_unreadable_reason`, called as `core/doctor.py` already
  calls it. For an errno-carrying `OSError` it keeps the type and strerror and drops the absolute
  path, because this outcome reaches MCP in PR 2.
- A `ValueError` (an unknown kind) propagates.

**Per entry, on an allow-list:**
- `title`, the verified label, the kind's declared `fields` (`Company` and `Best For` for stories),
  the matched keywords, and `body`.
- `path` is never carried.
- The `category` and `metrics` floor keys are not shown. A hand-typed `Category:` still feeds
  `rank`, and any keyword it matched is listed.

### 3. The job description

The order is the one cv and triage use. A section added after a fetch leaves the older cached copy
on disk, so "section, then cache" is the only order that agrees with them:

1. **The note's `## Job description` section**, read with `jdsection.read(note.body)` the way
   `cv/engine.py::_run_one` reads it. `jd_source = "note"`. When the note holds more than one
   such section, the first is used, as cv and triage use it, and the `- Job description:` line
   says so ("...; the note has N job description sections and this is the first, the one triage
   and cv read"). That is informational, not a degradation: added in the final review (ruling
   R13), because a write-once brief built on a stale first section should be visible before it
   is locked in.
2. **Otherwise the cached dossier**, via `DossierCache.peek(note.fm)`. `jd_source = "cached"` when
   `jd_text(d)` is non-empty, with the dossier's `built_at`.
3. **Otherwise `jd_source = "none"`**, with a reason code: `no_source` (no section and no cached
   copy), `cache_empty` (a cached copy with no JD), or `cache_unreadable`. The cache reason's
   path-free text appears in the brief's `- Job description:` line, built from
   `_unreadable_reason`. The `no_jd` sentence names no cause.

`jd_source` is prep's own vocabulary. cv's `"fetched"` is not reused, because prep never fetches.

**The floor** for either source is `cache.jd_arrived({"jd": {"markdown": text}})`. That is the
predicate triage and cv call, including for a note's own section (`triage/engine.py`'s
`dossier_cache.jd_arrived(d)` pre-judge gate; `cv/engine.py::_run_one`). It is reached through a
cache built with the configured floor:
`self.dossier_cache(self._dossier_dir(), 0, self.config.min_jd_chars)`. That is the read-only
construction `Sluice.doctor`'s census uses, given the real floor, so read-only use has one route
and the floor has one rule. The fetch closure there is lazy and `peek` never reaches it.

**If `jdsection.read` raises, the error is not caught.** That is cv's posture: the reader sits
outside the dossier `try`, so a reader bug is loud and never reads as "no JD". The CLI's top-level
handler reports it with exit 1.

**`DossierCache.peek(lead) -> dict | None`:**
- reads `self._path(lead)`, the key derivation shared with `get_or_build` and `forget`;
- returns `None` on `FileNotFoundError`, which includes a missing cache directory;
- raises `ValueError` with the FIXED, path-free messages "the cached dossier is not valid JSON" and
  "the cached dossier is not a JSON object". `_unreadable_reason` passes an errno-less message
  through as it is, so a message naming the path would carry the user's home directory into the
  brief, `--json` and MCP;
- lets any other `OSError` propagate. `prep_brief` catches `(OSError, ValueError)` and records
  `cache_unreadable`. A partial file left by a concurrent triage or cv `json.dump` is one real way
  into that arm;
- ignores the TTL and the content. Freshness is a fetch-scheduling concept, and for an
  interview-stage lead the cached page may be the only copy of a posting since taken down. Nothing
  prunes the cache by age;
- never calls the fetcher, never creates a directory or file, and never writes;
- takes `note.fm`, never a dossier dict (see `forget`).

**Keywords** are `jd_keywords(note.fm.get("role", ""), jd)`. The role is never `None`, which would
stem to `none`.

### 4. The brief

`degradations(*, kinds, jd, jd_source, role, min_jd_chars, jd_arrived, rejected_page, open_fence)
-> list[Degradation]` is pure. `min_jd_chars` only fills the `jd_below_floor` sentence's slot; the
decision is the `jd_arrived` bool the facade passes in, never a second comparison.
- `kinds` maps each kind to `(entries, pending_count)`.
- The facade computes the three bools: `jd_arrived` (§3), `rejected_page` (Decision 7) and
  `open_fence` (`jdsection.open_fence(note.body)`).
- Each `Degradation` has a `code` and its fixed `sentence`, in the order Decision 7 lists them.
- A sentence's slots take only counts, kind names, command names and `min_jd_chars`, never a
  scraped or vault value.

`build_brief(*, slug, fm, status, jd, jd_source, jd_line, built_at, kinds, degradations) -> Brief` is
pure.
- `jd_line` is the `- Job description:` line's value: source and length, or the `none` reason.
- **Ordering, per kind.** Entries sort by `title` with `str.casefold` first.
  - When the codes include neither `no_jd` nor `jd_no_keyword`, `rank` follows. It is a stable
    sort, so ties stay in title order.
  - With `no_jd`, the brief adds "No job description to match against, so the entries are in title
    order."
  - With `jd_no_keyword`, it adds "The job description adds no word beyond the role's that the
    ranker can match (it reads words of four or more ASCII letters), so the entries are in title
    order."
- **Why the casefold sort is needed.** The reader's listing is byte-sorted (`sorted(os.listdir)`).
  Casefold order differs from that only when titles differ in case, which is why the casefold step
  is not redundant.
- **No interview stage.** The issue asks for selection "against the job ad and the interview
  stage". The deterministic core knows only the status, which says nothing about what an interview
  will test. Asking about the stage, and steering rehearsal by it, is the coach's job (§9).

`render_markdown(brief)` returns the exact text `--write` appends:

```markdown
## Interview prep brief (job-sluice) <!-- sluice:interview-prep -->

- Lead: Example Ltd
- Role: <role>
- Status: interview
- Job description: from the note (2140 characters)

### Stories

- Entries: 2, 1 not yet verified

#### Outcome reached

- Verified: no
- Company: Example Ltd
- Best For: incident response, leadership
- Matched: incident, leadership

> **Situation.** ...
> ...
```

The rules below are each pinned in §Testing.

- **A closed line grammar.** Every line is one of:
  - the tagged heading;
  - `### ` or `#### ` plus a single-line value;
  - `- <Label>: <single-line value>`;
  - `> ` plus body text, or a bare `>`;
  - a blank line;
  - a fixed sentence template with its slots filled as defined above.

  No value ever starts a line. A scraped company or role beginning with ```` ``` ````, `~~~`, `#`
  or `<!--` therefore cannot open a fence, a heading or an HTML block. Measured: an unlabelled
  company line starting ```` ``` ```` hid a later `## Follow-up answers` section from the judge.
- **CR and LF in a single-line value become spaces.** A title comes from a filename, and the
  renderer does not trust where its input came from.
- **The brief's own headings are levels 3 and 4**, so none of them can end the section.
- **Bodies are blockquoted line by line.**
  - Each entry body is split by the same rule as `jdsection`'s own reader (CR, CRLF and LF), and
    every line gets `> `, blank ones included (a blank line becomes `>`).
  - Measured: unquoted, a body containing `## Job description` became the note's JD and hid a later
    `## Follow-up answers` section. Blockquoted, neither happened.
  - The blockquote also holds an unclosed `<!--`, which `jdsection.neutralise` does not handle (it
    closes an unclosed fence, not a comment).

`to_json(brief)` carries the same content as a dict, on an allow-list. Its shape is fixed here,
because PR 2's grader overwrite reads it:
`{"lead": {"slug", "company", "role", "status"}, "jd": {"source", "chars", "built_at", "reason"},
"degradations": [{"code", "sentence"}], "kinds": {<kind>: {"pending": int, "entries":
[{"title", "verified": bool, "fields": {...}, "matched": [...], "body"}]}}}`. `verified` is a JSON
BOOLEAN, never the rendered "yes"/"no". It excludes `path`, the JD text and the markdown. It is serialised with
`json.dumps`'s default `ensure_ascii=True`, for the reason CLAUDE.md gives for
`apply/packet.py::render_json`.

### 5. The facade and the write

`Sluice.prep_brief(*, lead: str, write: bool = False) -> BriefResult`.

`BriefResult`, in `core/app.py`, has the shape of `AskResult`/`AnswerResult`/`DismissResult`. It
carries only plain data, so `mcpserver.py` imports nothing beyond it. `cli.py`'s handler also
imports `core/prepbrief.py` lazily, inside its body, for `HEADING`: the stderr lines that name the
section derive its text from `HEADING.lstrip("# ")`, so a renamed heading cannot leave the CLI
naming a section the note does not hold. The module is pure, so the lazy-import rule is untouched.
Its fields:
- `outcome`, `slug`, `status`, `candidates`;
- `suggestions` (at most `SUGGESTION_LIMIT`) and `more_suggestions` (the count of the rest);
- `markdown` (str or None) and `brief` (the `to_json` dict or None);
- `jd_source` and `jd_text` (the full text, or "");
- `appended` (bool): True exactly when the append landed (`written`, `written_hidden`, or a
  `not_found` after the write), as `AnswerResult.appended`; added in the final review (ruling R12);
- `reason` (str):
  - for `not_writable`: the degradation sentences joined with "; ", followed by "the note already
    has a brief" when it does;
  - for `entries_unreadable` and `write_failed`: the path-free error text;
  - "" for every other outcome.

The steps:

1. **Resolve** with `Sluice._resolve_exact(lead)`. A miss is `not_found`, with suggestions. An exact
   slug shared by more than one note is `ambiguous`, with `candidates`.
2. **Read** the entries (§2) and the JD (§3). Compute the three bools, then call `degradations` and
   `build_brief`. The outcome is `ok`, or `entries_unreadable`.
3. **With `write`, refuse when degraded.** A non-empty `degradations` gives `not_writable`. The
   status and `triage_blocked` are READ for one degradation and nothing else. No status is ever
   written.
4. **Otherwise append.** `store.append_body_section(note.ref, prepbrief.TAG, markdown)` runs on the
   store that `_resolve_exact` returned, with no `require_status`. A `False` therefore means only
   that the tag is already in the file.
5. **Classify from CONTENT** after re-reading via `core/app.py::_reread_lead`.
   `jdsection.sections(body, prepbrief.TAGGED_HEADING)` matches only the tagged heading line:
   - `written_section(body, markdown)` is true only when `markdown` minus its heading line, stripped,
     is one of those sections. That is `record_answer`'s rule.
   - `tagged_section_exists(body)` is true when that list is non-empty.

   | append returned | re-read | outcome |
   |---|---|---|
   | True | `written_section` | `written` |
   | True | not `written_section` | `written_hidden` (a fence opened by a concurrent edit swallowed it; an open fence present beforehand is refused by `note_open_fence`) |
   | False | `tagged_section_exists` | `already_written` (its content may be older than this render; Decision 6) |
   | False | not `tagged_section_exists` | `present_unreadable` (the tag sits elsewhere in the note) |
   | either | the note is gone | `not_found`, after `record_answer`; `appended` says whether the brief landed first |

   - `VaultConflict` or `FileNotFoundError` gives `conflict`.
   - Any other `OSError` gives `write_failed`, with a path-free reason.
   - Both catches are narrower than `record_answer`'s `VaultConflict`-only catch.

**Invariants.**
- *Never-clobber.* The only write is an EOF append through `_cas_write`. No body text is replaced
  and no frontmatter key is touched. No store method is added, so there is no new CodeQL sink.
- *Never-regress.* No status is written. The status and `triage_blocked` are read for one
  degradation only.
- *Empty-config-abstains.* No config key is added. At the shipped `min_jd_chars: 0`, `jd_arrived`
  refuses only an empty JD, which `no_jd` already reports.
- *Fabrication gate.* Untouched. Entries never pass through `build_bundle`/`assign_codes`, so no
  entry gets an `[id]`. Three things read a lead body: `jdsection.read` (triage, cv),
  `followups.answers` (triage) and MCP `get_lead`. §4's grammar and the `note_open_fence` refusal
  keep the section invisible to the first two.

### 6. The CLI: `job-sluice leads prep`

```
job-sluice leads prep --lead SLUG [--write] [--json]
```

- **No flags.** The brief goes to stdout, exit 0. A degraded brief still prints with its sentences
  and exits 0.
- **`--write`.** The brief goes to stdout, then one outcome line goes to stderr:

| outcome | stderr | exit |
|---|---|---|
| `written` | `leads prep: SLUG: written to the note` | 0 |
| `already_written` | `...: not written -- the note already has an "Interview prep brief (job-sluice)" section; the brief above is a fresh render and may differ from it. Delete that section to write a fresh one` | 0 |
| `not_writable` | `...: not written -- <reason>` | 1 |
| `written_hidden` | `...: written, but an unclosed code fence before it now hides it, and whichever section that fence sits in (the job description or the follow-up answers, say) will read the brief as its own text until the fence is closed` | 1 |
| `present_unreadable` | `...: the note carries the interview-prep tag but no readable "Interview prep brief (job-sluice)" section -- fix the note by hand` | 1 |
| `conflict` | the `_lead_refusal` conflict line | 1 |
| `write_failed` | `...: could not write the note -- <reason>` | 1 |
| `not_found` with `appended` | `...: written, but the note moved or went before it could be read back, so whether the brief reads as its own section is unchecked; run the same command again to check` | 1 |

- **Refusals:**
  - `not_found` prints the `_lead_refusal` line, then `did you mean:` with one slug per line, then
    `... and N more` when there are more.
  - `ambiguous` prints the `_lead_refusal` line.
  - `entries_unreadable` prints its reason.
  - Each exits 1.
- **`--json`, with or without `--write`.** Stdout is ONLY the document:
  `{"outcome", "slug", "status", "candidates", "suggestions", "more_suggestions", "brief",
  "reason", "appended"}`. There is no stderr outcome line. Every outcome's exit code is identical to the
  non-JSON run, so a script cannot read a hidden or failed write as success.
- **Handler conventions.** The handler imports `Sluice` inside its body. Output goes through the
  `safeout` wrapper, and any log line through `core/log.py::get_logger`.
- **`--lead` help text:** "the exact store-issued slug (the note's filename)".

**`get_lead`'s content warning** (`mcpserver.py::_GET_LEAD_CONTENT_WARNING`) gains a sentence in
PR 1, since that is what puts the section into `body`. The sentence names
`prepbrief.TAGGED_HEADING`, which mcpserver imports from the allow-listed module. It says:
- a section under that exact line is sluice's own brief;
- its Lead and Role lines are scraped;
- its entry text is the user's own vault entries;
- an entry marked `Verified: no` was placed in the folder by hand and has not been reviewed through
  `<kind> verify`.

The sentence also states its limit: text written under a verbatim copy of that line, tag included,
cannot be told apart from sluice's own. A heading without the tag is not described this way.

### 7. `read_by_prep`, doctor and `verify_outcome`

`read_by_prep` follows CLAUDE.md's `EvidenceKind` rule: one flag per question a reader asks, with
the messages keyed on the flags. It is also exactly what `prep_brief` iterates (§2).

- **`core/doctor.py::classify_store`.** The prep clause is a SUFFIX after the arm chain, the shape
  `judge_fields` was moved to in #196 ("an elif here sat behind an earlier arm and never fired").
  - A `read_by_prep` kind gets `, and read by \`leads prep\` interview briefs`.
  - For that kind, `nothing reads this corpus yet` is suppressed.
  - After PR 1, no registered kind reaches the `nothing reads` clause. It is KEPT, because a future
    kind that nothing reads must still say so, and a row drives it with a synthetic kind.
- **`core/protocols.py::verify_outcome`** gets the same SUFFIX shape, not a third arm. Its two
  existing branches return early, so an arm placed after them would never fire for a kind that
  carries either earlier flag. That is #196's bug, in the sibling function.
  - A `read_by_prep` kind appends `and include {subject} in interview prep briefs`, where
    `{subject}` is the caller's own `subject` argument ("it", "them" or "stories entries").
  - This changes `stories add`'s notice, the `init` summary, MCP `propose_evidence`'s result, and
    the REGISTERED descriptions of the evidence MCP tools, in PR 1.
  - CLAUDE.md's "same two flags in the same order" sentence becomes "the same flags, with prep as a
    suffix in both".
- **Prose saying stories go unread** is found by grepping for the CLAIM (`nothing reads`,
  `not yet consumed`, `consumed by nothing`) across `sluice/`, `tests/` and `docs/`, not by a hand
  list. Today's hits include:
  - `core/vault.py::Vault.preflight`;
  - `Sluice.doctor`'s comment on `stories`' `blocks=()`;
  - `core/doctor.py::ComponentCheck`'s docstring, which will say stories blocks no CAPABILITY
    because prep is not one;
  - `tests/test_doctor.py`'s messages and loop docstring;
  - `tests/test_doctor_verdict.py`;
  - `docs/USAGE.md`, `docs/TROUBLESHOOTING.md` and `docs/ARCHITECTURE.md`.

  The assertions stay and only their reasons change, with ONE named exception:
  `tests/test_doctor.py`'s per-kind loop, whose `else` arm flips for `stories` (§Testing).
- **Two places the grep misses:**
  - `core/protocols.py::Store.read_pending_evidence`'s "this reader's three consumers" is reworded
    without a count.
  - `mcpserver.py`'s `list_evidence` description ("nothing reads until a human verifies them")
    STAYS, because prep reads only the inbox's length, never its content.

### 8. Docs and prose (PR 1)

- **`docs/USAGE.md`:** a `### job-sluice leads prep` section covering the flags, the streams, the
  exit codes (identical under `--json`), each degradation code and its way out, how to refresh, and
  why inbox entries are left out. The `leads` intro's write-convention paragraph names prep's half
  and fixes its existing omission of `rename`.
- **`README.md`:** the Commands table's `leads` row gains `prep`.
- **`docs/ARCHITECTURE.md`:** entries for `core/prepbrief.py` (modelled on its `core/followups.py`
  bullet, and naming its importers: the facade; mcpserver, for `TAGGED_HEADING`, through the
  allow-list; `cli.py`'s `leads prep` handler, for `HEADING`, lazily; no sub-app), `core/rank.py`,
  `DossierCache.peek` and `jdsection.open_fence`, plus the evidence paragraph's `stories` row.
- **`docs/CONFIGURATION.md` and `sluice.yaml.example`:** `min_jd_chars`'s text also names its new
  effect, that a JD below the floor is refused by `leads prep --write`.
- **`.rulesync/rules/CLAUDE.md`**, then regenerate:
  - a sentence on `core/prepbrief.py` stating its import shape: the facade calls it, mcpserver reads
    `TAGGED_HEADING` through the allow-list, `cli.py`'s `leads prep` handler imports `HEADING`
    lazily, and no sub-app imports it (the same importers `docs/ARCHITECTURE.md`'s bullet
    names);
  - the `leads` write-convention paragraph gains `leads prep`: it previews by default because a
    brief is tool-computed, and its flag is `--write` for Decision 8's reason;
  - the `EvidenceKind` flags sentence gains `read_by_prep`;
  - the `classify_store`/`verify_outcome` sentence is updated (§7), with no count.
- **Literals that move**, named so none is "fixed" by loosening:
  - `tests/test_docs_claims.py::test_the_command_tree_walk_is_not_vacuous`'s count. Its docstring
    loses its stale numbers and literal history and KEEPS its never-derive argument ("a derived
    expected value compares the walk against itself and can never fail").
  - `tests/test_core_layering.py::test_core_module_discovery_finds_the_real_fleet`'s count, which
    rises by the two new core modules.
  - `tests/test_app_operations.py`'s facade-name disjointness set, which gains `prep_brief`.

### 9. PR 2: the `prep_brief` MCP tool and the `interview_prep` prompt

**`prep_brief(lead: str)`** is read-only and registered at EVERY privilege level, in `get_lead`'s
shape: a module-level function taking `sluice`, plus a `_tool` closure over `holder.sluice`.

- **Call.** It calls `sluice.prep_brief(lead=lead)` and never passes `write`, so the model has no
  write argument to set.
- **Return value.** `{outcome, slug, status, candidates, suggestions, more_suggestions, reason,
  brief, jd}`, with `jd = {"source", "text"}`.
  - The JD text is complete, because the coach probes for gaps between the posting and the entries.
    It is unbounded, matching `get_lead`'s `body`.
  - No path is returned on any outcome.
  - Every outcome is structured, because mcp 2.1.1 and 2.2.0 both discard a raised tool exception's
    message.
- **One warning per provenance**, as separate keys like `cv_run`/`cv_signoff`:
  - `jd_warning` treats the JD as `get_lead` treats `body`: untrusted scraped content, even from the
    note's section.
  - `entries_warning` says the entries are the user's own vault entries. An entry marked
    `Verified: no` has not been reviewed, so the coach asks the user before treating it as settled.
  - `suggestions_warning` comes on `not_found` whenever there are suggestions, because suggested
    slugs are built from scraped company and role names.
- **Entry bodies are returned as structured data.** `mcpserver.py::list_evidence` omits `body`
  because a listing has no use for the largest field. Here the body is the point, and the tool
  returns one lead's entries, not a corpus.

**The `interview_prep` prompt** is registered beside `career_interview` at every level, with **no
arguments**: Claude Code 2.1.295 passed only an argument's first word, and a slug contains spaces.

- **Text source.** The text comes from `onboard/coach`'s new
  `assemble_prep_prompt(*, write: bool = True, read=read_playbook)`, the same injection seam
  `assemble_prompt` has, built from a new playbook, `sluice/onboard/coach/rehearse.md`.
- **Rosters.** `PLAYBOOKS` stays `career_interview`'s, and `PREP_PLAYBOOKS = ("rehearse",)` is added.
  - The playbook is not named `interview`, because the coach's `interview.md` means interviewing the
    USER about their career.
  - The prompt's description says "prepare for a job interview".
- `assemble_prep_prompt` must never raise, and it imports no backend module.

**The playbook** follows the coach's phase method, with an exit criterion for each phase:
1. Ask which interview it is, then find the slug with `list_leads`.
2. Read the brief with `prep_brief`.
3. Ask how the interview will run, as an open question. The answer comes from the user and is never
   inferred.
4. Name each requirement in the JD that no entry evidences, and ask the user for a real example.
   The coach never supplies one.
5. The user tells each chosen story. The coach checks it for completeness (a situation, the user's
   own action, an outcome) and for fidelity to what the user's note says happened. It never treats
   an unverified entry as settled.
6. At the `--write` level, offer `propose_evidence` for a new story in the user's own words. It
   lands in `_inbox/` and reaches a brief only after `<kind> verify`. At read-only, give the
   `stories add` command instead.

Exit when each requirement the user cares about has a rehearsed story or an acknowledged gap, then
summarise.

**The playbook's neutrality.** It names no role, sector or employer, and holds no opinion about
WHICH competencies, values or answer content matter.
- Requirements come only from the JD and the user's own words.
- Feedback covers only completeness against the STAR structure and fidelity to the user's note.
  STAR is method, not opinion: the store names the corpus `STAR Stories`.
- No list of interview formats or example answers is given.
- `tests/test_coach_prompt.py::_EXAMPLE_MARKERS` gains `and so on` and `etc.` (no current playbook
  contains either).

**Prose corrected in PR 2.** These are already stale:
- the `README.md` MCP section and `docs/ARCHITECTURE.md`'s first MCP roster paragraph, which omit
  `setup_status`;
- the `mcpserver.py` module docstring;
- `list_evidence`'s allow-list sentence;
- `docs/MCP.md`'s "That is the whole surface without `--write`".

Each is rewritten to point at the registered roster instead of restating it, wherever the sentence
allows. Three more corrections:
- `onboard/coach/handoff.md`'s "sluice writes a CV only from evidence entries the user has verified:
  their experience, skills and stories" is corrected, because stories never reach a CV.
- CLAUDE.md's sentence on `mcpserver.py` importing `sluice.onboard` names the `interview_prep`
  prompt, and says `assemble_prep_prompt` imports no backend.
- `scripts/coach_eval/README.md` describes both runners (§10), the prep persona directory, the
  read-only server flag and each runner's scorecard keys.

**Client documentation** covers only clients where the prompt was MEASURED (the owner's rule). PR 2
measures Claude Code. A slash-command form in the docs keeps
`tests/test_coach_command_prefix.py`'s naming-rule paragraph.

### 10. PR 2: a second eval runner for the prep coach

The coach is the product, so PR 2 ships its evals.

**Why a separate runner.** `scripts/coach_eval/run.py`'s loop is the setup interview's. It carries
per-run state that prep does not have: `p.vault_env`, `p.scenario` and `p.focus`; the hand edit,
the retired board and the user messages that `rubric.scenario_checks` needs; and `grade()`'s
`setup_save_reached` overwrite. A generic profile with hooks for all of that would rewrite the setup
loop in order to share it. The prep coach therefore gets its OWN runner,
`scripts/coach_eval/prep.py`.
- The prep runner REUSES the stateless helpers: `_claude`, `_text`, `_final_text`, `_json_object`,
  the isolation module, the sandbox `TemporaryDirectory` discipline, `_outside_repo` and the MCP
  config writer.
- **What changes in `run.py`, listed rather than claimed away.** The setup LOOP and `grade` keep
  their behaviour. These edits are made, and each is pinned:
  - `DISALLOWED_SLUICE_TOOLS` gains `prep_brief`, so the setup coach can never call it. The existing
    roster tests pin this unchanged, and go red without it. `COACH_DISALLOWED` therefore gains
    `mcp__sluice__prep_brief`, and that is the ONLY change to the setup coach's argv.
  - `coach_args` gains keyword parameters (`tools`, `disallowed`, `allowed`) whose defaults are run's
    own constants.
  - `write_mcp_config(sandbox, server_args) -> Path` is extracted from `_run_in_sandbox`, which calls
    it with exactly today's arguments.
  - `_saved_runs` also yields each run's `runner`, read from its scorecard (missing means setup).
    `regrade` and `print_grader_prompts` dispatch on it.
  - `main()` stays in `run.py` and gains the prep flags.
- **The import direction is one way at module scope.** `prep.py` does
  `from scripts.coach_eval import run` at module scope and calls `run._claude(...)` and the other
  helpers BY ATTRIBUTE at call time, so a single monkeypatch of `run._claude` governs both runners.
  `run.py` reaches `prep` only through function-local imports inside `main`, `regrade` and
  `print_grader_prompts`. One way keeps `run.py` importable without `prep`. And prep must use
  attribute access, never `from ...run import _claude`: a by-name binding is what a cycle breaks,
  and it would also put prep's calls out of reach of a patch on `run._claude`.

**The prep runner.**
- **Command and tools.** `command = "/mcp__sluice__interview_prep"`, and the coach runs with
  `--tools ""`, meaning no built-in tools at all.
  - There is no WebSearch, because the playbook takes requirements only from the JD and the user.
  - `prep.kept(write)` is `list_leads` and `prep_brief`, plus `propose_evidence` at the write level.
    `prep.disallowed(write)` selects between HAND-LISTED tuples (`READ_ONLY_DISALLOWED`,
    `WRITE_DISALLOWED`), as `run.SETUP_TOOLS` and `DISALLOWED_SLUICE_TOOLS` are hand-listed. It is
    NEVER computed from `list_tools()`, which would make the roster row compare a derivation with
    itself. They are functions of the level because `propose_evidence` is not registered at
    read-only, so no constant pair can match both levels.
  - "A disallowed tool was attempted" means any tool call whose name is not in the kept set, so a
    built-in is caught too. `rubric.prep_deterministic` receives that set as an argument from
    `prep.py`, PREFIXED as the client reports it (`{f"mcp__sluice__{n}" for n in kept(write)}`, the
    same set `--allowedTools` and the init check get, run.py's `COACH_EXPECTED_TOOLS` shape).
    `kept`/`disallowed` themselves stay bare, for the roster row. `rubric.py` never imports
    `prep`.
  - `coach_args`' allowed and disallowed lists, and the init check
    (`isolation.check_init_event`, run every turn), are all derived from those per `persona.write`.
- **Persona type.** `PrepPersona` is a separate frozen dataclass in `personas.py`. `load_prep_personas`
  loads it from `scripts/coach_eval/personas/prep/*.json`. `load_personas` globs only the top
  directory, so it never sees these files, and `Persona(**json)` never receives prep keys. Fields:
  - `id`, `name_seed`, `location`;
  - `company`, the employer being interviewed for;
  - `role_seed`, a faker seed for the role;
  - `status`, one canonical status;
  - `write`, a bool;
  - `jd_template`;
  - `stories`: a list of `{title, company, best_for, body, verified}`, where `company` is the PAST
    employer the story happened at;
  - `max_turns`.

  `__post_init__` refuses an unknown status, a story missing its body or company, and a
  placeholder in ANY story field (title, company, best_for or body).
- **Text rules.**
  - Only the JD template may carry placeholders, and only `{company}`, `{location}` and `{role}`.
  - Story bodies carry none. A story happened at its own past employer, so filling in the target
    company would make every story claim past work there, and would confuse the grader's ground
    truth.
- **Seeding (`seed_vault`).**
  - **Where it writes.** The seeder never builds `Sluice()`, and reads only the SANDBOX's own
    config (through `sandbox_vault`), never `SLUICE_CONFIG` or `VAULT_DIR`. It constructs
    `Vault(str(run.sandbox_vault(sandbox, vault_env=False)))` explicitly, through its one seam,
    `seed_vault(persona, sandbox, *, store=None)`: `None` builds that Vault, and a test passes a spy
    store. It RAISES when `upsert`
    is not `created` or the JD append returns `False`, and after seeding it READS THE LEAD BACK and
    raises unless `note.status == persona.status`, the JD section is present and every story is
    listed. A read-back, rather than `update_fields`' return, because a persona seeded at `new`
    makes no change and returns `False` legitimately. So a seed that silently did nothing cannot
    produce an eval of an empty or wrong vault. CLAUDE.md puts precedence
    in the FACTORY and never ahead of an explicit constructor argument, so neither `VAULT_DIR` nor
    `SLUICE_CONFIG` in the developer's environment can redirect it.
  - `sandbox_vault` already refuses a vault outside the sandbox. A synthetic lead must never land in
    the developer's real Obsidian vault.
  - **The lead.** It is created with `Vault.upsert` from a `Lead` whose url is derived as
    `https://jobs.example.com/<persona id>`, a reserved host the guard asserts.
  - **The JD.** It is appended with `append_body_section(ref, jdsection.TAG, jdsection.render(jd))`,
    exactly as `Sluice._write_job_description` does.
  - **The status.** It is set with `update_fields(ref, {"status": persona.status})`.
  - **The stories.** Each is placed as a note in the stories folder, carrying `verified:` when
    `verified` is true and none otherwise. That is the same note shape the PR 1 tests write.
- **Read-only runs.** `serve.py` gains `--read-only`, and a persona with `write: false` uses it, so
  the playbook's read-only branch has an eval. `serve.py` otherwise runs with `--write`, as it does
  today.
- **The simulated user.** `PREP_USER_SYSTEM` and `prep_user_prompt(p, transcript)`:
  - The user is told the interview's company and role, and is given ITS OWN STORIES VERBATIM as the
    real record of what it has done.
  - When asked to tell a story, it retells one of them in its own words without contradicting the
    note.
  - Asked about a requirement no story covers, it says honestly whether it has such an experience.
    It may describe one that fits its situation, but never one that contradicts a story.
  - It replies DONE only after the coach's summary.

  This is what gives the fidelity check ground truth.
- **What "successful" means, defined once.** `rubric.prep_brief_ok(events)` returns the
  `prep_brief` calls from `rubric._calls` that are `ok` AND whose payload outcome
  (`rubric._outcome(call)`) is `"ok"`. Every tool outcome is structured (§9), so `not_found`,
  `ambiguous` and `entries_unreadable` are not errors, and the harness's plain "not `is_error`"
  test would count a guessed slug's `not_found` as success. The grader's facts,
  `prep_brief_reached`, both overwrites and the rubric's first check all use this one predicate.
- **The grader.** `PREP_GRADER_PROMPT` and `prep_grader_prompt(events, transcript)`. The grader sees
  only the conversation, so it is TOLD the facts, as `save_fact` does for setup: what the first
  SUCCESSFUL `prep_brief` call returned (each entry's title, verified label and body, plus the JD
  text), or that no call succeeded. It returns JSON with these fields:
  - `gaps_probed`: 1 to 5;
  - `no_supplied_example`: "yes", or "no" with a quote;
  - `unverified_handled`: 1 to 5, or "not exercised";
  - `fidelity_feedback`: 1 to 5;
  - `coaching_quality`: 1 to 5;
  - `notes`.
- **Overwrites in code, never trusted to the grader** (`grade_prep`):
  - When no `prep_brief` call SUCCEEDED (none was made, or every one was denied, failed or came
    back as a non-`ok` outcome), every field except `coaching_quality` and `no_supplied_example`
    becomes "not exercised". `no_supplied_example` is judged from the chat alone, and a run with no
    brief is the one most likely to invent an answer, so the grader is told there were no facts
    (the named constant `NO_BRIEF_FACT`, setup's `NO_SAVE_FACT` analogue) and still scores it.
  - When the returned facts hold no unverified entry (no `verified: false` among
    `brief["kinds"][*]["entries"][*]`), `unverified_handled` becomes "not exercised", whatever the
    grader said.
  - `prep_brief_reached` is written beside the grade, as `setup_save_reached` is for setup.
- **The rubric** (`rubric.prep_deterministic(events, *, slug, write, kept, max_turns)`), from tool
  calls only:
  - a SUCCESSFUL `prep_brief` call happened;
  - every SUCCESSFUL `prep_brief` call named the seeded slug, as
    `_when(bool(prep_brief_ok(events)), all(...))`. With no successful call it reads
    "not exercised", never "pass": `all([])` is `True`, and the setup rubric's own docstring
    forbids reporting a check whose subject never happened as passing. A coach that guessed a
    fragment, read the bounded suggestions and then called with the real slug passes, because that
    recovery is what suggestions exist for;
  - no disallowed tool was attempted;
  - `propose_evidence` was called only at the write level;
  - no tool call was denied (`rubric.failed_calls`, setup's `no_tool_denied` precedent: an all-denied
    run must not score green);
  - the turn count is within `max_turns`.

  The slug comes from the seeder's return value.
- **Files and scorecards.**
  - `copy_prep_files` copies the lead note, the stories folder and `_inbox/`.
  - Each scorecard carries `"runner": "prep"`.
  - `regrade` and `print_grader_prompts` choose by that key: a missing scorecard, or one with no
    `runner` key, is a setup run (every existing saved run, and every existing regrade test), and
    an unknown value is refused.
- **Selection.** `main()` gains `--prep-persona ID` and `--prep-all`, mutually exclusive with the
  existing modes. `--all` keeps meaning the setup personas only, unchanged. The prep entry point is
  `prep.run_persona(p, out_dir, grader_model)`, setup's signature, and `--grader-model` and `--out`
  reach it exactly as they reach setup's.

**Persona content (neutrality).**
- Every story and JD is synthetic and HAND-WRITTEN, never adapted from a real vault.
- **Companies.** The persona's `company` and every story's `company` are checked against
  `_REVIEWED_FIXTURE_IDENTITIES`, never the spare pool, because `_in_use_identities()` does not read
  `scripts/coach_eval/`.
- **Locations** are checked against `tests/conftest.py`'s `LOCATIONS`, and the role is a seeded
  faker title.
- **No place-name recogniser.** Under #27, a list of real places in `tests/` would itself be a leak.
  A per-persona digest over the canonical JSON is the free-text backstop.
- **The taxonomy sweep** reads the RAW template, placeholders unfilled, and every story's title,
  best_for and body (each is free author text, and the brief renders the first two as the entry
  heading and the Best For line). Prose
  that restated a role would trip `NO_TAXONOMY_WORDS` (about one in five faker titles does, measured)
  and push the sweep to drop it, which is why the role arrives only through `{role}`.
- **Board-name guard.** `tests/test_coach_eval.py::test_no_persona_file_names_a_board` is extended
  to `personas/prep/`, with a scope assertion that it read at least one prep file. Board names are
  real firms, and a JD template ("apply via ...") is the likeliest text to name one. The guard stays
  WHOLE-FILE and is never narrowed. Some registry ids are ordinary words, so persona prose must
  avoid them; the guard's failure message says a match on an ordinary word is fixed by rewording
  the persona, never by changing the guard. The ids are derived from the registry at run time,
  never listed in the spec or the tests.

**The personas:**
- one with strong verified stories, at the write level;
- one with gaps, where no story covers a stated requirement;
- one whose only stories are unverified, at read-only.

**Runs** use the established isolation flags and the sonnet grader. A paid run needs the owner's
go-ahead. Read the transcripts, not the scores.

## Residuals, stated

- **A written brief goes stale.** Its entries and JD are as of the write. Refreshing means deleting
  it by hand and running `--write` again (Decision 6). The `already_written` line says so, and so
  does the pending line.
- **The ranker reads ASCII letters only** (words of four or more letters), as cv's does. A posting
  written wholly in another script adds no keyword, so its brief is unranked and `--write` refuses
  it, with a sentence saying why. Printing and `--json` still work. Widening the tokeniser would
  change cv's ranking too, so it is out of scope here.
- **A rejected page can lose its marker.** Any human action that clears the markers after the judge
  spoke has this effect. Examples: `leads unpark --apply` (which also moves the lead to
  `needs_review`) or `leads answer` (`Sluice.record_answer`) clearing `triage_blocked`; a stamp deleted by hand; `leads dismiss`/`leads expire`, or a hand edit,
  moving the status off `unjudgeable`. None of them empties the cache. If the page's chrome adds
  keywords, the page then passes every degradation. This is accepted, because each route is a human
  decision taken after the judge spoke.
- **The status read is not re-checked at write time.** A triage verdict landing between prep's read
  and its append could let a brief through that the new status would have refused. The window is
  milliseconds, and the result is an append the user can delete. Re-checking would need a status
  guard on `append_body_section` that matches a SET of refusals rather than one allowed set, which
  the store contract does not offer.
- **Keyword noise.** `jd_keywords` has no stopword list, so a common word such as "team" can match
  and show under "Matched". It is not filtered: filtering only the display would disagree with the
  ranking, and filtering the ranking would change cv's.
- **Slug matching is byte-exact**, while the store resolves names up to case and canonical
  equivalence (`core/names.py::fold_note_name`). A slug typed in a different case is `not_found`,
  and the suggestions show the real one. `leads dismiss` behaves the same way.
- **Heading matching ignores case.** `_is_heading` casefolds, so a recased copy of the whole tagged
  line reads as sluice's section. `append_body_section`'s own tag test is exact, so with no
  exact-case tag in the note it appends again, and the user sees a second section.
- **A thin but real JD** passes once it adds one keyword beyond the role. No threshold is shipped;
  a configured `min_jd_chars` is the user's lever.
- **Drafts need verifying.** A story drafted with `stories add` does not appear until it is verified
  (Decision 2). The brief counts such entries and names the command.

## Out of scope

- Cover notes, and the cover-note half of #195 (a separate spec).
- Writing a brief through MCP.
- Surfacing track's `## Interview materials` links in the brief.
- Recording an inbox note's origin.
- Widening the fixture identity collectors over `tests/`' pre-existing lowercase `"company":`
  literals. Prep's tests avoid that shape instead (see Testing).
- Widening the ranker's tokeniser.
- Any LLM in the CLI path.

## Testing (PR 1)

**General rules for these rows.**
- Every row is offline.
- **Fixtures are real notes.** Leads and entries are REAL notes in a real `Vault`, built through
  note text or the `Company:` field key that `tests/test_fixture_name_neutrality.py`'s collectors
  read. Pure `build_brief`/`render_markdown` rows take their `fm` and entries from notes read back
  that way.
- **Identities.** Companies come from `_REVIEWED_FIXTURE_IDENTITIES`. Roles come from the seeded
  titles fixture, never a literal. Keyword-match rows get their determinism from the JD and
  `Best For` text, and assert their precondition first: the word they rely on is in
  `jd_keywords(role, "")` (or absent from it), and that list is non-empty where the row needs a
  role word.
- **Hostile values** are the one exception to those rules:
  - The rendering-safety rows need values such as ```` ```x ````, `# x`, `<!-- x` and
    `\r## Job description\rx`.
  - Where one carries a company name, the base is taken AT RUN TIME from
    `_REVIEWED_FIXTURE_IDENTITIES` (imported) and prefixed with markup. The row asserts the base is
    on the roster.
  - Markup-only values that name no firm (`# x`, `<!-- x`, `## Follow-up answers`, sluice's own
    heading text) are permitted literals for both company and role.
  - These are the only rows that may hand a pure function a hand-built `fm`.
- **A write-ready lead** has at least one entry for EACH `read_by_prep` kind, a usable JD (a section,
  or a dossier cached through `get_or_build` with a stub fetcher), and a body that does not end
  inside a fence. Every row that needs a WRITE seeds one.
- **Cache files `get_or_build` would not persist** (empty, or below the floor) are written directly
  at `cache._path(note.fm)`, in `_assemble`'s shape. `_path` is the real key derivation.
- **Chmod rows** carry `tests/conftest.py::UNREADABLE_DIR` and assert the outcome before the reason.
- **Witnesses** MOVE or DELETE code, run against checked-hash bytecode, are committed first, and are
  recorded as having gone red.

**`core/rank.py`** (new `tests/test_rank.py`)
- **`jd_keywords`.** Role plus JD gives sorted, unique, lowercased `[a-z]{4,}` words, and both
  `jd=None` and `""` work. Witness: `return []`.
- **No `role` key.** One story's `Best For` contains `none`, and the JD does not. For a lead note
  with no `role` key, `none` is absent from that story's `Matched:` line. Witness: pass
  `note.fm.get("role")` instead of the `""` default.
- **`matched_keywords`.** Two keywords that share a stem both show, while the score counts the stem
  once.
- **Haystack parity.** Three stories each match JD words in exactly ONE field, a different one each
  (`Best For`, a hand-typed `Category:`, the title), by one, two and three distinct words. Each
  matched word shows in its own story's `Matched:` line, and the ranked order is three, two, one.
  Witness: build `matched_keywords`' haystack from `best_for` alone.
- **The cv binding.** The witness is the existing
  `tests/test_dossier_guard.py::test_the_cv_consumer_proceeds_with_an_empty_jd`. Mutant: call
  `jd_keywords` directly at the `build_bundle` call site.

**`jdsection`**
- `sections(body, prepbrief.TAGGED_HEADING)` returns the tagged heading's section.
  - It does not match an untagged heading with the same text, or two spaces between heading and tag.
  - It DOES match a recased copy and one with trailing whitespace. The recased match is pinned as
    the stated residual.
- `open_fence` is true for a body ending inside a backtick fence and inside a tilde fence, and false
  for a closed fence and for no fence. Witness: return the scanner's flags instead of its opener.

**`DossierCache.peek`**
- It returns a stale entry, and one below `min_jd_chars`.
- A missing file and a missing directory both give `None`, and the directory still does not exist
  afterwards.
- A recording fetcher records zero calls.
- Invalid JSON and a JSON list each raise `ValueError`, with a message that contains no path.
- Found through `cache_key`'s three arms, each seeded by `get_or_build(note.fm)` with a stub
  fetcher: an `fm` with `lead_id`, with url only, and with neither. Witness: recompute the url hash
  in `peek`.

**Entries**
- **Labels and the inbox.** A verified entry reads `yes`, a hand-placed one reads `no`, and a
  `stories add` entry is absent.
  - The pending line reads 1, and names `job-sluice stories verify` and the delete-and-rewrite step.
  - No inbox title, field or body text appears in the markdown or JSON.
  - Witness: read the pending entries into the brief.
- **`entries_unreadable`**, three rows, each leaving the note byte-identical:
  - the kind's folder is a symlink;
  - `_inbox` alone is a symlink, so `read_evidence` succeeds and the count read raises;
  - a story file is invalid UTF-8.

  Witnesses: delete each read from the `try`; drop `UnicodeDecodeError`.
- **Path-free reason.** A chmod-000 story file gives a `reason` with neither the absolute path nor
  `HOME`. Witness: format with `str(exc)`.
- **No `path`.** It appears in neither the markdown nor the JSON, for a relative and an absolute
  vault.
- **Consumer witness for the flag.** Monkeypatch `EVIDENCE_KINDS` so `skills` carries
  `read_by_prep` and `stories` does not. The brief has a `### Skills` section with the skill entry,
  pointers name `job-sluice skills verify` and `skills add`, and `stories` appears nowhere.
  Witnesses: hard-code `"stories"` in `prep_brief`; hard-code the noun in `render_markdown`.
- **Per-kind pin, derived.** Seed one entry per kind and run `prep_brief`. The kinds appearing
  equal `{k for k, s in EVIDENCE_KINDS.items() if s.read_by_prep}`, with a non-empty control.

**JD precedence**
- **Section first.** A note with a section never reads a seeded cache file. Witness: swap the order.
- **Cache next.** With no section, the cache is used and `built_at` is shown.
- **Empty cache.** An empty cached JD gives `none` with `cache_empty`.
- **Unreadable cache**, three rows. Through the facade, the `reason`, the markdown and the `--json`
  stdout each contain neither the cache path nor `HOME`:
  - invalid JSON;
  - a JSON list;
  - a chmod-000 file.

  Witnesses: drop `OSError` from the catch; format with `str(exc)`; put the path in `peek`'s message.
- **No fetches.** No fetch stub is called under any outcome.
- **Reader errors propagate.** A `jdsection.read` that raises reaches the caller.
- **The JD is never embedded.** Seed a JD holding the sentinel `ZZ-SENTINEL-77`, once in the section
  and once in the cache. It is absent from `markdown`, the appended bytes and the `--json` stdout,
  and present in `BriefResult.jd_text`. Witnesses: append the JD in `render_markdown`; add `jd_text`
  to `to_json`.

**Degradations.** Pure `degradations(...)` rows, then rows through the facade. Each fixture is
FREE OF EVERY OTHER degradation, asserted as `[d.code for d in degradations(...)] == [that one]`.
- **`no_entries`:** some kind has none.
- **`no_jd`, alone.** Run at `min_jd_chars` 0 AND at a positive floor. Witnesses, each named
  separately: drop the keyword check's "only when a JD exists" guard; drop the floor check's guard.
- **`jd_below_floor` boundary**, at a configured floor N, for both the section and the cache, with
  JD text that adds keywords. A JD of exactly N characters gives `[]` and writes; N-1 gives
  `jd_below_floor` alone. Witness: compare with `<=` instead of calling `jd_arrived`.
- **`jd_no_keyword`:** a section reading "TBD" at `min_jd_chars: 0`. The entry that sorts LAST by
  title has a `Best For` holding a role word (precondition asserted), and the printed order stays
  title order, with the no-word sentence. Witness: treat any non-empty JD as usable.
- **`jd_rejected_page`**, two rows:
  - a cached JD on a lead whose stored status is `Unjudgeable` (recased). Witness: read
    `note.fm["status"]` raw instead of `note.status`;
  - a cached JD on a `research` lead whose `triage_blocked` holds a bare ISO date.
- **A typed word in `triage_blocked` is not a stamp.** A `research` lead with NO section, a cached
  JD that adds keywords, and a `triage_blocked` holding a word a person typed writes. Witness: read
  `triage_blocked` directly instead of through `blocked_stamp`.
- **`note_open_fence`:** a hand-pasted section ending inside a fence refuses, and the note stays
  byte-identical. Witness: drop the condition.
- **Two at once.** An empty kind AND no JD give both codes, in Decision 7's order, with `reason`
  joining both sentences.
- **One decision, not two** (pure rows with deliberately inconsistent inputs): `jd_arrived=False`
  with a JD longer than `min_jd_chars` gives `[jd_below_floor]`, and `jd_arrived=True` with a JD
  shorter than `min_jd_chars` gives `[]`. Witness: compare `len(jd)` to `min_jd_chars` inside
  `degradations`.
- **`to_json`'s shape.** `to_json(build_brief(...))` over one verified and one hand-placed entry
  gives `verified` as the booleans `true` and `false` under `kinds[<kind>]["entries"]`. Witness:
  emit the rendered "yes"/"no".

Every degradation's sentence names its way out. Each `--write` row leaves the note byte-identical
and still prints the brief. Witness: delete each condition.

**Controls that write.** An `unjudgeable` lead WITH its own section writes, and so does a stamped
`research` lead with one. Witness: refuse every `unjudgeable` lead whatever the JD source.

**Ordering**
- **Casefold.** Files `alpha.md` and `Beta.md`: the reader returns `Beta`, `alpha`, and the brief
  shows `alpha`, `Beta`. Witness: delete the casefold pre-sort.
- **Ranked.** With a usable JD, entries rank by matched keywords. Ties stay in title order, and
  the tie row uses titles that differ in case (`Beta` and `alpha`, equal non-zero scores), so the
  brief shows `alpha` then `Beta`. Witness: delete the presort before `rank` only.
- **No JD.** The entry sorting LAST by title has a `Best For` holding a role word (precondition
  asserted), and the order stays title order, with the no-JD sentence. Witness: order by rank
  whatever the codes are.

**Rendering safety.** These rows go through the real readers, over a WRITTEN note seeded write-ready.
The hostile body is pinned literally: `## Job description` first, then an unclosed fence, then an
unclosed `<!--`, then `## Follow-up answers`. Each row asserts in this order:
1. the outcome is not `not_writable`;
2. the reader assertions;
3. the outcome is `written`.

- **Cache-only JD** (no section): `jdsection.read(note.body)` gives `("", 0)` afterwards. Witness:
  drop the blockquote prefix (raw gives count 1).
- **A note with its own JD section:** the read returns that section, with count 1. Witness: drop the
  prefix (count 2).
- **A follow-up answer after the brief.** The lead is seeded at `research` with a section. Write the
  brief, then call `Sluice.record_answer`. The outcome is `answered`, and
  `followups.answer_sections` returns exactly that dated answer, with no story text. Witness: drop
  the prefix.
- **Line grammar.** Company and role values of ```` ```x ````, `~~~ x`, `# x`, `<!-- x` and
  `## Follow-up answers`, composed per the hostile-value rule:
  - every line matches §4's grammar;
  - each hostile value DOES appear;
  - the read-back section equals `render_markdown` minus its heading;
  - `answer_sections` still finds a section appended after the brief.

  Witness: delete the whole `- Lead: ` / `- Role: ` prefix.
- **Golden render**, from read-back notes: `render_markdown` equals an expected string byte for
  byte. That string covers the header lines, each kind's heading and `Entries:` line, and one
  entry's fields and quoted body, with the role taken from the titles fixture. Witness: delete any
  one header line.
- **CR and LF**, a pure, permitted hostile row:
  - Inputs, handed straight to `build_brief` because the frontmatter reader cannot carry a CR: a
    title (a filename can carry a CR), company and role values composed with
    `\r## Job description\rx`, and a body holding `a\r## Job description\rx` (a bare CR before a
    level-2 heading, the only shape that separates the two split rules, measured).
  - Expected: `jdsection.read == ("", 0)` over the rendered text, plus read-back equality.
  - Witnesses: delete the CR/LF-to-space replacement; split bodies on `\n` only (red as
    `jdsection.read` giving count 1).
- **Heading levels.** Entry headings at level 2 cut the read-back section short. Witness: render
  them at level 2.

**Write.** The byte relation is `after == before + sep + "\n" + markdown + "\n"`. `sep` is `""` when
`before` ends in a newline and `"\n"` otherwise. The frontmatter is byte-identical.
- **Reruns.** A rerun gives `already_written` with the note byte-identical. So does a rerun after the
  entries change.
- **Pending, then verified.** Write with one pending story, then verify it and rerun:
  `already_written`, and the written section's pending line names the delete-and-rewrite step.
  Delete the section and write again: the story is present.
- **Degraded rerun.** On a note that already holds a brief, a degraded `--write` gives
  `not_writable`, and its `reason` says the note already has a brief.
- **Concurrent fence.** A spy store's `append_body_section` inserts an unclosed fence before EOF and
  then appends. The outcome is `written_hidden`, and the stderr line equals §6's fixed line
  verbatim (built from the same constant the handler prints), containing neither `triage` nor
  `cv `. Witness: classify by heading presence, not content.
- **Tag out of place.** The tag in a frontmatter value, plus a hand-typed untagged
  `## Interview prep brief (job-sluice)` section, gives `present_unreadable` with the note
  byte-identical. Witness: read with `jdsection.sections(body, HEADING, TAG)`, the optional-tag form.
- **Recased sections.** A brief whose whole tagged line was recased by hand gives `written`, and
  `sections` then returns two sections. One whose heading words were recased but whose exact tag
  survives gives `already_written`, with the note byte-identical.
- **Note gone.** If the re-read finds no note, the outcome is `not_found`.
- **A stale good copy behind a failed refetch.** A good cached copy older than the TTL, then a
  refetch that returns nothing through triage's pre-judge gate, marks the lead. Prep then refuses
  with `jd_rejected_page`, and its sentence names the stale-copy case.
- **Each way out unblocks a write.**
  - A stamped `research` lead with a cached JD: the sentence names
    `leads unpark --apply "<slug>"` with the real slug. Run it, and the next `--write` gives
    `written`.
  - A stamped lead moved by hand to `interview`: the sentence names the hand delete of
    `triage_blocked`, never `unpark`. Delete it, and the next `--write` gives `written`.
  - A stamped `research` lead awaiting a follow-up reply: the sentence names `leads answer`.
  - An `unjudgeable` lead: the sentence names the hand status change.
  - Witness: name `leads unpark` for every stamped lead.
- **Exceptions.** A spy store's `append_body_section` raising `VaultConflict` or `FileNotFoundError`
  (parametrised) gives `conflict`. A `PermissionError` gives `write_failed`, with a path-free reason.
  Witness: catch each one alone.
- **Any status.** `new`, `shortlist`, `interview` and `rejected` all write.
- **No LLM, no citation.** Monkeypatch `Sluice.backend` and `sluice.cv.bundle.build_bundle` /
  `assign_codes` to raise, then run `prep_brief(write=True)` to `written`.

**Resolve**
- An exact slug resolves.
- **Suggestions span every status.** A fragment matching a lead seeded at `interview` gives
  `not_found`, with that slug in `suggestions`, and nothing written even with `--write`. Witness:
  compute suggestions from a triage-status-filtered read.
- **The bound.** An empty and a one-letter fragment over more than `SUGGESTION_LIMIT` leads give
  exactly `SUGGESTION_LIMIT` suggestions, with `more_suggestions` counting the rest. Witness: drop
  the bound.
- **Twins** give `ambiguous`.
- **Case.** A case-changed slug gives `not_found`, with the real slug suggested.

**CLI**
- Each outcome's stream and exit code is as in §6.
- `--write --json` on `written_hidden` exits 1 with only JSON on stdout.
- **Preview equals write.** `leads prep --lead S` stdout equals `markdown + "\n"`, and a following
  `--write` changes the note by exactly `sep + "\n" + markdown + "\n"`.
- **JSON safety.** `--json` gives one parseable document on every outcome, and a body holding DEL and
  U+2028 survives as escaped JSON.
- **The heading has one spelling.** With `monkeypatch.setattr(prepbrief, "HEADING",
  "## Renamed brief (job-sluice)")`, the `already_written` and `present_unreadable` stderr lines
  contain `Renamed brief (job-sluice)` and not `Interview prep brief`. The handler imports
  `prepbrief` inside its body, so the patch governs it. Witness: hard-code the heading text in
  `cli.py`.
- `did you mean:` prints the bounded list and the `... and N more` line.
- Existing guards stay green:
  - `tests/functional/test_cli_contract.py::test_every_declared_dest_is_read_by_its_handler`;
  - `tests/test_backend_timeout_config.py::test_importing_the_cli_loads_no_backend_module`.

**Doctor and `verify_outcome`**
- **The stories row** reads `read by \`leads prep\`` and NOT `nothing reads this corpus yet`.
  Witnesses: delete the suffix; delete the suppression.
- **The loop's `else` arm** flips for `stories`. Its anti-vacuity line becomes
  `assert any(s.read_by_prep and not s.read_by_composer for s in EVIDENCE_KINDS.values())`, which
  proves this arm is reached, with a message naming the prep arm.
- **The `nothing reads` clause** is driven by a synthetic unread kind monkeypatched into
  `doctor.EVIDENCE_KINDS` (`tests/test_triage_record.py`'s precedent).
- **Suffix in both functions.** A synthetic kind with `read_by_prep` AND `names_in_skills_pool` gets
  the prep clause in BOTH `classify_store` and `verify_outcome`, with `verify_outcome` using the
  caller's subject ("stories entries", not "it"). Witnesses:
  - make `classify_store`'s prep clause an elif inside the arm chain;
  - make `verify_outcome`'s an arm after the early returns;
  - hard-code "it".
- **The evidence tools' registered descriptions** carry the new suffix, and the existing
  description tests are updated to the new text, never loosened.

**MCP (PR 1)**
- `get_lead`'s content warning contains `prepbrief.TAGGED_HEADING`, and names the Lead and Role
  lines as scraped.
- That sentence joins `tests/test_coach_prompt.py::test_registered_descriptions_name_no_preference`
  in PR 1, by name, with a scope assertion.

## Testing (PR 2)

**The tool**
- `prep_brief` is in all three exact read-tier tool-set equalities in
  `tests/functional/test_mcp_contract.py`, at both levels, and its schema is exactly `{lead}`.
- **Through the SDK**, each with no path anywhere in the response:
  - `ok`, with both warnings;
  - `not_found`, with suggestions and `suggestions_warning`;
  - `ambiguous`;
  - `entries_unreadable` (a real chmod-000 file, `UNREADABLE_DIR`).
- The JD text and entry bodies equal the facade's. The `ok` reply's top-level key set is asserted
  exactly, and `reply["brief"] == prepbrief.to_json(...)` for the same fixture, so the envelope
  the eval reads is pinned at the server too. Witness: rename `brief` in the reply.
- **Read-only.** On a WRITE-READY fixture, the note is byte-identical after a call at both levels.
  An in-test control shows that `Sluice.prep_brief(lead=..., write=True)` on that same fixture does
  write.
- **AST pin.** It finds exactly ONE call whose function is the `prep_brief` attribute of the facade
  object, and asserts its keywords are exactly `[lead]` by name: no `write` keyword, no `**`
  expansion (a keyword whose `arg` is `None`), and no positional `*args`. The module function's own
  name is not counted.

**The prompt**
- The prompt roster is `{"career_interview", "interview_prep"}` in `test_mcp_contract.py` and
  `tests/test_ai_setup_contract.py`, at both levels.
- `interview_prep` has no arguments.
- Its served text names `prep_brief`, and names `propose_evidence` at the write level only.

**Playbook guards.** Each roster is pinned to ITS OWN prompt, extended and never loosened.
- **Exact sets.** `test_every_packaged_playbook_is_found_and_used` and
  `tests/test_renderer_template.py`'s shipped-files equality become
  `== set(PLAYBOOKS) | set(PREP_PLAYBOOKS)`: still exact, both directions.
- **Each prompt carries its own roster.** Every `PLAYBOOKS` member is in `assemble_prompt()`, and
  every `PREP_PLAYBOOKS` member is in `assemble_prep_prompt()` at both levels. `assemble_prompt()`
  does not contain `rehearse.md`'s text.
- **Rendered surfaces.** `tests/onboard_prose.py::rendered_artefacts` gains `rendered:prep_prompt`
  at both levels. `tests/test_onboard_questions.py`'s carries-every-playbook pin maps `PLAYBOOKS` to
  `rendered:coach_prompt` and `PREP_PLAYBOOKS` to both `rendered:prep_prompt` surfaces.
- **Text-only sweeps.** `test_no_playbook_introduces_an_example_list` and
  `test_no_playbook_mentions_the_retired_form` parametrise over the union, with a scope assertion
  that `rehearse` is in it.
- **No-preference sweep.** It covers `assemble_prep_prompt` at both levels. A planted-word row passes
  `read=`, as `test_the_sweep_reports_a_planted_role_word` does.
- `PREP_PLAYBOOKS` joins `_NOT_PROSE` beside `PLAYBOOKS`.

**Wording sweeps**
- `_EXAMPLE_MARKERS` gains `and so on` and `etc.`.
- These join `test_registered_descriptions_name_no_preference` by name, with a scope assertion:
  - the tool's and the prompt's registered descriptions;
  - `jd_warning`, `entries_warning` and `suggestions_warning`.

**Packaging and imports**
- The packaging test is unchanged: `tests/test_packaging.py::_expected_playbooks` already lists
  `onboard/coach/*.md` from the tree. A row asserts `rehearse.md` is among what it found, in both the
  wheel and the sdist.
- `assemble_prep_prompt` imports no `sluice.backends` or `sluice.core.backends` module, checked in a
  subprocess.

**The prep eval runner** (`tests/test_coach_eval.py`)
- **Setup changes only where listed.** Every existing setup test passes unchanged. `coach_args()`
  with no keywords equals the argv built from run's own constants. Its only change from today is
  `mcp__sluice__prep_brief` in `--disallowedTools`.
- **One patch governs both runners.** Patch `run._claude` to a recording fake AND patch
  `subprocess.run` to a tripwire that fails the test if called (as `_stub_claude` does), then
  drive one stubbed prep persona end to end. The fake records the expected calls (coach turns,
  user turns and the grader), and the tripwire never fires. Witness: import `_claude` by name in
  `prep.py` (red on the tripwire, with no process spawned).
- **Rosters.** Parametrised over `write`: `prep.kept(write)` plus `prep.disallowed(write)` equal
  that level's real `list_tools()`, and the two are disjoint. Witness: register a new read-only tool
  without placing it on either tuple (red at both levels). `_server_tool_names` gains a `write`
  parameter, and the setup roster is still checked at the write level, the only level setup runs
  at. Persona ids from `load_personas` and `load_prep_personas` are disjoint (both non-empty, scope
  assertion), because the two runners share the `<id>.*` file names.
- **No built-ins.** The prep coach argv carries `--tools ""`, and the init check fails when
  WebSearch or any disallowed tool is present.
- **Personas.** `load_personas` never returns a prep persona, and `load_prep_personas` returns every
  prep file (scope assertion). `PrepPersona` refuses an unknown status, a story without a body or
  company, and a placeholder in each story field, parametrised over title, company, best_for and
  body. Witness: drop each field from the check in turn.
- **The seeder is isolated.** With a decoy `VAULT_DIR` and `SLUICE_CONFIG` exported, each pointing
  at a temp directory, `seed_vault` writes nothing under either. The read-back then runs AFTER
  `monkeypatch.delenv("VAULT_DIR")`, through a store built on the sandbox vault: `prep_brief`
  returns `ok` with the right labels, and the lead's url host is `jobs.example.com`. Parametrised
  raise rows, each passing a spy store through `seed_vault(..., store=)`:
  - `upsert` not `created`;
  - the JD append returning `False`;
  - a read-back status that differs from the persona's;
  - a read-back missing the JD section;
  - a read-back missing a story.

  Witness: delete each check in turn.
- **The simulated user.** `prep_user_prompt` contains every seeded story body verbatim.
- **The rubric.**
  - `test_prep_rubric_deterministic_checks`: every check passes on a well-formed run.
  - **The slug check needs a subject.** Only a `not_found` call, or only a denied call: the slug
    check reads NOT_EXERCISED (`None`), never `True`. Witness: drop the `_when`.
  - A parametrised each-check-fails row covers: no `prep_brief`; a DENIED `prep_brief`; a
    `prep_brief` whose only outcome is `not_found`; a successful call on a wrong slug; a disallowed
    tool attempted, a built-in included;
    `propose_evidence` at read-only; a DENIED `propose_evidence` at the write level; too many turns.
- **The grader.**
  - A `not_found` call followed by an `ok` call: `prep_grader_prompt` carries the `ok` call's
    entries, and `unverified_handled` is NOT overwritten when that call holds an unverified entry.
  - A fragment `not_found`, then an `ok` call with the seeded slug: the rubric's slug check passes.
  - Only a `not_found` call: every field except `coaching_quality` and `no_supplied_example` reads
    "not exercised", and `no_supplied_example` keeps the grader's value.
  - With only a denied `prep_brief`, the same holds, whatever the grader said.
  - With no successful call, `prep_grader_prompt` contains `NO_BRIEF_FACT` and no entry title from
    any persona. Witness: drop the no-facts sentence.
  - With no entry whose `verified` is `false` in the returned brief's kinds, `unverified_handled`
    reads "not exercised", whatever the grader said.
  - The tool-result payloads in these rows are built by CALLING the real module-level
    `mcpserver.prep_brief(sluice, lead)` on a seeded sandbox vault, and wrapping its
    `json.dumps` in a tool_result block. So the envelope keys the eval reads (`outcome`, `brief`,
    `jd.text`) come from the server, never typed by hand.
  - Their identities follow PR 1's rule: companies from `_REVIEWED_FIXTURE_IDENTITIES` and the
    role from the titles fixture, or a prep persona's seeded content.
  - Both overwrites also hold in a regrade.
- **Forwarded flags.** The grader-model-reaches-the-argv row, and the live run's `--grader-model`
  row, are parametrised over both runners. A saved prep run's `--print-grader-prompt` writes
  `prep_grader_prompt`'s exact text and calls nothing.
- **Regrade dispatch.** A saved run with `"runner": "prep"` regrades through `grade_prep`. A missing
  scorecard or key regrades as setup (the existing tests' case). An unknown value is refused.
- **Read-only server.** `serve.py --read-only` runs the server without `--write`.
- **The persona guard.**
  - Every company is on the roster, and every location is in `LOCATIONS`.
  - Only the JD template carries placeholders, and only the three allowed ones.
  - The raw template and every story's title, best_for and body go through
    `expresses_a_preference`.
  - `test_no_persona_file_names_a_board` reads `personas/prep/`, with a scope assertion.
  - A per-persona digest forces a reviewed re-digest on any change.
  - A scope assertion checks that the guard found the prep personas.
