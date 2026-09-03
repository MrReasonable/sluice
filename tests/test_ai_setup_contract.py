"""`docs/AI-SETUP.md` is an instruction sheet handed to an autonomous agent, so its safety
claims have to be true of the code and not merely true when they were written.

The ordinary README sweeps already cover this file for free: `test_docs_claims.py`'s `_DOCS`
globs `docs/*.md`, so its command names, config keys and links are checked there, and its very
first draft was caught naming a retired `cv.baseline_rel`. What none of those sweeps can see is
the part that makes this file different from every other doc in the tree. It tells an agent what
it must NOT do, and it justifies each prohibition with a property of the code:

  - `verify` carries no `--all` and no `--yes`
  - the MCP server exposes nothing that verifies, at any `--write` level
  - `experience add` proposes rather than promotes

Those three are what make "never mark evidence verified" enforceable rather than aspirational.
Note what that does and does not buy: measured, each of the three code properties is ALSO held
by a pre-existing test (`test_evidence_cli.py`, `test_mcpserver.py`, `test_evidence_store.py`),
so mutating any one of them reddens more than this file. The novel coverage here is the rule-text
sweep plus the BINDING of those properties to the prose that cites them -- if a property moves,
this file is what says a shipped document is now lying about it.
If one stops holding, this file goes on telling an agent that a bulk promotion path does not
exist while it does, and the trust root behind the whole CV fabrication gate is a doc comment.

The rules themselves are pinned too, by heading. A rule quietly dropped from this file has no
other guard: nothing else in the tree references it, the prose still reads coherently without
it, and the failure surfaces as an agent doing the forbidden thing in someone's real vault.
"""

import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOC = os.path.join(_ROOT, "docs", "AI-SETUP.md")

# The three rules, keyed on a distinctive phrase from each. Measured: this catches a DELETION,
# and it also catches a rephrasing of the phrase itself -- so it is stricter than "the property
# is still stated", not looser. That is the safe direction (a reworded rule gets a deliberate
# test edit rather than silently losing its guard), but it is not what an earlier version of
# this comment claimed.
_RULES = ("never invent", "never mark evidence verified", "never fabricate")


def _doc():
    with open(_DOC, encoding="utf-8") as fh:
        return fh.read()


def test_the_docs_stated_rule_count_matches_the_rules_it_states():
    """A spelled number in prose is this repo's most-repeated drift surface, and this file has
    one: the doc says "The three rules" and this module lists three. Bind them, so adding a
    fourth rule cannot leave the heading saying three -- which is exactly how the neighbouring
    "three dead components" claim went stale twice on this branch, the second time surviving a
    case-sensitive grep that missed it at the start of a sentence."""
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    text = _doc().lower()
    assert len(_RULES) in words, f"no spelling known for {len(_RULES)} rules"
    assert f"the {words[len(_RULES)]} rules" in text, (
        f"this module lists {len(_RULES)} rules, but docs/AI-SETUP.md does not say "
        f"'the {words[len(_RULES)]} rules'. One of the two has drifted.")
    for n, w in words.items():
        if n != len(_RULES):
            assert f"the {w} rules" not in text, (
                f"docs/AI-SETUP.md says 'the {w} rules' while this module lists {len(_RULES)}")


def test_the_three_rules_are_all_still_stated():
    """Anti-vacuity: every assertion below is about the file's CONTENT, and a file that lost a
    rule would still satisfy each of them individually."""
    text = _doc().lower()
    missing = [r for r in _RULES if r not in text]
    assert not missing, (
        f"docs/AI-SETUP.md no longer states: {missing}. Each rule is the only thing standing "
        "between an autonomous agent and a silent, expensive failure in a real vault; none of "
        "them is restated anywhere else in the tree.")


def test_verify_really_carries_no_bulk_promotion_flag():
    """The doc tells an agent that no bulk path exists. Check that against the real parser."""
    from sluice.cli import _build_parser

    import argparse

    parser = _build_parser()
    actions = {}

    def walk(p, path):
        leaf = True
        for act in p._actions:
            if isinstance(act, argparse._SubParsersAction):
                leaf = False
                for name, sub in act.choices.items():
                    walk(sub, path + [name])
        if leaf and path:
            actions[" ".join(path)] = {
                opt for act in p._actions for opt in act.option_strings}

    walk(parser, [])
    verify_cmds = [k for k in actions if k.endswith(" verify")]
    # SCOPE first: a walk that found no `verify` command would pass the flag check below while
    # having examined nothing at all.
    assert sorted(verify_cmds) == ["experience verify", "skills verify", "stories verify"], (
        f"the evidence `verify` roster has changed: {sorted(verify_cmds)}. docs/AI-SETUP.md "
        "names this set when it tells an agent promotion is a human action.")
    for cmd in verify_cmds:
        forbidden = {"--all", "--yes", "-y", "--force"} & actions[cmd]
        assert not forbidden, (
            f"`job-sluice {cmd}` grew {sorted(forbidden)}. docs/AI-SETUP.md tells an agent no "
            "bulk promotion path exists, and an agent that finds one has been told it is safe "
            "to use. A bulk verifier is a new trust root, not a convenience.")


def test_no_mcp_tool_can_verify_evidence_at_any_write_level():
    """`--write` is a per-registration trust decision, and the doc says verification is not in
    it at ANY level. Both levels are checked, because 'not in the read-only set' is the weaker
    claim and the one that would survive someone adding a write-side verifier."""
    # NOT `importorskip`: `mcp` is in the `test` extra precisely so this runs for real, and a
    # test that skips itself is how a safety gate goes silently absent. The tree-wide
    # `test_no_test_module_uses_importorskip` sweep enforces that, and caught this line.
    import asyncio

    from sluice.core.config import Config
    from sluice.mcpserver import build_server

    for write in (False, True):
        server = build_server(Config(), write=write)

        async def _names(srv=server):
            from mcp import Client
            async with Client(srv, raise_exceptions=True) as client:
                return {t.name for t in (await client.list_tools()).tools}

        names = asyncio.run(_names())
        assert names, f"the MCP server registered no tools at write={write}"

        # Keyed on EVIDENCE VERIFICATION only. A `sign_off` clause used to sit here too, and it
        # did not belong: CV sign-off (`cv_signoff`, a write-level tool) is a different concept
        # that docs/AI-SETUP.md explicitly leaves to the human but never claims is impossible.
        # It matched nothing today only because `cv_signoff` carries no underscore there, so the
        # clause was one rename away from failing a legitimate tool for the wrong reason.
        offenders = {n for n in names if "verif" in n}
        assert not offenders, (
            f"MCP registers {sorted(offenders)} at write={write}. docs/AI-SETUP.md tells an "
            "agent that nothing at any level can mark evidence verified, and the MCP surface "
            "is the one an agent reaches for first.")

        # ANTI-VACUITY. `offenders` being empty is this sweep's success case, so it reads the
        # same whether the predicate is working or matches nothing at all. Prove it still bites.
        planted = {n for n in (names | {"verify_evidence"}) if "verif" in n}
        assert planted == {"verify_evidence"}, (
            f"the offender predicate no longer catches a plainly-named verifier: {planted}")


def test_experience_add_cannot_be_handed_a_verified_flag():
    """`add`'s flags are derived from `EvidenceKind.fields`, so a `verified` field would mint a
    `--verified` flag: exactly what an agent shelling out to the CLI would reach for."""
    from sluice.core.protocols import EVIDENCE_KINDS

    assert EVIDENCE_KINDS, "the kind registry is empty, so this sweep examined nothing"
    for name, kind in EVIDENCE_KINDS.items():
        assert "verified" not in kind.fields, (
            f"`{name}` lists `verified` among its user-facing fields, which generates a "
            "`--verified` flag on `add` and makes docs/AI-SETUP.md's rule 2 unenforceable.")


def test_the_docs_own_camofox_commands_match_the_install_guide():
    """The setup sequence and INSTALL.md both tell the user how to start Camofox. Two copies of
    a command drift; this is the cheap check that they have not, and it is here rather than in
    prose because the last time this repo stated the same fact twice the copies disagreed about
    whether the thing was even possible."""
    doc = _doc()
    with open(os.path.join(_ROOT, "docs", "INSTALL.md"), encoding="utf-8") as fh:
        install = fh.read()

    # EXTRACT the clone line from INSTALL and require it verbatim in AI-SETUP, rather than
    # spelling the upstream URL here. Hardcoding it would make this file a THIRD place the URL
    # lives, so a corrected URL in both docs would still leave the test asserting the old one --
    # the drift this check exists to catch, one level up.
    clone = re.search(r"^git clone \S+camofox-browser\S*$", install, re.M)
    assert clone, "docs/INSTALL.md no longer shows a camofox-browser clone command"
    assert clone.group(0) in doc, (
        f"docs/AI-SETUP.md does not carry INSTALL's clone line verbatim ({clone.group(0)!r}); "
        "the two describe the same setup step and must not drift apart")
    assert "make build" in doc and "make build" in install, (
        "the build step is missing from one of docs/AI-SETUP.md / docs/INSTALL.md")

    # Reuse the repo's own fence parser rather than a regex. A first cut here used
    # `re.findall(r"^```(?:bash|...)?\n(.*?)^```")` and silently matched NOTHING in the block
    # it was written for: findall pairs openers with closers left to right, so a fence whose
    # info string is outside the alternation lets its closing ``` terminate a later match and
    # every pairing after it shifts. `_shell_blocks` scans line by line for exactly this
    # reason -- its own docstring explains that CommonMark's closing rule is a length
    # COMPARISON no backreference can express. Measured: the bad regex reported "docker compose
    # up" absent from a file that had just been mutated to contain it, inside a ```bash fence.
    from tests.test_docs_claims import _shell_blocks

    def _fenced(text):
        return "\n".join(_shell_blocks(text))

    # AI-SETUP must not carry its own `docker run` INVOCATION -- the flags live in INSTALL
    # alone, because two copies of a long command line is exactly how the compose-vs-run error
    # got shipped. Scoped to fenced blocks: prose that POINTS AT the invocation is the whole
    # point of the split, and an unscoped substring check flags that too (it did, immediately).
    # SCOPE FIRST. Both checks below are NEGATIVE, and a negative sweep's success case is
    # "found nothing" -- so an extraction that returns nothing satisfies them while the
    # forbidden command sits in the file. Measured: changing AI-SETUP's fence from ```bash to a
    # bare ``` (an info string `_shell_blocks` drops) took this module from 1 failure to 9
    # passes with `docker compose up -d` still in the doc. These two positives are what stop
    # that; both are true today.
    assert clone.group(0) in _fenced(doc), (
        "AI-SETUP's clone line is not inside a shell fence this sweep can see, so the negative "
        "checks below are examining nothing")
    assert "docker run" in _fenced(install), (
        "INSTALL's docker run is not inside a shell fence this sweep can see, so the negative "
        "checks below are examining nothing")

    assert "docker run" not in _fenced(doc), (
        "docs/AI-SETUP.md has grown its own docker run command; keep the invocation in "
        "INSTALL.md and link to it, or the two will disagree about ports and volumes")

    # The upstream repository ships no compose file, so this fails from a fresh clone. It was
    # documented in both files for one commit before anyone tried it. Fenced blocks only, for
    # the same reason as above and demonstrated the same way: AI-SETUP now WARNS about the
    # command in prose, and an unscoped check flags the warning as the offence.
    for name, text in (("AI-SETUP.md", doc), ("INSTALL.md", install)):
        assert "docker compose up" not in _fenced(text), (
            f"docs/{name} instructs `docker compose up` against a repository that ships no "
            "compose file; upstream documents `docker run`")


@pytest.mark.parametrize("rule", _RULES)
def test_the_rule_sweep_is_falsified_by_a_missing_rule(rule):
    """`test_the_three_rules_are_all_still_stated` passes over a file that has every rule, which
    is also what it does over a file whose check is broken. Run the real filter over a corpus
    with one rule removed and confirm it complains."""
    text = _doc().lower().replace(rule, "")
    assert [r for r in _RULES if r not in text] == [rule]
