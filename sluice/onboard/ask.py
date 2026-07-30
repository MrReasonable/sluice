"""The impure half: terminal prompting, `$EDITOR`, and the non-TTY refusal.

Everything that touches a terminal, a subprocess or a temp file lives here, so `questions` and
`plan` stay pure and the property that matters -- a run that answers nothing produces a config that
expresses nothing -- is a table test over a dict rather than a wizard transcript.

Two askers satisfy one two-method interface (`ask(question)` and `ask_prose(prompt)`, each returning
None for "no answer"), and `collect`/`collect_profile` are written against that interface alone. The
point is CONVERGENCE: `--no-input` is the same wizard with the prompting removed, never a second
code path with its own idea of what a blank means.
"""
import os
import shlex
import subprocess
import tempfile

from sluice.onboard.questions import BadAnswer, parse_url


class MissingAnswer(RuntimeError):
    """A required answer was not supplied and cannot be asked for.

    Raised rather than read from stdin: a wizard blocking on a pipe is a hung CI job with no
    diagnosis, and the fix a user needs is the flag name, so the message carries it.
    """


# Keyed to `plan._PROFILE_PROMPTS`, in ask order. Shipped prose: swept by
# tests/onboard_prose.py, so no exemplar, no proposed taxonomy.
_PROFILE_QUESTIONS = (
    ("who", "In a sentence or two: your background, and what you do?"),
    ("target_shape", "The shape of role you want, and the shape that is wrong?"),
    ("grounding", "What should the judge assume you already satisfy?"),
    ("patterns", "Wording in a job ad that attracts you, and wording that repels you?"),
    ("industry", "Sectors you will or will not work in?"),
)


def edit_in_editor(prompt, *, editor=None, run=None):
    """Open a temp file pre-filled with `prompt` and return what the user wrote, else None.

    EVERY failure mode returns None, which the caller renders as tier 3 -- the scaffold comment
    stays. The wizard must not be able to fail because of an editor.

    `editor` is passed IN rather than read from `$EDITOR` here: resolving the environment is the
    caller's job, so a test can pin "no editor" without the developer's real `$EDITOR` leaking into
    the run. `shlex.split`, never `shell=True` -- `code --wait` is a normal value for this variable
    and shelling it out would also make it an injection sink.

    The prompt is written as `#` comment lines and those lines are dropped from the result, so
    "user changed nothing" and "user deleted everything" both collapse to None rather than writing
    the instructions into the profile as if they were an answer.
    """
    if not editor:
        return None
    run = run or (lambda argv: subprocess.call(argv))
    scaffold = "".join(f"# {line}\n" for line in prompt.split("\n"))
    fd, path = tempfile.mkstemp(suffix=".md", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(scaffold)
        try:
            # ValueError as well as OSError: `shlex.split` raises on an unbalanced quote, and
            # $EDITOR is user-supplied, so a malformed value would otherwise escape the "every
            # failure mode returns None" contract this docstring states.
            argv = [*shlex.split(editor), path]
        except ValueError:
            return None
        try:
            if run(argv) != 0:
                return None
        except OSError:
            # The editor is not installed, or is not executable. Not the user's problem to debug
            # mid-setup: fall through to the scaffold like every other failure here.
            return None
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    # Drop the EXACT scaffold lines, not every line starting with `#`. A user writing Markdown
    # headings ("# My background") in their editor would otherwise have them silently deleted --
    # in a file whose whole purpose is prose they wrote.
    scaffold_lines = set(scaffold.rstrip("\n").split("\n"))
    kept = [ln for ln in body.split("\n") if ln not in scaffold_lines]
    return "\n".join(kept).strip() or None


class TtyAsker:
    """Prompts on a terminal, re-asking on a bad answer."""

    # Whether the caller should run the parts of the wizard that only make sense with a human
    # present. It lives on the ASKER because `cmd_init` used to derive it from `sys.stdin.isatty()`
    # independently, which meant an INJECTED asker could never reach the interactive half: under
    # pytest isatty() is always False, so the board walk, the profile interview and the
    # `.init-scaffold.md` rescue -- the one data-loss guard in the command -- were unreachable.
    interactive = True

    def __init__(self, *, stdin=None, stdout=None, editor=None):
        self.stdin = stdin
        self.stdout = stdout
        self.editor = editor

    def _say(self, text):
        print(text, file=self.stdout)

    def _read(self):
        """A line, or "" at EOF.

        EOF is Ctrl-D on a real terminal and an exhausted script in a test. Both mean "no more
        answers", so it is treated as a blank rather than re-asked -- otherwise a bad answer typed
        as the last line would spin forever.
        """
        return self.stdin.readline()

    def ask(self, q):
        self._say("")
        self._say(q.prompt)
        if q.hint:
            for line in q.hint.split("\n"):
                self._say(f"  {line}")
        if q.default is not None:
            self._say(f"  [blank = {q.default}]")
        else:
            self._say("  [blank = skip]")
        while True:
            raw = self._read()
            if raw == "" or not raw.strip():
                # PARSE the default too. Returning it raw made the fresh-install TTY run write a
                # cwd-relative vault_dir, and it is the only question with a default -- so the most
                # common path was the unprotected one.
                return q.parse(q.default) if q.default is not None else None
            try:
                return q.parse(raw)
            except BadAnswer as e:
                self._say(f"  {e}")

    def ask_prose(self, prompt):
        self._say("")
        self._say(prompt)
        self._say("  [blank = open $EDITOR, or keep the neutral default]")
        raw = self._read()
        if raw != "" and raw.strip():
            return raw.strip()
        return edit_in_editor(prompt, editor=self.editor)

    def ask_ids(self, prompt, allowed):
        """A comma-separated subset of `allowed`. An unrecognised id is re-asked, not dropped:
        silently ignoring a typo'd board leaves the user believing they selected it."""
        self._say("")
        self._say(prompt)
        self._say(f"  {', '.join(sorted(allowed))}")
        self._say("  Every registered board stays enabled either way -- this only replaces a "
                  "board's built-in example search with your own.")
        self._say("  To stop a board running at all: sluice ingest disable ID")
        self._say("  [blank = skip, and every source runs its own example search]")
        while True:
            raw = self._read()
            if raw == "" or not raw.strip():
                return []
            picked = [s.strip() for s in raw.split(",") if s.strip()]
            unknown = [s for s in picked if s not in allowed]
            if not unknown:
                return picked
            self._say(f"  not a registered source: {', '.join(unknown)}")

    def ask_text_plain(self, prompt):
        self._say(prompt)
        raw = self._read()
        return raw.strip() if raw != "" else ""

    def ask_url(self, prompt):
        """Re-asked on a parse failure. A mistyped board URL that is silently skipped is a source
        the user believes is configured and is not."""
        while True:
            self._say(prompt)
            raw = self._read()
            if raw == "" or not raw.strip():
                return None
            try:
                return parse_url(raw)
            except BadAnswer as e:
                self._say(f"  {e}")


class NoInputAsker:
    """Answers come only from flags. Nothing is prompted for and nothing is inferred."""

    interactive = False

    def __init__(self, *, presets=None):
        self.presets = presets or {}

    def ask(self, q):
        # Exactly two arms, then None. A middle arm taking `q.default` would mean that the moment a
        # future question gains a default, `--no-input` silently writes it into a config nobody was
        # asked about -- which is the wizard filling a gate on the user's behalf.
        if q.key in self.presets:
            return q.parse(self.presets[q.key])
        if q.key == "vault_dir":
            raise MissingAnswer(
                "sluice init needs to know where your vault is. Pass --vault PATH, or run without "
                "--no-input to be asked.")
        return None

    def ask_prose(self, prompt):
        return None

    # `--no-input` selects no boards, so no `sources:` block is written and every source keeps its
    # own neutral example search. That is exactly what entering through the TTY wizard produces,
    # which is the convergence property stated at the top of this module.
    def ask_ids(self, prompt, allowed):
        return []

    def ask_text_plain(self, prompt):
        return ""

    def ask_url(self, prompt):
        return None


def collect(asker, questions):
    """Answers for `questions`, with every skipped question ABSENT.

    None, "" and [] are all dropped rather than stored, so a blank cannot be mistaken downstream for
    a deliberate empty list -- `build_plan` distinguishes "unanswered" from "answered empty" only by
    the key's absence.
    """
    out = {}
    for q in questions:
        value = asker.ask(q)
        if value is None or value == "" or value == []:
            continue
        out[q.key] = value
    return out


def collect_sources(asker, source_ids) -> dict:
    """Two passes, not one 22-deep interrogation: select boards, then collect each board's searches.

    A search is a label plus a URL pasted from a browser, so the URL is re-asked on a parse failure
    rather than dropped -- a mistyped board URL that is silently skipped leaves the user believing a
    source is configured when it is not.

    An empty selection returns `{}`, which emits the commented example block and lets every source
    run its own neutral example search: the abstain default, unchanged.
    """
    # The prompt describes what this actually COLLECTS -- searches -- not what it sounded like it
    # collected. It read "Which boards do you want to scrape?", but the answer never reached
    # enablement: `_render_sources` emits only the picked ids and an unlisted source defaults to
    # enabled, so picking one of 22 still ran all 22. Measured.
    #
    # The fix is the wording, NOT emitting `enabled: false` for the rest. That would write 21
    # preferences the user never stated -- the empty-config-abstains invariant inverted -- and a
    # board registered later would silently fall outside the list. `sluice ingest disable ID` is
    # the explicit route, and the bracket hint says so.
    picked = asker.ask_ids("Which boards do you want to add your own searches to?", source_ids)
    out = {}
    for sid in picked:
        searches = []
        while True:
            label = asker.ask_text_plain(f"{sid} -- search label (blank to finish)?")
            if not label:
                break
            url = asker.ask_url(f"{sid} -- URL for {label!r}?")
            if url:
                searches.append([label, url])
        out[sid] = {"enabled": True, "searches": searches}
    return out


def collect_profile(asker):
    """Prose answers per judging-profile heading, keyed for `build_plan(profile_answers=...)`.

    An unanswered heading is ABSENT, and `_render_profile` then keeps `_DEFAULT_CRITERIA`'s own
    prose for it -- which is what stops `sluice init` stripping the judge's abstain instructions.
    """
    out = {}
    for key, prompt in _PROFILE_QUESTIONS:
        answer = asker.ask_prose(prompt)
        if answer:
            out[key] = answer
    return out
