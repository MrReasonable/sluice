"""Guards for the Docker channel (#104, PR 4): the Dockerfile, its build context, and the
compose file that wires it up.

WHY TEXT, NOT A YAML PARSE, for the compose half: `pyyaml` is a guarded optional import in
`sluice/` (CLAUDE.md's stdlib-only rule), so a test needing it is a test that can skip itself
into uselessness on a bare install. `tests/test_ci_wiring.py` states the same rule for the
workflow files and this module follows it.

No real `docker build` runs here. Pulling a base image needs network, which this suite
deliberately does not have; the sequencing spec fixes this as a text check on the Dockerfile
source for exactly that reason. The real build runs in CI's `docker` job instead.
"""

import re
from pathlib import Path

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
# `job[-_]sluice`, not `job-sluice`: PyPI normalises the two spellings to the same project, so
# `pip install job_sluice` installs precisely the forbidden thing while reading as a different
# string. The wheel FILENAME is `job_sluice-...whl`, which is why the lookarounds matter --
# they are what still lets the underscore spelling through when it is part of a path.
#
# Deliberately not a fixed contiguous literal: the sequencing spec names
# `pip install --no-cache-dir job-sluice` as the ordinary phrasing a literal would silently
# miss. The negative lookbehind/lookahead are what keep `/tmp/wheels/job_sluice-1.0.0.whl` and
# `./job-sluice/...` from matching -- a path-borne name is the ALLOWED case, and the whole point
# of the guard is to tell it apart from an index-borne one.
_PYPI_INSTALL = re.compile(
    r"""pip3?\s+install\b[^\n;&|]*?(?<![\w./'"-])['"]?job[-_]sluice(?:\[[^\]]*\])?['"]?(?![\w./-])"""
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


# A line that LOOKS like a short-form mount, matched loosely on purpose. It is the SCOPE half
# of the guard below: every line this finds must also parse, or the guard fails. Without it a
# shape the strict pattern cannot read is silently skipped, and a per-line miss reads exactly
# like a pass -- measured, before this was split in two: an access-mode suffix (`:ro`) dropped
# a `/Users/<name>/vault` mount entirely while the assertion beside it still passed on the four
# unsuffixed lines.
_MOUNT_LINE = re.compile(r'^\s*-\s+["\']?[^"\'\s]+:[^"\'\s]+["\']?\s*$')

# The strict read. Optional surrounding quotes, and an optional trailing access mode, because
# both are ordinary compose spellings that the first version of this guard could not see.
_MOUNT = re.compile(
    r'^\s*-\s+["\']?(?P<src>.+?)["\']?:(?P<tgt>/[^:"\']*)(?::(?P<mode>[a-z,]+))?["\']?\s*$'
)

# `$HOME`/`${HOME}` is a home-rooted path that starts with neither `/` nor `~`, so the literal
# prefix test cannot see it. Checked separately rather than by widening that test, which would
# also have to understand `${VAR:-...}` to avoid rejecting every legitimate expansion.
_HOME_VAR = re.compile(r"\$\{?HOME\b")


def _compose_mount_lines() -> list:
    """Every short-form mount line, scoped to an INDENTED `volumes:` block.

    Scoped rather than swept file-wide because `extra_hosts:` entries have the identical shape
    (`- "host.docker.internal:host-gateway"`) -- caught by the scope assertion below on its very
    first run, which is precisely the job that assertion exists to do. The top-level `volumes:`
    declaration block is excluded by the indent test: its children declare named volumes and are
    not mounts at all.
    """
    out, indent_of_block = [], None
    for line in COMPOSE.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent_of_block is not None:
            # `>=`, not `>`: YAML lets a block sequence sit at its KEY's own indentation, and
            # compose files are commonly written that way. With `>` those entries matched
            # neither branch, so they left BOTH `lines` and `pairs` short by one and the scope
            # assertion -- the thing written to stop exactly this -- passed with the source
            # unchecked. Measured with an absolute-path mount appended at the key's indent.
            if stripped.startswith("- ") and indent >= indent_of_block:
                # EVERY entry, not only the ones that look like short-form mounts. Filtering
                # here would make a long-form entry (`- type: bind` with a `source:` key on the
                # next line) invisible to the scope assertion too, so `lines` and `pairs` would
                # both stay short by one and the guard would pass with that source unchecked --
                # measured, and the long form is already live in this file for `env_file`.
                out.append(line)
                continue
            if indent <= indent_of_block:
                indent_of_block = None
        if stripped == "volumes:" and indent > 0:
            indent_of_block = indent
    return out


def _compose_volume_pairs() -> list:
    """(source, target) for every mount line that PARSES.

    Deliberately returns less than `_compose_mount_lines` when a line is unreadable, so the
    caller can compare the two counts and fail on the difference rather than silently checking
    a subset."""
    pairs = []
    for line in _compose_mount_lines():
        match = _MOUNT.match(line)
        if match:
            pairs.append((match.group("src").strip(), match.group("tgt").strip()))
    return pairs


def test_no_compose_mount_source_is_an_absolute_or_home_rooted_path():
    """Closes the one real gap in the repo-wide neutrality sweep.

    `tests/test_no_leaked_files.py` covers every tracked file, so a `/Users/<name>/...` or
    `/home/<name>/...` vault path in this file is already caught -- but its pattern is anchored on those two roots
    and so cannot see `~/vault`, a drive-lettered path, or any other absolute root. A mount
    source is exactly where someone's real vault location would end up."""
    lines = _compose_mount_lines()
    pairs = _compose_volume_pairs()
    assert lines, "found no mount lines in docker-compose.yml; nothing was checked"
    # THE SCOPE ASSERTION, and the reason this guard is split into a loose finder and a strict
    # reader. Asserting only on the violations is the fail-open shape this repo has been bitten
    # by repeatedly: for a NEGATIVE guard, finding nothing IS the success case, so a line the
    # strict pattern cannot read vanishes and looks identical to a line that passed.
    assert len(pairs) == len(lines), (
        f"{len(lines) - len(pairs)} mount line(s) in docker-compose.yml did not parse, so they "
        f"were never checked: {[ln.strip() for ln in lines if not _MOUNT.match(ln)]}. Widen the "
        f"pattern -- do NOT drop the line from scope"
    )
    bad = []
    for source, _target in pairs:
        # The literal, AND any `${VAR:-default}` default -- the default is what ships when the
        # variable is unset, which is the common case and the one a reader never sees exercised.
        candidates = [source.strip("\"'")]
        candidates += re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^{}]*)\}", source)
        for candidate in candidates:
            if (candidate.startswith(("/", "~"))
                    or re.match(r"^[A-Za-z]:[\\/]", candidate)
                    or _HOME_VAR.search(candidate)):
                bad.append((source, candidate))
    assert not bad, (
        f"compose mount sources must be relative or named volumes, never absolute, "
        f"home-rooted or $HOME-rooted: {bad}"
    )


def test_compose_pins_the_vault_directory_into_the_container():
    """The load-bearing line in the compose file, and it closes a Critical.

    `stores/vault.py:_make` is `Vault(os.environ.get("VAULT_DIR") or config.vault_dir or None)`,
    so this env var outranks a configured value BY CONSTRUCTION. It has to, because the config
    directory is bind-mounted from the host: a `job-sluice init` run there leaves an absolute
    HOST path in `vault_dir` that means nothing inside the container.

    Without the pin the failure is silent and unrecoverable. `Vault` never checks that its
    directory exists and `upsert`'s create arm makedirs it, so a wrong path is CREATED in the
    container's ephemeral layer; leads land there as `created`, which is on `ingest/sink.py`'s
    allowlist, so they are recorded in a seen.db that lives in a PERSISTENT volume. Remove the
    container and the notes are gone while seen.db still suppresses them -- forever, since it
    has no removal path."""
    text = COMPOSE.read_text()
    match = re.search(r"^\s*VAULT_DIR:\s*(\S+)\s*$", text, re.MULTILINE)
    assert match, "docker-compose.yml does not pin VAULT_DIR"
    pinned = match.group(1)
    targets = [target for _source, target in _compose_volume_pairs()]
    assert targets, "extracted no volume targets; the comparison below would be vacuous"
    assert pinned in targets, (
        f"VAULT_DIR is pinned to {pinned!r}, which is not one of the container paths anything "
        f"is mounted at ({targets}). The pin only protects the vault if it names the mount"
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


def test_compose_persists_the_whole_working_directory():
    """The second Critical this file closed, and the reason the mount is /work rather than a
    list of five directories.

    The cwd-relative artefact paths in `sluice/` land, under WORKDIR, in
    the container's writable layer -- which `docker compose run --rm` deletes, while the POINTER
    to a rendered CV survives in the persistent vault note. The lead then wedges: `cv run` says
    `skipped-has-cv`, `apply prep` says `missing_file`.

    The cwd-relative set is DERIVED here rather than hand-listed, so a sixth one added later
    cannot silently escape the guarantee -- which is exactly what a hand-list would allow."""
    relative_defaults = []
    for path in (ROOT / "sluice").rglob("*.py"):
        if path.name == "paths.py":
            continue  # the resolver itself, not a consumer
        relative_defaults += re.findall(r'=\s*"(\./[^"]+)"', path.read_text())
    assert relative_defaults, (
        "found no cwd-relative artefact defaults in sluice/; this guard would pass vacuously"
    )
    # WORKDIR is READ from the Dockerfile, never hardcoded here. Hardcoding `/work` was the
    # round-3 finding, raised independently by two reviewers: change `WORKDIR` to anything else
    # and this guard stayed green while the mount went inert and the Critical reopened verbatim.
    # The LAST WORKDIR wins, which is Docker's own rule for repeated instructions.
    workdirs = re.findall(r"^WORKDIR\s+(\S+)\s*$", _uncommented(DOCKERFILE), re.MULTILINE)
    assert workdirs, "no WORKDIR in the Dockerfile; this guard has nothing to anchor on"
    workdir = workdirs[-1].strip('"\'')
    assert workdir.startswith("/"), (
        f"WORKDIR is {workdir!r}, a relative path. Docker resolves it against the previous "
        f"WORKDIR, so the mount below may still be correct -- but this guard compares it to "
        f"mount targets as though it were absolute, and its failure message would name a "
        f"fragment. Spell WORKDIR absolutely."
    )
    targets = [target for _source, target in _compose_volume_pairs()]
    assert workdir in targets, (
        f"docker-compose.yml does not persist {workdir!r}, the container's WORKDIR. "
        f"{len(relative_defaults)} cwd-relative paths in sluice/ resolve under it "
        f"({sorted(set(relative_defaults))}), so without this mount a rendered CV dies with the "
        f"container while the vault note still points at it"
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
