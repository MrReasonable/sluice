"""The nine `job-sluice {experience,skills,stories} {add,list,verify}` handlers (#164).

Imported from `cli.py` inside `_build_parser()` rather than at cli.py's module scope.
That keeps it off cli.py's import list but NOT off the critical path: `_build_parser()`
runs on EVERY invocation, to build the whole argparse tree, so this module loads
unconditionally. It is safe to, and that is the point -- at module scope it imports
`EVIDENCE_KINDS` and nothing else: config-shaped data (relpaths, field tuples, two
flags), not a store and not a backend.

What is genuinely deferred is one layer further in. `from sluice.core.app import
Sluice` sits inside each `cmd_evidence_*` BODY, so an offline command -- and its tests
-- never pulls in the vault/backend machinery.

This docstring used to credit the `_build_parser` import for that deferral (#164
review, M4). It is false in a way that matters rather than merely imprecise: it invites
someone to "restore" the laziness it describes by hoisting the per-function `Sluice`
import up here, which would move a heavy import onto every single invocation while
reading as a tidy-up.
"""
import argparse
import sys

from sluice.core.protocols import EVIDENCE_KINDS
from sluice.core.protocols import verify_outcome  # noqa: F401 -- re-exported for cli.py


def field_flag(field: str) -> str:
    """`Signal Value` -> `--signal-value`. One place, so the parser and the command
    body cannot disagree about what argparse called the destination."""
    return "--" + field.lower().replace(" ", "-")


# A field a kind used to take, mapped to the field that replaced it. 4.0 (#364/#365/#368,
# #364 spec §4.2) retired `Skills:` on experience entries for `Tools:`, so `experience add
# --skills` would otherwise die as a bare argparse "unrecognized arguments" that never says
# what to type instead. Keyed by field and applied to a kind only when its EvidenceKind
# lists the field in `legacy_fields`, so the retired flag exists exactly where it existed.
RETIRED_FIELDS = {"Skills": "Tools"}


def retired_flag_action(retired: str, replacement: str):
    """An argparse Action class for a retired field flag: it refuses at parse time naming
    the replacement, through `parser.error` -- exit 2 and a usage line, never a traceback.
    An Action rather than a post-parse check, so the refusal names the flag the user
    actually typed even when some other argument is also wrong."""

    class _Retired(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            parser.error(f"{field_flag(retired)} was retired in 4.0 -- sluice no longer "
                         f"reads {retired}:; use {field_flag(replacement)} instead")
    return _Retired


def field_dest(field: str) -> str:
    """`Signal Value` -> `signal_value` -- the attribute argparse puts the flag's
    value under. Kept as its own function (rather than inlined at each call site)
    because it must produce the SAME string argparse derives internally from
    `field_flag`'s `--signal-value` (hyphens to underscores); one function used by
    both the parser-building loop's implicit dest and this module's own `getattr`
    calls is what keeps that agreement structural rather than coincidental.
    """
    return field.lower().replace(" ", "_")


def cmd_evidence_add(args, config) -> int:
    """Propose one entry (#164). Never citable on its own -- `verified` is not among
    the flags this command exposes (see `EvidenceKind.fields`' own docstring), so
    there is no way to shell in a verified entry; only `... verify` can promote one.
    """
    from sluice.core.app import Sluice

    spec = EVIDENCE_KINDS[args.kind]
    fields = {f: getattr(args, field_dest(f)) or "" for f in spec.fields}
    body = args.body or ""
    if args.body_file:
        # Its OWN try, separate from the store call's below: a missing/unreadable
        # file and a symlinked inbox are both OSError, but they need DIFFERENT
        # wording -- a shared except would misreport one as the other (#164 Task 7
        # review, IMPORTANT 2: this open() used to sit outside any try at all, so a
        # bad --body-file crashed with a raw traceback instead of a named exit 1).
        try:
            if args.body_file == "-":
                body = sys.stdin.read()
            else:
                with open(args.body_file, encoding="utf-8") as fh:
                    body = fh.read()
        except OSError as e:
            print(f"{args.kind} add: could not read --body-file {args.body_file!r}: {e}",
                  file=sys.stderr)
            return 1
    try:
        # A handle, not a path. `Store.propose_evidence` promises only a non-empty OPAQUE
        # handle a caller may show a user -- the vault's happens to be a filesystem path,
        # a SQL- or API-backed store's would not be -- so this is printed and nothing else
        # is done with it, exactly as `cmd_init` treats `write_document`'s return.
        handle = Sluice(config).add_evidence(kind=args.kind, name=args.name,
                                             fields=fields, body=body)
    except FileExistsError as e:
        # The store's OWN message, not a wording invented here: it distinguishes a name
        # already in the inbox from one already in the CITABLE set, and only the store
        # knows which (#164 review, H2b). Both messages name the reduced slug the entry
        # would actually be filed under, which is the identity that clashed.
        print(f"{args.kind} add: {e}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as e:
        print(f"{args.kind} add: {e}", file=sys.stderr)
        return 1
    print(f"proposed: {handle}")
    print(f"(unverified -- run `job-sluice {args.kind} verify` to {verify_outcome(spec)})")
    return 0


def cmd_evidence_list(args, config) -> int:
    """List one kind's entries. For a kind that declares them, every line shows the
    entry's `Company:` (or `(none)`), any `Tools:` it declares and its `Label:` (or
    `(none)`), so `core/doctor.py`'s eligibility, tools and skill-label rows -- "not on
    your CV", "no company", the unusable-`Tools:` row and "cv skills (no Label)", which
    report a COUNT and never the entry's own text, by this codebase's own "no doctor row
    carries user-authored text" rule -- have somewhere actionable to point a user at: this
    command is the resolving command (`job-sluice experience list`, `job-sluice skills
    list`) those rows name.

    Keyed on the kind's declared `fields` rather than a hardcoded `args.kind ==
    "experience"` check, so a future kind that grows either field gets this for free
    rather than needing a second hand-written branch here.
    """
    from sluice.core.app import Sluice

    try:
        entries = Sluice(config).list_evidence(kind=args.kind, pending=args.pending)
    except (ValueError, OSError) as e:
        # Same shape as `add`'s and `verify`'s -- a named exit 1, never a traceback.
        # The store reads a vault a HUMAN edits, so an unreadable or vanished entry
        # (a dangling symlink left by a sync client, a directory named `x.md`) is an
        # ordinary state of the world here, not an internal invariant failure.
        print(f"{args.kind} list: {e}", file=sys.stderr)
        return 1
    if not entries:
        print(f"no {'pending' if args.pending else 'verified'} {args.kind} entries")
        return 0
    fields = EVIDENCE_KINDS[args.kind].fields
    show_company, show_tools = "Company" in fields, "Tools" in fields
    show_label = "Label" in fields
    for e in entries:
        marker = "pending" if args.pending else e["verified"]
        line = f"{e['title']}  [{marker}]"
        declared = e.get("fields") or {}
        if show_company:
            # ALWAYS shown, blank included: "no company" is one of the doctor rows this
            # listing resolves, so the entry carrying none must be findable here.
            company = declared.get("Company", "")
            company = company.strip() if isinstance(company, str) else ""
            line += f"  Company: {company or '(none)'}"
        if show_tools:
            tools = declared.get("Tools", "")
            # Blank is absent (core/tokens.py::tool_items declares no item for it) --
            # omitted rather than printed as a bare "Tools: " on every entry that has not
            # annotated one, which would be noise on every line.
            #
            # WHITESPACE-ONLY is blank, and the check is on the stripped value for that
            # reason: `tool_items` splits on commas and drops every item that is empty
            # after stripping, so `Tools: "   "` contributes nothing THERE while a bare
            # truthiness test would print an empty `Tools:` suffix HERE -- one value
            # described two ways. Only the QUOTED spelling reaches this line as
            # whitespace (measured: `_parse_fm_spaced` hands back `''` for an unquoted
            # run of spaces), and a human editing their own vault can write it.
            #
            # `isinstance` rather than a bare `.strip()`: this is a display path,
            # `core/protocols.py`'s Store contract does not require the field to be a
            # `str`, and raising `AttributeError` out of `list` over one odd field is not
            # a trade this command should make. A non-str value abstains.
            if isinstance(tools, str) and tools.strip():
                line += f"  Tools: {tools.strip()}"
        if show_label:
            # ALWAYS shown, blank included, for the same reason as Company above: doctor's
            # "cv skills (no Label)" row counts the notes a CV lists under a slug title, and
            # names this command as where to find them (a slug title with `(none)` here).
            label = declared.get("Label", "")
            label = label.strip() if isinstance(label, str) else ""
            line += f"  Label: {label or '(none)'}"
        print(line)
    return 0


def cmd_evidence_verify(args, config) -> int:
    """Review and promote pending entries -- the one operation that grants citability
    to the CV fabrication gate. Constructs the asker from `sys.stdin.isatty()` because
    this IS the CLI boundary, the one place that call belongs; everything below it
    (the facade, the store) reads `asker.interactive` instead of asking again.
    """
    from sluice.core.app import Sluice
    from sluice.onboard.ask import NoInputAsker, TtyAsker

    asker = TtyAsker(stdin=sys.stdin, stdout=sys.stdout) if sys.stdin.isatty() \
        else NoInputAsker()
    try:
        report = Sluice(config).verify_evidence_interactive(
            kind=args.kind, asker=asker, only=args.id)
    except (ValueError, OSError) as e:
        # Mirrors `add`'s handler exactly, and for the same reason: this reads and
        # writes a vault a HUMAN edits, so an unknown kind, an unreadable inbox or a
        # symlinked evidence directory are ordinary states of the world -- each of
        # which reached the user as a raw traceback before this (#164 whole-branch
        # review, IMPORTANT 2). `main`'s own `except ValueError` catches only the
        # config-usage class and exits 2; these are command failures, so they are named
        # here and exit 1 like `add`'s.
        #
        # This arm catches only what fails for the WHOLE batch, before or around the
        # review loop. A single entry that cannot be read or promoted no longer reaches
        # here at all: `verify_evidence_interactive` isolates it into `report["failed"]`
        # so the rest of the queue is still offered (#164 review, H2).
        print(f"{args.kind} verify: {e}", file=sys.stderr)
        return 1
    if report["not_found"]:
        # Ruling R11: a non-matching --id must not read as "nothing is pending" --
        # that quiet-empty shape is indistinguishable from an empty inbox, which is
        # exactly the class of silent wrong-default this codebase refuses elsewhere
        # (empty-config-abstains, a retired config key raising by name). Same shape
        # as cmd_leads_dismiss's "no lead matching '<slug>'" refusal.
        print(f"{args.kind} verify: no pending entry matching '{args.id}'",
              file=sys.stderr)
        return 1
    if not report["interactive"]:
        for title in report["skipped"]:
            print(f"pending: {title}")
        print(f"{args.kind} verify: promotion needs an interactive terminal; "
              f"nothing was promoted", file=sys.stderr)
        return 0
    for title in report["promoted"]:
        print(f"verified: {title}")
    for title in report["unchanged"]:
        print(f"changed since you reviewed it, not promoted: {title}", file=sys.stderr)
    for title, reason in report["failed"]:
        # Printed AFTER the promotions, so what DID succeed is still reported when
        # something else in the same batch failed. Before per-item isolation existed
        # the exception unwound past the loop in `Sluice.verify_evidence_interactive`
        # and discarded `report` whole, so the promotions this run had already written
        # to disk were never mentioned at all (#164 review, H2).
        print(f"not promoted: {title} -- {reason}", file=sys.stderr)
    # A batch that promoted some entries and failed on others is still a failure: exit 1
    # so a scripted caller sees it, while stdout above still names everything promoted.
    return 1 if report["failed"] else 0
