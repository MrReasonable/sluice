"""Stage the built wheel for nfpm (#104, PR 5 of 7).

nfpm packages FILES; it has no idea what a wheel is. This unpacks the one the release
workflow's `build` job produced into the tree `nfpm.yaml` copies to /usr/lib/job-sluice.

THE HARD INVARIANT, the same one the Dockerfile carries: the package is built from the wheel
in `dist/`, NEVER from `pip install job-sluice`. Installing from PyPI would race the `pypi`
job in the same release and either fail outright or silently ship the PREVIOUS version under
this release's tag.

`zipfile` rather than `pip install --target`: a wheel IS a zip, so the stdlib unpacks it with
no network, no resolver and no build environment -- which keeps this runnable in the same
offline conditions the test suite runs under. `--target` would also drag pyyaml and tzdata in
as vendored copies, and the whole point of the deb/rpm shape is that those come from
`python3-yaml`/`python3-pyyaml` and the distro's own tzdata instead.
"""
import argparse
import pathlib
import shutil
import sys
import zipfile

# Where the staged tree lands. nfpm.yaml's `src` names this exact path, and the guard in
# tests/test_release_publish_wiring.py compares the two BY VALUE -- importing this constant and
# parsing that YAML. An earlier guard text-matched the path against nfpm.yaml's raw source
# instead and was satisfied by a COMMENT there that mentions it, so changing the real `src:`
# stayed green. A constant is what makes the comparison possible at all.
DEFAULT_OUT = pathlib.Path("build/linux-packages/lib")


def find_wheel(dist: pathlib.Path) -> pathlib.Path:
    """The single wheel in `dist`, or an error naming what was found instead.

    The count is ASSERTED, not assumed. Two wheels matter as much as zero: a glob that
    happens to match both would stage whichever it ended on, and the package would carry a
    version nobody chose -- silently, since every later step succeeds on either. The
    Dockerfile makes the same check in `sh` for the same reason.
    """
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"expected exactly one wheel in {dist}/, found {len(wheels)}: "
            f"{[w.name for w in wheels]}. This package is built from the wheel the `build` "
            f"job produced; it must never be resolved from an index."
        )
    return wheels[0]


def stage(wheel: pathlib.Path, out: pathlib.Path) -> None:
    """Unpack `wheel` into `out`, replacing anything already there, with normalised modes.

    Removed first rather than merged into: a stale `sluice/` from an earlier version would
    otherwise contribute modules deleted since, and the package would ship files the wheel
    does not contain.

    THE MODES ARE SET HERE, and this is the second attempt at that. `zipfile.extractall` does
    not restore mode bits, so without normalisation the staged tree carries whatever the build
    runner's umask produced -- 0644/0755 under the usual 022, but 0600/0700 under a stricter
    one, which installs a package readable only by root.

    The first fix put `file_info.mode: 0644` on nfpm.yaml's `type: tree` entry instead. That
    was worse than the problem: nfpm carries a tree's file_info onto the DIRECTORY entries it
    synthesises too, so every directory under /usr/lib/job-sluice shipped without its search
    bit. Measured on the built .deb -- `drw-r--r--` on `sluice/` and every subpackage -- and
    then on a real install: `job-sluice --version` worked as root and died with
    `ModuleNotFoundError: No module named 'sluice'` as an ordinary user. Root bypasses
    directory traversal checks, which is exactly why three container runs as root had reported
    the package healthy.

    Doing it here is testable offline in this suite and needs no new import. It does NOT make
    the result independent of nfpm -- these modes reach the .deb precisely because nfpm's tree
    expansion copies each entry's on-disk mode. What it removes is the dependence on nfpm
    applying ONE BLANKET MODE to files and directories alike, which is the behaviour that broke
    the package. (An earlier version of this sentence claimed the stronger, false thing.)

    Two hazards this loop does not have, both established by execution rather than assumed:
    `zipfile.extractall` does not restore symlinks -- it writes a link's target as ordinary
    file content -- so `Path.chmod` and `Path.is_dir`, which each follow links, cannot reach a
    target outside the tree; and it sanitises `..` members, so a traversal entry lands inside
    `out` rather than above it.
    """
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(out)

    # Directories need the search bit or nothing under them can be reached; regular files must
    # not have it. Set explicitly rather than masked, so the result is the same whatever umask
    # the extraction ran under.
    out.chmod(0o755)
    for path in out.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=pathlib.Path, default=pathlib.Path("dist"))
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    wheel = find_wheel(args.dist)
    stage(wheel, args.out)

    # Asserted rather than trusted: `nfpm.yaml` copies this tree to /usr/lib/job-sluice and the
    # shim puts exactly that path first on sys.path, so a wheel that unpacked without a `sluice/`
    # would produce an installable package whose every invocation dies on ImportError.
    if not (args.out / "sluice" / "__init__.py").is_file():
        raise SystemExit(
            f"{wheel.name} unpacked to {args.out}/ without a sluice/ package -- "
            f"got {sorted(p.name for p in args.out.iterdir())}"
        )

    print(wheel.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
