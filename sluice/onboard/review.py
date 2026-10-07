"""In-session setup, pure: proposed changes in, checkbox units and finished artefact texts
out. No I/O -- `Sluice.setup_snapshot` reads, `Sluice.apply_setup` checks and writes.

A UNIT is what one checkbox approves: one Judging Profile heading, one Candidate Profile
field, one config key, one search, one Role Brief section. Every change that cannot be shown
in full or applied safely is SET ASIDE here, with a reason naming a remedy that exists for
that unit, before any form is built.
"""
import dataclasses
import os
import re
from dataclasses import dataclass
from typing import TypedDict

from sluice.core import formfit
from sluice.core.protocols import ArtefactWrite
from sluice.core.vault import parse_frontmatter, set_frontmatter_line
from sluice.onboard import edit as _edit
from sluice.onboard import plan as _plan
from sluice.onboard import questions as _questions
from sluice.onboard.emit import flow_list, scalar

UNIT_KINDS = ("profile", "candidate", "config", "brief", "search")
ROLE_BRIEF_SECTIONS = ("The role, as researched", "Title variants seen on boards",
                       "Pay structure", "Signals of a good posting and a poor one",
                       "Sources consulted")
NOTE_NAMES = {"profile": "the Judging Profile note", "candidate": "the Candidate Profile note",
              "brief": "the Role Brief note", "config": "your sluice config file"}
REMEDY = {"profile": "edit it in the Judging Profile note in Obsidian",
          "candidate": "edit it in the Candidate Profile note in Obsidian",
          "brief": "edit it in the Role Brief note in Obsidian",
          "config": "edit it in your sluice config file",
          "search": "edit `sources:` in your sluice config file"}
NO_VAULT_YET = ("choose where your notes live first: propose a `vault_dir` config change, "
                "or set $VAULT_DIR where the sluice MCP server runs")
DEFAULT_VAULT = ("sluice is using a vault in whatever folder the MCP server was started from, "
                 "so notes written now would land where nothing else reads them; set `vault_dir` "
                 "in your sluice config file by hand, then restart the server")
CLEARED = "(unset: back to the shipped default)"


class ChangeIn(TypedDict, total=False):
    """One proposed change, as the setup_review tool receives it."""
    kind: str
    target: str
    value: str
    clear: bool
    label: str
    url: str
    remove: bool


@dataclass(frozen=True)
class Change:
    kind: str
    target: str
    value: str | None = None
    clear: bool = False
    label: str | None = None
    url: str | None = None
    remove: bool = False


@dataclass(frozen=True)
class SetAside:
    label: str
    reason: str
    key: str | None = None   # the unit key it would have had, so a reason maps to ITS box


@dataclass(frozen=True)
class Unit:
    key: str          # stable id, e.g. "config:lead_ttl_days"
    kind: str
    artefact: str     # "config", "profile", "candidate", "brief"
    title: str        # the checkbox label
    before: str | None
    after: str
    change: Change


def _label(c):
    return f"{c.kind}: {c.target}"


def _search_label(c):
    return (c.label or "").strip()


def _search_url(c):
    """The url as the unit will write it. Falls back to the stripped raw string when parse_url
    refuses, so a bad url still gets the stable key its set-aside needs to match its box."""
    raw = c.url or ""
    try:
        return _questions.parse_url(raw)
    except ValueError:
        return raw.strip()


def unit_key(c) -> str:
    """The stable id of the unit a change targets: two changes with one key would be two boxes
    writing one thing, so propose keeps the first and sets the rest aside."""
    if c.kind == "search":
        # No verb in the key: an add and a remove of one search are contradictory writes to one
        # thing, so the second must be set aside as a duplicate (the verb stays in the title).
        return f"search:{c.target}:{_search_label(c)}:{_search_url(c)}"
    if c.kind == "profile":
        return f"profile:{_heading(c.target) or c.target}"
    if c.kind == "candidate":
        return f"candidate:{_candidate_field(c.target) or c.target}"
    return f"{c.kind}:{c.target}"


def parse_changes(raw) -> tuple:
    out, bad = [], []
    for item in raw or []:
        if not isinstance(item, dict):
            bad.append(SetAside(repr(item)[:60], "a change must be an object"))
            continue
        fields = {f.name for f in dataclasses.fields(Change)}
        try:
            c = Change(**{k: v for k, v in item.items() if k in fields})
        except TypeError:
            bad.append(SetAside(str(item.get("kind")), "a change needs `kind` and `target`"))
            continue
        out.append(c)
    return out, bad


def _questions_by_key():
    return {q.key: q for q in _questions.catalogue()}


def _heading(target):
    want = (target or "").lstrip("#").strip()
    return next((h for h in _plan.PROFILE_HEADINGS if h.lstrip("#").strip() == want), None)


def _candidate_field(target):
    return _plan._CANDIDATE_KEY_BY_ANSWER.get(target)


def _render(value):
    return flow_list(value) if isinstance(value, list) else scalar(value)


def _display(value):
    return CLEARED if value in (None, [], "") else _render(value)


def prose_problem(text) -> str | None:
    if not text or not text.strip():
        return "it is empty"
    if formfit.hides_text(text):
        return "it contains a control character that could change what the form displays"
    for line in text.splitlines():
        s = line.lstrip(" ")
        if s.startswith("#"):
            return "a line starts with `#`, which would add a heading"
        if s.strip() == "---":
            return "a line is `---`, which would break the note"
        if "<!--" in s or "-->" in s:
            return "it contains a comment marker that could hide the text after it"
    return None


def _fits(title, body):
    return (len(formfit.describe(title, body)) <= formfit.DESC_MAX_CHARS
            and formfit.entry_lines(title, body) <= formfit.FORM_LINES
            and not formfit.hides_text(formfit.describe(title, body)))


def _replaces(unit) -> bool:
    return unit.before is not None and unit.before != unit.after


def unit_body(unit) -> str:
    """What one box shows: the new text, and in full the text it replaces. There is no
    shortened form -- a tick approves deleting what "Replaces:" shows, so a box that could not
    show it would approve deleting text the user never saw; `propose` sets such a unit aside."""
    if _replaces(unit):
        return f"New:\n{unit.after}\n\nReplaces:\n{unit.before}"
    return f"New:\n{unit.after}"


def set_aside_reason(unit) -> str:
    if _replaces(unit) and _fits(unit.title, f"New:\n{unit.after}"):
        return (f"the text it would replace cannot be shown in full beside it in the review "
                f"form -- {REMEDY[unit.kind]}")
    return f"it does not fit the review form in full -- {REMEDY[unit.kind]}"


def vault_path_problem(raw) -> str | None:
    """Why a `vault_dir` answer cannot be taken in the setup path, or None. `parse_path` makes
    any answer absolute, and a relative one would resolve against the folder the MCP server was
    started from: in `init` that is the user's own terminal, but here it is wherever the client
    launched the server, which the user never chose and cannot see. So only an answer that
    already says where it is -- absolute, or anchored at `~` -- is taken. The test runs on the
    EXPANDED text, as `core/paths.py` expands at ingress: `expanduser` leaves a `~user` it cannot
    resolve unchanged, and `parse_path` would then anchor `~nosuchuser/notes` at that same
    folder, so a prefix check on the raw text would wave it through."""
    text = os.path.expanduser((raw or "").strip())
    if os.path.isabs(text):
        return None
    return ("a relative path would be resolved against the folder the sluice MCP server was "
            "started from, which is not one you chose; give the full path, or one under your "
            "home folder starting with `~/`")


def _vault_problem(c, snap, batch):
    """A reason no vault unit (or, on a first run, no unit at all) can be written now. Only a
    `vault_dir` change that will itself be accepted counts as naming the vault: counting a
    refused one would show the other boxes, only for every tick to be set aside on retry. A
    refused `vault_dir` change itself falls through, so `_config_unit` names its own reason."""
    names_vault = c.kind == "config" and c.target == "vault_dir" and not c.clear
    if not names_vault and not snap.config_exists and not snap.vault_from_env and not any(
            b.kind == "config" and b.target == "vault_dir" and not b.clear
            and vault_path_problem(b.value) is None for b in batch):
        return NO_VAULT_YET
    if (c.kind in ("profile", "candidate", "brief") and snap.config_exists
            and snap.vault_is_default and not snap.vault_from_env):
        return DEFAULT_VAULT
    return None


def propose(changes, snap) -> tuple:
    units, aside, seen_keys, seen_titles = [], [], set(), set()
    qs = _questions_by_key()
    for c in changes:
        key = unit_key(c)

        def skip(reason):
            aside.append(SetAside(_label(c), reason, key))

        if c.kind not in UNIT_KINDS:
            skip(f"`{c.kind}` is not a kind of setup change")
            continue
        if (c.target or "").strip().lower() == "verified":
            skip("no setup change can mark evidence verified")
            continue
        if key in seen_keys:
            skip("this batch already proposes a change to the same thing; propose one value")
            continue
        problem = _vault_problem(c, snap, changes)
        if problem:
            skip(problem)
            continue
        artefact = "config" if c.kind in ("config", "search") else c.kind
        if artefact in snap.unreadable:
            skip(f"{NOTE_NAMES[artefact]} could not be read ({snap.unreadable[artefact]})")
            continue
        try:
            unit = _unit(c, snap, qs)
        except ValueError as exc:      # BadAnswer, EditRefused, FrontmatterEditRefused
            skip(f"{exc} -- {REMEDY[c.kind]}")
            continue
        if not _fits(unit.title, unit_body(unit)):
            skip(set_aside_reason(unit))
            continue
        if unit.title in seen_titles:      # titles key the form; never two boxes, one title
            skip("this batch already proposes a change with the same title")
            continue
        seen_keys.add(key)
        seen_titles.add(unit.title)
        units.append(unit)
    return units, aside


def _unit(c, snap, qs):
    if c.kind == "config":
        return _config_unit(c, snap, qs)
    if c.kind == "search":
        return _search_unit(c, snap)
    if c.kind in ("profile", "brief"):
        return _prose_unit(c, snap)
    return _candidate_unit(c, snap)


def _config_unit(c, snap, qs):
    q = qs.get(c.target)
    if q is None:
        raise ValueError(f"`{c.target}` is not a setting the interview can change")
    if q.key == "vault_dir" and snap.config_exists:
        raise ValueError("moving the vault would leave sluice's record of leads it has already "
                         "seen behind, so none of them would ever be created in the new vault")
    if q.key == "vault_dir" and snap.vault_from_env:
        raise ValueError("$VAULT_DIR decides the vault where the server runs, so this setting "
                         "would change nothing")
    if q.key == "vault_dir" and not c.clear and vault_path_problem(c.value):
        raise ValueError(vault_path_problem(c.value))
    if q.key == "backend":
        stages = [snap.settings.get(d) for d in q.writes_to]
        if len(set(stages)) > 1:
            raise ValueError("the stages use different backends today, so one value would "
                             "overwrite choices that disagree")
        if snap.config_text and any(
                _edit.is_active(snap.config_text, f"{d.split('.')[0]}.model")
                for d in q.writes_to):
            raise ValueError("a stage names its own model, which would no longer match a new "
                             "backend")
    value = None if c.clear else q.parse(c.value or "")
    if snap.config_text:
        # Rehearse the edit NOW so a key this editor cannot place (a value spread over several
        # lines, a duplicate) is set aside before the form; found only at write time it would
        # be reported `failed` after the user had ticked the box.
        for d in q.writes_to:
            if c.clear:
                _edit.clear_key(snap.config_text, d)
            else:
                _edit.set_key(snap.config_text, d, _render(value))
    before = snap.settings.get(q.writes_to[0])
    if q.key == "vault_dir":
        # The RESOLVED path, in full: the user must see every value before it is written, and
        # this one decides where every note goes. Showing it is no disclosure. The "no
        # absolute path in a response" rule covers paths the SERVER discovers (the config's
        # location, an existing vault -- which setup_status still masks as set/unset), not the
        # user's own typed answer echoed back to them; the form's state carries it anyway.
        # A leading `~` is shown expanded, so the home folder the server resolved it to is
        # the one part of the box the user did not type -- and the part they most need to check.
        before, after = None, _display(value)
    else:
        before, after = _display(before), _display(value)
    return Unit(f"config:{q.key}", "config", "config", f"Config: {q.key}", before, after, c)


def _search_unit(c, snap):
    if c.target not in snap.source_ids:
        raise ValueError(f"`{c.target}` is not a registered source")
    label = _search_label(c)
    if not label:
        raise ValueError("a search needs a label")
    if c.clear:
        # `clear` means nothing for a search; read as an add it would write the opposite of
        # what was asked, so it is refused by name rather than ignored.
        raise ValueError("`clear` does not apply to a search; use `remove: true` to remove one")
    url = _questions.parse_url(c.url or "")
    verb = "remove" if c.remove else "add"
    c = dataclasses.replace(c, label=label, url=url)
    # Checked against what is configured NOW, so the user is never shown a box that cannot be
    # written (build_writes' editor would refuse the same three cases after the tick).
    current = [list(e) for e in snap.searches.get(c.target, [])]
    if not c.remove and [label, url] in current:
        raise ValueError("that search is already configured")
    if c.remove and [label, url] not in current:
        raise ValueError("that search is not configured")
    if c.remove and len(current) == 1:
        raise ValueError(f"it is the last search for {c.target}, and an empty list makes the "
                         f"source run its built-in example search; run `job-sluice ingest "
                         f"disable {c.target}` to stop it")
    return Unit(unit_key(c), "search", "config",
                f"Search on {c.target}: {verb} {label} ({url})", None,
                f"[{label}, {url}]", c)


def _snapshot_notes_are_the_target(snap) -> bool:
    """False on a first run with no $VAULT_DIR: the snapshot then read the cwd-relative default
    vault, not the one being chosen, so its notes say nothing about what a write will replace.
    A unit shows no "Replaces:" then -- as `_note_writes(existing=False)` creates rather than
    edits -- and a note that does exist in the chosen vault abstains in the store."""
    return snap.config_exists or snap.vault_from_env


def _prose_unit(c, snap):
    if c.kind == "profile":
        heading = _heading(c.target)
        if heading is None:
            raise ValueError(f"`{c.target}` is not a Judging Profile heading")
        title = f"Judging Profile: {heading.lstrip('#').strip()}"
        default = _plan.default_sections()[heading]
    else:
        if c.target not in ROLE_BRIEF_SECTIONS:
            raise ValueError(f"`{c.target}` is not a Role Brief section")
        heading, title, default = f"## {c.target}", f"Role Brief: {c.target}", BRIEF_PLACEHOLDER
    if not c.clear:
        problem = prose_problem(c.value)
        if problem:
            raise ValueError(problem)
    text = snap.notes.get(c.kind) if _snapshot_notes_are_the_target(snap) else None
    before = section_text(text, heading) if text is not None else None
    after = default if c.clear else c.value.strip()
    return Unit(f"{c.kind}:{heading}", c.kind, c.kind, title, before, after, c)


def _candidate_unit(c, snap):
    field = _candidate_field(c.target)
    if field is None:
        raise ValueError(f"`{c.target}` is not a Candidate Profile field the interview sets")
    value = "" if c.clear else (c.value or "").strip()
    if formfit.hides_text(value):
        raise ValueError("it contains a control character that could change what the form "
                         "displays")
    literal = scalar(value)
    if parse_frontmatter(f"---\n{field}: {literal}\n---\n").get(field, "") != value:
        raise ValueError("that value does not survive sluice's frontmatter reader unchanged "
                         "(a leading or trailing quote, or an escaped character)")
    text = snap.notes.get("candidate") if _snapshot_notes_are_the_target(snap) else None
    before = parse_frontmatter(text).get(field) if text is not None else None
    if text is not None:
        set_frontmatter_line(text, field, literal)     # refuses an unsafe note shape now
    return Unit(f"candidate:{field}", "candidate", "candidate",
                f"Candidate Profile: {field}", before, value or CLEARED, c)


BRIEF_PLACEHOLDER = "Not researched yet."

# A CommonMark ATX heading: up to three spaces, one to six `#`, then a space, a tab or the end of
# the line. Nothing else ends a section -- an Obsidian tag line (`#remote`) or `#hashtag` prose is
# BODY, so the form's "Replaces:" shows it and the write removes it with the rest. Cutting at any
# leading `#` made the preview stop at a tag while the old text below it survived the write.
_ATX_HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")


def is_heading(line) -> bool:
    """The one section boundary `section_text`, `replace_section` and `headings` share, so the
    text the form shows, the text the write replaces and the headings check cannot disagree."""
    return _ATX_HEADING.match(line.rstrip("\r\n")) is not None


def section_text(text, heading):
    """EVERYTHING a section edit would replace -- every line after the heading up to the next
    heading, blank edges trimmed, `init`'s prompt comment included -- or None when the heading
    is absent. Never cut at a comment: the "Replaces:" preview must show all the text the write
    deletes, including any the user typed below `init`'s prompt."""
    lines = text.splitlines()
    try:
        i = [ln.rstrip("\r") for ln in lines].index(heading)
    except ValueError:
        return None
    body = []
    for ln in lines[i + 1:]:
        if is_heading(ln):
            break
        body.append(ln.rstrip("\r"))
    return "\n".join(body).strip() or None


ROLE_BRIEF_INTRO = ("What the career coach found when it researched the role you chose, and "
                    "where it looked. Nothing in sluice reads this note to judge a lead or write "
                    "a CV: it records what the coach's questions were based on. Edit it freely.")


class SectionRefused(ValueError):
    """A section edit this note's shape cannot take safely; the message says why."""


def headings(text) -> list:
    return [ln.rstrip("\r") for ln in text.splitlines() if is_heading(ln)]


def brief_section_lines(section, text) -> list:
    return ["", (text.strip() if text else BRIEF_PLACEHOLDER), ""]


def render_role_brief(sections) -> str:
    lines = ["# Role Brief", "", ROLE_BRIEF_INTRO, ""]
    for s in ROLE_BRIEF_SECTIONS:
        lines += [f"## {s}"] + brief_section_lines(s, sections.get(s))
    return "\n".join(lines).rstrip() + "\n"


def replace_section(text, heading, body_lines) -> str:
    lines = text.splitlines(keepends=True)
    nl = "\r\n" if any(ln.endswith("\r\n") for ln in lines) else "\n"
    at = [i for i, ln in enumerate(lines) if ln.rstrip("\r\n") == heading]
    if len(at) > 1:
        raise SectionRefused(f"the heading `{heading}` appears more than once, so an edit "
                             f"would land in one copy while the other is still read")
    new = [f"{b}{nl}" for b in body_lines]
    if not at:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += nl
        sep = [nl] if lines and lines[-1].strip() else []
        return "".join(lines + sep + [heading + nl] + new)
    i = at[0]
    j = next((k for k in range(i + 1, len(lines)) if is_heading(lines[k])), len(lines))
    head = lines[i] if lines[i].endswith(("\n", "\r")) else lines[i] + nl
    return "".join(lines[:i] + [head] + new + lines[j:])


def _section_lines(unit):
    c = unit.change
    if unit.kind == "profile":
        heading = _heading(c.target)
        return heading, _plan.profile_section_lines(heading, None if c.clear else c.value)
    return f"## {c.target}", brief_section_lines(c.target, None if c.clear else c.value)


def _edit_note(text, units):
    before = headings(text)
    added = []
    for u in units:
        heading, body = _section_lines(u)
        if heading not in before:
            added.append(heading)
        text = replace_section(text, heading, body)
    if headings(text) != before + added:
        raise SectionRefused("the edit would change the note's headings")
    return text


def _aside_for(unit, reason):
    return SetAside(_label(unit.change), reason, unit.key)


def _first_run_answers(units):
    answers, sources = {}, {}
    for u in units:
        if u.kind == "config" and not u.change.clear:
            answers[u.change.target] = _questions_by_key()[u.change.target].parse(
                u.change.value or "")
        elif u.kind == "search" and not u.change.remove:
            src = sources.setdefault(u.change.target, {"enabled": True, "searches": []})
            src["searches"].append([u.change.label, u.change.url])
    return answers, sources


def build_writes(units, snap, *, env_vault=None) -> tuple:
    """Finished artefact texts for the ticked units. `env_vault` is $VAULT_DIR as the caller
    read it: on a first run it IS the vault answer, as cmd_init uses it (review.py reads no
    environment itself). Every SetAside carries its unit's key."""
    if not units:    # nothing ticked means nothing written, even on a first run
        return [], []
    by_art = {}
    for u in units:
        by_art.setdefault(u.artefact, []).append(u)
    if not snap.config_exists:
        return _first_run_writes(units, by_art, snap, env_vault)
    writes, aside = [], []
    if "config" in by_art:
        w, a = _config_update(by_art["config"], snap)
        writes += w
        aside += a
    w, a = _note_writes(by_art, snap, existing=True)
    return writes + w, aside + a


def _note_writes(by_art, snap, *, existing):
    """Profile, Role Brief and Candidate Profile writes. `existing=False` (a first run with no
    $VAULT_DIR) treats every note as absent: the snapshot read the cwd-relative default vault,
    not the one being created, so its notes say nothing about the target. A create that finds a
    note there anyway abstains in the store and is reported as a conflict."""
    writes, aside = [], []
    for art in ("profile", "brief"):
        if art not in by_art:
            continue
        text = snap.notes.get(art) if existing else None
        try:
            if text is None:
                writes.append(ArtefactWrite(art, _create_note(art, by_art[art]), None))
            else:
                writes.append(ArtefactWrite(art, _edit_note(text, by_art[art]),
                                            snap.sha_for(art)))
        except ValueError as exc:
            aside += [_aside_for(u, f"{exc} -- {REMEDY[art]}") for u in by_art[art]]
    if "candidate" in by_art:
        w, a = _candidate_write(by_art["candidate"],
                                snap.notes.get("candidate") if existing else None,
                                snap.sha_for("candidate") if existing else None)
        writes += w
        aside += a
    return writes, aside


def _create_note(art, units):
    if art == "profile":
        answers = {_plan._PROFILE_PROMPTS[_heading(u.change.target)][0]: u.change.value
                   for u in units if not u.change.clear}
        return _plan.build_plan({}, profile_answers=answers).profile_text
    return render_role_brief({u.change.target: u.change.value for u in units
                              if not u.change.clear})


def _declares_anything(candidate_text):
    """cmd_init's create gate, read off the RENDERED note as cmd_init reads it
    (has_any_declared over the parsed note), never off the answers."""
    return any(v for v in parse_frontmatter(candidate_text).values())


def _candidate_write(units, text, sha):
    if text is None:
        answers = {u.change.target: (u.change.value or "").strip() for u in units
                   if not u.change.clear}
        try:
            new = _plan.build_plan({}, candidate_answers=answers).candidate_text
        except ValueError as exc:            # FrontmatterRoundTripError
            return [], [_aside_for(u, str(exc)) for u in units]
        if not _declares_anything(new):
            return [], []
        return [ArtefactWrite("candidate", new, None)], []
    try:
        new = text
        for u in units:
            value = "" if u.change.clear else (u.change.value or "").strip()
            new = set_frontmatter_line(new, _candidate_field(u.change.target), scalar(value))
        changed = {_candidate_field(u.change.target) for u in units}
        before, after = parse_frontmatter(text), parse_frontmatter(new)
        if any(after.get(k) != v for k, v in before.items() if k not in changed):
            raise ValueError("another field would read differently after the edit")
    except ValueError as exc:
        return [], [_aside_for(u, f"{exc} -- {REMEDY['candidate']}") for u in units]
    return [ArtefactWrite("candidate", new, sha)], []


def _expected(q, change, snap):
    if change.clear:
        return [(d, snap.defaults.get(d)) for d in q.writes_to]
    value = q.parse(change.value or "")
    return [(d, value) for d in q.writes_to]


def _search_setting(source_id):
    return f"sources.{source_id}.searches"


def _created_source_settings(source_id, snap):
    """The (setting, value) pairs a search ADDS beside its own list when it creates the source's
    block. A source absent from the config reads as nothing at all, and once the block exists the
    loader fills its other settings in -- so a search creating it changes those too, and the
    config check would set the search aside as touching what it was not approved to touch.
    Declared at the DEFAULTS the snapshot loaded (`source_defaults`), so the check stays exact: a
    block created with any other `enabled` or `tuning` is still refused. A source the snapshot
    already lists in `searches` has a block, and a search leaves its other settings alone."""
    if source_id in snap.searches:
        return []
    return [(f"sources.{source_id}.{k}", v) for k, v in snap.source_defaults.items()]


def _config_update(units, snap):
    """Each unit is applied to the text only when ALL of its edits succeed (a fan-out key
    either reaches every block or none). Searches are allowed to change only their own
    source's searches, and must read the exact resulting list (spec: the config check)."""
    text, settings, expect, aside, applied = snap.config_text, [], [], [], False
    searches = {sid: [list(e) for e in v] for sid, v in snap.searches.items()}
    qs = _questions_by_key()
    for u in units:
        c = u.change
        try:
            if u.kind == "search":
                fn = _edit.remove_search if c.remove else _edit.add_search
                candidate = fn(text, c.target, c.label, c.url)
                cur = searches.setdefault(c.target, [])
                if c.remove:
                    cur.remove([c.label, c.url])
                else:
                    cur.append([c.label, c.url])
                settings.append(_search_setting(c.target))
                if not c.remove:
                    created = _created_source_settings(c.target, snap)
                    settings += [k for k, _ in created]
                    expect += created
            else:
                q = qs[c.target]
                candidate = text
                for d in q.writes_to:
                    candidate = (_edit.clear_key(candidate, d) if c.clear
                                 else _edit.set_key(candidate, d,
                                                    _render(q.parse(c.value or ""))))
                settings += list(q.writes_to)
                expect += _expected(q, c, snap)
            text, applied = candidate, True
        except ValueError as exc:
            aside.append(_aside_for(u, f"{exc} -- {REMEDY[u.kind]}"))
    if not applied:
        return [], aside
    expect += [(_search_setting(sid), lst) for sid, lst in searches.items()
               if _search_setting(sid) in settings]
    return [ArtefactWrite("config", text, snap.sha_for("config"),
                          tuple(dict.fromkeys(settings)), tuple(expect))], aside


def _first_run_writes(units, by_art, snap, env_vault):
    answers, sources = _first_run_answers(units)
    if snap.vault_from_env and env_vault:
        answers["vault_dir"] = _questions.parse_path(env_vault)
    if not answers.get("vault_dir"):
        return [], [_aside_for(u, NO_VAULT_YET) for u in units]
    qs = _questions_by_key()
    settings, expect = ["vault_dir"], [("vault_dir", answers["vault_dir"])]
    for u in by_art.get("config", []):
        if u.kind == "config":
            q = qs[u.change.target]
            settings += list(q.writes_to)
            expect += _expected(q, u.change, snap)
        else:
            settings.append(_search_setting(u.change.target))
            created = _created_source_settings(u.change.target, snap)
            settings += [k for k, _ in created]
            expect += created
    expect += [(_search_setting(sid), spec["searches"]) for sid, spec in sources.items()]
    p = _plan.build_plan(answers, sources=sources)
    writes = [ArtefactWrite("config", p.config_text, None, tuple(dict.fromkeys(settings)),
                            tuple(expect))]
    existing = bool(snap.vault_from_env)
    # init writes the Judging Profile and the Leads view on every first run; so does setup.
    profile_units = by_art.get("profile", [])
    if not profile_units and (not existing or snap.notes.get("profile") is None):
        writes.append(ArtefactWrite("profile", p.profile_text, None))
    if not existing or snap.notes.get("view") is None:
        writes.append(ArtefactWrite("view", p.view_text, None))
    w, a = _note_writes(by_art, snap, existing=existing)
    return writes + w, a


def status_view(snap) -> dict:
    qs = _questions.catalogue()
    config = {}
    for q in qs:
        if q.key == "vault_dir":
            config[q.key] = "set" if (snap.vault_from_env or snap.settings.get("vault_dir")) \
                else "unset"
        else:
            v = snap.settings.get(q.writes_to[0])
            config[q.key] = None if v in (None, [], "") else v
    def present(art):
        if art in snap.unreadable:
            return "unreadable"
        text = snap.config_text if art == "config" else snap.notes.get(art)
        return "absent" if text is None else "present"
    profile = snap.notes.get("profile")
    brief = snap.notes.get("brief")
    candidate = snap.notes.get("candidate")
    return {
        "config_exists": snap.config_exists,
        "vault": {"decided_by_env": snap.vault_from_env,
                  "is_default": snap.vault_is_default and not snap.vault_from_env},
        "artefacts": {a: present(a) for a in ("config", "profile", "candidate", "brief")},
        "unreadable": dict(snap.unreadable),
        "config": config,
        "searches": {sid: list(v) for sid, v in snap.searches.items() if v},
        "profile": {h: (section_text(profile, h) if profile else None)
                    for h in _plan.PROFILE_HEADINGS},
        "candidate": {k: (parse_frontmatter(candidate).get(f) if candidate else None)
                      for k, f in _plan._CANDIDATE_KEY_BY_ANSWER.items()},
        "brief": {s: (section_text(brief, f"## {s}") if brief else None)
                  for s in ROLE_BRIEF_SECTIONS},
        "kinds": {"config": [q.key for q in qs], "profile": list(_plan.PROFILE_HEADINGS),
                  "candidate": list(_plan._CANDIDATE_KEY_BY_ANSWER),
                  "brief": list(ROLE_BRIEF_SECTIONS), "search": list(snap.source_ids)},
    }
