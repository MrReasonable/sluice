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
    return _strip_comments(path.read_text())


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


# A pip install naming the BARE distribution `job-sluice`, with no local wheel or directory
# path -- regardless of interposed flags, quoting, or an extras suffix.
#
# Deliberately not a fixed contiguous literal: the sequencing spec names
# `pip install --no-cache-dir job-sluice` as the ordinary phrasing a literal would silently
# miss. The negative lookbehind/lookahead are what keep `/tmp/wheels/job_sluice-1.0.0.whl` and
# `./job-sluice/...` from matching -- a path-borne name is the ALLOWED case, and the whole point
# of the guard is to tell it apart from an index-borne one.
_PYPI_INSTALL = re.compile(
    r"""pip3?\s+install\b[^\n]*?(?<![\w./'"-])['"]?job-sluice(?:\[[^\]]*\])?['"]?(?![\w./-])"""
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


def _compose_volume_pairs() -> list:
    """(source, target) for every volume line in the compose file.

    Split on the LAST colon preceding an absolute target, greedily: a source like
    `${SLUICE_VAULT:-./vault}` contains its own colon, and a non-greedy split lands inside the
    parameter expansion instead of between source and target."""
    pairs = []
    for line in COMPOSE.read_text().splitlines():
        match = re.match(r"^\s*-\s+(.+):(/[^:]*)$", line)
        if match:
            pairs.append((match.group(1).strip(), match.group(2).strip()))
    return pairs


def test_no_compose_mount_source_is_an_absolute_or_home_rooted_path():
    """Closes the one real gap in the repo-wide neutrality sweep.

    `tests/test_no_leaked_files.py` covers every tracked file, so a `/Users/<name>/...` or
    `/home/<name>/...` vault path in this file is already caught -- but its pattern is anchored on those two roots
    and so cannot see `~/vault`, a drive-lettered path, or any other absolute root. A mount
    source is exactly where someone's real vault location would end up."""
    pairs = _compose_volume_pairs()
    assert pairs, "extracted no volume mounts from docker-compose.yml; nothing was checked"
    bad = []
    for source, _target in pairs:
        # Check the literal AND any `:-default` inside a parameter expansion: the default is
        # what ships when the variable is unset, which is the common case.
        candidates = [source] + re.findall(r":-([^}]*)\}", source)
        for candidate in candidates:
            if candidate.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", candidate):
                bad.append((source, candidate))
    assert not bad, (
        f"compose mount sources must be relative or named volumes, never absolute or "
        f"home-rooted: {bad}"
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
