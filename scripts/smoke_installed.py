#!/usr/bin/env python3
"""Post-release smoke check: does the PUBLISHED package actually install and run?

`tests/test_packaging.py` already inspects a wheel built from the source tree, thoroughly --
templates present, metadata correct, console script named right. What it cannot do is install a
published artefact from a real registry onto a clean machine and execute it, which is a
different question with a different failure set: a missing runtime dependency, a console script
that resolves to nothing, package data that the wheel carries but the .deb's unpack step drops,
an image whose entrypoint is wrong.

Run against an INSTALLED `job-sluice`, never against a checkout:

    python3 scripts/smoke_installed.py 2.2.0

FOR THE .deb/.rpm, POINT PYTHONPATH AT THE SHIPPED TREE:

    PYTHONPATH=/usr/lib/job-sluice python3 scripts/smoke_installed.py 2.2.0

Those packages deliberately do NOT install into system site-packages -- `nfpm.yaml` unpacks the
wheel to /usr/lib/job-sluice and ships /usr/bin/job-sluice as a launcher that adds it to the
path. So the CLI works unaided and `import sluice` does not, which splits this script's checks
in two: the CLI-driven ones pass either way, and the import-driven ones fail with
ModuleNotFoundError unless pointed at the tree. Measured on the published 2.2.0 .deb -- 3 of 5
errored before PYTHONPATH was set, which reads as a broken package and is nothing of the kind.

Every check below is OFFLINE and needs no config, no vault, no browser and no API key. That is
a hard constraint rather than a convenience: a smoke test that needs credentials is one that
gets skipped in the environment it was written for, and the point is to run it everywhere the
package is published.

WHY THIS RUNS AS A NON-ROOT USER in the .deb/.rpm jobs. Verifying a Linux package as root
cannot see a non-root failure and looks identical to success -- root bypasses directory
traversal checks, so a package whose directories are mode `drw-r--r--` installs and runs
perfectly for root and is unusable for everyone else. That shipped once here. The release
workflow now checks the modes statically with `dpkg-deb -c` / `rpm -qlvp`, which is a good
proxy; actually importing the package as an ordinary user is the direct evidence.
"""
import argparse
import os
import subprocess
import sys
import tempfile


# The floor every channel run actually uses: NO workflow passes `--source-floor`, so this
# literal governs all of them. Named rather than inlined in `add_argument` so a guard can pin
# it -- measured, `default=10 -> 0` survived the whole suite, and at 0 `len(ids) < floor` can
# never be true, so a package shipping ZERO source plugins reports `all 5 checks passed`. That
# is the exact failure `check_sources_load` exists to catch, switched off by a literal.
_SOURCE_FLOOR = 10


class SmokeFailure(Exception):
    """A check failed. Raised rather than `sys.exit` so every check can run and report."""


# Variables `sluice/` reads that carry NO `SLUICE_` prefix. A prefix sweep alone misses every
# one of them -- measured, all five reached the child while this file's docstring claimed a
# clean machine. Hand-listed here because nothing in a stdlib-only script can discover them,
# and kept honest by `test_the_env_sweep_covers_every_variable_sluice_reads`, which derives the
# real roster from `sluice/`'s own source and fails when this tuple falls behind.
_UNPREFIXED_ENV = ("SEEN_DB", "VAULT_DIR", "DOSSIER_DIR", "TRIAGE_AUDIT", "EDITOR")

# Prefixes covering the rest. `CAMOFOX_` is here rather than above because it is a family.
_ENV_PREFIXES = ("SLUICE_", "XDG_", "CAMOFOX_")

_SANDBOX_HOME = None


def _sandbox_home():
    """A HOME the caller demonstrably does not own, created once and reused.

    NOT `os.getcwd()`, which was the first version and is a NO-OP for anyone running the script
    from their own home directory: `HOME` is then reassigned to itself, the stripped `XDG_*`
    variables fall back to `~/.config` and `~/.local/state`, and the run reads the caller's real
    install while reporting a clean machine. Measured from a seeded home -- two shipped-enabled
    sources came back `disabled`. The guard that was supposed to catch this compared the child's
    `HOME` against `os.getcwd()`, which is equally true in that case, so it asserted that the
    assignment happened rather than that it changed anything.
    """
    global _SANDBOX_HOME
    if _SANDBOX_HOME is None:
        _SANDBOX_HOME = tempfile.mkdtemp(prefix="sluice-smoke-home-")
    return _SANDBOX_HOME


def _clean_env():
    """The environment a machine that has never run sluice would have.

    `core/paths.py:resolve` consults an explicitly-named value -- env var first, then config key
    -- BEFORE any XDG fallback, so one variable inherited from the caller's shell retargets a
    check at their real install. Measured against a SYNTHETIC config and a SYNTHETIC health
    store: an exported `SLUICE_CONFIG` flipped two sources enabled->disabled, and a seeded
    `SLUICE_HEALTH` moved the reported baseline off zero. That is not an exotic state -- this
    repo's own CLAUDE.md instructs a developer to export `SLUICE_CONFIG`, so it is the NORMAL
    local one, and the docstrings below claim a clean machine on every run.

    Two rosters, because one is not enough: the PREFIX families above, and the UNPREFIXED names
    a prefix sweep cannot see. An earlier version claimed to be "derived by prefix, never
    hand-listed" and was wrong twice over -- the prefixes ARE a hand-list, and they missed
    `SEEN_DB`/`VAULT_DIR`/`DOSSIER_DIR`/`TRIAGE_AUDIT` entirely. Inert at the time, because no
    check here builds a store; the cost was latent, since `sqlite3.connect` creates a 0-byte
    file merely by opening one, which permanently disarms the relocation refusal.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_ENV_PREFIXES) and k not in _UNPREFIXED_ENV}
    env["HOME"] = _sandbox_home()
    return env


def _run(args, *, trust_env=False, **kw):
    """Run a command, returning `(rc, stdout, stderr)`. Never raises on a non-zero exit.

    `cwd` is deliberately NOT the repository: see `check_not_the_source_tree`.

    Sandboxed by DEFAULT rather than per-call: the two call sites that passed no `env=` were
    the ones reading the caller's real config, and a guard every caller must remember to opt
    into is one that a later caller silently does not.

    `trust_env` is the one deliberate exception, and it exists for the CONTAINER channel. The
    shipped image SETS `XDG_CONFIG_HOME`/`XDG_STATE_HOME`/`XDG_CACHE_HOME` (`Dockerfile`), so
    there the variables this function strips ARE the artefact under test -- sandboxing them
    would certify paths the image does not use and leave its real ones unexercised. Nothing a
    developer exported can reach a process inside a freshly pulled image, which is why the
    exception is safe exactly there and nowhere else.
    """
    if not trust_env:
        kw.setdefault("env", _clean_env())
    p = subprocess.run(args, capture_output=True, text=True, timeout=120, **kw)
    return p.returncode, p.stdout, p.stderr



def check_not_the_source_tree(report):
    """The imported `sluice` must not be the working directory's own copy.

    THE check that makes every other one meaningful, and it is first for that reason. Python
    puts the current directory on `sys.path`, so a smoke run executed from a checkout imports
    the SOURCE, passes everything, and certifies a package it never loaded. This repo has
    already been bitten by the same mechanism the other way round -- probing one worktree's
    venv from another measured the wrong tree.

    Locally, against an editable install, this check SHOULD fail: that is the honest answer,
    and it is why the workflow runs from a temporary directory.
    """
    import sluice
    module = os.path.realpath(sluice.__file__)
    # The package's PARENT directory decides it. A checkout has `pyproject.toml` beside
    # `sluice/`; an installed tree -- site-packages, dist-packages, or the .deb's
    # /usr/lib/job-sluice -- does not. Tested this way rather than by comparing against the
    # working directory, which was the first version and is wrong: a venv created inside the
    # temp dir you are running from puts site-packages "inside the working directory" too, so
    # every CI job would have failed on a correct install. Positive evidence about what the
    # module IS beats negative evidence about where you happen to be standing.
    parent = os.path.dirname(os.path.dirname(module))
    if os.path.exists(os.path.join(parent, "pyproject.toml")):
        raise SmokeFailure(
            f"imported sluice from {module}, whose parent {parent} holds a pyproject.toml -- "
            "this is a source checkout, not an installed package. Run from a temp dir with "
            "the checkout off sys.path.")
    report("import path", module)


def check_version(report, expected, trust_env=False):
    """`job-sluice --version`, `sluice.__version__` and the installed distribution metadata
    must all agree with the released tag.

    Three sources, not one, because they can disagree and the repo's own packaging is built so
    they cannot: `pyproject.toml` declares the version `dynamic` and setuptools reads the
    attribute, so a mismatch here means the built artefact was assembled from something other
    than the source it claims. An editable install with stale dist-info produces exactly that,
    and it has already cost a red suite in this repo.
    """
    from importlib.metadata import version as dist_version

    import sluice
    attr = sluice.__version__
    dist = dist_version("job-sluice")
    rc, out, err = _run(["job-sluice", "--version"], trust_env=trust_env)
    if rc != 0:
        raise SmokeFailure(f"`job-sluice --version` exited {rc}: {err.strip() or out.strip()}")
    cli = out.strip().split()[-1] if out.strip() else ""

    disagree = {k: v for k, v in
                {"sluice.__version__": attr, "dist metadata": dist, "--version": cli}.items()
                if v != expected}
    if disagree:
        raise SmokeFailure(
            f"expected {expected!r} everywhere, but: {disagree} "
            "-- the artefact does not agree with itself about what it is")
    report("version", f"{expected} (attribute, metadata and CLI agree)")


def check_sources_load(report, floor, trust_env=False):
    """`ingest list-sources` must enumerate real sources.

    Proves three things at once that `--version` cannot: the console script resolves, the
    plugin package auto-imports its siblings, and the registry is populated. The failure this
    catches is a package that installs and answers `--version` while shipping no source
    modules at all -- every board silently gone, which reads as a healthy install.

    A FLOOR rather than an exact count: sources are added and retired routinely, and a smoke
    test that has to be edited whenever a board is added is one that gets disabled. The floor
    is far below the real fleet, because what is being distinguished is "the plugins shipped"
    from "they did not", not one count from another.
    """
    rc, out, err = _run(["job-sluice", "ingest", "list-sources"], trust_env=trust_env)
    if rc != 0:
        raise SmokeFailure(f"`ingest list-sources` exited {rc}: {err.strip() or out.strip()}")
    ids = [ln.split()[0] for ln in out.splitlines() if ln.strip()]
    if len(ids) < floor:
        raise SmokeFailure(
            f"`ingest list-sources` listed {len(ids)} source(s), expected at least {floor} -- "
            f"the plugin package did not ship or failed to autoload. Got: {ids}")
    report("sources", f"{len(ids)} enumerated")


def check_packaged_template(report):
    """The shipped Jinja2 template must be present as PACKAGE DATA in the install.

    Package data is the half of a build that metadata checks miss and that a source-tree test
    cannot see at all: running from a checkout, the template is simply there on disk. It is
    reachable through `importlib.resources` only if the artefact actually carried it -- and
    the `.deb`/`.rpm` unpack a wheel into `/usr/lib/job-sluice`, a second chance to lose it.

    Read as bytes rather than merely listed, since a zero-byte or unreadable entry would
    satisfy an existence check while breaking the renderer.
    """
    from importlib.resources import files
    template = files("sluice") / "templates" / "cv_plain.html.j2"
    if not template.is_file():
        raise SmokeFailure(
            "sluice/templates/cv_plain.html.j2 is missing from the installed package -- "
            "package data did not survive the build, so the `template` renderer cannot run")
    if not template.read_bytes().strip():
        raise SmokeFailure("the packaged template is present but empty")
    report("package data", "cv_plain.html.j2 present and non-empty")


def check_offline_commands(report, trust_env=False):
    """A handful of commands must work with NO config, NO vault and NO network.

    `--help` proves argparse builds its whole tree, which is more than it sounds: `cli.py`
    imports the evidence command package inside `_build_parser`, so a missing module there
    fails here rather than at first use. `ingest list-sources --health` additionally exercises
    the health store's path resolution on a machine that has never run sluice -- which `_run`
    now delivers by stripping `SLUICE_*`/`XDG_*` rather than merely repointing HOME, since
    those outrank it.

    `doctor` is deliberately NOT included: it probes backends and the vault, so on a clean
    machine its failure is correct behaviour and would make this test a liar.
    """
    for args in (["--help"], ["ingest", "list-sources", "--health"]):
        rc, out, err = _run(["job-sluice", *args], trust_env=trust_env)
        if rc != 0:
            raise SmokeFailure(
                f"`job-sluice {' '.join(args)}` exited {rc} on a clean machine: "
                f"{err.strip() or out.strip()}")
    report("offline commands", "--help and list-sources --health both exit 0")


CHECKS = (
    ("import path", check_not_the_source_tree),
    ("version", check_version),
    ("sources", check_sources_load),
    ("package data", check_packaged_template),
    ("offline commands", check_offline_commands),
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version", help="the released version the artefact must claim, e.g. 2.2.0")
    ap.add_argument("--channel", default="unknown",
                    help="label for the output only (pypi, deb, rpm, docker, ...)")
    ap.add_argument("--source-floor", type=int, default=_SOURCE_FLOOR,
                    help="minimum sources `ingest list-sources` must enumerate")
    ap.add_argument("--trust-env", action="store_true",
                    help="do not sandbox the environment -- for the CONTAINER channel, where "
                         "the image's own XDG_* variables are the artefact under test")
    args = ap.parse_args(argv)

    lines, failures = [], []

    def report(name, detail):
        lines.append(f"  ok    {name}: {detail}")

    for name, fn in CHECKS:
        try:
            if fn is check_version:
                fn(report, args.version, trust_env=args.trust_env)
            elif fn is check_sources_load:
                fn(report, args.source_floor, trust_env=args.trust_env)
            elif fn is check_offline_commands:
                fn(report, trust_env=args.trust_env)
            else:
                fn(report)
        except SmokeFailure as exc:
            failures.append((name, str(exc)))
            lines.append(f"  FAIL  {name}: {exc}")
        except Exception as exc:                      # noqa: BLE001 -- report, never mask
            # A check raising something unexpected is itself a finding: it means the installed
            # package could not even be interrogated. Reported with its type so the cause is
            # not flattened into "something went wrong".
            failures.append((name, f"{type(exc).__name__}: {exc}"))
            lines.append(f"  ERROR {name}: {type(exc).__name__}: {exc}")

    print(f"post-release smoke [{args.channel}] job-sluice {args.version}")
    print("\n".join(lines))
    if failures:
        print(f"\n{len(failures)} of {len(CHECKS)} checks failed on the {args.channel} artefact.")
        return 1
    print(f"\nall {len(CHECKS)} checks passed on the {args.channel} artefact.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
