"""`job-sluice mcp install`, end to end: check the extra and the launcher, detect, ask (or apply
the flags), read, write, read back, report. Every OS touchpoint arrives injected from
`cli.py::cmd_mcp_install`, so tests drive the whole command against fakes.

Non-interactive is `--yes`, a stdin that is not a terminal, or `--dry-run`; `--client` only
narrows the list. What is printed follows the spec's neutrality rule: the argv install
registers in full, an existing entry's argv redacted, pinned values, any other env key by NAME
only, never a client's output."""
import json
from dataclasses import dataclass, replace

from sluice import mcpextra
from sluice.mcpinstall import clients, routes, server
from sluice.mcpinstall.server import PINNED_ENV


@dataclass(frozen=True)
class Options:
    clients: tuple[str, ...]
    read_only: bool
    replace: bool
    yes: bool
    dry_run: bool


class _Ask:
    """Prompts on the terminal. An empty line or end of input takes the default, so a closed
    stdin never loops."""

    def __init__(self, stdin, out):
        self.stdin, self.out = stdin, out

    def _line(self) -> str:
        return (self.stdin.readline() or "").strip()

    def yes_no(self, prompt: str, default: bool) -> bool:
        print(f"{prompt} [{'Y/n' if default else 'y/N'}]", file=self.out)
        answer = self._line().lower()
        return default if not answer else answer in ("y", "yes")

    def pick(self, rows) -> set:
        names = [name for name, _, _ in rows]
        print("Register job-sluice in:", file=self.out)
        for name, label, ticked in rows:
            print(f"  [{'x' if ticked else ' '}] {name} ({label})", file=self.out)
        print("Press Enter for the ticked ones, or type names separated by commas:",
              file=self.out)
        while True:
            answer = self._line()
            if not answer:
                return {name for name, _, ticked in rows if ticked}
            picked = {p.strip() for p in answer.split(",") if p.strip()}
            unknown = sorted(picked - set(names))
            if not unknown:
                return picked
            print(f"  not in the list: {', '.join(unknown)}", file=self.out)


def _env_lines(env: dict) -> list:
    pinned = [f"{k}={v}" for k, v in sorted(env.items()) if k in PINNED_ENV]
    other = sorted(k for k in env if k not in PINNED_ENV)
    lines = []
    if pinned:
        lines.append("env: " + " ".join(pinned))
    if other:
        lines.append("other env keys: " + ", ".join(other))
    return lines


def _why_missing(detected) -> str:
    if isinstance(detected, clients.Unsupported):
        return detected.reason
    return "not found on this machine"


def run(opts: Options, *, host, deps, argv0, stdin, out, err, interactive, find_spec) -> int:
    if find_spec("mcp") is None:
        print(f"job-sluice: {mcpextra.NOT_INSTALLED}", file=err)
        return 2
    try:
        launcher = server.resolve_launcher(argv0, windows=host.platform == "win32")
    except server.LauncherError as exc:
        print(f"job-sluice: {exc}", file=err)
        return 2
    ask = _Ask(stdin, out) if interactive and not opts.yes and not opts.dry_run else None

    considered = [c for c in clients.ROSTER if not opts.clients or c.name in opts.clients]
    found, outcomes = [], []
    for c in considered:
        detected = clients.detect(c, host)
        if isinstance(detected, clients.Found):
            found.append(c)
        elif c.name in opts.clients:
            outcomes.append(routes.Outcome(c.name, "failed", _why_missing(detected)))
    if not found:
        _report(outcomes, {}, None, host, out)
        print("No MCP client was found on this machine. To register one by hand: "
              f"{clients.DOCS_URL}#install-in-your-client", file=out)
        return _exit(outcomes, opts)

    write = not opts.read_only
    if ask and not opts.read_only:
        write = ask.yes_no("Register it with the write tools (the coach's saves, evidence "
                           "proposals and review, leads, CVs)?", True)
    spec = server.build_spec(launcher, host.env, write)
    print("Registering: " + " ".join(spec.argv), file=out)
    for line in _env_lines(spec.env_dict):
        print("  " + line, file=out)

    rows = [(c, clients.config_path(c, host)) for c in found]
    states = {c.name: routes.read_state(c, path) for c, path in rows}
    def _writable(c):
        st = states[c.name]
        # Codex's existing entry is never edited (install only appends), so it is not offered.
        return (isinstance(st, routes.FileState) and not _same(st, spec)
                and not (c.route == "append" and isinstance(st.current, routes.Entry)))
    writable = [c for c, _ in rows if _writable(c)]
    selected = {c.name for c in writable}
    if ask and writable:
        # An entry already the same is listed unticked, so the list shows every client found;
        # picking it changes nothing (it reports `unchanged` below).
        selected = ask.pick([
            (c.name, "already registered", False) if _same(states[c.name], spec) else
            (c.name, "different entry" if isinstance(states[c.name].current, routes.Entry)
             else "not registered", True)
            for c, _ in rows if c in writable or _same(states[c.name], spec)])

    paths = dict((c.name, p) for c, p in rows)
    for c, path in rows:
        state = states[c.name]
        where = deps.display(path)
        if isinstance(state, routes.Unreadable):
            outcomes.append(routes.Outcome(c.name, "failed",
                                           f"{where} could not be read: {state.reason}"))
        elif _same(state, spec):
            outcomes.append(routes.Outcome(c.name, "unchanged"))
        elif c.route == "append" and isinstance(state.current, routes.Entry):
            outcomes.append(_guarded(c, routes.edit_by_hand, c, state, spec, deps))
        elif c.name in selected:
            outcomes.append(_guarded(c, _one, c, state, spec, opts, ask, deps, host, out))
        if isinstance(state, routes.FileState) and outcomes and outcomes[-1].client == c.name:
            shadows = clients.local_entries(c, state.doc)
            if shadows:
                last = outcomes.pop()
                outcomes.append(replace(last, details=last.details + (
                    f"{shadows} project(s) also have a local-scope job-sluice entry, which takes "
                    "precedence in that project: remove it there with `claude mcp remove "
                    "job-sluice -s local`",)))
    _report(outcomes, paths, spec, host, out)
    # A client the user left unticked gets no outcome (the six are the spec's whole vocabulary,
    # and declining is not one of them), but the report still names it, so every client found
    # is accounted for.
    left_out = [c.name for c in writable if c.name not in selected]
    if left_out:
        print("not selected: " + ", ".join(left_out), file=out)
    return _exit(outcomes, opts)


def _guarded(c, fn, *args) -> routes.Outcome:
    """One client's write, isolated: an error nothing anticipated ends THIS client `failed` and the
    run goes on, so a write an earlier client already made still reaches the report. A copy taken
    before the error is named by routes.py's own boundary (`routes._unexpected`); only the type is
    printed, since a message can quote a config file. Ctrl-C (not an `Exception`) still stops."""
    try:
        return fn(*args)
    except Exception as exc:      # noqa: BLE001
        return routes.Outcome(c.name, "failed", f"unexpected error: {type(exc).__name__}")


def _same(state, spec) -> bool:
    return isinstance(state, routes.FileState) and isinstance(
        state.current, routes.Entry) and routes.matches(state.current, spec)


def _one(c, state, spec, opts, ask, deps, host, out) -> routes.Outcome:
    if isinstance(state.current, routes.Entry):
        print(f"{c.name} already has a job-sluice entry with different settings:", file=out)
        print("  now: " + " ".join(clients.redact_argv(state.current.argv, deps.display)),
              file=out)
        for line in _env_lines(dict(state.current.env)):
            print("       " + line, file=out)
        print("  new: " + " ".join(spec.argv), file=out)
        for line in _env_lines(spec.env_dict):
            print("       " + line, file=out)
        if not (opts.replace or (ask and ask.yes_no(f"Replace {c.name}'s entry?", False))):
            return routes.Outcome(c.name, "refused",
                                  "an entry with different settings exists; run with --replace "
                                  "to replace it")
    if c.route == "command":
        refused = routes.refusal(c, state)
        if refused:
            return refused
    # Keyed by route, never an if/else chain ending in a default: a route with no writer is a
    # KeyError here (and `clients.Client` refuses an unknown one at import), not some other
    # route's writer run on this client's file.
    if opts.dry_run:
        return _PREVIEW[c.route](c, state, spec, deps, out)
    return _APPLY[c.route](c, state, spec, deps, host)


def json_one_line(value) -> str:
    # Printed, so ASCII-escaped like clients.snippet's JSON (see the note there).
    return json.dumps(value)


def _preview_command(c, state, spec, deps, out) -> routes.Outcome:
    if isinstance(state.current, routes.Entry) and c.remove_before_add:
        # The run removes the old entry first (`routes.apply_command`); a preview hiding that
        # step would not show the one that cannot be undone if the add then fails.
        print(f"{c.name}: would run: " + " ".join(clients.remove_argv(c)), file=out)
    print(f"{c.name}: would run: " + " ".join(clients.add_argv(c, spec)), file=out)
    return routes.Outcome(c.name, "dry-run")


def _preview_json(c, state, spec, deps, out) -> routes.Outcome:
    print(f"{c.name}: would write {deps.display(state.path)}: "
          + json_one_line(clients.entry_value(c, spec)), file=out)
    return routes.Outcome(c.name, "dry-run")


def _preview_append(c, state, spec, deps, out) -> routes.Outcome:
    # The same pre-parse the write makes, so the preview never promises an append the run
    # would refuse.
    if routes.appended(c, state, spec) is None:
        return routes.cannot_append(c, state, spec, deps)
    print(f"{c.name}: would append to {deps.display(state.path)}:", file=out)
    for line in clients.snippet(c, spec).splitlines():
        print("    " + line, file=out)
    return routes.Outcome(c.name, "dry-run")


_PREVIEW = {"command": _preview_command, "json": _preview_json, "append": _preview_append}
_APPLY = {
    "command": lambda c, state, spec, deps, host: routes.apply_command(c, state, spec, deps,
                                                                       host.which),
    "json": lambda c, state, spec, deps, host: routes.apply_json(c, state, spec, deps),
    "append": lambda c, state, spec, deps, host: routes.apply_append(c, state, spec, deps),
}


def _report(outcomes, paths, spec, host, out) -> None:
    for o in outcomes:
        if o.kind == "dry-run":
            continue
        c = clients.by_name(o.client)
        note = clients.measured_note(c, host)
        head = f"{o.client}: {o.kind}" + (f" - {o.reason}" if o.reason else "")
        print(head + (f" [{note}]" if note else ""), file=out)
        for line in o.details:
            print("  " + line, file=out)
        if o.kind in ("registered", "replaced", "unchanged"):
            print("  next: " + c.next_step, file=out)
        elif o.kind in ("failed", "manual") and spec is not None and o.client in paths:
            if o.paste:
                instruction, text = o.paste
            else:
                where = clients.display_path(paths[o.client], host)
                instruction = f"paste this into {where} instead (see {clients.DOCS_URL}#{c.anchor})"
                text = clients.snippet(c, spec)
            print(f"  {instruction}:", file=out)
            for line in text.splitlines():
                print("    " + line, file=out)


def _exit(outcomes, opts) -> int:
    bad = any(o.kind in ("failed", "refused")
              or (o.kind == "manual" and o.client in opts.clients) for o in outcomes)
    return 1 if bad else 0
