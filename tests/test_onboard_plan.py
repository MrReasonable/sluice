"""`build_plan` is a pure function from a dict to two strings, which is what lets the load-bearing
property be a unit test instead of a wizard transcript."""
import dataclasses
import pathlib
import re

import pytest
import yaml

from sluice.core.config import load_config
from sluice.cv.config import load_cv_config
from sluice.onboard.plan import build_plan
from sluice.onboard.questions import catalogue
from sluice.track.config import load_track_config
from sluice.triage.config import load_triage_config

VAULT = "/example/vault"
LOADERS = (load_config, load_triage_config, load_cv_config, load_track_config)


def _plan(tmp_path, answers=None, **kw):
    return build_plan(answers or {}, **kw)


def _written(tmp_path, answers=None, **kw):
    path = tmp_path / "config.yaml"
    path.write_text(_plan(tmp_path, answers, **kw).config_text, encoding="utf-8")
    return str(path)


# ── the enumerated differential (replaces v1's 13-field hand-list) ───────────
@pytest.mark.parametrize("loader", LOADERS, ids=lambda f: f.__name__)
def test_an_unanswered_wizard_writes_a_config_identical_to_no_config_at_all(tmp_path, loader):
    """Field-for-field against the code defaults, ENUMERATED not hand-listed -- so a future
    catalogue key rendered with a value cannot slip past, and nothing has to be kept in step by
    hand. `vault_dir` is the one legitimate difference: it is the wizard's only required answer."""
    emitted = dataclasses.asdict(loader(_written(tmp_path)))
    baseline = dataclasses.asdict(loader(None))
    fields = set(emitted)
    assert fields, "the field sweep enumerated nothing"          # SCOPE
    for name in sorted(fields - {"vault_dir"}):
        assert emitted[name] == baseline[name], f"{loader.__name__}.{name} was overridden"


def test_the_template_contains_every_catalogue_key_COMMENTED(tmp_path):
    """SCOPE, paired with the differential above: that assertion passes just as happily on an
    EMPTY file, since the loaders would return the neutral code defaults and every gate would
    abstain for the wrong reason -- the all([]) shape that has shipped three times here.

    No `#?`: the key must be demonstrably COMMENTED, not merely present. And the match is anchored
    to a key LINE, so a comment that merely mentions the key mid-sentence cannot satisfy it."""
    text = _plan(tmp_path).config_text
    for q in catalogue(default_vault=VAULT):
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#\s*{re.escape(leaf)}:", text, re.M), \
                f"{dotted} is not present-and-commented in the template init writes"


def test_uncommenting_any_single_key_yields_a_config_that_still_LOADS(tmp_path):
    """The file's own headline instruction, executed.

    `# <- uncomment and set YOUR OWN` was false for 16 of 19 keys: the block header rendered
    commented, so uncommenting a nested key left an indented key with no parent and PyYAML raised
    a ParserError pointing at line 1 rather than the edited line. Nothing tested the uncomment
    path, which is the one action the file tells every user to take."""
    text = _plan(tmp_path).config_text
    lines = text.splitlines()
    targets = [i for i, ln in enumerate(lines) if "<- uncomment and set YOUR OWN" in ln]
    assert len(targets) >= 15, f"only {len(targets)} uncommentable keys found"   # SCOPE

    def uncomment(src, i):
        return src[:i] + [src[i].split("#")[0] + src[i].split("# ", 1)[1].split(":")[0] + ": []"] \
            + src[i + 1:]

    # A ROOT key is uncommented alongside each nested one, because ONE key alone is not the
    # falsifying case: an all-commented document with a single indented key still parses, so an
    # earlier version of this test stayed GREEN with the commented-header defect fully restored.
    # It is the COMBINATION -- a key at column 0 followed by an indented key whose parent is
    # commented out -- that raises, and it is also what a real user does, since the template offers
    # 4 root keys and 16 nested ones.
    root = next(i for i in targets if not lines[i].startswith("  "))
    nested = [i for i in targets if lines[i].startswith("  ")]
    assert nested, "no nested key in the template; this test would prove nothing"   # SCOPE

    for i in nested:
        edited = uncomment(uncomment(list(lines), i), root)
        path = tmp_path / f"edit{i}.yaml"
        path.write_text("\n".join(edited), encoding="utf-8")
        try:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AssertionError(
                f"uncommenting line {i + 1} ({lines[i].strip()!r}) alongside a root key broke the "
                f"file: {exc}") from None


def test_the_architecture_doc_describes_the_header_behaviour_that_ships(tmp_path):
    """`docs/ARCHITECTURE.md` is the architecture of record, and this exact claim has now gone
    stale TWICE -- the second time because a `.replace()` with a mismatched anchor silently no-op'd
    and the fix was reported as landed without being checked.

    So the doc is asserted against the RENDERED OUTPUT rather than trusted. A reader following a
    stale architecture doc re-introduces a measured bug."""
    doc = (pathlib.Path(__file__).resolve().parent.parent / "docs/ARCHITECTURE.md").read_text(
        encoding="utf-8")
    onboard = doc.split("## `onboard/`")[1].split("\n## ")[0]
    assert onboard.strip(), "the onboard/ section vanished"          # SCOPE

    active = [ln for ln in _plan(tmp_path).config_text.splitlines()
              if re.match(r"^[a-z_]+:\s*$", ln)]
    assert active, "no active block header in the rendered config"   # SCOPE
    assert "HEADER stays ACTIVE" in onboard, \
        "the doc no longer describes the active block headers the renderer emits"
    assert "HEADER is commented" not in onboard


def test_prose_mentioning_a_key_does_NOT_satisfy_the_scope_matcher():
    """NEGATIVE CONTROL. Widening the matcher to `^[#\\s]*` would let an explanatory comment stand
    in for the key -- the matched-by-adjacent-prose bug this repo already shipped."""
    prose = "# set accept_titles: to whatever you like\n"
    assert not re.search(r"^\s*#\s*accept_titles:", prose, re.M)


def test_answers_become_active_keys(tmp_path):
    path = _written(tmp_path, {"accept_titles": ["example role"], "perm_floor": 90000,
                               "lead_ttl_days": 90})
    assert load_triage_config(path).accept_titles == ["example role"]
    assert load_triage_config(path).perm_floor_gbp == 90000
    assert load_config(path).lead_ttl_days == 90


def test_one_backend_answer_fans_out_to_every_block(tmp_path):
    path = _written(tmp_path, {"primary_backend": "openai", "fallback_backend": "anthropic"})
    for loader in (load_triage_config, load_cv_config, load_track_config):
        assert loader(path).primary_backend == "openai"
        assert loader(path).fallback_backend == "anthropic"


def test_the_fan_out_covers_every_config_declaring_a_backend():
    """DISCOVERED, reusing the neutral-defaults sweep's own helper rather than a second, weaker
    copy. #63's lesson: a hand-list of dataclasses leaks exactly like the hand-list of fields it
    replaced -- four were named there and there were six.

    `.values()`, not the bare dict: the helper is keyed by CLASS NAME, so iterating it yields
    strings and `dataclasses.fields` raises TypeError. Measured, not assumed."""
    from tests.test_sluice_neutral_defaults import _discover_config_dataclasses
    declared = {cls.__module__.split(".")[1]
                for cls in _discover_config_dataclasses().values()
                if "primary_backend" in {f.name for f in dataclasses.fields(cls)}}
    assert declared, "the sweep found no config declaring primary_backend"
    q = {x.key: x for x in catalogue(default_vault=VAULT)}["primary_backend"]
    assert {d.split(".")[0] for d in q.writes_to} == declared


def test_every_emitted_key_is_documented_in_the_example_config():
    # Resolved from __file__, not the cwd: a repo-relative open() makes this test depend on
    # where pytest was invoked from.
    root = pathlib.Path(__file__).resolve().parent.parent
    example = (root / "sluice.yaml.example").read_text(encoding="utf-8")
    for q in catalogue(default_vault=VAULT):
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            assert re.search(rf"^\s*#?\s*{re.escape(leaf)}:", example, re.M), \
                f"{dotted} is written by init but undocumented in sluice.yaml.example"


def test_no_answer_emits_a_scalar_that_loads_as_a_bool_where_an_int_is_meant(tmp_path):
    data = yaml.safe_load(_plan(tmp_path, {"lead_ttl_days": 1, "perm_floor": 1}).config_text)
    assert data["lead_ttl_days"] is not True and isinstance(data["lead_ttl_days"], int)
    assert isinstance(data["triage"]["perm_floor_gbp"], int)


def test_nasty_answers_still_yield_loadable_yaml(tmp_path):
    # cv_employers, not cv_name (#133/#107: cv_name no longer exists -- identity moved
    # to the vault, and load_cv_config now RAISES on cv.name rather than loading it, so
    # a hostile-character regression there could not even be observed through this
    # loader any more). cv_employers is parse_csv-shaped, same as accept_titles below,
    # but on a DIFFERENT sub-app's config block, so this still proves the emitter's
    # escaping survives the loader across more than one `*Config`.
    #
    # vault_dir carries the SCALAR branch of _render_value (`scalar(value)`, not
    # `flow_list(value)`) -- without it, cv_employers/accept_titles being both LIST-
    # shaped left `scalar()` with no hostile witness in this file at all. Mutating
    # `_render_value`'s scalar branch from `scalar(value)` to a bare `value` survived
    # the whole suite before this row was added (measured); it reddens here now.
    # vault_dir's parser (parse_path) never runs at build_plan/load_config time --
    # `_grouped` takes answers already-parsed, and load_config's own vault_dir read is
    # a bare passthrough (`str(data.get("vault_dir") or "")`, core/config.py) -- so an
    # arbitrary hostile string is a legitimate answer to give it here.
    path = _written(tmp_path, {"vault_dir": '/example/O\'Example: "the #1"',
                               "cv_employers": ['O\'Example: "the #1"'],
                               "accept_titles": ["yes", "#hash", "back\\slash"]})
    assert load_config(path).vault_dir == '/example/O\'Example: "the #1"'
    assert load_cv_config(path).employers == ['O\'Example: "the #1"']
    assert load_triage_config(path).accept_titles == ["yes", "#hash", "back\\slash"]

    # The in-situ CONTROL-character arm, restored. `test_onboard_emit.py`'s
    # `test_a_control_character_survives_the_whole_config_render` used to drive its CONTROLS
    # corpus through `build_plan(...).config_text` and the real loader from the config side;
    # it was retargeted onto the Candidate Profile note for #133/#107 (identity moved to the
    # vault, so the old `cv_contact` question this test drove no longer exists -- see that
    # file's own comment on the retarget). That left only a `scalar()`-in-isolation unit test
    # (`test_control_characters_round_trip_rather_than_breaking_the_file`) exercising the
    # CONTROLS corpus at all -- nothing any longer proved a control character survives
    # `build_plan`'s REAL rendered text through the REAL loader. `\x0b`/`\x0c` are the two
    # `test_onboard_emit.py`'s CONTROLS corpus names as routine copy-paste artefacts (out of a
    # CV or a PDF); driven here through both a root scalar (`vault_dir`) and a nested sub-app
    # list (`cv_employers`), the same two shapes the NASTY-corpus assertions above already
    # cover, without reintroducing a retired `cv.name`/`cv.contact` key.
    # Its OWN directory, so its config file is a DIFFERENT file from the nasty-answer one
    # above. `_written` always writes `<dir>/config.yaml`, so a shared `tmp_path` made
    # `ctrl_path == path` and the second call silently overwrote the first: the assertions
    # above pass only because they run before this line, and adding an assertion after it
    # -- or reordering the two blocks -- would check this control-character config against
    # the nasty-answer expectations while staying green.
    ctrl_dir = tmp_path / "control-characters"
    ctrl_dir.mkdir()
    ctrl_path = _written(ctrl_dir, {"vault_dir": "/example/a\x0bb\x0cc",
                                    "cv_employers": ["a\x0bb"]})
    assert ctrl_path != path, "each config must be its own file, not one overwritten twice"
    assert load_config(ctrl_path).vault_dir == "/example/a\x0bb\x0cc"
    assert load_cv_config(ctrl_path).employers == ["a\x0bb"]


def test_every_key_renders_below_its_own_section_header_in_its_own_block(tmp_path):
    """POSITIONAL, because a count cannot falsify the defect this replaces.

    The old assertion was `text.count("-- Want") == 1`, satisfied identically by a header attached
    to its keys and by one attached to none of them. Measured, it was the latter: `-- Want` sat at
    root above `lead_ttl_days` alone while the six triage gates it describes rendered ~40 lines
    below, unlabelled -- and that blurb ("an unset gate passes every lead through") is the abstain
    doctrine's only appearance next to a gate. `-- Providers` likewise carried "API keys come from
    the environment, never this file" into `cv:` only."""
    text = _plan(tmp_path).config_text
    lines = text.splitlines()

    def block_of(i):
        """The YAML block a line belongs to: the nearest `foo:`/`# foo:` header above it."""
        for j in range(i, -1, -1):
            m = re.match(r"^#?\s*([a-z_]+):\s*$", lines[j])
            if m and not lines[j].startswith(("  ", "# -")):
                return m.group(1)
        return ""

    for q in catalogue():
        if not q.section:
            continue
        for dotted in q.writes_to:
            leaf = dotted.split(".")[-1]
            key_at = next(i for i, ln in enumerate(lines)
                          if re.match(rf"^\s*#?\s*{re.escape(leaf)}:", ln)
                          and block_of(i) == (dotted.split(".")[0] if "." in dotted else ""))
            header_at = [i for i, ln in enumerate(lines)
                         if f"-- {q.section} " in ln and block_of(i) == block_of(key_at)]
            assert header_at, f"{dotted} renders with no '{q.section}' header in its own block"
            assert min(header_at) < key_at, f"{dotted} renders ABOVE its own section header"


def test_the_safety_blurbs_reach_every_block_they_govern(tmp_path):
    """The two blurbs that carry safety information, named explicitly: they are the reason the
    positional test above exists rather than a tidier count."""
    text = _plan(tmp_path).config_text
    assert text.count("-- Want") == 2, "root (lead_ttl_days) and triage (the six gates)"
    assert text.count("-- Providers") == 3, "cv, triage and track each take a backend"
    assert text.count("API keys come from the environment") == 3


def test_a_fan_out_question_still_emits_its_header_once_per_block(tmp_path):
    """What the hoist was originally added for: ONE question writing three blocks must not emit
    three headers in a single block."""
    lines = _plan(tmp_path).config_text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln == "cv:")
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^[a-z_]+:\s*$", lines[i])), len(lines))
    assert end > start + 1, "the cv: block rendered empty"        # SCOPE
    assert sum("-- Providers" in ln for ln in lines[start + 1:end]) == 1


def test_notes_explain_what_a_configured_gate_will_do(tmp_path):
    notes = "\n".join(_plan(tmp_path, {"relevance_keep": ["example role"]}).notes)
    assert "example role" in notes and "dropped before triage" in notes


def test_an_unanswered_run_reports_no_gates(tmp_path):
    assert not any("keep ONLY" in n for n in _plan(tmp_path).notes)
