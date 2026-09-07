# Terminal output safety — design (#280)

Status: proposed, **after two `/review-plan` rounds** — round 1: 18 findings (0 Critical, 7 High);
round 2: 22 findings (0 Critical, 6 High). **Four of round 2's six Highs were defects in round 1's
own fixes**, which is this repo's standing pattern. §9 keeps the record.

Reviewing stops here. Across forty findings and ten reviewer passes, **not one said the design is
wrong**: every High in both rounds was either a false factual claim in this document's prose or a
guard that could not fail. The residual uncertainty is now entirely in test mechanics — which
mutants die, which guards are red, how an excepthook orders against a `finally` — and those are
cheaper to settle by writing the code and running it than by a third pass over prose.

Issue: **#280** (untrusted LLM- and scraper-derived text reaches the operator's terminal
unsanitised, at every print site).

## A note on counts and rosters

**This document deliberately states almost no counts, and hand-lists nothing it could derive.**
That is a change of method forced by the reviews, not a stylistic choice. Four counts or rosters
written here were measured wrong across two rounds, each time in a passage correcting a previous
error:

- `104` log call sites — the pattern keyed on `_log.` and could not see the three sites in two
  modules that bind the logger as `log`. Real answer 107. This is CLAUDE.md's own documented
  "hand-listed names lose to an import alias" hazard, reproduced.
- The `ensure_ascii=False` roster named a site that is not one (`vault.py`'s `alt_urls`, which is
  at the default) and omitted one that is (`core/dossier.py`, whose call is `json.dump` — the
  roster grep searched `json.dumps`, so it could not match half the API).
- `45 test files drive cli.main` — the grep matched any import from `sluice.cli`. An AST walk
  gives 43 importing and 21 actually calling `main`.
- `eight sluice loggers` — nine entries, six carrying handlers.

So: where a number is needed, the **command** is given rather than its output, and where a roster
is load-bearing a **guard derives it** (§6). Every claim about current behaviour was produced by
running the shipped code at `3ae9021a`, and every absence claim was run with a must-be-present
control, because a sweep that finds nothing and a sweep that is broken are indistinguishable from
their output alone.

**No literal control character appears in this file.** Every one is written as an escape.
`onboard/emit.py::_needs_hex` learned this the hard way — its docstring is `r`-prefixed because
without it the docstring itself held six real control characters. The first draft of this spec
then reproduced the same mistake in the same paragraph that cites it: two real bytes, ESC and BEL,
caught by a byte scan of the file.

---

## 1. The problem

`job-sluice` prints scraped job-board text, LLM output about a composed CV, and Gmail message
fields to the operator's terminal, verbatim. A terminal is not a display surface: a `\x1b`
byte in that text is an instruction. Nothing in `sluice/` filters it at any site.

The exposure is on the **default path**. `ingest`, `triage`, `cv run`, `apply prep`, `track
run` and `leads dedupe` all print derived text on an ordinary invocation; none needs a flag.

### 1.1 The roster is four channels, not three sites

#280's own table names three print sites, one of them mis-attributed (§1.2). Re-derived:

```
grep -rn 'print(' sluice/ --include='*.py'
grep -rnE '[A-Za-z_]*log\.(warning|info|error|exception|debug)' sluice/ --include='*.py'
```

Note the second pattern's `[A-Za-z_]*` prefix: keyed on `_log.` alone it misses the modules that
bind the logger as `log`.

| Channel | Representative site | Provenance of the untrusted part |
|---|---|---|
| `print` to stdout/stderr | `cli.py::cmd_leads_dedupe` — `n.fm.get('url','')` | scraped board url |
| | `cli.py::cmd_cv_run` — `violations` / `audit_flags` / `slop` / `voice_flags` | LLM output about a CV derived from a scraped JD |
| | `cli.py::_print_signoff_claims` — held fabrication and style claims | same |
| | `cli.py`'s track dead-letter listing — `label`, `message_id`, `proposal`, `hint` | **Gmail**: anyone who can email the operator |
| `apply/packet.py::render_text` | the whole packet — company, role, location, salary, url | scraped; **a separate file #280 never names** |
| `logging` to stderr | `core/vault.py`'s rename failure — `"could not rename %s -> %s: %s"` with a lead slug | scraped; **a channel #280 never counts** |
| `onboard/ask.py::TtyAsker._say` | wizard echo | operator-typed |

Two of those rows are additions to the issue's picture, and the `logging` one matters most: it is
a whole channel a stream-level fix does not automatically reach (§4.3).

### 1.2 Two corrections to #280's own text

- The table's third row names **`cmd_leads_list`, which does not exist.** The `leads`
  subcommands are `reconcile`, `rename`, `dedupe`, `add`, `dismiss`, `expire`. The site meant
  is the scraped-`url` print inside **`cmd_leads_dedupe`**.
- The symbol is **`UNTRUSTED_DERIVED_CONTENT_WARNING`**, not `UNTRUSTED_DERIVED_CONTENT`.

Worth stating that round 1's fix to §2 then invented `cmd_apply_preview` and an
`apply preview --json` subcommand, neither of which exists — the same error, committed by this
document one section after documenting it. `apply` has exactly `prep` and `record`.

One trap for anyone starting: `grep -rn sanitiz sluice/` returns hits, all of them
`core/vault.py::_sanitize` — the **filename** sanitiser for note stems, unrelated to terminal
output. The claim that nothing sanitises terminal output is correct; that grep does not show it.

### 1.3 The threat set, and why `\t` is not in it

The characters that matter are the ones that change terminal **state** rather than adding a glyph:

| Character | What it does |
|---|---|
| `\x1b` (ESC) | opens CSI/OSC — colour, cursor positioning, screen clear, window title; on permissive terminals OSC 52 writes the **clipboard** |
| `\x0d` (CR) | returns to column 0, so following text **overwrites and hides** what was printed |
| `\x08` (BS) | same, one column at a time |
| `\x07` (BEL) | audible/visual alert |
| `\x0b` `\x0c` | vertical tab / form feed — cursor movement |
| `\x80`-`\x9f` (C1) | a UTF-8-decoding terminal may treat `\x9b` as CSI directly |
| `\x7f` (DEL) | terminal-dependent |
| U+2028 / U+2029 | line separators; also the pair `emit.py` already escapes |
| lone surrogates | no valid encoding; downstream corruption |

`\t` is not in this table. Horizontal tab advances to the next tab stop and can do nothing else —
it cannot recolour, reposition arbitrarily, hide prior output, or touch the clipboard.

**This dissolves the constraint #280 treats as the blocker.** `audit_flags` is `cv/audit.py`'s
`"<verdict>\t<claim>\t<cited-id>"` and `voice_flags` is `cv/voice.py`'s `"flag\t<phrase>\t<why>"`;
both are tab-separated by contract, and `cli.py::cmd_cv_run` prints them with the tabs intact as
visual columns. A blanket control-character strip would destroy that field structure — which is
why the answer is not to special-case `\t`, but to scope the policy to the characters that are
actually dangerous. `\t` was never in scope.

Note where the contract's tab is consumed: `cv/audit.py` and `cv/voice.py` parse on it
(`line.partition("\t")[0]`) **before** anything prints, and `cli.py::_print_signoff_claims`
partitions on it again. By the time a tab reaches a stream, every sluice parse that depends on it
has already run. It survives as display structure only.

---

## 2. What is already safe, what is not, and the code change that follows

Stating this precisely matters: a fix described as introducing protection where protection already
existed is the kind of claim this repo's reviews keep catching. **The first draft got this section
wrong in the direction that mattered, three reviewers caught it, and round 1's correction then got
its supporting roster wrong in both directions.** Hence §6 guard 8, which derives what this
section can only assert.

### 2.1 `json.dumps` at its DEFAULT is safe; `ensure_ascii=False` is not

Measured:

| call | `\x1b` | `\x7f` | `\x9b` (C1 CSI) | U+2028 |
|---|---|---|---|---|
| `json.dumps(payload)` | escaped | escaped | escaped | escaped |
| `json.dumps(payload, ensure_ascii=False)` | escaped | **RAW** | **RAW** | **RAW** |

JSON mandates escaping C0 only, which is why `\x1b` survives the comparison and made a
one-character probe report the whole class safe. DEL, the entire C1 block — including `\x9b`,
which §1.3's own table names as a direct CSI — and the line separators pass through.

`sluice/apply/packet.py::render_json` is the **one printed** `json.dumps` using
`ensure_ascii=False`; `cli.py::cmd_apply_prep` prints its result on `apply prep --json`, and its
fields are scraped. The full set of `ensure_ascii=False` sites is derivable rather than listed
here — `grep -rn ensure_ascii sluice/ --include='*.py'` — and every other one writes to a file, a
prompt or frontmatter rather than to a stream. **Do not trust that sentence on its own: §6 guard 8
sweeps the call sites and asserts each non-printed one's sink, so a future `ensure_ascii=False`
added on a print path reddens rather than silently re-opening this hole.**

**The consequence is worse than an uncovered path: the wrapper would CORRUPT that output.**
`escape_for_terminal` rewrites a raw `\x9b` to the four characters `\x9b`, and `\x` is not a legal
JSON escape. Measured end to end, `json.loads` on the wrapped output raises
`JSONDecodeError: Invalid \escape`. `apply prep --json` is the documented machine-readable
channel, so one scraped C1 byte would make it unparseable.

**Therefore this design includes a code change it did not originally have: `render_json` drops
`ensure_ascii=False`.** Smallest fix that makes the claim true, makes the wrapper genuinely inert
on every printed JSON path, and removes the corruption. Non-ASCII is then emitted as `\uNNNN` —
still valid JSON, still round-tripping, and `tests/test_apply_packet.py::test_render_json_roundtrips`
asserts the round trip rather than the byte form, so it holds either way. Verified in review that
the full-suite failure set is byte-identical with and without the change.

### 2.2 Two protections that do exist

- **Note filename stems are already stripped.** `core/vault.py::_sanitize` maps
  `[<>:"/\\|?*\x00-\x1f]` to `-`, so a slug printed by `leads dedupe`, `expire` or `reconcile`
  carries no C0 character *when sluice created the note*. It does **not** cover `\x7f`, the C1
  block, or a note a human placed in the vault by hand — and it does not touch frontmatter
  **values**, which is where `url`, `title` and `company` live. Never cite this bullet as terminal
  protection (§5).
- **A handful of sites already use `!r`.** `repr` escapes control characters as a side effect, so
  e.g. `cv/validate.py`'s `UNSOURCED SKILL {item!r}` is already inert. Under the new policy these
  have nothing left to escape — the policy does not fight them.

---

## 3. Decisions

Five sub-decisions were open in #280. Each is settled with the measurement or the existing repo
precedent that settles it.

### 3.1 Escape, not strip or replace

**Escape**, in `\xNN` / `\uNNNN` form. Stripping silently modifies the evidence an operator is
looking at, and this codebase is loud rather than silent by policy. Replacing with a visible
placeholder shows that *something* was there but not *what*. Escaping keeps the byte identifiable
and matches `onboard/emit.py`, which reached the same conclusion for YAML.

**Accepted ambiguity:** backslash is not itself escaped, so the four characters `\x1b` could in
principle be four literal characters from the source data. `emit.py` escapes backslash first
because YAML must round-trip; terminal output does not, and escaping backslashes would mangle
every path sluice prints. This is also what keeps the function idempotent (§4.1) — one decision,
two consequences.

### 3.2 Always, never gated on `isatty()`

**Always.** Settled by the repo's own precedent: `cli.py::cmd_init`, `core/app.py` and
`evidence/wizard.py` each carry a written rule that a behaviour gate must be asked of the injected
asker, never derived from `isatty()` independently, because *under pytest `isatty()` is always
`False`* and the gated half becomes unreachable. (What they still permit is `isatty()` *choosing
which asker to construct* — `evidence/commands.py` does that. What is forbidden is a second,
independent `isatty()` test deciding whether a behaviour runs, which is exactly what a TTY-gated
sanitiser would be.) Measured: `isatty()` is `False` on both streams under pytest, so a TTY-gated
sanitiser would be **off in every test in the suite** — a gate that hides a guard.

Independently, TTY-gating checks the wrong moment. `job-sluice cv run > run.log` has a non-TTY
stdout, so the bytes are written raw; `cat run.log` then renders every escape at **view** time.
The terminal that interprets the bytes is frequently not this process's stdout.

### 3.3 `\t` stays, untouched, at every site

See §1.3. `audit_flags` and `voice_flags` keep their columns.

### 3.4 One policy function, two chokepoints — not a per-site helper

A `safe(...)` helper each print site calls is **opt-out by omission**: a new print site is
unguarded by default, and the tree grows a guarded site beside an unguarded one — the "reads as
coverage" shape #280's own in-code comment warns about.

### 3.5 `!r` does not become the policy

Same opt-out-by-omission flaw, plus it adds quotes that would reword messages across the CLI.

### 3.6 Scope: the never-legitimate set only; `\n` is a stated residual

`\n` is the one character the stream **cannot** judge. `cli.py` legitimately prints `"\nNext:"`;
an injected `\n` inside a scraped title is byte-identical, and only the call site knows the
difference. Escaping it at the stream would turn every deliberate blank line into a literal `\n`.

**Decision: `\n` is not escaped, and the residual is documented rather than closed.** An injected
newline forges an output *line* — a scraped title of `Senior Engineer\ncv: 0 violations, all clear`
prints a convincing fake status row. Real, and bounded: it cannot hide prior output, recolour,
reposition the cursor, or reach the clipboard, all of which need CR or ESC and all of which this
design closes. Closing it needs per-field call-site knowledge at an enumerated roster, which
reintroduces §3.4's flaw for exactly the sites it touches.

Owner's call, 2026-09-07: ship the un-opt-out-able backstop with the residual stated.

---

## 4. The design

### 4.1 `core/safeout.py` — the policy, with one home for the vocabulary

`onboard/emit.py::_needs_hex` already defines this character class, *measured* against a real
PyYAML parser: C0, DEL, the whole C1 block, lone surrogates, U+2028/2029. The terminal set is that
set **minus `\n` and `\t`**. Rather than a second vocabulary that agrees today and drifts later,
the predicate moves into `core/` and both callers derive from it:

```
core/safeout.py
    is_control(ch) -> bool        # C0 | DEL | C1 | lone surrogate | U+2028 | U+2029
    escape_for_terminal(s) -> str # escapes is_control(ch) and ch not in "\n\t"
    wrap(stream)                  # the delegating wrapper class (§4.2)

onboard/emit.py
    imports is_control            # onboard sits on core; not a boundary violation
```

`emit.py` keeps escaping `\n`/`\t` through its own `_ESCAPES` table *before* the predicate runs,
so its behaviour is unchanged and its existing tests prove it — confirmed in review that
`_ESCAPES` runs first, so those characters never reach the predicate and the extraction cannot
change their handling.

The escape form reuses `emit.py::_hex_escape`'s measured lesson: `\xNN` takes exactly two hex
digits, so U+2028 needs `\uNNNN`; written as `\x2028` it reads back as `\x20` plus a literal `28`.

Two properties, both load-bearing, both with a guard row in §6:

- **Idempotent.** The escaped form contains only backslash, `x`/`u` and hex digits, none in the
  threat set, and backslash is deliberately not escaped (§3.1). This matters because a log record
  can pass through both the Formatter and the wrapped stream.
- **Stateless per character.** Measured: `print("x")` calls `write()` **twice** — once for the
  text, once for the newline. Any line-oriented implementation is broken by that.

### 4.2 Chokepoint 1 — the stream wrapper, an excepthook, and a `try/finally`

Installed at the top of `cli.py::main()`: a thin object holding the real stream, delegating every
attribute via `__getattr__` and overriding only `write`. It covers every `print` site,
`apply/packet.py::render_text`, and any print site added later, with **zero call-site changes**.

**Install before `_build_parser()`.** `parser.parse_args` raises `SystemExit` on `--help`, a bad
flag or a missing subcommand, and `argcomplete.autocomplete(parser)` exits the process outright
when `_ARGCOMPLETE` is set. Installing after either would leave those paths unwrapped.

**Restore in a `try/finally`.** The first draft installed and returned. Reviewers measured the
consequence: a large number of test files drive `cli.main` in-process (`grep -rlE 'from
sluice\.cli import|sluice\.cli\.main|cli\.main\(' tests/` overstates it — it matches any import
from the module; an AST walk for actual `main` calls is the honest measure), so without a restore
the wrappers nest for the whole session and any test asserting raw printed output becomes
order-dependent. Restore-to-the-saved-original, verified in review to nest correctly to depth 3.

**An escaping `sys.excepthook`, installed and restored in the same `try/finally`.** This closes
the one genuine design gap either review round found, and it was created by round 1's own fix:
`finally` runs during unwinding, so the streams are restored **before** the interpreter prints an
uncaught traceback. Measured — wrapper installed, a `RuntimeError` carrying a scraped title raised
inside, restore in `finally` — the deliberate print came out escaped while the traceback's last
line reached the terminal as a live `ESC [2J` screen-clear plus raw C1, confirmed under `cat -v`.
`main()` catches `ValueError` only, so every other exception class escapes, and sluice's messages
interpolate slugs and urls without `!r`. The excepthook escapes the formatted traceback before it
prints; §6 guard 4 raises a non-`ValueError` through `main()` to hold it.

Delegation must be genuine, not a partial `TextIOBase` subclass — `.buffer`, `.fileno()`,
`.isatty()` and `.encoding` all forward. Nothing in `sluice/` reaches through for those (measured
with a must-be-present control), but the `mcp` library does, and that makes delegation a
correctness requirement rather than a nicety (§7.1).

One bypass exists in principle and does not in practice: a stream **captured into an object before
the wrapper is installed** keeps the original. `onboard/ask.py::TtyAsker` holds an injected
`stdout`, constructed in two places (`grep -rn 'TtyAsker(' sluice/`), both inside command bodies
that run after the install. The implementation must not hoist either to module scope; §6 guard 3
is the sweep, and §6 records the proxy's known limit.

### 4.3 Chokepoint 2 — the logging Formatter

`core/log.py::get_logger` gains a `Formatter` applying the same function to the formatted record.

**Why a second chokepoint.** The first draft called this "the half a stream wrapper CANNOT do",
which a reviewer showed is false as an absolute — a late-bound stream resolving `sys.stderr` at
write time would route even pre-existing loggers through the wrapper. Round 1 then replaced that
with a justification resting on a possible `mcp serve` opt-out, which §7.1 in the same pass
concluded was unnecessary — so the two sections contradicted each other. The reason that is
actually true, and that neither round stated:

> **A Formatter escapes whatever stream its handler ends up holding.** A `main()`-scoped wrapper
> can only ever escape the stream object it replaced. Those are different guarantees, and the
> logging one is strictly stronger for the channel it covers.

The eager-binding fact is still what makes a *single* stream-wrapper design wrong as specified:
`logging.StreamHandler(sys.stderr)` captures the stream object at construction, and importing
`sluice.cli` instantiates loggers before `main()` runs, so their handlers hold the original
stderr. Probed directly, a pre-existing logger is not wrapped and one created afterwards is. The
roster is derived by §6 guard 5 rather than named here — the first draft's count was wrong, and so
was its description of the entries that were not loggers.

The alternative — installing the wrapper at `cli.py` module scope, above every other import — was
rejected: it makes importing `cli.py` side-effecting, and a future import moved above the install
line silently reopens the hole with nothing going red.

### 4.4 The comment that must change

`cli.py::cmd_cv_run` carries a comment (greppable by `Sanitising THIS loop alone`) asserting that
*"nothing in sluice sanitises terminal output at any site"* and that sanitising one loop *"would
leave the others raw while reading as coverage"*. Both become false.

**Rewritten, not deleted.** What a later reader needs is the current policy and its residuals: why
`\t` survives, why `\n` does not, and where the escaping happens. Leaving it would have the tree
assert a decision that no longer holds.

### 4.5 What does not change

- Every printed `json.dumps` at its default. After `render_json`'s fix, that is all of them.
- Every `!r` site. Nothing left to escape.
- `core/vault.py::_sanitize`. Different job, different layer (§5).
- `mcpserver.py`'s own module — but `mcp serve` DOES run under `main()`, so §7.1 is a real
  interaction, not a non-interaction.

---

## 5. What this does not close

- **A forged output line** (§3.6). Injected `\n`. Bounded to adding a plausible-looking row.
- **Tab padding.** A model or board can emit many `\t` to push text rightward. No state change, no
  persistence, and closing it would destroy the `audit_flags`/`voice_flags` contract (§1.3).
- **A child process inheriting fd 1 or 2.** A Python-level text wrapper cannot cover a subprocess
  writing to the terminal directly. Vacuous today for a reason worth stating rather than relying
  on: every sluice subprocess that COULD carry untrusted output captures it
  (`core/backends.py`, `cv/render.py` both pass `capture_output=True`). A later
  `capture_output=False` reopens this silently. A third subprocess site inherits fds BY DESIGN
  rather than by omission: `onboard/ask.py::edit_in_editor`'s `subprocess.call(argv)` opens the
  user's own `$EDITOR` on a temp file and deliberately does not capture, since the whole point is
  an interactive editor session at the real terminal. It stays benign because the temp file holds
  only sluice's own scaffold prose (the `init` wizard's prompt, rendered as `#` comment lines) —
  never scraped or LLM-derived text — so there is nothing untrusted for the inherited fds to carry.
  A grep for `capture_output` alone will not surface this site; it is named here so a reader
  auditing the residual does not have to rediscover it.
- **A note a human placed in the vault by hand — not a residual, listed to prevent a misreading.**
  Such a note may carry a filename stem holding `\x7f` or a C1 character `_sanitize` never saw.
  On the way to a **terminal** that stem is escaped by the `print`/logging chokepoints like
  anything else — but NOT on the way through `input()` (see the next bullet, which is the actual
  residual this one used to claim did not exist). What remains uncovered regardless is the stem
  *on disk*, which is `_sanitize`'s scope and a separate concern. Do not cite §2.2's `_sanitize`
  bullet as terminal protection.
- **`input()` on a real tty bypasses both chokepoints, and is covered by neither by
  construction — a residual closed per call site, not by the wrapper.** CPython's `input()` takes
  the C-level `PyOS_Readline` path when `sys.stdin`/`sys.stdout` report tty file descriptors, and
  writes its prompt through that path directly — never through `sys.stdout.write`, so
  `core/safeout.py::_Escaped.write` never sees it. Measured under a pty: an adjacent `print()` was
  correctly escaped while the `input()` prompt on the same command delivered a live `ESC[2J`
  screen clear. `sluice/cli.py::cmd_cv_signoff` is the only `input()` call in `sluice/`, prompting
  with a note slug that is reachable from a scraped company string (`_sanitize` maps only
  `\x00-\x1f`, so `\x9b`/`\x7f`/U+2028 survive into it) — it now escapes its own prompt with
  `safeout.escape_for_terminal` explicitly, because nothing upstream can do it for the call site.
  `tests/test_output_safety_sweeps.py`'s AST sweep over every `input(...)` call in `sluice/` is
  what keeps this closed going forward — but it is a SYNTACTIC sweep (constant-literal or an
  `escape_for_terminal(...)`-wrapped argument), not a semantic proof: a prompt built by a helper
  function that itself forgets to escape, or an aliased import the sweep's name-matching does not
  anticipate, could slip past it undetected. The residual is therefore real, narrower than before
  this fix, and enforced by a guard rather than by the wrapper's own construction the way every
  `print`/logging site is.
- **Bidirectional-override characters** (U+202A-U+202E, U+2066-U+2069). They reorder text visually
  without being control characters in the C0/C1 sense. Deliberately out of scope: a *rendering*
  concern with real false-positive risk for legitimate right-to-left text, and admitting them
  means classifying scripts rather than characters.

---

## 6. Guards

A **new** print or log site must be covered by construction, and removing the escaping must redden
something. Round 1 proposed three guards; a reviewer executed the first and found it could not
fail. Round 2 executed the rewritten set and found three more that could not. This is the third
version, and every claim below was run.

**Guard 1 — each chokepoint has its own mutant, and its own witness.** The binding decides the
outcome, which is what both earlier versions got wrong. Measured, for a handler stream bound
*before* the wrapper (production: `get_logger`'s eager `StreamHandler` at import) versus *after*
(a logger created inside a test):

| binding | formatter deleted | stream wrapper deleted |
|---|---|---|
| **before** (production shape) | raw ESC — **mutant killed** | survives |
| **after** (test-created logger) | survives | survives |

So a test that creates its own logger witnesses nothing, which is what round 1's table recorded.
The witnesses are therefore **different for the two chokepoints**: the stream wrapper's mutant is
killed by a `print` assertion, and the Formatter's by a logger whose handler holds the *original*
stream. Two further traps, both measured: `caplog` records `LogRecord.getMessage()`, the raw
message, so it cannot observe chokepoint 2 at all; and `capsys` cannot see import-time loggers,
whose handlers hold the pre-pytest stderr. Before accepting either mutant, check no pre-existing
test already kills it — candidates are `tests/test_log.py`,
`tests/functional/test_cli_contract.py`, `tests/test_cv_run_cli.py`,
`tests/test_leads_dedupe_cli.py`.

**The fixture's control character must not sit in an identity field.** Measured against
`tests/test_fixture_name_neutrality.py`'s collectors: `company: "Example Co\x1b"` collects as an
identity and fails `test_no_unreviewed_employer_name_in_test_fixtures`, while the same character
in a `url` is collected by none of them. Two scope limits on that, both from review: it holds for
**Python fixtures under `tests/`** and not for `tests/fixtures/*/raw.json`, where the per-source
digest covers every key; and unswept is not licensed — use a reserved domain
(`example.invalid`, RFC 2606), so the guard does not park an unreviewed hostname in the tree.
Never relax a collector to make this guard pass.

**Guard 2 — no literal control character anywhere in `sluice/` source.** Keeps §4.2's premise
true over time. Written as a **byte scan**, not a grep: read each file and inspect `ord(ch)`,
because a regex over file bytes is the wrong engine. It must **assert the file count it scanned**
and carry an **in-test positive control** (a synthetic string holding `\x1b`, asserted flagged by
the same scanner) — without the walk breaking means it enumerates nothing and passes.

**Guard 3 — no bypass of the chokepoints.** An AST sweep for module-scope `TtyAsker(...)`,
module-scope `StreamHandler(...)`, and `sys.stdout`/`sys.stderr` captured outside a function body;
the *probe alphabet* is derived by walking the AST, the *expected value* is hand-written as empty.
Verified implementable and currently empty. **State its limit in the test:** these arms are a
syntactic proxy for "captured before `main()` runs", and the proxy is known-incomplete for a
stream captured inside a function that executes at import time — `core/log.py::get_logger` is
exactly that, and is covered by chokepoint 2 rather than by this sweep.

**Guard 4 — `main()` restores both streams on every exit.** Three rows, because `main()` has three
exits, not the two §4.2 originally claimed: normal return, error return, and `SystemExit` (from
`parse_args` on `--help` or a bad command; `tests/` already holds `pytest.raises(SystemExit)`
sites). The `SystemExit` row is the one that matters — measured, a `try/finally` and a broken
inline-restore-before-each-return **both pass** without it, while the inline version leaks the
wrapper on that path. A fourth row raises a non-`ValueError` through `main()` and asserts the
traceback was escaped (§4.2's excepthook).

**Guard 5 — the logging roster is derived, with a floor.** Assert every `sluice.*` logger carrying
a handler formats through the escaping Formatter. **It needs an anti-vacuity floor**, and round
1's wording forbade one by conflating a hand-written *count* with a hand-written *floor*: measured,
the `sluice.*` roster is empty at test start and a mis-keyed filter passes vacuously. Assert
`sluice.cli` is in the enumerated set and the set is non-empty, then assert the property.

**Guard 6 — the four load-bearing properties.** Idempotence, stateless-per-write, `\n` passing
through unescaped (the stated residual — asserted so a later tightening is deliberate rather than
accidental), and `\t` surviving with the `audit_flags` field structure intact. Verified in review
that all four are falsifiable by a MOVE/DELETE mutant on the character set.

**Guard 7 — every printed JSON path stays parseable.** Drive `apply prep --json` with a
control-character-bearing lead through the installed wrapper and assert `json.loads` succeeds.
Build the packet from a **blank `CandidateProfile`** — `build_packet` omits blank profile keys, so
no identity is needed; if any field must be populated, warned-field values follow the
`SYNTHETIC-<FIELD>-<N>` shape the neutrality ratchet expects. Guard 1's placement rule binds this
lead too.

**Guard 8 — every `ensure_ascii=False` site's sink is a file or a prompt.** Sweep the call sites
and assert none is on a print path. This is the guard that makes §2.1's prose safe to rely on: its
roster was wrong in both directions when hand-written, twice.

Beyond the guards, the existing suite is the regression check for §4.1's `emit.py` extraction —
`tests/test_onboard_emit.py::CONTROLS` already spans the full predicate domain.

---

## 7. Risks, each with a named verification

### 7.1 `mcp serve` runs under `main()`

Dispatch confirmed: `pyproject.toml` maps `job-sluice = "sluice.cli:main"`, `cli.py` sets
`func=cmd_mcp_serve`, and `main()` calls `args.func`. So the wrapper is installed before the MCP
stdio transport starts. The real mechanism, read in the installed `mcp/server/stdio.py`:

```
stdio_server() -> _claim_fd(1, sys.stdout, "wb", ...)
                  -> _is_backed_by_fd(stream, 1):  return stream.buffer.fileno() == fd
                     ... except (AttributeError, OSError, ValueError): return False
                  -> if not _is_backed_by_fd(...): return stream.buffer, None
```

Two opposite outcomes:

- **Genuine delegation.** `stream.buffer.fileno()` is 1, so mcp dups fd 1 and serves the wire from
  a private binary duplicate — **bypassing the text wrapper entirely.** No opt-out needed.
- **A partial wrapper without `.buffer`.** `_is_backed_by_fd` catches the `AttributeError` and
  returns `False`; `_claim_fd` then falls through to `return stream.buffer, None`, which touches
  `.buffer` again **unguarded**. `mcp serve` dies at startup.

**Round 1's named verification could not fail** and has been replaced.
`tests/functional/test_mcp_contract.py` says in its own docstring "No subprocess, no stdio, no
network" and drives `build_server(...)` directly, so `sys.stdout` is never wrapped — green either
way, which is §3.2's "gate that hides a guard" inside this fix's own verification story.

**What shipped, corrected against this section's own earlier draft.** This paragraph originally
specified an end-to-end smoke test that requests a tool whose response carries a C1 and a
non-ASCII character, asserting the received frame parses and the value round-trips. That test was
not written; `tests/functional/test_mcp_stdio_smoke.py` is **liveness-only** — it drives
`job-sluice mcp serve` over real stdio, sends `initialize`, and asserts a frame comes back and
parses (`json.loads(line)["id"] == 1`). The reason is `build_server()`'s own shape
(`sluice/mcpserver.py::build_server`): every registered tool closes over one `Sluice(config)` built
at server construction, so a real tool CALL — as opposed to the handshake this test drives — needs
a working vault and config to answer at all. `test_mcp_stdio_smoke.py`'s own docstring states the
sandboxing reason the subprocess inherits the ambient environment rather than an explicit `env=`
dict: `conftest.py`'s `monkeypatch.setenv` sandbox must reach the child, or a tool call would touch
a developer's real config and vault. Building that vault state inside a smoke test is a materially
bigger test than the delegation regression it exists to guard (see that file's own docstring: its
entire value is as a regression check on `_Escaped.__getattr__`'s `.buffer` delegation, not as a
first exercise of a real tool), so the unit test named above (`.buffer`/`.fileno()`/`.encoding`
forwarding) plus this liveness check are the verification that shipped.

**Stated residual, not closed by anything above.** The runtime constraint is a range
(`mcp>=2.0.0,<3`) while only the `test`/`mcp` extras pin `mcp==2.1.1`, so the dup-and-divert
mechanism §7.1 verified above is a third-party implementation detail this repo does not control
going forward. An in-range future `mcp` that writes frames through `sys.stdout` as *text* rather
than dup'ing fd 1 would route every response through `_Escaped.write`, which rewrites a raw
DEL/C1/U+2028 character inside a JSON string into its `\xNN`/`\uNNNN` escape — turning a valid JSON
string body into an invalid one, the same class of bug `apply/packet.py::render_json` was
changed to prevent (`ensure_ascii=False` plus the wrapper) on the channel that IS guarded. No test
here would catch that regression until it ships, because nothing in this repo pins `mcp`'s stdio
transport behaviour — only its version range.

### 7.2 The `emit.py` extraction changes YAML output

Verified by `emit.py`'s existing tests, which load every emission back through a real parser, and
whose `CONTROLS` fixture spans the whole predicate domain. Confirmed in review that `_ESCAPES`
runs before the predicate, so the extraction cannot change `\n`/`\r`/`\t` handling. An unchanged
green is the evidence.

### 7.3 An existing test asserts exact output containing a threat-set character

Surfaces as a test failure at implementation time, not silently. **The resolution rule: update the
expected string to its escaped form. Never narrow a guard or relax an assertion to make it pass.**
The case is reachable — `tests/test_onboard_emit.py` already plants control characters and a lone
surrogate — so leaving "relax it" available was a real hazard.

---

## 8. Delivery

One PR: a new module, two chokepoint edits, an excepthook, one `ensure_ascii` fix, one comment
rewrite, one import change in `emit.py`, documentation, and the guards.

1. **Guard tests first, honestly labelled.** Guards 1 and 6 are red against current `main`.
   Guards 2, 3, 4, 5, 7 and 8 are **green from birth** — nothing wraps a stream today, and
   `json.loads` accepts raw C1 because JSON mandates only C0 escaping, so guard 7 can only be red
   between tasks 3 and 5. For those, the in-test positive control is what proves the check fires;
   say so in the test rather than implying every guard was seen to fail. (Guard 6's redness is an
   import error, not an observed assertion failure — also worth stating.)
2. `core/safeout.py` — `is_control`, `escape_for_terminal`, `wrap`.
3. `cli.py::main()` — install the wrapper and the excepthook before `_build_parser()`, restore
   both in a `try/finally`.
4. `core/log.py::get_logger` — the escaping `Formatter`.
5. `apply/packet.py::render_json` — drop `ensure_ascii=False` (§2.1).
6. `onboard/emit.py` — import `is_control` from core; behaviour unchanged.
7. `cli.py::cmd_cv_run` — rewrite the `Sanitising THIS loop alone` comment (§4.4).
8. `docs/ARCHITECTURE.md` — add `safeout.py` to the `core/` enumeration (extending the existing
   catch-all bullet that already names `log.py` is enough), and record in the section describing
   `cli.py` that `main()` installs a process-wide output filter **and** that `get_logger` carries
   the second chokepoint — a one-chokepoint page for a two-chokepoint design would be its own
   drift. Note `core/stem.py` is already absent from that page; adding it in the same pass is
   optional and unrelated.
9. `.rulesync/rules/CLAUDE.md` — record the policy and its residuals, then `npm run rulesync`.
   It is the operating manual every agent reads, and the `\t`/`\n` distinction is exactly the kind
   of thing a later change would otherwise undo by accident.

---

## 9. Review record

**Round 1: 18 findings (0 Critical, 7 High). Round 2: 22 (0 Critical, 6 High).** Five reviewers
each round, each in an isolated worktree at `3ae9021a`.

Round 1's Highs, four of them raised independently by two reviewers: `render_json`'s
`ensure_ascii=False` breaking the "`--json` is safe" claim and corrupting that JSON; §7.1's named
verification being unable to fail; guard 1's single mutant masked by the overlapping chokepoints;
the wrapper never being restored. Plus guard 3 unimplementable, §4.3's "cannot" overstated, and
the logger roster wrong.

**Round 2 found that four of its six Highs were defects in round 1's own fixes**: the `try/finally`
created the traceback hole (§4.2); §2.1's correction invented `cmd_apply_preview`; guard 1's new
mutation table was inverted for production loggers while §4.3 asserted the opposite binding; and
the "red against main" roster was corrected only for the one guard named instead of re-derived.
Round 2 also caught §4.3 and §7.1 contradicting each other on the mcp opt-out, §8 task 8's false
rationale, and guard 5's missing anti-vacuity floor.

One reviewer reported `json.dumps` as safe at `ensure_ascii=False`; that holds for C0 only, and a
probe using `\x1b` alone yields the wrong generalisation. Two reviewers and a direct measurement
of `\x7f`/`\x9b`/U+2028 say otherwise, and the measurement governs. Similarly, round 1 and round 2
reviewers reported opposite mutation tables for guard 1; both were right about the logger they
tested, and §6 guard 1 now carries both rows and the binding that distinguishes them.

Verified clean across both rounds, recorded so it is not re-derived: no personal data, absolute
path, hostname, credential or line-number citation anywhere in the spec; every `§N`
cross-reference resolves; §8's task order is workable; the `emit.py` extraction is
behaviour-preserving; `core/safeout.py` is correctly a plain module rather than a registered seam,
and correctly not folded into `core/log.py` (which would drag `urllib.request` into the
dependency-free `emit.py`); the import direction is legal, with `core/` importing nothing from
`onboard/`; `escape_for_terminal` is idempotent despite the unescaped backslash; the `try/finally`
nests correctly to depth 3; §7.1's trace matches the installed `mcp/server/stdio.py` verbatim; and
no invariant is breached — `render_text` has three callers, all `print`, `prep_one` performs no
vault-note write and no status transition (it does stage a CV file), and `cv/engine.py` hands
unescaped violations to the model, so the fabrication gate and its single retry are untouched.
