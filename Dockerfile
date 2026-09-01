# job-sluice as a container image (#104, PR 4 of 7).
#
# THE POINT OF THIS IMAGE is the `render` extra working with zero host setup. WeasyPrint links
# natively against cairo/pango/gdk-pixbuf, so `pip install` can never supply them -- README.md
# and docs/TROUBLESHOOTING.md both say so in terms.
#
# This was once "the only place it can be solved once, for everybody". #104's PR 5 falsified
# that: the .deb and .rpm recommend WeasyPrint, so apt and dnf resolve the same libraries
# natively. Two things still distinguish the image. It works on any host Docker runs on rather
# than only the Debian and Fedora families, so it is the one packaged answer on macOS and
# Windows until the Homebrew tap lands; and it INSTALLS those libraries rather than recommending
# them, so no local policy can decline them the way --no-install-recommends can.
#
# The base image is DIGEST-pinned, the same discipline every `uses:` in .github/workflows/ takes.
# The trailing comment names the tag the digest resolved from; .github/dependabot.yml's `docker`
# ecosystem is what keeps it current, and without that entry this pin would freeze permanently
# and accrue CVEs while LOOKING well-secured -- the argument dependabot.yml's own header already
# makes for action pins (#3). The digest is the multi-arch INDEX digest, not a per-platform one,
# which is what lets one FROM serve both linux/amd64 and linux/arm64.
#
# The version tag is kept IN the reference rather than in a trailing comment: a Dockerfile
# `FROM` takes one or three arguments and rejects a trailing `# tag` outright (measured -- it is
# a parse error, not a lint warning), so the `uses: ...@sha # vX.Y.Z` idiom every workflow file
# here uses does NOT transfer. `name:tag@digest` is valid and records the same fact.
FROM python:3.14-slim@sha256:656d12e70054d5fda18a045e2494c96701e9792dd1445f95b3d038df954f57e9

# WeasyPrint's runtime shared libraries, plus an ssh CLIENT. NOT a Python dependency and not
# makeable into one.
#
# `openssh-client` is here for the `claude-max` backend (#209), which is the SHIPPED DEFAULT for
# primary_backend. It shells out to the `claude` CLI, and this image deliberately does not carry
# that CLI -- it is a ~325MB self-contained binary and bundling it would more than double the
# image for a backend many users do not choose. `ClaudeMaxBackend` already knows how to reach one
# elsewhere (`cmd_template = ["ssh", host] + base` when a host is configured), so what was missing
# was only the client to do it with. Measured on this image: +11MB (573 -> 584), against the
# ~325MB the bundled CLI would add.
#
# It gives the container no ACCESS by itself: no key is baked in, and the host must authorise one.
# See docs/INSTALL.md's Docker section for the key and the forced-command wrapper that restricts
# it to a single `claude --print`.
#
# No `tzdata` package here, deliberately: pyproject.toml already ships the `tzdata` WHEEL as a
# runtime dependency, and its comment gives this exact case as the reason ("a bare container
# often does not [have the system tz database] either"). Installing the apt package too would
# be a second source of truth for the same data.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libcairo2 \
      libpango-1.0-0 \
      libpangoft2-1.0-0 \
      libgdk-pixbuf-2.0-0 \
      libffi8 \
      shared-mime-info \
      fonts-dejavu-core \
      openssh-client \
 && rm -rf /var/lib/apt/lists/*

# THE HARD INVARIANT: this installs the wheel built by the release workflow's `build` job, which
# arrives in the build context at dist/. It must NEVER be `pip install job-sluice` from PyPI --
# that races the `pypi` job in the same release and would either fail outright or silently ship
# the PREVIOUS version under this release's tag. tests/test_docker_channel.py pins this against
# the Dockerfile source, tolerantly enough that interposed flags or quoting cannot defeat it.
COPY dist/*.whl /tmp/wheels/

# The wheel count is asserted, not assumed. A glob that matches nothing does not expand to
# nothing in POSIX sh -- it expands to its own literal text, so an unguarded loop would run once
# over a filename that does not exist and "prove" there was exactly one wheel. That is the same
# present-and-inert failure `shopt -s nullglob` exists to close in .github/workflows/testpypi.yml,
# in a shell that has no nullglob to switch on; `[ -f "$w" ] || continue` is the stand-in.
# Two wheels matter as much as zero: pip would install whichever the glob happened to end on.
RUN set -eu; \
    n=0; whl=''; \
    for w in /tmp/wheels/*.whl; do [ -f "$w" ] || continue; n=$((n + 1)); whl="$w"; done; \
    if [ "$n" -ne 1 ]; then \
      echo "expected exactly one wheel in the build context's dist/, found $n" >&2; \
      exit 1; \
    fi; \
    pip install --no-cache-dir "${whl}[render,google,mcp,completion]"; \
    rm -rf /tmp/wheels

# A non-root user whose home is /app, NOT under the usual home root.
#
# tests/test_no_leaked_files.py sweeps EVERY tracked file for /home/<x> and /Users/<x> -- its
# _GATE_PATHSPEC is empty, which means exactly that, and a test forbids narrowing it. Only three
# home-rooted literals are allow-listed in the whole repo, none of them usable here. So an
# ordinary `useradd -m -d /home/<name>` would fail the suite, in a file nobody would think to
# look at for a neutrality violation. /app sidesteps it and reads better anyway. (The angle
# brackets above are load-bearing too: the sweep's character class excludes `<` and `>` so a
# documented placeholder is not itself a hit -- this comment was caught by that guard, in
# exactly the spelling it warns about, before it reached CI.)
RUN useradd --create-home --home-dir /app --shell /usr/sbin/nologin --uid 1000 sluice

# XDG roots, spelled ABSOLUTELY. sluice/core/paths.py IGNORES a relative XDG_* value (it warns
# and falls back), so a relative value here would silently relocate every piece of state to the
# fallback under $HOME with only a log line to say so. These three paths are also the documented
# bind-mount points -- see docker-compose.yml.
ENV XDG_CONFIG_HOME=/app/config \
    XDG_STATE_HOME=/app/state \
    XDG_CACHE_HOME=/app/cache

# Pre-created and owned by `sluice` so that a fresh Docker NAMED VOLUME mounted at any of them
# inherits this ownership rather than being created root-owned, which is what would otherwise
# make the first write fail as a non-root user. Docker seeds an empty named volume from the
# image's directory at that path, ownership included; there is nothing equivalent for a bind
# mount, whose ownership comes from the host (see docker-compose.yml's note).
RUN mkdir -p /app/config/sluice /app/state/sluice /app/cache/sluice /work \
 && chown -R sluice:sluice /app /work

# /work, not /app: the five CV working directories, the render script and DEFAULT_VAULT are
# deliberately cwd-relative in this codebase (docs/CONFIGURATION.md calls them "a workspace
# you're standing in, not per-system state"), so the container needs a stable, writable cwd for
# them that is distinct from the per-system state above.
WORKDIR /work
USER sluice

# Build-time only, so no literal version string lives in this file -- which also keeps it out of
# tests/test_release_version.py's marker walk. `image.source` is the load-bearing one: it is what
# links the GHCR package to this repository.
ARG VERSION=0.0.0+unknown
ARG REVISION=unknown
LABEL org.opencontainers.image.title="job-sluice" \
      org.opencontainers.image.description="A job-application pipeline: ingest, triage, CV tailoring, apply, track." \
      org.opencontainers.image.source="https://github.com/MrReasonable/sluice" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

# The entrypoint prepares SSH for the claude-max backend when a key is supplied, then execs
# job-sluice -- see packaging/docker-entrypoint.sh for why a copy is needed rather than a bind
# mount straight into ~/.ssh. With no key supplied it does nothing but exec, so an API-key
# install pays nothing for it.
COPY --chown=sluice:sluice packaging/docker-entrypoint.sh /usr/local/bin/sluice-entrypoint
ENTRYPOINT ["/usr/local/bin/sluice-entrypoint"]
CMD ["--help"]
