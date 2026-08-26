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
# `$HOME`/`${HOME}` and their Windows equivalents. All are home-rooted while starting with
# neither `/` nor `~`, so the prefix test cannot see them. `%USERPROFILE%` is the cmd spelling
# and `$USERPROFILE`/`${USERPROFILE}` the shell one; a compose file written on Windows uses the
# first, and it names the user's profile directory as squarely as `$HOME` does.
_HOME_VAR = re.compile(r"\$\{?(?:HOME|USERPROFILE)\b|%USERPROFILE%", re.IGNORECASE)


def _compose_env_file_paths() -> list:
    """The shipped file's env_file paths."""
    return _env_file_paths(yaml.safe_load(COMPOSE.read_text()))


def _env_file_paths(document) -> list:
    """Every `env_file:` path in the document, whichever of its three spellings is used.

    In scope for the home-rooted sweep because it is a HOST path exactly like a bind-mount
    source, and this file already uses the long form for it -- so `- path: ~/secrets/.env`
    would have been a personal host path the volumes-only sweep never looked at.
    """
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


def _env_mapping(value) -> dict:
    """`environment:` as a mapping, whichever of compose's two spellings was used.

    Compose accepts `environment: {KEY: value}` AND `environment: [- KEY=value]`. Calling
    `.get()` on the list form raises AttributeError, which would replace the failure message
    the guard below exists to print with a traceback -- hiding the property, not reporting it.
    """
    if isinstance(value, list):
        pairs = [str(item).split("=", 1) for item in value]
        return {pair[0]: (pair[1] if len(pair) == 2 else "") for pair in pairs}
    return value or {}


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
    # `include:` and `extends:` pull in services this reader never sees, so the roster would
    # shrink silently rather than the guard failing. Neither is used today; asserting their
    # absence forces a human to widen the reader instead of letting the scope quietly narrow.
    assert "include" not in document, (
        "docker-compose.yml now uses `include:`, which brings in services this reader does not "
        "resolve -- widen it rather than letting the per-service pins cover a subset"
    )
    extending = [n for n, s in services.items() if isinstance(s, dict) and "extends" in s]
    assert not extending, (
        f"services {extending} use `extends:`, whose inherited keys this reader does not "
        f"resolve -- widen it rather than letting the per-service pins cover a subset"
    )
    return {
        name: {
            "env": _env_mapping(service.get("environment")),
            "mounts": _mount_pairs(service.get("volumes") or []),
            # Compose's own `working_dir:` OVERRIDES the image's WORKDIR for that service, and
            # it lives here -- per service -- which is exactly where the previous version of
            # this guard stopped looking. `None` when unset, so the caller falls back to the
            # Dockerfile's WORKDIR rather than this file inventing a default.
            "cwd": service.get("working_dir"),
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
            # A UNC path embeds a private HOSTNAME, which the neutrality rule names in its own
            # right -- and it starts with a separator the POSIX-rooted checks do not recognise.
            or c.startswith("\\\\")
            or re.match(r"^[A-Za-z]:[\\/]", c)
            or _HOME_VAR.search(c)]


def _default_of(value: str) -> str:
    """The `:-default` half of a `${VAR:-default}` expansion, or the value unchanged.

    Only the default is ever a literal in this file; what `VAR` expands to at runtime is the
    reader's own environment and is none of this repository's business. Deliberately narrow: it
    matches the whole value or nothing, so a path that merely CONTAINS an expansion is left
    alone for the sweep to judge.
    """
    m = re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*:-([^}]*)\}", value.strip())
    return m.group(1) if m else value


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
    env_files = _compose_env_file_paths()
    # The same scope assertion the mounts arm carries. Forcing this reader to return []
    # previously left the sweep green even against a compose file whose env_file was
    # `~/secrets/.env` -- a whole arm of the guard disappearing without a single test noticing.
    assert env_files, "found no env_file paths in docker-compose.yml; that arm checked nothing"
    host_paths = [source for source, _target in pairs] + env_files
    # `/dev/null` is exempt, and nothing else absolute is. A bind mount's SOURCE must exist, so a
    # mount that is meant to be absent still needs to name something -- and /dev/null is the
    # portable "nothing" compose files use for exactly that (#209's optional ssh key). It is a
    # POSIX device rather than a location on anyone's disk, so it cannot be the personal path
    # this guard exists to catch. Written as an exact-match exemption on the DEFAULT half of the
    # expansion rather than a pattern, so `/dev/null-ish` or `/dev/nullvault` is still caught.
    # Exempt ONLY a value whose non-default half is itself clean. `${HOME:-/dev/null}` has a
    # `/dev/null` default and a home-rooted variable, and exempting on the default alone would
    # wave it through -- the sweep's whole subject.
    #
    # The literal, NOT `os.devnull`, and deliberately so (raised in review, declined). What is
    # being compared here is a STRING INSIDE docker-compose.yml, read as bytes off disk. That
    # string is consumed by a Linux container and is `/dev/null` no matter what machine runs
    # pytest, whereas `os.devnull` is a property of the HOST -- `nul` on Windows. Swapping it in
    # would make this exemption stop matching the shipped compose file on a Windows checkout, and
    # the sweep would fail on a file that had not changed. A host-OS constant is the wrong
    # authority for the content of a file that never leaves the repository.
    exempt = [v for v in host_paths
              if _default_of(v) == "/dev/null" and not _home_rooted_hits(v.split(":-")[0])]
    host_paths = [v for v in host_paths if v not in exempt]
    # RE-ANCHORED after the filter, not before. The `assert pairs` above proves the READER found
    # mounts; it says nothing about what survives to be judged. Measured: a `_default_of` that
    # returned a constant emptied this list and the sweep passed while checking nothing -- the
    # exemption was entirely unwitnessed.
    assert host_paths, (
        "every mount source was exempted, so this sweep checked nothing -- _default_of is "
        "matching more than the one `/dev/null` sentinel it is allowed to exempt")
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
    "\\\\fileserver\\share\\vault",   # UNC -- embeds a private hostname
    "%USERPROFILE%/vault",                 # the Windows spelling of $HOME
    "${USERPROFILE}/vault",
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


def test_the_environment_reader_handles_both_compose_spellings():
    """`_env_mapping`'s list branch is unexercised by the real file, which uses the mapping
    spelling exclusively -- the same invisibility the source fixtures above exist to remove."""
    assert _env_mapping({"VAULT_DIR": "/work/vault"}) == {"VAULT_DIR": "/work/vault"}
    assert _env_mapping(["VAULT_DIR=/work/vault"]) == {"VAULT_DIR": "/work/vault"}
    assert _env_mapping(["FLAG"]) == {"FLAG": ""}          # a bare name is valid compose
    assert _env_mapping(["A=b=c"]) == {"A": "b=c"}         # only the FIRST `=` splits
    assert _env_mapping(None) == {}
    assert _env_mapping([]) == {}


def test_the_env_file_reader_handles_every_compose_spelling():
    """Compose accepts a scalar, a list of strings, and a list of `{path, required}` maps. The
    shipped file uses only the last, so the other two reached no test -- and this arm of the
    sweep is the one that would see a `~`-rooted secrets path."""
    cases = [
        ({"services": {"a": {"env_file": ".env"}}}, [".env"]),
        ({"services": {"a": {"env_file": [".env", "other.env"]}}}, [".env", "other.env"]),
        ({"services": {"a": {"env_file": [{"path": ".env", "required": False}]}}}, [".env"]),
    ]
    assert cases, "no fixtures; this guard would pass vacuously"
    for document, expected in cases:
        assert _env_file_paths(document) == expected, document
    # And the shape the sweep exists to refuse, through the real predicate.
    assert _home_rooted_hits(_env_file_paths(
        {"services": {"a": {"env_file": [{"path": "~/secrets/.env"}]}}})[0])


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


def _entrypoint_expands(name: str, script: str) -> bool:
    """True only for a real shell EXPANSION of `name` -- `$NAME` or `${NAME...}`.

    Two narrowings, each closing a way this fails open. A bare `name in script` substring
    test accepts a variable whose name is merely a PREFIX of one the script reads
    (`SLUICE_CLAUDE` on the strength of `SLUICE_CLAUDE_HOST`). And a token-bounded match --
    the first fix -- is satisfied by a COMMENT, which reads nothing: measured,
    `SLUICE_CLAUDE_KEY` appears six times in the script and is expanded zero times, yet
    passed. Only an expansion can support the claim.

    FULL-LINE comments are dropped, and nothing else is.

    A previous version also stripped `'...'` on the grounds that sh does not expand there.
    That is true of a single-quoted STRING and false of a single quote sitting inside a
    double-quoted one -- and this very script has such a line (`echo "... 'ssh
    $SLUICE_CLAUDE_HOST ...' ..."`). Measured: the strip removed the expansion from it. A
    guard that hides a real read is worse than one that counts an extra, so the unsafe half
    is gone rather than patched a third time; distinguishing the two needs a shell parser,
    not another regex.

    The claim this supports is therefore "the script EXPANDS this variable" -- which is what
    is checkable. An expansion inside an error message counts, and that is honest rather
    than ideal: `echo "$FOO"` does read `$FOO`. What it still excludes, and what the failure
    actually was, is a variable the script merely NAMES in prose.
    """
    live = re.sub(r"^[ \t]*#[^\n]*$", "", script, flags=re.M)
    return re.search(rf"\$\{{?{re.escape(name)}(?![A-Za-z0-9_])", live) is not None


def _unpinned_neutralised(doc: dict, names: set) -> list:
    """(service, name, value) for every service that does NOT resolve `name` to the empty pin.

    Reads the PARSED document per service rather than grepping the file, because YAML `<<` merge
    is KEY-level: a service that merges the shared anchor and then declares its own
    `environment:` replaces that mapping wholesale and silently loses the pin, while the literal
    still sits in the file for a grep to find. That is the shape recorded against this very
    compose file at #173, where a flat search certified a service that had lost VAULT_DIR.

    At module scope so a synthetic document can be run through it. Inline, it could only ever see
    the live file -- which is clean, so neutralising the assertion changed nothing and the mutant
    survived.
    """
    services = doc.get("services") or {}
    assert services, "the document declares no services; this check would be vacuous"
    out = []
    for svc_name, svc in services.items():
        env = svc.get("environment") or {}
        assert isinstance(env, dict), (
            f"service {svc_name} uses the LIST spelling for environment:, which this check "
            f"cannot read -- convert it or teach the check that form")
        for n in names:
            if env.get(n, None) != "":
                out.append((svc_name, n, env.get(n, "<absent>")))
    return out


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
    # The container ENTRYPOINT is a reader too, and a real one: it consumes
    # SLUICE_CLAUDE_SSH_USER and SLUICE_CLAUDE_KEY, neither of which sluice itself ever sees
    # (#209). Leaving it out would have forced a genuine variable to be deleted or the guard to
    # be dropped -- and the guard's actual claim is "something in this image reads it", not
    # "sluice/ reads it".
    #
    # Matched on the BARE name, not a quoted literal. Python spells an env lookup
    # `os.environ["X"]`, shell spells it `${X:-}` -- so the quoted-literal test above finds
    # nothing in a shell script however many times the variable appears. Measured: three
    # occurrences, zero matches, and the guard failed on a variable that was genuinely read.
    entrypoint = (ROOT / "packaging" / "docker-entrypoint.sh").read_text()


    # Deliberately set-and-not-read. `SLUICE_CLAUDE_KEY` names a path on the HOST and is the
    # bind-mount SOURCE; the entrypoint reads a fixed container TARGET instead. It appears in
    # `environment:` ONLY to pin it empty, because `env_file: .env` would otherwise inject the
    # host path into the container -- which is what made the documented setup fail. So it is a
    # neutraliser, not a knob, and the "something reads this" rule does not apply to it. Held to
    # the stricter standard in exchange: it must be pinned empty, never given a value here.
    NEUTRALISED = {"SLUICE_CLAUDE_KEY"}
    # Asserted per SERVICE, from the PARSED document, not by grepping the file. YAML `<<` merge
    # is key-level: a service that merges the shared anchor and then declares its own
    # `environment:` replaces that mapping wholesale and silently loses the pin, while the
    # literal still sits in the file for a grep to find. That is not hypothetical here -- it is
    # the shape recorded against this very compose file at #173, where a flat search over the
    # document certified a service that had lost VAULT_DIR.
    import yaml
    doc = yaml.safe_load(COMPOSE.read_text())
    violations = _unpinned_neutralised(doc, NEUTRALISED)
    assert not violations, (
        f"these services do not resolve the neutralised variables to the empty pin: "
        f"{violations}. They are exempt from the reads-it rule ONLY because they are pinned "
        f"empty; without that, env_file injects the host path and the entrypoint skips setup.")
    for n in NEUTRALISED:
        assert re.search(rf"^\s+{n}:\s*\"\"\s*$", COMPOSE.read_text(), re.M), (
            f"{n} is exempt from the reads-it rule only because it is pinned EMPTY; it now "
            f"carries a value, so either it is read (drop the exemption) or the pin broke")

    unknown = [n for n in names
               if n not in NEUTRALISED
               and f'"{n}"' not in source and f"'{n}'" not in source
               and not _entrypoint_expands(n, entrypoint)]
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
        # The EFFECTIVE cwd, not the image's. Compose's `working_dir:` overrides WORKDIR per
        # service, and the guard read only the Dockerfile -- measured, adding
        # `working_dir: /srv/run` to a service left all three compose guards GREEN while the
        # cwd-relative artefacts resolved into the layer `compose run --rm` deletes. That is
        # the same Critical this guard exists for, reopened one level up: round 5 moved the
        # check per service, and `working_dir:` is precisely a per-service key.
        effective = service["cwd"] or workdir
        assert effective.startswith("/"), (
            f"service {name!r} sets a relative working_dir {effective!r}; compose resolves it "
            f"against the image WORKDIR, but this guard compares it to absolute mount targets"
        )
        targets = [target for _source, target in service["mounts"]]
        assert effective in targets, (
            f"service {name!r} does not persist {effective!r}, its effective working directory "
            f"({'working_dir:' if service['cwd'] else 'the image WORKDIR'}). "
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


def test_a_commented_expansion_does_not_count_as_a_read():
    """The falsify partner for `_entrypoint_expands`, calling the REAL function.

    An earlier version defined its own copy of the logic inline, which meant a mutation applied
    to the real one left this green -- measured, twice over. That is the "testing the helper
    reproduces the defect one level up" failure (#170), and it is why the function is at module
    scope rather than nested inside the guard that uses it.

    Synthetic input rather than the live script, because the live script is CLEAN: an assertion
    made against it passes today and starts failing for the wrong reason the moment the shape
    appears. The prose row is the failure that actually happened -- `SLUICE_CLAUDE_KEY` appeared
    six times in comments and error text, expanded zero times, and the guard passed it.
    """
    assert not _entrypoint_expands("FOO", "# uses $FOO for the thing\nexec x\n"), \
        "a comment is not a read"
    assert not _entrypoint_expands("FOO", 'echo "set FOO to a path" >&2\n'), \
        "prose naming it is not a read"
    assert not _entrypoint_expands("FOO", "exec x ${FOOBAR}\n"), \
        "a longer name is not this one"

    # ...and these ARE reads, including the one inside a message, which the docstring admits.
    assert _entrypoint_expands("FOO", "exec x $FOO\n")
    assert _entrypoint_expands("FOO", 'exec x "${FOO:-d}"\n')
    assert _entrypoint_expands("FOO", 'echo "running with $FOO" >&2\n')
    # A single quote inside a double-quoted string must not hide the expansion behind it -- the
    # bug an earlier version of this stripper had, measured against the real entrypoint.
    assert _entrypoint_expands("FOO", """echo "tries 'ssh $FOO ...' next"\n""")


def test_the_neutralised_pin_is_checked_per_service_not_by_grepping_the_file():
    """The per-service check, against SYNTHETIC compose input.

    The live file is clean, so neutralising the per-service assertion changes nothing and the
    mutation survives -- a guard whose live input lacks the shape it guards is invisible when it
    breaks. This supplies the shape: a document whose literal pin is present for a grep to find,
    while a service that declares its own `environment:` has silently lost it. YAML `<<` merge is
    KEY-level, so the anchor's whole mapping is replaced -- the hazard recorded against this very
    compose file at #173.
    """
    import yaml

    doc = yaml.safe_load("""
        x-shared: &shared
          environment:
            SLUICE_CLAUDE_KEY: ""
            CAMOFOX_URL: http://example.invalid
        services:
          good:
            <<: *shared
          bad:
            <<: *shared
            environment:
              CAMOFOX_URL: http://example.invalid
        """)
    violations = _unpinned_neutralised(doc, {"SLUICE_CLAUDE_KEY"})
    assert violations == [("bad", "SLUICE_CLAUDE_KEY", "<absent>")], (
        f"the per-service check must flag exactly the service that lost the merged pin, and "
        f"leave the one that kept it alone: {violations}")
