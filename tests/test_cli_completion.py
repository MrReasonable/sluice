"""Shell-completion wiring (`sluice[completion]`, argcomplete): the guarded import, the
`.completer` attachment, and the completer functions themselves.

Argcomplete is an OPTIONAL extra -- see pyproject.toml's `completion` group -- guarded the
same way `yaml`/`jinja2`/`weasyprint`/the Google libs already are, so a bare install must
never need it and `main()` must behave identically whether or not it is importable. Whether
or not this test environment happens to have argcomplete installed is irrelevant to that
property, so the import-guard tests below monkeypatch `sluice.cli.argcomplete` directly
rather than depending on the real package being present or absent.

The completer functions are the other half: `argcomplete` reads real data (registered source
ids, the real status vocabulary) rather than a hand-maintained list that could drift from the
CLI it describes -- see the module docstring above `_complete_source_id` in cli.py for why
that is the whole reason this shipped as argcomplete rather than a generated static file.
They run at TAB-press time inside the user's shell, so the property that matters most is that
they can never raise: an exception there would break completion for every future command in
that shell session, not just this one.
"""
import argparse

import pytest

import sluice.cli as cli


def test_main_calls_autocomplete_only_when_argcomplete_is_importable(monkeypatch):
    """`main()` must not require argcomplete to run -- offline commands and every existing
    CLI test construct `main()` with no `completion` extra installed. Faking BOTH states here
    (rather than relying on whatever happens to be installed in this environment) is what
    makes the assertion independent of that environment.
    """
    calls = []

    class _FakeArgcomplete:
        def autocomplete(self, parser):
            calls.append(parser)

    monkeypatch.setattr(cli, "argcomplete", _FakeArgcomplete())
    monkeypatch.setattr(cli, "load_config", lambda: __import__(
        "sluice.core.config", fromlist=["Config"]).Config())
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert len(calls) == 1, "argcomplete.autocomplete() must run exactly once when importable"

    calls.clear()
    monkeypatch.setattr(cli, "argcomplete", None)
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert calls == [], "main() must not touch argcomplete when the import failed"


def _action(parser: argparse.ArgumentParser, path: tuple, name: str):
    """The Action for `name` (an option string like `--source`, or a positional dest like
    `id`) within the subcommand parser reached by walking `path` (e.g. `("ingest", "run")`)
    from the root.

    Navigates via argparse's own `_SubParsersAction`/`.choices`, so it cannot drift from what
    `_build_parser` actually builds -- but, unlike a tree-wide search, it also cannot silently
    resolve to a DIFFERENT subcommand's action that happens to share the same option string or
    positional dest. `id` is reused by `test-source`/`enable`/`disable`: an earlier version of
    this helper searched the whole tree and ignored `path` entirely, so all three parametrized
    cases below actually resolved to whichever one the walk reached first -- silently retesting
    one action three times rather than each subcommand's own, caught in review.
    """
    target = parser
    for segment in path:
        sub_action = next(a for a in target._actions if isinstance(a, argparse._SubParsersAction))
        target = sub_action.choices[segment]
    for action in target._actions:
        if action.option_strings and name in action.option_strings:
            return action
        if not action.option_strings and action.dest == name:
            return action
    return None


@pytest.mark.parametrize(
    "path, action_name, expected_completer",
    [
        (("ingest", "run"), "--source", cli._complete_source_id),
        (("ingest", "test-source"), "id", cli._complete_source_id),
        (("ingest", "enable"), "id", cli._complete_source_id),
        (("ingest", "disable"), "id", cli._complete_source_id),
        (("track", "confirm"), "--to", cli._complete_status),
    ],
)
def test_completable_actions_carry_the_expected_completer(path, action_name, expected_completer):
    """Every action this module claims to complete live must actually carry the RIGHT
    `.completer` -- the attribute `argcomplete` looks for. Checking only `hasattr` would pass
    even if the attach site were wired to the wrong function (e.g. `--to` picking up
    `_complete_source_id` after a copy-paste), since any completer at all satisfies `hasattr`;
    asserting identity against the actual function object is what catches that.
    """
    parser = cli._build_parser()
    action = _action(parser, path, action_name)
    assert action is not None, f"no action found for {action_name!r} under {path}"
    assert action.completer is expected_completer, (
        f"{action_name!r} under {path} has completer {action.completer!r}, "
        f"expected {expected_completer!r}")


def test_action_resolves_each_subcommands_own_positional_not_a_shared_first_match():
    """`test-source`, `enable` and `disable` all name their positional `id` AND all three
    correctly share the identical `_complete_source_id` function object -- by design, they
    complete the same kind of value. That similarity is exactly what let a real bug hide
    behind a passing suite: `_action`'s prior version ignored its own `path` argument and did
    a global first-match search, so all three parametrized cases above silently re-resolved
    whichever subcommand's `id` the walk reached FIRST (`test-source`, added earliest in
    `_build_parser`) rather than each one's own -- and every assertion in the test above still
    passed, because a shared `.completer` looks identical either way. Caught in review by
    mutation: reverting `_action` to the old whole-tree search left all 9 tests in this file
    green.

    This test is what closes that gap: it asserts the three lookups return three DISTINCT
    Action objects (`is not`, not `==` -- argparse doesn't define equality, so an `==` check
    would trivially pass by identity anyway and prove nothing beyond what `is` already proves
    more directly), which is only possible if `_action` actually navigated to each subcommand's
    own parser rather than returning the first match from a global walk.
    """
    parser = cli._build_parser()
    test_source_id = _action(parser, ("ingest", "test-source"), "id")
    enable_id = _action(parser, ("ingest", "enable"), "id")
    disable_id = _action(parser, ("ingest", "disable"), "id")
    assert test_source_id is not enable_id, "enable's 'id' resolved to test-source's own action"
    assert test_source_id is not disable_id, "disable's 'id' resolved to test-source's own action"
    assert enable_id is not disable_id, "disable's 'id' resolved to enable's own action"
    # ...and they DO correctly share the same completer -- the similarity that made the bug
    # invisible to the parametrized test above, confirmed deliberately rather than assumed.
    assert test_source_id.completer is enable_id.completer is disable_id.completer


def test_complete_source_id_returns_real_registered_ids():
    """Not a hand-listed fixture: this must reflect whatever the plugin registry actually
    has loaded, which is the entire point of using a live completer over a generated file.
    """
    from sluice.ingest import sources as registry

    real_ids = {s.id for s in registry.all_sources()}
    assert real_ids, "the registry enumerated nothing -- this test would pass vacuously"

    got = cli._complete_source_id("", parsed_args=None)
    assert set(got) == real_ids

    one = next(iter(real_ids))
    prefix = one[:2]
    filtered = cli._complete_source_id(prefix, parsed_args=None)
    assert one in filtered
    assert all(i.startswith(prefix) for i in filtered), "prefix filtering let a mismatch through"


def test_complete_status_returns_the_real_canonical_vocabulary():
    from sluice.core.status import CANONICAL

    assert cli._complete_status("", parsed_args=None) and set(
        cli._complete_status("", parsed_args=None)) == set(CANONICAL)
    assert cli._complete_status("app", parsed_args=None) == ["applied"]


def test_completers_never_raise_on_a_broken_environment(monkeypatch):
    """The property that matters most: an exception mid-completion breaks the user's shell,
    not just this command. Simulate the registry/status import blowing up and assert both
    completers degrade to an empty list rather than propagating.
    """

    def _boom(*a, **k):
        raise RuntimeError("simulated failure reading live state")

    monkeypatch.setattr(
        "sluice.ingest.sources.all_sources", _boom, raising=True)
    assert cli._complete_source_id("", parsed_args=None) == []

    import sluice.core.status as status_mod

    monkeypatch.setattr(status_mod, "CANONICAL", property(_boom), raising=False)
    # CANONICAL is a frozenset, not a callable -- breaking the *import* path instead, which is
    # what a real corrupted-module failure would look like.
    monkeypatch.delattr(status_mod, "CANONICAL", raising=False)
    assert cli._complete_status("", parsed_args=None) == []
