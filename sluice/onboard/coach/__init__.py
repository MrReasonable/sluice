"""The coach's two prompts, each assembled from Markdown playbooks packaged beside this file.

- `career_interview` (`assemble_prompt`, the `PLAYBOOKS` roster): the career coach that sets up
  a job hunt. A persona core plus one playbook per phase, each with its method and exit
  criteria, followed by the units `setup_save` accepts, derived from its own tables. Later
  phases add files rather than growing one text.
- `interview_prep` (`assemble_prep_prompt`, the `PREP_PLAYBOOKS` roster): the rehearsal coach
  that prepares the user for one lead's interview from the `prep_brief` tool. It reads none of
  the setup playbooks, and each prompt's roster is pinned to that prompt alone.

The shipped text names no role, sector, seniority or employer, and gives no example list of
either; the coach's expertise comes from researching the user's chosen role in the session
(spec: The career coach / Neutrality). tests/test_coach_prompt.py sweeps the assembled text.

The playbooks are DATA files rather than module constants for two reasons. A phase is a page
of prose, and a Python string literal that long is unreviewable as prose. And
tests/onboard_prose.py's constant roster would otherwise have to treat each page as a label
string; it sweeps each assembled prompt whole instead (`rendered:coach_prompt`, and
`rendered:prep_prompt` at both privilege levels).

Nothing here writes, and tests/test_mcpserver.py's onboard sweep pins that this package has no
write path of its own. What each coach itself writes goes through one server tool: the setup
coach saves the changes the user agreed to through `setup_save`, and the rehearsal coach, at
`--write`, may queue a story through `propose_evidence`, which lands in the inbox and reaches no
brief until the user verifies it. That promotion is the user's, not the coach's: the write-level
note points them at `job-sluice stories verify` or the server's `verify_evidence`, whose form
puts a human in front of every entry it promotes.

Building `career_interview`'s prompt imports `core/app.py` and the backends (its units list
what each backend needs, see `_backend_requirements`). Building `interview_prep`'s imports
neither, and tests/test_coach_prompt.py checks that in a fresh interpreter.
"""
from importlib import resources

from sluice.onboard import plan as _plan
from sluice.onboard import questions as _questions
from sluice.onboard import review as _review

# Assembly order is the session's order: who the coach is and the rules it keeps, then each
# phase as the conversation reaches it.
PLAYBOOKS = ("persona", "open", "discovery", "research", "interview", "review", "handoff")

READ_ONLY_NOTE = ("This sluice server is read-only. You can interview and research, but "
                  "before saving ask the user to restart the server with `--write` "
                  "(`job-sluice mcp serve --write`), or nothing can be written.")
UNITS_INTRO = ("Every change you send to `setup_save` is one of these. Use `setup_status` "
               "for the current values and the exact targets.")
LIST_NOTE = ("A key marked (list) takes its `value` as a list of strings, one item each, never "
             "comma-joined, so an item that holds a comma stays one item.")
BACKENDS_INTRO = ("What each `backend` needs before it can run. Every one needs something, and "
                  "none is recommended here. An API key is a separate credential a provider "
                  "issues for programs to use, and a subscription to a chat service does not "
                  "include one. Whether something the user already has meets a line below is "
                  "for them to check against that line, never to infer from a provider's name.")
NO_REQUIREMENT_STATED = "what it needs is not stated here; `doctor` reports whether it is ready."

# The interview_prep prompt's playbooks, a roster of its own: the setup coach never reads them,
# and the prep coach never reads PLAYBOOKS.
PREP_PLAYBOOKS = ("rehearse",)
# The prep coach's capture rule, one note per privilege level. It lives here rather than in
# rehearse.md, which both levels share, because only a `--write` server registers
# `propose_evidence`: a read-only coach told to call it would reach for a tool that is not there.
PREP_READ_ONLY_NOTE = (
    "This sluice server is read-only, so it cannot save a story. When the user tells a story "
    "that is not in their entries, give them the command to add it themselves: "
    "`job-sluice stories add`, with `--best-for` set to the requirement they told it for, in "
    "the posting's own words, or in the user's own words when the requirement came from them. "
    "Any `--name` or `--body` you write into the command is the user's own telling: never "
    "reword it or add a part they did not tell. "
    "Then `job-sluice stories verify` includes it in a brief. A brief finds a story by its "
    "title and Best For, never its body, so one added without Best For is hard to find for "
    "the next interview.")
PREP_WRITE_NOTE = (
    "This sluice server can save a story for the user to review. When the user tells a story "
    "that is not in their entries, offer to save it with `propose_evidence` (kind `stories`), "
    "in the user's own words: never reword it, add a part they did not tell, or save it "
    "without their yes. Set its `Best For` field to the requirement they told it for, in the "
    "posting's own words, or in the user's own words when the requirement came from them, and "
    "show them that Best For with the story before they say yes. A brief finds a story by its "
    "title and Best For, never its body, so one saved without Best For is hard to find for "
    "the next interview. Show the name you will pass and say the store reduces it: it is "
    "lowercased, every run of characters other than a plain letter a to z or a digit becomes "
    "one hyphen (an apostrophe, a decimal point and an accented letter included), and it is "
    "cut at 80 characters. A saved story lands in the `_inbox/` folder of their stories, not among their entries, and it reaches a brief only after they review and verify it "
    "themselves, with `job-sluice stories verify` or this server's `verify_evidence`. Never "
    "tell them it is in their brief.")


def read_playbook(name: str) -> str:
    """One packaged playbook's text. `importlib.resources`, not a path beside `__file__`, so it
    reads the same from a wheel, an sdist install or a source tree."""
    return resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")


def _units() -> str:
    """The units the coach may propose, DERIVED from the same tables `setup_save` validates
    against. A hand-written list in a playbook would drift from them silently, and the coach
    would then propose targets the tool sets aside."""
    lines = ["## What you can propose", "", UNITS_INTRO, "", LIST_NOTE, "",
             "Config keys (`kind: config`):"]
    for q in _questions.catalogue():
        if _questions.is_list(q):
            # The prompt is `init`'s TTY question, which asks for one comma-separated line;
            # setup_save takes a list, so that instruction must not reach the coach.
            lines.append(f"- `{q.key}` (list): {q.prompt.replace(', comma-separated', '')}")
        else:
            lines.append(f"- `{q.key}`: {q.prompt}")
    lines += ["", "Judging Profile headings (`kind: profile`):"]
    lines += [f"- {h.lstrip('#').strip()}" for h in _plan.PROFILE_HEADINGS]
    lines += ["", "Candidate Profile fields (`kind: candidate`):"]
    lines += [f"- `{k}`" for k in _plan._CANDIDATE_KEY_BY_ANSWER]
    lines += ["", "Role Brief sections (`kind: brief`):"]
    lines += [f"- {s}" for s in _review.ROLE_BRIEF_SECTIONS]
    lines += ["", "Searches (`kind: search`, `target` a source id, `label`, `url`, "
                  "`remove: true` to remove one)."]
    lines += ["", BACKENDS_INTRO]
    lines += [f"- `{b}`: {need}" for b, need in _backend_requirements()]
    return "\n".join(lines)


def _backend_requirements():
    """(backend, what it needs) for every backend in the DEFAULT_MODELS roster. A per-token
    backend's line is DERIVED from the credential map its factory reads (`api_key_env`), so the
    prompt cannot claim it needs less than construction demands; a key-less backend states its
    own requirement on its registered factory (`requirement`), since there is no key to derive
    from. A backend with neither renders NO_REQUIREMENT_STATED, which tests/test_coach_prompt.py
    fails on: an unstated requirement reads as "needs nothing" and steers the user to it."""
    from sluice.core import plugins
    from sluice.core.app import api_key_env
    from sluice.core.backends import DEFAULT_MODELS
    import sluice.backends  # noqa: F401  -- import triggers factory self-registration

    out = []
    for b in sorted(DEFAULT_MODELS):
        var = api_key_env(b)
        if var:
            need = f"an API key in the `{var}` environment variable where the sluice server runs."
        else:
            need = getattr(plugins.get("backend", b), "requirement", "") or NO_REQUIREMENT_STATED
        out.append((b, need))
    return out


def assemble_prompt(*, write: bool = True, read=read_playbook) -> str:
    """The prompt the MCP server serves. `read` is the playbook source, a parameter so the
    neutrality sweep can drive THIS function with a planted word rather than a copy of it;
    the server always uses the packaged files. `write` is the server's privilege level: a
    read-only server's coach is told to ask for a restart before saving."""
    parts = [read(n).strip() for n in PLAYBOOKS] + [_units()]
    if not write:
        parts.append(READ_ONLY_NOTE)
    return "\n\n".join(parts) + "\n"


def assemble_prep_prompt(*, write: bool = True, read=read_playbook) -> str:
    """The interview_prep prompt: the PREP_PLAYBOOKS only (never the setup playbooks), then the
    capture note for the server's privilege level. `read` is the same injection seam
    `assemble_prompt` has. It imports no backend: unlike `assemble_prompt` it lists no units,
    so it needs no backend requirement text, and no backend's import can fail it."""
    parts = [read(n).strip() for n in PREP_PLAYBOOKS]
    parts.append(PREP_WRITE_NOTE if write else PREP_READ_ONLY_NOTE)
    return "\n\n".join(parts) + "\n"
