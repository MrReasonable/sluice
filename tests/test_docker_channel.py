"""Guards for the Docker channel (#104, PR 4): the Dockerfile, its build context, and the
compose file that wires it up.

WHY THIS ONE PARSES YAML while `tests/test_ci_wiring.py` deliberately does not.

Not because pyyaml is optional -- it is not. `pyproject.toml`'s
`dependencies = ["pyyaml", "tzdata"]` makes it a hard runtime dependency and several modules in
this directory already import it at module scope; the try/except in the config modules is
defensive. Those sibling guards said otherwise until this change corrected them.

And not because they pin "strings" while this pins "structure" -- an earlier version of this
paragraph claimed that, and it is also false: `test_ci_wiring.py` parses `needs: [...]` flow
lists, slices job blocks and asserts step ORDER. That was the second wrong reason given for a
decision that is nonetheless right.

The rule that survives: **parse when the guard needs YAML's OWN semantics** -- quoting,
indentation, anchors, merge keys, long form versus short. Text-match when what is pinned is a
literal the file contains, which is most of what those guards do and which a parse would only
make harder to read. `#170`'s third-patch rule is the tiebreaker for an ambiguous case, and it
is what settled this one: the hand-rolled scanner this replaced needed three patches in three
review rounds -- block scoping, then collecting every entry, then same-indent sequences -- each
closing a YAML shape nobody had thought to ask about, and the third had reproduced the very
fail-open it was written to close.

No real `docker build` runs here. Pulling a base image needs network, which this suite
deliberately does not have; the sequencing spec fixes this as a text check on the Dockerfile
source for exactly that reason. The real build runs in CI's `docker` job instead.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
COMPOSE = ROOT / "docker-compose.yml"
RELEASE_PLEASE = ROOT / ".github" / "workflows" / "release-please.yml"
DEPENDABOT = ROOT / ".github" / "dependabot.yml"

IMAGE = "ghcr.io/mrreasonable/job-sluice"

# The extras baked into the image. An EQUALITY, not a subset probe: a subset check alone is
# satisfied by an empty extraction (`all([])` is True), and the shape this is extracted from --
# a quoted suffix inside a backslash-continued multi-line `RUN` -- is the hardest in the file to
# match. `pip install` only WARNS on an unknown extra rather than failing, so a silently-empty
# or silently-shortened extraction would let the image ship without google/mcp/completion and
# nothing anywhere would be red.
_EXPECTED_EXTRAS = {"render", "google", "mcp", "completion"}


def _uncommented(path: Path) -> str:
    """`path`'s source with whole-line comments removed.

    Load-bearing for the invariant guard below, and measured rather than assumed: the tolerant
    `pip install job-sluice` pattern scores exactly ONE hit on the real Dockerfile, and it is the
    COMMENT that explains the invariant. Stripping comment lines takes it to zero. The tempting
    alternative -- narrowing the pattern to anchor on `RUN`, or to reject a leading `#` -- turns
    the fixture green while discarding the tolerance the sequencing spec mandates, which is the
    worse failure because from the outside it looks identical.
    """
    return _fold_continuations(_strip_comments(path.read_text()))


def _strip_comments(source: str) -> str:
    """The ONE comment-stripping rule, shared by the real-Dockerfile guard and every synthetic
    fixture below.

    Deliberately one function and not two: a fixture that reimplements the rule it is meant to
    exercise can drift away from it, and then the falsify partner passes while the guard it
    claims to witness has changed underneath -- measured in this repo as a partner that was red
    while its guard stayed green, because the two built their inputs differently.
    """
    return "\n".join(ln for ln in source.splitlines()
                     if not ln.lstrip().startswith("#"))


def _fold_continuations(source: str) -> str:
    """Join backslash-continued lines into one, AFTER comment lines are removed -- the order
    Docker's own parser uses, so a comment sitting inside a continuation is dropped rather than
    welded into the command.

    Without this the invariant guard cannot see the single likeliest spelling of the thing it
    forbids. Measured: `RUN pip install --no-cache-dir \\` + newline + `job-sluice[render]` did
    NOT match, because the pattern's `[^\n]*?` stops dead at the newline -- and every `RUN` in
    this repo's own Dockerfile is written with exactly that continuation idiom. The guard was
    blind in the direction that fails GREEN.
    """
    return re.sub(r"\\[ \t]*\n[ \t]*", " ", source)


# A pip install naming the BARE distribution, with no local wheel or directory path --
# regardless of interposed flags, quoting, an extras suffix, or a line continuation.
#
# `[^\n;&|]*?` and not `[^\n]*?`: folding continuations (below) deliberately removes the newline,
# which also removed the only thing bounding this match to ONE command. Measured after the fold
# landed: `RUN pip install ./dist/x.whl \\` + `&& job-sluice --version` matched, and so did an
# `&& echo "installed job-sluice"` -- both correct code, both would have failed the build. A
# shell separator ends the command, so it must end the match too. That is a repair introducing
# its own defect, in the direction that fails LOUD rather than green, which is why it survived
# one round.
#
# `job[-_.]+sluice` under IGNORECASE, not `job-sluice`: PEP 503 normalises a project name by
# lowercasing it and collapsing any run of `-`, `_` and `.` to a single `-`, so `job_sluice`,
# `job.sluice`, `job__sluice` and `Job-Sluice` all install PRECISELY the forbidden thing while
# reading as different strings. The wheel FILENAME is `job_sluice-...whl`, which is why the
# lookarounds matter -- they are what still lets those spellings through when part of a path.
#
# `(?:-\S+\s+)*` between `pip` and `install` because global options may sit there
# (`pip -q install ...`, `pip --no-cache-dir install ...`); without it the guard sees no
# `pip install` at all and passes.
#
# Deliberately not a fixed contiguous literal: the sequencing spec names
# `pip install --no-cache-dir job-sluice` as the ordinary phrasing a literal would silently
# miss. The negative lookbehind/lookahead are what keep `/tmp/wheels/job_sluice-1.0.0.whl` and
# `./job-sluice/...` from matching -- a path-borne name is the ALLOWED case, and the whole point
# of the guard is to tell it apart from an index-borne one.
_PYPI_INSTALL = re.compile(
    r"""pip3?\s+(?:-\S+\s+)*install\b[^\n;&|]*?"""
    r"""(?<![\w./'"-])['"]?job[-_.]+sluice(?:\[[^\]]*\])?['"]?(?![\w./-])""",
    re.IGNORECASE,
)


def _installs_from_pypi(source: str) -> bool:
    return bool(_PYPI_INSTALL.search(source))


# ── the hard invariant ───────────────────────────────────────────────────────
#
# The Dockerfile installs the wheel the release workflow's `build` job produced, never
# `pip install job-sluice` from PyPI. The latter RACES the `pypi` job in the same release: it
# would either fail outright (the version is not on the index yet) or, worse, silently install
# the PREVIOUS release and ship it under this release's tag.


def test_the_dockerfile_never_installs_the_published_package():
    assert not _installs_from_pypi(_uncommented(DOCKERFILE)), (
        "the Dockerfile installs job-sluice from an index. It must install the wheel from the "
        "build context's dist/, which is the artefact the release workflow built, attested and "
        "published -- installing from PyPI races the `pypi` job in the same release"
    )


def test_the_dockerfile_installs_a_local_wheel():
    """The positive half. Without it the guard above is satisfied by a Dockerfile that
    installs nothing at all -- an image with no application in it, passing a test whose name
    says the install is correct."""
    source = _uncommented(DOCKERFILE)
    assert re.search(r"pip\s+install[^\n]*\$\{?whl", source), (
        "the Dockerfile no longer installs a wheel from a local path; the negative guard "
        "beside this one would then pass vacuously"
    )
    assert re.search(r"^\s*COPY\s+dist/\*\.whl\s", source, re.MULTILINE), (
        "the Dockerfile must COPY the wheel from dist/ -- the `docker` job downloads the "
        "build artifact to exactly that path, and .dockerignore re-includes exactly that glob"
    )


def test_the_pypi_install_guard_catches_an_interposed_flag():
    """The evasion the sequencing spec names by hand: ordinary phrasing that a fixed
    contiguous literal would sail straight past."""
    assert _installs_from_pypi('RUN pip install --no-cache-dir job-sluice')


def test_the_pypi_install_guard_crosses_a_line_continuation(tmp_path):
    """The likeliest real spelling, and the one the first version of this guard could not see.
    Every `RUN` in this repo's Dockerfile is written with this idiom.

    It goes through `_uncommented` on a real FILE rather than calling the two helpers directly,
    and that is the whole point of the fixture. Composed by hand, this test passed even with
    `_uncommented` reverted to skip the fold -- it was exercising the helpers, not the call site
    the guard actually uses, which is this repo's recorded "#170" defect: when a fix is one call
    site, the test has to exercise THAT call site or it reproduces the bug one level up."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.13-slim\n"
        "# a comment that mentions pip install job-sluice and must stay invisible\n"
        "RUN pip install --no-cache-dir \\\n"
        "      job-sluice[render,google]\n"
    )
    assert _installs_from_pypi(_uncommented(dockerfile))


def test_the_pypi_install_guard_catches_the_underscore_spelling():
    """PyPI normalises `job_sluice` and `job-sluice` to the same project, so the underscore
    installs the forbidden thing while reading as a different string."""
    assert _installs_from_pypi("RUN pip install job_sluice")


def test_the_pypi_install_guard_catches_pep_503_name_variants():
    """PEP 503 normalises the project name, so every one of these resolves to the same PyPI
    project and installs the forbidden thing under a different-looking string."""
    for spelling in ("job_sluice", "job.sluice", "job__sluice", "Job-Sluice", "JOB_SLUICE"):
        assert _installs_from_pypi(f"RUN pip install {spelling}"), spelling


def test_the_pypi_install_guard_catches_a_global_pip_option_before_install():
    """`pip -q install job-sluice` and `pip --no-cache-dir install job-sluice` are ordinary
    spellings; without allowing options in that position the guard sees no `pip install` at
    all and passes."""
    assert _installs_from_pypi("RUN pip -q install job-sluice")
    assert _installs_from_pypi("RUN pip --no-cache-dir install job-sluice")


def test_the_pypi_install_guard_catches_a_quoted_extras_spelling():
    assert _installs_from_pypi("""RUN python -m pip install 'job-sluice[render]'""")


def test_the_pypi_install_guard_catches_the_forbidden_phrase_on_a_real_run_line():
    """The third fixture, and it exists to bound the comment-stripping rather than the pattern.

    `_strip_comments` is what stops the Dockerfile's own explanatory comment tripping the
    guard, and it strips WHOLE-LINE comments only. Over-widen it -- strip trailing `#`
    fragments, say, or drop any line MENTIONING pip -- and it would stop seeing a real install
    too. This fixture is a genuine forbidden install on a RUN line that also carries a trailing
    comment, and asserts it is still caught."""
    smuggled = "RUN echo ok && pip install job-sluice  # from PyPI -- the forbidden shape"
    assert _installs_from_pypi(_strip_comments(smuggled))


def test_the_guard_does_not_fire_on_a_command_chained_after_a_local_wheel_install(tmp_path):
    """The FALSE-POSITIVE direction, which folding line continuations opened up.

    Removing the newline to join a continuation also removed the only thing bounding the match
    to one command, so `pip install ./dist/x.whl && job-sluice --version` -- correct code, and
    the obvious way to smoke-test an install in the same layer -- started matching. A guard that
    fails the build for correct code is not merely noisy: the actionable reading of it is "stop
    verifying your install", which is the opposite of the point.

    Read through `_uncommented` on a real file, so it exercises the fold rather than the pattern
    alone."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python:3.13-slim\n"
        "RUN pip install --no-cache-dir /tmp/wheels/job_sluice-1.0.0-py3-none-any.whl \\\n"
        "      && job-sluice --version \\\n"
        '      && echo "installed job-sluice"\n'
    )
    assert not _installs_from_pypi(_uncommented(dockerfile))


def test_the_guard_does_not_fire_on_a_local_wheel_path():
    """The discriminating control. A path-borne name must NOT match, or the guard would
    forbid the very thing it exists to require."""
    assert not _installs_from_pypi(
        'RUN pip install --no-cache-dir "/tmp/wheels/job_sluice-1.0.0-py3-none-any.whl"')
    assert not _installs_from_pypi('RUN pip install ./dist/job_sluice-1.0.0-py3-none-any.whl')


# ── the image and its build context ──────────────────────────────────────────


def test_the_base_image_is_digest_pinned():
    """The same discipline every `uses:` in .github/workflows/ takes. A floating tag means the
    published image is not reproducible and its contents change under a rebuild with nothing
    recording that they did.

    Note the tag is kept INSIDE the reference (`name:tag@sha256:...`) rather than in a trailing
    `# tag` comment: a Dockerfile FROM takes one or three arguments and rejects a trailing
    comment outright, so this repo's `uses: ...@<sha>  # vX.Y.Z` idiom does not transfer."""
    match = re.search(r"^FROM\s+(\S+)\s*$", _uncommented(DOCKERFILE), re.MULTILINE)
    assert match, "no FROM line found in the Dockerfile"
    assert "@sha256:" in match.group(1), (
        f"the base image {match.group(1)!r} is not digest-pinned"
    )


def test_the_dockerfile_installs_exactly_the_expected_extras():
    """EQUALITY first, then the subset. See `_EXPECTED_EXTRAS` for why the equality is what
    supplies the non-vacuity here rather than a separate anchor."""
    match = re.search(r"\$\{?whl\}?\[([a-z,]+)\]", _uncommented(DOCKERFILE))
    assert match, (
        "could not extract the extras from the Dockerfile's pip install; the guard below "
        "would pass vacuously on an empty set"
    )
    found = set(match.group(1).split(","))
    assert found == _EXPECTED_EXTRAS, (
        f"the image installs {sorted(found)}, expected {sorted(_EXPECTED_EXTRAS)}"
    )
    declared = _pyproject_extras()
    assert found <= declared, (
        f"the Dockerfile installs extras that pyproject.toml does not declare: "
        f"{sorted(found - declared)}. `pip install` only WARNS on an unknown extra, so this "
        f"would ship an image silently missing them"
    )


def _pyproject_extras() -> set:
    """The keys of [project.optional-dependencies], by text rather than tomllib -- this module
    reads several non-Python files already and one parser is enough."""
    text = (ROOT / "pyproject.toml").read_text()
    block = text.split("[project.optional-dependencies]", 1)[1]
    block = block.split("\n[", 1)[0]
    keys = set(re.findall(r"^([a-z][a-z0-9-]*)\s*=", block, re.MULTILINE))
    assert keys, "extracted no extras from pyproject.toml; the caller would compare against {}"
    return keys


def test_the_dockerignore_denies_everything_before_re_including_the_wheel():
    """Deny-all-then-allow, so the build context cannot carry `.git`, `sluice.local.yaml`, a
    vault or a credential BY CONSTRUCTION rather than by remembering to exclude each new one.

    Measured against a real context: `*` followed by `!dist/*.whl` yields exactly the wheel --
    the sdist tarball, a nested directory, a stray secrets file and `.git/` were all excluded
    with no further rules needed."""
    rules = [ln.strip() for ln in DOCKERIGNORE.read_text().splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    assert rules, ".dockerignore has no effective rules"
    assert rules[0] == "*", (
        f".dockerignore's first effective rule is {rules[0]!r}, not '*'. The deny-all must come "
        f"FIRST -- a re-include placed above it is inert, and the context silently widens"
    )
    assert any(r.startswith("!") and r.endswith(".whl") for r in rules), (
        ".dockerignore never re-includes the wheel, so the build context is empty"
    )


# ── the compose file ─────────────────────────────────────────────────────────


# `${VAR:-default}` contains its own colon, so the source/target split still needs a regex --
# but only AFTER yaml has handled quoting, indentation, anchors, merge keys and the long form.
# Greedy source, because the target is the LAST colon-separated field that starts with `/`.
_SHORT_FORM = re.compile(r"^(?P<src>.+):(?P<tgt>/[^:]*)(?::(?P<mode>[A-Za-z,]+))?$")

# `$HOME`/`${HOME}` is home-rooted while starting with neither `/` nor `~`, so the prefix test
# cannot see it. Checked separately rather than by widening that test, which would then have to
# understand `${VAR:-...}` to avoid rejecting every legitimate expansion.
_HOME_VAR = re.compile(r"\$\{?HOME\b")


def _compose_env_file_paths() -> list:
    """Every `env_file:` path in the document, whichever of its three spellings is used.

    In scope for the home-rooted sweep because it is a HOST path exactly like a bind-mount
    source, and this file already uses the long form for it -- so `- path: ~/secrets/.env`
    would have been a personal host path the volumes-only sweep never looked at.
    """
    document = yaml.safe_load(COMPOSE.read_text())
    out = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "env_file":
                    entries = value if isinstance(value, list) else [value]
                    for entry in entries:
                        if isinstance(entry, dict):
                            out.append(str(entry.get("path", "")))
                        elif isinstance(entry, str):
                            out.append(entry)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return [value for value in out if value]


def _iter_volume_specs(node):
    """Every `volumes:` LIST anywhere in the parsed document, entry by entry.

    A list, specifically: compose's top-level `volumes:` declares named volumes and is a MAPPING,
    so this walks past it rather than needing an indent rule to exclude it. The `x-sluice` anchor
    is walked like any other node, and merge keys are already resolved by the parser -- so a
    mount reaches this whether it was written in the anchor, in a service, or in both.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "volumes" and isinstance(value, list):
                yield from value
            else:
                yield from _iter_volume_specs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_volume_specs(item)


def _compose_services() -> dict:
    """`{service: {"env": {...}, "mounts": [(source, target), ...]}}`, merge keys resolved.

    THE ANCHOR IS NOT WHAT COMPOSE RUNS, and the difference is not cosmetic. YAML's `<<` merge
    is KEY-level: a service that merges `*sluice` and then declares its own `environment:`
    replaces the anchor's entire environment mapping rather than adding to it, so it silently
    loses `VAULT_DIR` while keeping every mount. Measured on a copy -- and with the guards
    reading a flat union over the whole document, all three of them passed.

    That is the first Critical this file closed, reopened verbatim and invisible. So the pins
    below ask what each SERVICE resolves to, not what appears somewhere in the document.
    """
    document = yaml.safe_load(COMPOSE.read_text())
    services = document.get("services") or {}
    assert services, "docker-compose.yml declares no services; the per-service pins are vacuous"
    return {
        name: {
            "env": (service.get("environment") or {}),
            "mounts": _mount_pairs(service.get("volumes") or []),
        }
        for name, service in services.items()
    }


def _compose_volume_pairs() -> list:
    """(source, target) for every mount in the compose file.

    Both spellings are handled by the parser rather than by pattern-guessing: the long form
    arrives as a dict (`{type, source, target}`), the short form as a string. An entry that is
    neither, or a short form this cannot split, raises rather than being skipped -- for a
    NEGATIVE guard, silently dropping an entry is indistinguishable from that entry passing,
    which is the failure every previous version of this function shipped with.
    """
    return _mount_pairs(_iter_volume_specs(yaml.safe_load(COMPOSE.read_text())))


def _mount_pairs(specs) -> list:
    """The ONE mount reader, shared by the flat sweep and the per-service view above, so the
    two cannot disagree about what a mount is."""
    pairs = []
    for spec in specs:
        if isinstance(spec, dict):
            # `source` is legitimately absent for `type: tmpfs` and for an anonymous
            # `type: volume`; only the target is required. An empty source sweeps clean, which
            # is correct -- there is no host path to be personal.
            source, target = spec.get("source", ""), spec.get("target", "")
            assert target, f"long-form mount declares no target: {spec!r}"
            pairs.append((str(source), str(target)))
            continue
        assert isinstance(spec, str), f"unrecognised volume entry {spec!r} in docker-compose.yml"
        if ":" not in spec:
            # An ANONYMOUS volume: a target and no source, which is the idiom for shadowing a
            # subpath of a bind mount. There is no source to sweep, so it contributes an empty
            # one rather than aborting -- raising here would fail the build for correct compose.
            pairs.append(("", spec))
            continue
        match = _SHORT_FORM.match(spec)
        assert match, (
            f"could not split the mount {spec!r} into source and target. Fix this reader -- do "
            f"NOT let the entry through unchecked, which is what makes a negative guard pass "
            f"for the wrong reason"
        )
        pairs.append((match.group("src").strip(), match.group("tgt").strip()))
    return pairs


def _home_rooted_hits(value: str) -> list:
    """Every reason `value` names somewhere personal: [] when it is clean.

    Checks the literal AND any `${VAR:-default}` default, because the default is what ships
    when the variable is unset -- the common case, and the one a reader never sees exercised.
    `$HOME`/`${HOME}` is checked separately because it is home-rooted while starting with
    neither `/` nor `~`, and widening the prefix test to cover it would mean teaching that test
    about `${VAR:-...}` just to avoid rejecting every legitimate expansion.
    """
    candidates = [value.strip("\"'")]
    candidates += re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^{}]*)\}", value)
    return [c for c in candidates
            if c.startswith(("/", "~"))
            or re.match(r"^[A-Za-z]:[\\/]", c)
            or _HOME_VAR.search(c)]


def test_no_compose_mount_source_is_an_absolute_or_home_rooted_path():
    """Closes the one real gap in the repo-wide neutrality sweep.

    `tests/test_no_leaked_files.py` covers every tracked file, so a `/Users/<name>/...` or
    `/home/<name>/...` vault path in this file is already caught -- but its pattern is anchored on those two roots
    and so cannot see `~/vault`, a drive-lettered path, or any other absolute root. A mount
    source is exactly where someone's real vault location would end up."""
    # SCOPE is now held by `_compose_volume_pairs` itself: it raises on any entry it cannot read
    # rather than skipping it, so there is no longer a subset for this sweep to run over silently.
    # The non-vacuity anchor stays -- for a negative guard, finding nothing IS the success case,
    # so "no mounts at all" must not read as a pass.
    pairs = _compose_volume_pairs()
    assert pairs, "found no mounts in docker-compose.yml; nothing was checked"
    # env_file paths are host paths too, and this file uses the long form for one -- so a
    # `- path: ~/secrets/.env` was exactly the `~`-rooted shape this guard exists to catch,
    # sitting in the one place a volumes-only sweep never looked.
    host_paths = [source for source, _target in pairs] + _compose_env_file_paths()
    bad = [(value, hit) for value in host_paths for hit in _home_rooted_hits(value)]
    assert not bad, (
        f"compose host paths must be relative or named volumes, never absolute, home-rooted "
        f"or $HOME-rooted: {bad}"
    )


# Synthetic inputs for the two halves of the mount guard. The real compose file's five sources
# are all named volumes or `./`-relative, so it exercises NEITHER half: neutralising the
# home-rooted predicate to never match left the sweep green, and so did dropping the mode group
# from the short-form reader. A guard whose live input lacks the shape it guards is invisible
# when it breaks -- which is how all three previous versions of this reader shipped their gaps.
_PERSONAL_SOURCES = [
    "/srv/exampleuser/vault",              # absolute
    "~/vault",                             # home-rooted
    '"~/vault"',                           # ...and quoted, which the first version missed
    "$HOME/vault",                         # env-rooted, starts with neither / nor ~
    "${HOME}/vault",
    "${SLUICE_VAULT:-$HOME/vault}",        # personal only in the DEFAULT
    "${SLUICE_VAULT:-/srv/exampleuser/v}",
    # Drive-lettered. The path after the drive letter is deliberately impersonal and does
    # NOT name a home root: what this fixture exercises is the `^[A-Za-z]:[\\/]` prefix,
    # and a home-root component would additionally trip test_no_leaked_files.py's
    # repo-wide sweep -- which it did, on the first run, from inside this very comment.
    # is the `^[A-Za-z]:[\\/]` prefix, and a `/Users/` component would additionally trip
    # tests/test_no_leaked_files.py's repo-wide sweep -- which it did, on the first run.
    "C:/data/vaults/personal",
    "C:\\data\\vaults\\personal",
]

_IMPERSONAL_SOURCES = [
    "sluice-workspace",                    # a named volume
    "./vault",
    "${SLUICE_VAULT:-./vault}",
    "${SLUICE_CONFIG_DIR:-./config}",
    "",                                    # an anonymous volume contributes no source
]


def test_the_home_rooted_predicate_flags_every_personal_shape():
    assert _PERSONAL_SOURCES, "no fixtures; this guard would pass vacuously"
    missed = [s for s in _PERSONAL_SOURCES if not _home_rooted_hits(s)]
    assert not missed, (
        f"the home-rooted predicate does not flag {missed}. Each of these names somewhere "
        f"personal, and the real compose file contains none of them -- so nothing else in the "
        f"suite would go red if this predicate stopped working"
    )


def test_the_home_rooted_predicate_clears_every_impersonal_shape():
    """The other direction, and not symmetry for its own sake: a predicate that flags a named
    volume or a `./`-relative default would fail the build for correct compose, and the
    actionable reading of that is "stop using named volumes"."""
    assert _IMPERSONAL_SOURCES, "no fixtures; this guard would pass vacuously"
    wrong = [(s, _home_rooted_hits(s)) for s in _IMPERSONAL_SOURCES if _home_rooted_hits(s)]
    assert not wrong, f"the predicate wrongly flags impersonal sources: {wrong}"


def test_the_mount_reader_handles_every_legitimate_compose_spelling():
    """`_mount_pairs` RAISES on an entry it cannot read, which is right -- a silently skipped
    entry is indistinguishable from one that passed. That makes the set it accepts load-bearing
    in the other direction: each of these is valid compose, and raising on one would abort the
    mount sweep, the vault pin and the WORKDIR pin together."""
    cases = [
        ("./vault:/work/vault", ("./vault", "/work/vault")),
        ("${SLUICE_VAULT:-./vault}:/work/vault", ("${SLUICE_VAULT:-./vault}", "/work/vault")),
        ("named-vol:/app/state", ("named-vol", "/app/state")),
        ("./a:/work/b:ro", ("./a", "/work/b")),
        ("./a:/work/b:z,ro", ("./a", "/work/b")),
        ("./a:/work/b:Z", ("./a", "/work/b")),       # SELinux, upper case
        ("/work/scratch", ("", "/work/scratch")),     # anonymous volume: target only
        ({"type": "bind", "source": "./x", "target": "/work/x"}, ("./x", "/work/x")),
        ({"type": "tmpfs", "target": "/work/tmp"}, ("", "/work/tmp")),  # no source, legitimate
    ]
    assert cases, "no fixtures; this guard would pass vacuously"
    for spec, expected in cases:
        assert _mount_pairs([spec]) == [expected], spec


def test_every_service_pins_the_vault_directory_into_its_own_mounts():
    """The load-bearing guard, and it closes a Critical twice over.

    `stores/vault.py:_make` is `Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None)`,
    so this env var outranks a configured value BY CONSTRUCTION. It has to, because the config
    directory is bind-mounted from the host: a `job-sluice init` run there leaves an absolute
    HOST path in `vault_dir` that means nothing inside the container.

    Without the pin the failure is silent and unrecoverable. `Vault` never checks that its
    directory exists and `upsert`'s create arm makedirs it, so a wrong path is CREATED in the
    container's ephemeral layer; leads land there as `created`, which is on `ingest/sink.py`'s
    allowlist, so they are recorded in a seen.db that lives on a PERSISTENT volume. Remove the
    container and the notes are gone while seen.db still suppresses them -- forever, since it
    has no removal path.

    PER SERVICE, not over the document: see `_compose_services`. A flat union let a service that
    merges the anchor and declares its own `environment:` drop `VAULT_DIR` while keeping every
    mount, with this guard still green.
    """
    services = _compose_services()
    for name, service in services.items():
        pinned = service["env"].get("VAULT_DIR")
        assert pinned, (
            f"service {name!r} does not pin VAULT_DIR. If it merges the `x-sluice` anchor and "
            f"declares its own `environment:`, YAML's key-level merge REPLACED the anchor's "
            f"mapping and dropped the pin -- re-state VAULT_DIR in that service"
        )
        targets = [target for _source, target in service["mounts"]]
        assert pinned in targets, (
            f"service {name!r} pins VAULT_DIR to {pinned!r}, which is not among its own mount "
            f"targets ({targets}). The pin only protects the vault if that service mounts it"
        )


def test_compose_camofox_url_carries_a_scheme_and_the_shipped_port():
    """A name-only env-var check cannot catch a wrong VALUE, and this is the value that got
    written without a scheme and survived a first reading by four reviewers.

    `core/camofox.py` concatenates this straight into a urllib request, so a bare `host:port`
    is parsed as a SCHEME and every fetch raises `unknown url type` -- through a blanket except,
    so it surfaces as an unexplained error rather than a configuration message."""
    match = re.search(r"^\s*CAMOFOX_URL:\s*(\S+)\s*$", COMPOSE.read_text(), re.MULTILINE)
    assert match, "docker-compose.yml does not set CAMOFOX_URL"
    value = match.group(1)
    default = re.search(r":-(https?://[^}]+)\}", value)
    assert default, (
        f"CAMOFOX_URL's default {value!r} carries no http(s) scheme; urllib parses a bare "
        f"host:port as a scheme and raises `unknown url type`"
    )
    shipped = re.search(r'_DEFAULT_URL\s*=\s*"([^"]+)"',
                        (ROOT / "sluice" / "core" / "camofox.py").read_text())
    assert shipped, "could not read _DEFAULT_URL from sluice/core/camofox.py"
    port = shipped.group(1).rsplit(":", 1)[1]
    assert default.group(1).rstrip("/").endswith(f":{port}"), (
        f"compose points Camofox at {default.group(1)!r} but sluice/core/camofox.py ships port "
        f"{port}; the two must agree or the container talks to nothing"
    )


def test_every_env_var_compose_sets_is_a_name_sluice_knows():
    """Deliberately a LITERAL-PRESENCE check, and no stronger claim than that.

    A "sluice actually READS this variable" sweep cannot be honest here: `core/app.py`'s
    `_PROVIDER_ENV` reads its keys through a variable (`os.environ.get(key_var, "")`), so no
    literal-based derivation can see them, and `DOSSIER_DIR` arrives through an `env_var=`
    keyword rather than an `os.environ` call. Presence still catches the failure that actually
    happens -- a misspelled name like SLUICE_CONFIG_FILE, which would be silently ignored --
    without overstating what it proves."""
    names = set(re.findall(r"^\s{4,}([A-Z][A-Z0-9_]{2,}):\s", COMPOSE.read_text(), re.MULTILINE))
    assert names, "found no environment variables in docker-compose.yml; nothing was checked"
    source = "\n".join(p.read_text() for p in (ROOT / "sluice").rglob("*.py"))
    unknown = [n for n in names if f'"{n}"' not in source and f"'{n}'" not in source]
    assert not unknown, (
        f"docker-compose.yml sets {unknown}, which appear nowhere in sluice/ as a literal. An "
        f"environment variable sluice does not know is silently ignored"
    )


def test_compose_and_the_release_job_agree_on_the_image():
    """A drift pin, with BOTH extractions asserted non-empty before they are compared.

    That is not ceremony: the recorded failure of this exact idiom in this repo is a pin that
    passed because both sides failed to extract and `None == None`."""
    compose_images = set(re.findall(r"^\s*image:\s*([^\s:]+(?:/[^\s:]+)*)",
                                    COMPOSE.read_text(), re.MULTILINE))
    assert compose_images, "extracted no image reference from docker-compose.yml"
    pushed = set(re.findall(r"(ghcr\.io/\S+?):\$?\{?", RELEASE_PLEASE.read_text()))
    assert pushed, "extracted no pushed image reference from release-please.yml"
    assert compose_images == pushed == {IMAGE}, (
        f"compose references {sorted(compose_images)} but the docker job pushes "
        f"{sorted(pushed)}; expected both to be {IMAGE!r}"
    )


def test_the_mcp_service_publishes_no_port():
    """`job-sluice mcp serve` speaks MCP over STDIO -- cli.py registers it as "run the MCP
    server (stdio transport)" and sluice/mcpserver.py says "over stdio". There is no HTTP or
    SSE transport in the module, so a published port would be a file asserting a mechanism
    that does not exist, and anyone configuring a client against it would get silence."""
    text = COMPOSE.read_text()
    assert "mcp serve" in text or '"serve"' in text, (
        "no mcp service found in docker-compose.yml; this guard would pass vacuously"
    )
    assert not re.search(r"^\s*ports:", text, re.MULTILINE), (
        "docker-compose.yml publishes a port, but nothing in this image serves over a socket"
    )


# ── the base image stays current ─────────────────────────────────────────────


def test_the_env_file_compose_reads_is_gitignored():
    """The compose file passes backend credentials through an optional `.env`, and never names
    a provider key. That design is only safe because `.env` cannot be committed -- an assumption
    nothing asserted until now, in a repo where the whole point of the neutrality gate is that a
    private job hunt must not reach a public remote."""
    ignored = [ln.strip() for ln in (ROOT / ".gitignore").read_text().splitlines()]
    assert ".env" in ignored, ".gitignore no longer ignores .env, which docker-compose.yml reads"
    assert "env_file" in COMPOSE.read_text(), (
        "docker-compose.yml no longer reads an env_file; this guard would pass vacuously"
    )


def test_every_service_persists_the_whole_working_directory():
    """The second Critical, and the reason the mount is the WORKDIR rather than a list of five.

    The cwd-relative artefact paths in `sluice/` land, under WORKDIR, in the container's
    writable layer -- which `docker compose run --rm` deletes, while the POINTER to a rendered
    CV survives in the persistent vault note. The lead then wedges: `cv run` says
    `skipped-has-cv`, `apply prep` says `missing_file`.

    The cwd-relative set is DERIVED rather than hand-listed, so a sixth one added later cannot
    silently escape the guarantee. WORKDIR is READ from the Dockerfile rather than hardcoded,
    so renaming it cannot leave this guard green while the mount goes inert. And it is asserted
    per SERVICE for the same reason the vault pin is.
    """
    relative_defaults = []
    for path in (ROOT / "sluice").rglob("*.py"):
        if path.name == "paths.py":
            continue  # the resolver itself, not a consumer
        relative_defaults += re.findall(r'=\s*"(\./[^"]+)"', path.read_text())
    assert relative_defaults, (
        "found no cwd-relative artefact defaults in sluice/; this guard would pass vacuously"
    )
    workdirs = re.findall(r"^WORKDIR\s+(\S+)\s*$", _uncommented(DOCKERFILE), re.MULTILINE)
    assert workdirs, "no WORKDIR in the Dockerfile; this guard has nothing to anchor on"
    workdir = workdirs[-1].strip('"\'')
    assert workdir.startswith("/"), (
        f"WORKDIR is {workdir!r}, a relative path. Docker resolves it against the previous "
        f"WORKDIR, so the mount may still be correct -- but this guard compares it to mount "
        f"targets as though it were absolute. Spell WORKDIR absolutely."
    )
    for name, service in _compose_services().items():
        targets = [target for _source, target in service["mounts"]]
        assert workdir in targets, (
            f"service {name!r} does not persist {workdir!r}, the container's WORKDIR. "
            f"{len(relative_defaults)} cwd-relative paths in sluice/ resolve under it, so a "
            f"rendered CV would die with the container while the vault note still points at it"
        )


def test_dependabot_covers_the_docker_ecosystem():
    """Without this the digest pin above is a liability rather than a control: a digest never
    updates itself, so the base image freezes permanently and the published image accrues known
    CVEs while LOOKING well-secured. That is the argument dependabot.yml's own header already
    makes for the action pins (#3), and it applies unchanged here."""
    assert re.search(r"^\s*-\s*package-ecosystem:\s*docker\s*$",
                     DEPENDABOT.read_text(), re.MULTILINE), (
        "dependabot.yml declares no `docker` ecosystem, so the Dockerfile's digest-pinned "
        "base image will never be bumped"
    )
