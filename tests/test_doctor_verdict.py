"""`doctor`'s default view: the capability verdict, the SETUP state, and the exit code (#243).

Before this, a fresh install printed a screenful of rows across four states, several of them
`dead`, and exited 1 -- on the very command `job-sluice init` tells a new user to run next. The README
spent a paragraph reassuring the reader that this was expected, which is prose doing a job
the output should do, and a setup script wiring `doctor` in got a failure on the happy path.

Two changes, tested here: a fifth state (`setup`) for a thing the user has not supplied yet,
which never reaches the exit code; and a short verdict by default, with the full table demoted
to `--verbose` rather than deleted.
"""
import ast
import os

import pytest

from sluice.core.doctor import (
    ALL_CAPABILITIES, CAPABILITIES, DEAD, DEGRADED, NOTICE, OK, SETUP, BackendCheck,
    BackendTarget, ComponentCheck, DoctorReport, RoleUse, classify_store,
)

_SLUICE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sluice")


def _backend_spec_subapps():
    """Every sub-app named in `enumerate_targets`' specs, from the source.

    Matched on the tuple's SHAPE -- (subapp, role, ...) where role is one of the two real
    role names -- rather than on a hand-listed set of sub-app names, which is what a sweep
    meant to catch a NEW sub-app must not depend on.
    """
    with open(os.path.join(_SLUICE, "core", "doctor.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "enumerate_targets")
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Tuple) or len(node.elts) < 2:
            continue
        a, b = node.elts[0], node.elts[1]
        if (isinstance(a, ast.Constant) and isinstance(a.value, str)
                and isinstance(b, ast.Constant) and b.value in ("primary", "fallback")):
            out.add(a.value)
    return out


# ── the roster is hand-written; nothing about its correctness is ──────────────
def test_a_row_cannot_block_a_capability_the_verdict_has_never_heard_of():
    """The membership half, enforced at construction (#243).

    `blocks` stopped being decoration when `verdict()` began reading it: an unrecognised
    name is silently dropped there, so the row keeps printing `blocks: <name>` under
    `--verbose` while the verdict calls that capability ready. This replaced a sweep of
    `core/doctor.py`'s source, which could not see rows minted in `core/app.py` and could
    not see a `blocks=` written as a NAME rather than a tuple of literals.
    """
    with pytest.raises(ValueError) as ei:
        ComponentCheck("store", "x", DEAD, "d", blocks=("sixthapp",))
    assert "sixthapp" in str(ei.value)
    assert "ingest" in str(ei.value), "the refusal must list the valid names"
    # A partial match is still a mistake, and an empty `blocks` is still legal -- the
    # unreadable `stories` corpus carries it, because nothing reads that corpus.
    with pytest.raises(ValueError):
        ComponentCheck("store", "x", DEAD, "d", blocks=("cv", "nope"))
    assert ComponentCheck("store", "x", DEAD, "d").blocks == ()


def test_every_backend_spec_names_a_labelled_capability():
    """The one direction a source sweep can still settle honestly.

    There is NO reachability guard here any more, and its absence is deliberate. The
    previous one asserted that every label was reachable from some `blocks=` in the tree
    -- and it could not fail: two rows spell their `blocks` as `ALL_CAPABILITIES`, so
    resolving that name made the swept set contain every label by construction and the
    equality was self-satisfying. Measured: adding a `("notify", "send notifications")`
    capability that nothing anywhere can block left the whole file green. A guard that
    cannot fail is worse than no guard, because it reads as coverage.

    Making it bite would mean asserting each label is reachable from a row naming IT, and
    that reds today for `apply` -- correctly, because `apply` has no config-level blocker:
    what stops it is a per-lead missing `tailored_cv`, which is data, not configuration.
    So the property is not enforced, and `docs/ARCHITECTURE.md` says so rather than
    claiming a guard it does not have. What IS enforced, at runtime and on every row
    wherever it is minted, is the other direction: `ComponentCheck.__post_init__` refuses
    a `blocks` naming anything outside the roster.
    """
    specs = _backend_spec_subapps()
    labelled = set(ALL_CAPABILITIES)
    assert specs <= labelled, f"a backend spec names an unlabelled sub-app: {specs - labelled}"
    # Anti-vacuity: `apply` is offline by contract and `ingest` drives a browser, so
    # exactly the three LLM-using sub-apps have backends. An empty or over-wide sweep
    # fails here rather than passing the subset check above.
    assert specs == {"triage", "cv", "track"}


def test_all_capabilities_matches_the_labelled_roster():
    # Deliberately named for what it checks. It compares VALUES, so a hand-listed tuple
    # with identical contents passes -- it cannot verify the word "derived", and an
    # earlier name claiming it did was a promise the assertion does not keep.
    assert ALL_CAPABILITIES == tuple(name for name, _ in CAPABILITIES)


def test_the_default_claude_path_constant_matches_every_shipped_config():
    """`classify` reads SETUP-vs-DEAD off `claude_path == _DEFAULT_CLAUDE_PATH`, so a
    loader changing its default silently turns a typo'd path into "not installed yet"."""
    from sluice.core.doctor import _DEFAULT_CLAUDE_PATH
    from sluice.cv.config import CvConfig
    from sluice.track.config import TrackConfig
    from sluice.triage.config import TriageConfig

    defaults = {TriageConfig().claude_max_path, TrackConfig().claude_max_path,
                CvConfig().compose_claude_path}
    assert defaults == {_DEFAULT_CLAUDE_PATH}


def test_capability_labels_are_distinct_and_non_empty():
    labels = [label for _, label in CAPABILITIES]
    assert all(label.strip() for label in labels)
    assert len(set(labels)) == len(labels), "two capabilities sharing a label is unreadable"


# ── the state model ───────────────────────────────────────────────────────────
def _report(*components, checks=()):
    return DoctorReport(checks=list(checks), components=list(components))


def _target(*roles):
    return BackendTarget(provider="p", model="m", host="", claude_path="claude",
                         uses=[RoleUse(s, r) for s, r in roles])


def test_setup_never_reaches_the_exit_code_even_under_strict():
    """The whole point of #243. `--strict` promotes DEGRADED, deliberately and unchanged;
    it must not also promote SETUP, or the flag would re-create exactly the fresh-install
    failure this issue removed."""
    rep = _report(ComponentCheck("store", "baseline_rel", SETUP, "not supplied", blocks=("cv",)))
    assert rep.exit_code() == 0
    assert rep.exit_code(strict=True) == 0


def test_dead_still_exits_one_and_strict_still_promotes_degraded():
    """The other half of the pair. Without this, #243 is indistinguishable from
    "doctor always exits 0"."""
    assert _report(ComponentCheck("renderer", "cv.renderer", DEAD, "broken")).exit_code() == 1
    degraded = _report(ComponentCheck("store", "Judging Profile", DEGRADED, "missing"))
    assert degraded.exit_code() == 0
    assert degraded.exit_code(strict=True) == 1


def test_notice_still_reaches_nothing():
    rep = _report(ComponentCheck("gates", "x", NOTICE, "abstaining (empty)"))
    assert rep.exit_code() == 0 and rep.exit_code(strict=True) == 0


def test_awaiting_setup_reads_both_row_types_in_report_order():
    backend = BackendCheck(_target(("triage", "primary")), SETUP, "KEY unset")
    rep = _report(
        ComponentCheck("store", "vault_dir", OK, "found"),
        ComponentCheck("store", "baseline_rel", SETUP, "not supplied", blocks=("cv",)),
        checks=[backend],
    )
    assert [c.subject if hasattr(c, "subject") else c.target.provider
            for c in rep.awaiting_setup()] == ["p", "baseline_rel"]


# ── the verdict ───────────────────────────────────────────────────────────────
def test_a_capability_nothing_blocks_is_ready():
    v = _report(ComponentCheck("store", "baseline_rel", SETUP, "x", blocks=("cv",))).verdict()
    assert "tailored CVs" in v.setup
    for label in ("scrape job boards", "triage leads", "send applications", "track replies"):
        assert label in v.ready
    assert v.broken == []


def test_a_dead_row_moves_its_capability_out_of_setup():
    """Worst-wins. Four unsupplied rows and one broken one on the same capability must
    report BROKEN: a user told "just set this up" about a capability that is actually
    misconfigured will keep supplying things that cannot help."""
    v = _report(
        ComponentCheck("store", "baseline_rel", SETUP, "x", blocks=("cv",)),
        ComponentCheck("renderer", "cv.renderer", DEAD, "unknown renderer", blocks=("cv",)),
    ).verdict()
    assert v.broken == ["tailored CVs"]
    assert "tailored CVs" not in v.setup and "tailored CVs" not in v.ready


def test_an_ok_row_never_blocks_and_a_blocks_less_degraded_row_never_blocks():
    """The sanctioned degrade must stay out of the verdict entirely.

    A keyless fallback is `auto` running primary-only, and `classify`'s DEGRADED row for
    it carries no `blocks` -- so counting DEGRADED unconditionally would put every default
    install's triage into a to-do bucket. The real `Judging Profile` row is the same
    shape: DEGRADED, no `blocks`, because triage falls back to the shipped neutral
    criteria rather than stopping.
    """
    v = _report(
        ComponentCheck("store", "Judging Profile", DEGRADED, "missing"),
        ComponentCheck("store", "vault_dir", OK, "found", blocks=("cv",)),
    ).verdict()
    assert v.setup == [] and v.broken == [] and v.degraded == []
    assert "triage leads" in v.ready and "tailored CVs" in v.ready
    assert v.degraded_blocking_rows == []


def test_a_degraded_row_that_names_a_capability_does_block_it():
    """`blocks` means the same thing on a DEGRADED row as on any other, and reading it on
    only two of the five states was measurably wrong: `classify_camofox`'s `CAMOFOX_USER`
    mismatch -- the 2026-08-15 incident where a run drove the wrong cookie profile and a
    board returned zero rows for days -- carries `blocks=("ingest",)`, and the verdict
    printed `Ready now: scrape job boards` directly above a `--verbose` row saying
    `blocks: ingest`, with the remedy shown nowhere.
    """
    from sluice.core.doctor import classify_camofox

    row = classify_camofox(resolved_user="scanner", session_env="other", user_env="",
                           probe_capable_sources=set())
    assert row.state == DEGRADED and row.blocks == ("ingest",), "fixture drifted"
    v = _report(row).verdict()
    assert v.degraded == ["scrape job boards"]
    assert "scrape job boards" not in v.ready
    assert v.degraded_blocking_rows == [row]


def test_a_dead_blocker_outranks_a_degraded_one_and_degraded_outranks_setup():
    """Worst-of, over the full ladder. Ordering DEGRADED above SETUP is deliberate: an
    unsupplied thing does not run and says so, while a misconfigured one runs and quietly
    does the wrong thing."""
    setup_row = ComponentCheck("store", "a", SETUP, "x", blocks=("cv",))
    degraded_row = ComponentCheck("store", "b", DEGRADED, "x", blocks=("cv",))
    dead_row = ComponentCheck("store", "c", DEAD, "x", blocks=("cv",))
    assert _report(setup_row, degraded_row).verdict().degraded == ["tailored CVs"]
    assert _report(setup_row, degraded_row, dead_row).verdict().broken == ["tailored CVs"]
    assert _report(setup_row).verdict().setup == ["tailored CVs"]


def test_a_backend_blocks_only_where_it_is_the_primary():
    """A shared target that is triage's primary and cv's fallback, with its key unset,
    stops triage and merely degrades cv -- which is what `Sluice.backend()`'s `auto` role
    does at runtime. Reporting it as blocking cv would overstate the damage on the
    commonest multi-sub-app config there is."""
    shared = BackendCheck(_target(("triage", "primary"), ("cv", "fallback")), SETUP, "KEY unset")
    v = _report(checks=[shared]).verdict()
    assert "triage leads" in v.setup
    assert "tailored CVs" in v.ready


def test_a_healthy_backend_blocks_nothing_even_as_primary():
    v = _report(checks=[BackendCheck(_target(("triage", "primary")), OK, "ok")]).verdict()
    assert v.setup == [] and v.broken == []


def test_verdict_carries_the_rows_behind_each_bucket():
    """The printer needs the remedies, and must not re-derive which rows mattered --
    a second derivation is a second opinion, and the two drift."""
    setup_row = ComponentCheck("store", "baseline_rel", SETUP, "add your CV", blocks=("cv",))
    dead_row = ComponentCheck("renderer", "cv.renderer", DEAD, "unknown renderer", blocks=("cv",))
    v = _report(setup_row, dead_row).verdict()
    assert v.setup_rows == [setup_row]
    assert v.broken_rows == [dead_row]


def test_the_verdict_and_the_table_can_never_disagree_about_a_row():
    """`verdict()` reads the classifiers rather than re-classifying, so every row it puts
    in a bucket is a row `--verbose` prints in that same state. Swept over a real
    `classify_store` result rather than hand-built rows."""
    rows = classify_store({
        "vault_exists": True, "baseline_exists": False, "criteria_present": True,
        # The unsupplied arm: a default `baseline_rel` nobody has written yet. Absent, this
        # fact takes the louder DEAD reading (#243), which would make this fixture produce
        # a broken row and assert about the wrong thing.
        "baseline_rel_is_default": True,
        "experience_total": 0, "experience_verified": 0,
        "skills_total": 0, "skills_verified": 0,
        "stories_total": 0, "stories_verified": 0,
        "candidate_name_present": False, "candidate_contact_present": False,
    })
    rep = _report(*rows)
    v = rep.verdict()
    assert v.setup_rows, "the fixture must produce at least one unsupplied row"
    for row in v.setup_rows:
        assert row.state == SETUP
        assert row in rep.components
    # `all([])` is True, so this fixture -- which produces no DEAD row -- cannot say
    # anything about `broken_rows` by iterating it. Assert the emptiness it actually has,
    # and cover the populated case on a report that HAS one.
    assert v.broken_rows == []
    dead = ComponentCheck("renderer", "cv.renderer", DEAD, "unknown renderer", blocks=("cv",))
    v2 = _report(*(list(rows) + [dead])).verdict()
    assert v2.broken_rows == [dead] and all(c.state == DEAD for c in v2.broken_rows)


# ── the CLI ───────────────────────────────────────────────────────────────────
@pytest.fixture
def _fresh_cli(monkeypatch):
    """The shape a real install has immediately after `job-sluice init`: a `claude` CLI on
    PATH, no fallback key, and a vault directory that EXISTS.

    The vault is load-bearing, not incidental. `tests/conftest.py` points `VAULT_DIR` at a
    per-test path and does not create it, and since #243 a vault the user NAMED and that
    is not there is DEAD (it has moved or been deleted) rather than SETUP -- so without
    this `mkdir` these rows would be asserting the exit code of a broken install while
    claiming to describe a fresh one.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    os.makedirs(os.environ["VAULT_DIR"], exist_ok=True)


def test_the_default_view_prints_the_verdict_and_not_the_table(_fresh_cli, capsys):
    from sluice.cli import main

    rc = main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Needs setup:" in out
    assert "Nothing is broken." in out
    # The table's own vocabulary must be absent, or "demoted to --verbose" is a claim
    # about a flag that changes nothing. `notice` is the state 18 rows carry on a fresh
    # install and is the loudest thing the old default printed.
    assert "notice" not in out
    assert "abstaining (empty)" not in out


def test_verbose_still_prints_every_row(_fresh_cli, capsys):
    from sluice.cli import main

    rc = main(["doctor", "--offline", "--verbose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "notice" in out and "abstaining (empty)" in out
    assert "Needs setup:" not in out, "the verdict is the default view, not a header on both"


def test_the_verbose_summary_counts_the_setup_state(_fresh_cli, capsys):
    """A fifth state that no summary line counts is a state a `--verbose` reader cannot
    see the size of -- the rows would be there and the totals would not add up."""
    from sluice.cli import main

    main(["doctor", "--offline", "--verbose"])
    out = capsys.readouterr().out
    assert "setup," in out or out.rstrip().endswith("setup")
    assert "dead, " in out and "notice" in out


def test_a_broken_row_says_so_and_exits_one(monkeypatch, capsys):
    """The message and the exit code must agree. A run printing "Nothing is broken" while
    exiting 1 (or the reverse) is worse than either alone, and the two are produced by
    different expressions -- `v.broken_rows` and `exit_code()` -- so nothing but a test
    holds them together."""
    from sluice.cli import main

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    # The vault must EXIST, or its own row blocks every capability and this test's
    # "Broken: tailored CVs" assertion passes for the wrong reason -- it would be
    # asserting about the vault while claiming to be about the renderer.
    os.makedirs(os.environ["VAULT_DIR"], exist_ok=True)
    monkeypatch.setattr("sluice.cv.config.load_cv_config",
                        _cv_config_with_unknown_renderer())
    rc = main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Nothing is broken." not in out
    assert "Something you configured is not working" in out
    # The BUCKET line and the ROW heading are distinct strings on purpose, so assert the
    # one this test means. `"Broken:" in out` matched either and could not tell them apart.
    assert "Broken:       tailored CVs" in out, "the capability bucket line"
    assert "\nNot working:\n" in out, "the row list heading"
    assert "\nBroken:\n" not in out, "the row heading must not reuse the bucket label"


def _cv_config_with_unknown_renderer():
    import dataclasses

    from sluice.cv.config import load_cv_config

    cfg = dataclasses.replace(load_cv_config(), renderer="nonesuch")
    return lambda: cfg


def test_strict_shows_the_rows_it_fails_on_and_never_says_nothing_is_broken(
        _fresh_cli, capsys):
    """The footer is a statement about the exit code, so the two must agree.

    A `--strict` run on a fresh install exits 1 on a keyless fallback and nothing is DEAD.
    The first cut of this view keyed its closing line on `broken_rows` alone, so it printed
    "Nothing is broken." and exited 1 -- each half true in isolation, together a
    contradiction on the one command whose job is to tell you where you stand. Worse, the
    rows deciding that exit code were the ones the view did not print.
    """
    from sluice.cli import main

    rc = main(["doctor", "--offline", "--strict"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Nothing is broken." not in out
    assert "--strict fails on the degraded rows above" in out
    # ...and the rows are actually there to be read.
    assert "\nWorking, but not properly:\n" in out
    assert "DEEPSEEK_API_KEY" in out


def test_a_plain_run_points_at_the_degraded_rows_without_listing_them(_fresh_cli, capsys):
    """A keyless fallback is a sanctioned degrade -- `auto` runs primary-only -- so a plain
    run must not present it as a task. It must not hide that it exists either: `--strict`
    would fail on it, and a user who never sees it cannot know why."""
    from sluice.cli import main

    rc = main(["doctor", "--offline"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Nothing is broken." in out
    assert "which --strict also fails on" in out
    assert "\nWorking, but not properly:\n" not in out


def test_a_clean_install_says_nothing_is_broken_with_no_strict_footnote(capsys):
    """The footnote appears only when there is something for it to be about."""
    from sluice.cli import _print_doctor_verdict

    _print_doctor_verdict(
        _report(ComponentCheck("store", "baseline_rel", SETUP, "x", blocks=("cv",))),
        offline=True, strict=False, exit_code=0)
    out = capsys.readouterr().out
    assert "Nothing is broken." in out
    assert "--strict" not in out.split("Nothing is broken.")[1]


# ── the renderer fork, through the REAL wiring rather than the pure classifier ─
def _doctor_with_renderer_error(monkeypatch, exc):
    """Drive `Sluice.doctor` with a renderer whose construction raises `exc`.

    Through the real `core/app.py` wiring on purpose. Every earlier test of this fork
    passed `missing_dependency=` straight to the pure classifier, so the expression that
    DERIVES it had no coverage at all -- which is how the case that actually fires in the
    field shipped misclassified.
    """
    from sluice.core.app import Sluice

    def _raise(self, cvcfg=None):
        raise exc
    monkeypatch.setattr(Sluice, "renderer", _raise)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    os.makedirs(os.environ["VAULT_DIR"], exist_ok=True)
    report = Sluice().doctor(offline=True)
    return {c.subject: c for c in report.components}["cv.renderer"], report


def test_an_uninstalled_render_dependency_is_setup_and_exits_zero(monkeypatch):
    from sluice.core.protocols import RenderDependencyError

    row, report = _doctor_with_renderer_error(
        monkeypatch, RenderDependencyError("could not load its rendering backend"))
    assert row.state == SETUP
    assert report.exit_code() == 0


def test_a_missing_native_library_is_setup_too_not_only_a_missing_python_package(
        monkeypatch):
    """The case `renderers/template.py` calls "the single likeliest real failure".

    Importing weasyprint with the extra installed but cairo/pango absent raises OSError,
    not ImportError -- the documented macOS `pip install` without
    `DYLD_FALLBACK_LIBRARY_PATH`. The first cut of #243 asked `isinstance(e.__cause__,
    ImportError)`, so that machine got exit 1 under a heading saying something it had
    configured was broken, on a row whose entire remedy is `pip install`. Raised here
    with the real cause shape rather than a bare exception, so this row would still be
    honest if the classification ever went back to inspecting `__cause__`.
    """
    from sluice.core.protocols import RenderDependencyError

    try:
        raise OSError("cannot load library 'libgobject-2.0-0'")
    except OSError as cause:
        exc = RenderDependencyError("could not load its rendering backend")
        exc.__cause__ = cause
    row, report = _doctor_with_renderer_error(monkeypatch, exc)
    assert row.state == SETUP, "a native library the user has not installed is a setup step"
    assert report.exit_code() == 0


def test_a_renderer_the_user_misconfigured_stays_dead_and_exits_one(monkeypatch):
    """The other side. A `cv.template` that is not a file, a template that is not valid
    Jinja2, and a `cv.render_script` that does not exist are all plain `RenderError` --
    things the user configured that do not work -- and must keep failing the exit code.
    Without this row the fix above is indistinguishable from "the renderer never fails"."""
    from sluice.core.protocols import RenderError

    row, report = _doctor_with_renderer_error(
        monkeypatch, RenderError("renderer 'template': cv.template is not a file: '/nope.j2'"))
    assert row.state == DEAD
    assert report.exit_code() == 1


def test_a_renderer_raising_without_a_from_clause_is_not_silently_reclassified(monkeypatch):
    """Why the seam DECLARES this rather than doctor inferring it.

    A renderer raising `RenderError` from inside an `except` without a `from` clause gets
    `__cause__ = None` (implicit chaining sets `__context__`), so a cause-sniffing
    classifier calls it broken however plainly its message says "not installed" --
    The shape is built here rather than cited from a shipped renderer: neither shipped
    renderer is written that way, and the point is what the seam must not hand a future
    one. Declared, it is simply DEAD
    because it did not claim otherwise, which is the safe reading rather than an accident.
    """
    from sluice.core.protocols import RenderError

    # RAISED inside the `except`, not merely constructed there: implicit chaining is set
    # by the raise statement, so building the object alone leaves both attributes None and
    # the fixture would not have the shape it claims to.
    try:
        try:
            raise ImportError("no module named 'somedep'")
        except ImportError:
            raise RenderError("renderer 'other': could not load its rendering backend")
    except RenderError as caught:
        exc = caught
    assert exc.__cause__ is None and exc.__context__ is not None
    row, _ = _doctor_with_renderer_error(monkeypatch, exc)
    assert row.state == DEAD


# ── the CLI-missing fork must not depend on which mode you ran ────────────────
def test_a_missing_claude_cli_is_setup_in_both_modes_not_only_offline(monkeypatch):
    """`--offline` and a live run must agree about the same fact (#243).

    While `cli_present` was computed only under `--offline`, they did not: offline said
    "CLI not on PATH" and called it SETUP, while a live run skipped the check, attempted
    the probe anyway, and reported the failure as `probe_error` -- DEAD, exit 1. So a
    fresh install with no `claude` got "Broken: triage leads, tailored CVs, track
    replies" from plain `job-sluice doctor`, the exact experience #243 removes, and
    `--offline` was the only invocation telling the truth.
    """
    from sluice.core.app import Sluice

    monkeypatch.setattr("shutil.which", lambda name: None)
    os.makedirs(os.environ["VAULT_DIR"], exist_ok=True)
    # `probe=` INJECTED, and not optional. `Sluice.doctor` defaults it to a real
    # `b.complete(...)`, and `tests/conftest.py` does not scrub `DEEPSEEK_API_KEY` --
    # which the shipped `fallback_backend` uses -- so with a key exported in the ambient
    # environment the `offline=False` leg opened a real outbound connection. The DNS guard
    # turns that into a hard failure rather than a request, but a test whose outcome
    # depends on whether a developer has a key exported is not hermetic either way. Every
    # other live-mode test in `tests/test_doctor.py` injects a probe; this one was the
    # exception. It also witnesses the probe-skip guard: the claude-max target is
    # classified from `cli_present` alone, so nothing may probe it.
    probed = []
    for offline in (True, False):
        report = Sluice().doctor(offline=offline, probe=lambda b: probed.append(b))
        claude = [c for c in report.checks if c.target.provider == "claude-max"]
        assert claude, "fixture drifted: no claude-max target"
        assert all(c.state == SETUP for c in claude), f"offline={offline}"
        assert "triage leads" in report.verdict().setup, f"offline={offline}"
    assert probed == [], (
        "a backend whose CLI is already known to be absent must not be probed -- the "
        "subprocess cannot succeed, and on a machine that does have `claude` installed "
        "it would be a live Claude Code invocation")


def test_a_claude_path_the_user_named_and_that_is_absent_stays_dead(monkeypatch):
    """SETUP is for the shipped bare `claude`. A path the user typed and that is not
    there is something they supplied that does not work, and a cron `doctor --strict`
    must keep firing on it."""
    import dataclasses

    from sluice.core.app import Sluice
    from sluice.triage.config import load_triage_config

    cfg = dataclasses.replace(load_triage_config(), primary_backend="claude-max",
                              claude_max_path="/opt/typo/claude")
    monkeypatch.setattr("sluice.triage.config.load_triage_config", lambda: cfg)
    monkeypatch.setattr("shutil.which", lambda name: None)
    os.makedirs(os.environ["VAULT_DIR"], exist_ok=True)
    report = Sluice().doctor(offline=True)
    named = [c for c in report.checks if c.target.claude_path == "/opt/typo/claude"]
    assert named and all(c.state == DEAD for c in named)
    assert report.exit_code() == 1


def test_a_fallback_whose_cli_is_absent_is_degraded_so_strict_still_fires():
    """A fallback that cannot run is a DEGRADE, whatever the reason it cannot run.

    Without this arm the two spellings of one fact got opposite `--strict` verdicts: a
    keyless per-token fallback failed the build while a claude-max fallback whose binary
    is simply absent passed it. That is exactly the silently-non-functional fallback
    `--strict` exists to catch -- the one you believe in and never test until the primary
    dies. Measured before the fix: `setup`, and `exit_code(strict=True) == 0`.
    """
    fallback = BackendCheck(_target(("triage", "fallback")), None, "")
    from sluice.core.doctor import classify

    c = classify(fallback.target, known=True, needs_key=False, key_present=False,
                 key_var="", cli_present=False, offline=True, probe_error=None)
    assert c.state == DEGRADED
    assert "primary-only" in c.detail
    assert _report(checks=[c]).exit_code(strict=True) == 1
    assert _report(checks=[c]).exit_code() == 0


def test_the_same_facts_on_a_primary_are_setup_not_degraded():
    """The mirror, so the two arms cannot be collapsed into one. Identical inputs except
    the ROLE: a primary that cannot run is the fresh-install case and exits 0 even under
    `--strict`; a fallback that cannot run fails a strict build."""
    from sluice.core.doctor import classify

    c = classify(_target(("triage", "primary")), known=True, needs_key=False,
                 key_present=False, key_var="", cli_present=False, offline=True,
                 probe_error=None)
    assert c.state == SETUP
    assert _report(checks=[c]).exit_code(strict=True) == 0


def test_an_unconfigured_vault_at_the_shipped_default_is_setup_and_exits_zero(
        monkeypatch, tmp_path):
    """The fresh-install direction of the explicit-vs-default rule -- #243's whole point,
    and the one arm no test could reach.

    `tests/conftest.py` sets `VAULT_DIR` for every test, so nothing ever constructed a
    `Vault` at its shipped default: measured, hardcoding `_dir_is_default = False` (which
    turns every unconfigured install into "your vault has moved", exit 1) left the entire
    suite green. Reaching it needs the env var GONE and a cwd with no `./vault`.
    """
    from sluice.core.app import Sluice
    from sluice.core.vault import Vault

    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert Vault().preflight()["vault_dir_is_default"] is True

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    report = Sluice().doctor(offline=True)
    row = {c.subject: c for c in report.components}["vault_dir"]
    assert row.state == SETUP
    assert report.exit_code() == 0
    assert report.verdict().ready == [], "nothing runs without a vault, however benignly"
    assert "job-sluice init" in row.detail, "the first row a pre-init user sees must say what to do"


def test_a_vault_the_user_named_and_that_is_gone_is_dead_and_exits_one(
        monkeypatch, tmp_path):
    """The mirror of the row above, through the real `Vault`, so the two directions of
    `vault_dir_is_default` are both pinned rather than only the loud one."""
    from sluice.core.app import Sluice
    from sluice.core.vault import Vault

    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "gone"))
    monkeypatch.chdir(tmp_path)
    assert Vault().preflight()["vault_dir_is_default"] is False

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    report = Sluice().doctor(offline=True)
    assert {c.subject: c for c in report.components}["vault_dir"].state == DEAD
    assert report.exit_code() == 1


def test_a_baseline_the_user_named_and_that_is_gone_is_dead_and_exits_one(
        monkeypatch, tmp_path):
    """The `baseline_rel` arm of the same rule, unreachable through the hand-built facts
    dicts the other tests use. A user who set `baseline_rel` told sluice where their CV
    IS; if it is not there they renamed or moved it, every `cv run` refuses before any
    spend, and `doctor` -- the command they run to find out why -- must not answer
    "Nothing is broken." Measured before the fix: `setup`, exit 0.
    """
    from sluice.core.app import Sluice
    from sluice.core.vault import Vault

    vault_dir = tmp_path / "vault"
    (vault_dir / "My CV").mkdir(parents=True)
    monkeypatch.setenv("VAULT_DIR", str(vault_dir))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")

    named = Vault(str(vault_dir), baseline_rel="My CV/renamed.md")
    assert named.preflight()["baseline_rel_is_default"] is False
    monkeypatch.setattr(Sluice, "store", lambda self: named)
    report = Sluice().doctor(offline=True)
    assert {c.subject: c for c in report.components}["baseline_rel"].state == DEAD
    assert report.exit_code() == 1

    # ...and the shipped default, absent, stays the quiet unsupplied case.
    monkeypatch.setattr(Sluice, "store", lambda self: Vault(str(vault_dir)))
    report = Sluice().doctor(offline=True)
    assert {c.subject: c for c in report.components}["baseline_rel"].state == SETUP
    assert report.exit_code() == 0


def test_a_blocking_degraded_row_prints_without_strict(capsys):
    """The mirror of `test_a_plain_run_points_at_the_degraded_rows_without_listing_them`.

    That row pins that a NON-blocking degraded row stays out of the default view; nothing
    pinned that a BLOCKING one appears in it. Measured: dropping
    `degraded_blocking_rows` from the printer left the capability line standing while the
    row and its remedy vanished -- so the user is told `Degraded: scrape job boards` and
    never told to set `CAMOFOX_USER`.
    """
    from sluice.cli import _print_doctor_verdict
    from sluice.core.doctor import classify_camofox

    row = classify_camofox(resolved_user="scanner", session_env="other", user_env="",
                           probe_capable_sources=set())
    _print_doctor_verdict(_report(row), offline=True, strict=False, exit_code=0)
    out = capsys.readouterr().out
    assert "Degraded:     scrape job boards" in out, "the capability line"
    assert "\nWorking, but not properly:\n" in out, "the row list"
    # Whitespace-collapsed: the detail is wrapped for this view, so the remedy sentence
    # is split across lines and a literal substring match would fail on the formatting
    # rather than on the thing being asserted.
    assert "Set CAMOFOX_USER to the profile you logged in as." in " ".join(out.split()), (
        "the remedy must reach the default view, not only --verbose")


# ── a DEAD row must never leave the headline saying everything is fine ────────
def test_a_store_that_cannot_be_built_reports_no_ready_capabilities(monkeypatch):
    """Measured before the fix: an unbuildable store printed
    `Ready now: scrape job boards, triage leads, send applications, track replies` above a
    row saying the store could not be constructed. Nothing in the pipeline can run without
    a store, so the row now names every capability -- `blocks` is what the verdict reads,
    and a DEAD row that names nothing changes no bucket."""
    from sluice.core import plugins
    from sluice.core.app import Sluice

    def _raise(self):
        raise plugins.UnknownAdapter("store", "nonesuch", ["vault"])
    monkeypatch.setattr(Sluice, "store", _raise)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude")
    report = Sluice().doctor(offline=True)
    v = report.verdict()
    assert v.ready == [], "a store that will not construct stops everything"
    assert set(v.broken) == {label for _, label in CAPABILITIES}
    assert report.exit_code() == 1


# ── `--require`: the signal a monitor points at what it depends on ───────────
def test_require_exits_nonzero_when_a_named_capability_is_not_ready(_fresh_cli, capsys):
    """The answer to what #243 took away from automation.

    `doctor`'s exit code now means "is anything BROKEN", so a cron alert no longer fires
    when an install goes from working to unusable through a `setup`-classified condition
    -- a systemd unit whose PATH lacks `~/.local/bin`, or an API key not exported outside
    an interactive shell. `--require` is the replacement, and it is a SHARPER signal than
    the one it replaces: the old exit 1 fired on a fresh install and on every gap
    indiscriminately, while this fires exactly when the thing you depend on stops working.
    """
    from sluice.cli import main

    assert main(["doctor", "--offline", "--require", "cv"]) == 1
    out = capsys.readouterr().out
    assert "REQUIRED but not ready: tailored CVs (needs setup)" in out


def test_require_is_satisfied_by_a_ready_capability(_fresh_cli, capsys):
    """The other half. Without it, the row above cannot tell "--require works" from
    "--require always fails"."""
    from sluice.cli import main

    assert main(["doctor", "--offline", "--require", "triage"]) == 0
    assert "REQUIRED" not in capsys.readouterr().out


def test_require_fails_on_any_bucket_that_is_not_ready(_fresh_cli):
    """`setup`, `degraded` and `broken` all fail it -- the question is "can I do this",
    and every one of those three answers no. Only READY passes, which is why `cmd_doctor`
    compares against that one constant rather than listing the failing states."""
    from sluice.core.protocols import CAPABILITY_BUCKETS, READY

    assert READY in CAPABILITY_BUCKETS
    assert [b for b in CAPABILITY_BUCKETS if b != READY] == ["needs setup", "degraded", "broken"]


def test_require_fails_through_the_cli_on_a_degraded_capability(_fresh_cli, monkeypatch,
                                                                capsys):
    """The bucket vocabulary above is a claim about constants; this drives the real CLI.

    Without it `--require` was only ever exercised against `ready` and `needs setup`, so
    widening `cmd_doctor`'s `!= READY` to `not in (READY, DEGRADED_CAP)` -- treating a
    quietly-misconfigured capability as good enough -- left the whole suite green. A
    degraded capability is precisely the one a monitor most needs to hear about: it runs,
    and does the wrong thing.
    """
    from sluice.cli import main

    # The real `classify_camofox` DEGRADED row: a session key set with no CAMOFOX_USER,
    # which drives the wrong cookie profile. `tests/conftest.py` scrubs both, so setting
    # one and leaving the other unset is what reaches that arm.
    monkeypatch.setenv("CAMOFOX_SESSION", "some-session")
    monkeypatch.delenv("CAMOFOX_USER", raising=False)
    assert main(["doctor", "--offline", "--require", "ingest"]) == 1
    out = capsys.readouterr().out
    assert "REQUIRED but not ready: scrape job boards (degraded)" in out


def test_require_fails_through_the_cli_on_a_broken_capability(_fresh_cli, monkeypatch,
                                                              capsys):
    """The fourth bucket, likewise driven through the CLI rather than asserted as a
    constant. `--require` must report the bucket it found, so the caller can tell "you
    have not set this up" from "what you configured does not work"."""
    from sluice.cli import main

    monkeypatch.setattr("sluice.cv.config.load_cv_config",
                        _cv_config_with_unknown_renderer())
    assert main(["doctor", "--offline", "--require", "cv"]) == 1
    assert "REQUIRED but not ready: tailored CVs (broken)" in capsys.readouterr().out


def test_require_accepts_both_spellings_and_reports_a_capability_once(_fresh_cli, capsys):
    """A cron line wants `--require triage,cv`; argparse convention is a repeated flag.
    Supporting only one would leave someone passing a string that matches no capability."""
    from sluice.cli import main

    assert main(["doctor", "--offline", "--require", "triage,cv"]) == 1
    comma = capsys.readouterr().out
    assert main(["doctor", "--offline", "--require", "triage", "--require", "cv"]) == 1
    assert capsys.readouterr().out == comma
    assert main(["doctor", "--offline", "--require", "cv,cv"]) == 1
    assert capsys.readouterr().out.count("REQUIRED but not ready") == 1


def test_an_unknown_capability_is_a_usage_error_not_a_failed_requirement(_fresh_cli, capsys):
    """Exit 2, not 1, and the distinction is the point: a monitor must be able to tell
    "I asked for something that does not exist" from "the thing I asked for is down".
    Raised BEFORE the preflight runs, so a typo costs a usage error rather than a full
    check, and it lists the valid names like every other name-keyed lookup here."""
    from sluice.cli import main

    assert main(["doctor", "--offline", "--require", "nonesuch"]) == 2
    err = capsys.readouterr().err
    assert "nonesuch" in err
    for name in ("ingest", "triage", "cv", "apply", "track"):
        assert name in err


def test_require_still_reports_a_broken_install_and_prints_in_the_verbose_view_too(
        _fresh_cli, capsys):
    """`--require` composes with the ordinary exit code rather than replacing it, and its
    answer is the thing the caller ran for -- so it must print whichever view they chose,
    which is why it sits outside the verbose/default branch."""
    from sluice.cli import main

    assert main(["doctor", "--offline", "--verbose", "--require", "cv"]) == 1
    out = capsys.readouterr().out
    assert "REQUIRED but not ready" in out
    assert "abstaining (empty)" in out, "the verbose table is still what was printed"


def test_the_buckets_map_covers_every_capability_and_uses_the_stable_keys(_fresh_cli):
    """`--require` looks up by capability NAME, never by display label: the labels are
    prose, free to be reworded, and a CLI contract keyed on them would break a user's
    alert to improve a sentence."""
    from sluice.core.app import Sluice
    from sluice.core.protocols import ALL_CAPABILITIES, CAPABILITY_BUCKETS

    v = Sluice().doctor(offline=True).verdict()
    assert set(v.buckets) == set(ALL_CAPABILITIES)
    assert all(b in CAPABILITY_BUCKETS for b in v.buckets.values())
    # ...and the buckets agree with the label lists they were built beside.
    labels = dict(CAPABILITIES)
    for name, bucket in v.buckets.items():
        listed = {"ready": v.ready, "needs setup": v.setup,
                  "degraded": v.degraded, "broken": v.broken}[bucket]
        assert labels[name] in listed


# ── the remedy must survive the view ──────────────────────────────────────────
def test_a_long_remedy_wraps_without_breaking_a_url_or_a_command(capsys):
    """The renderer's remedy is ~700 characters of genuine instruction, and at
    `textwrap`'s DEFAULTS this view split the INSTALL.md url at its `#system-` fragment
    and `pip install 'job-sluice[render]'` at its hyphen -- printing a link and a command
    that could not be copied, in the one row whose entire content is "run this, then read
    that". Both `break_long_words` and `break_on_hyphens` are off for that reason; this is
    what reds if either comes back.
    """
    from sluice.cli import _print_doctor_verdict

    detail = ("renderer 'template' could not load its rendering backend: pip install "
              "'job-sluice[render]'. If that extra is ALREADY installed, WeasyPrint "
              "additionally needs its native libraries -- see "
              "https://example.invalid/docs/INSTALL.md#system-libraries-for-pdf-rendering "
              "for the platform-specific install.")
    _print_doctor_verdict(
        _report(ComponentCheck("renderer", "cv.renderer", SETUP, detail, blocks=("cv",))),
        offline=True, strict=False, exit_code=0)
    out = capsys.readouterr().out
    assert "https://example.invalid/docs/INSTALL.md#system-libraries-for-pdf-rendering" in out
    assert "'job-sluice[render]'" in out
    # ...and it IS wrapped, or the assertions above would pass on an unwrapped dump and
    # this test would certify nothing about the wrap it exists to pin.
    body = [ln for ln in out.splitlines() if ln.startswith("      ")]
    assert len(body) > 1, "the detail must be wrapped across lines"
    assert all(len(ln) <= 86 or "https://" in ln or "'job-sluice[render]'" in ln
               for ln in body)


def test_the_renderer_row_states_its_remedy_once(capsys):
    """doctor used to append a generic "...and here is what to do" blurb to every renderer
    error, restating the remedy the error had already given -- one 1,207-character row that
    printed the pip command, the INSTALL.md link and the macOS DYLD note twice each. The
    remedy belongs at the raise site, which knows which of the four failures it is.
    """
    from sluice.core.doctor import classify_renderer

    message = "renderer 'template': cv.template is not a file: '/nope.j2'."
    assert classify_renderer(message).detail == message
    assert classify_renderer(message, missing_dependency=True).detail == message
