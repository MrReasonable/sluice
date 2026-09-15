#!/usr/bin/env python3
"""Decisions, and the thin I/O around them, for publishing the Homebrew bottles (#279).

Design: docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md. Every decision the release
channel makes lives here as a pure function over plain data, so its refuse branches are tested
offline (tests/test_homebrew_bottles.py) rather than by a dry run that only ever takes the accept
branch. The CLI at the bottom is the layer the workflow steps call.

THE TRUST RULE THIS FILE SERVES. A job either runs third-party code and references no secret, or
holds the tap token and runs only this file and scripts/render_homebrew_formula.py, which it loads from
its file to compare the formula with, and `git`, which it runs itself. The token jobs invoke it as
`python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"`: `-P` removes the script's own directory from
`sys.path`, and this file adds no entry of its own. Every artifact they read is untrusted data this
file validates before anything is published. Both files are standard library only, for that reason: a
dependency would be third-party code running beside the token.
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import NamedTuple

FORMULA_NAME = "job-sluice"
TAP_REPO = "homebrew-tap"

# The repository root, which the renderer is loaded from (see `_renderer`). The renderer is the one
# thing the validator must reproduce rather than restate, and loading it from its file adds nothing to
# sys.path, which `-P` keeps free of the script's own directory.
ROOT = Path(__file__).resolve().parent.parent

# The (runner label, bottle tag) pairs, declared ONCE. `plan` emits them for the bottle matrix, and
# tests/test_homebrew_bottles.py restates them by hand rather than importing them: an expectation
# read from here would compare this constant with itself. A runner's macOS decides the tag it
# builds, so a label that moves (`macos-latest`) would silently change which users get a bottle.
PLATFORMS = (("macos-15", "arm64_sequoia"), ("macos-26", "arm64_tahoe"))

PUSH_TARGETS = ("default", "auto")

_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_POSITIVE_INT_RE = re.compile(r"[1-9][0-9]*")
_HEX40_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
# GitHub account names: alphanumerics and hyphens, not starting with a hyphen, at most 39 chars.
_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
_SDIST_URL_RE = re.compile(
    r"https://files\.pythonhosted\.org/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/"
    r"(?P<file>[A-Za-z0-9._+!-]+)"
)


class Refusal(Exception):
    """A check refused. The CLI prints the message as a GitHub `::error::` and exits 1."""


# --- inputs -------------------------------------------------------------------------------------


def validate_push_target(value: str | None) -> str:
    """`default` (the release) or `auto` (the dry run), and nothing else.

    Fail loudly, naming both values: this input replaced an inferred observable that could not
    tell the release from the dry run, and a guessed default is the failure it exists to prevent.
    """
    if value not in PUSH_TARGETS:
        raise Refusal(
            f"PUSH_TARGET must be 'default' or 'auto', got {value!r}. 'default' always pushes the "
            f"tap's default branch (the release); 'auto' pushes it only while "
            f"Formula/{FORMULA_NAME}.rb does not exist yet, and a bump-VERSION scratch branch "
            f"after that (the dry run)."
        )
    return value


def validate_version(value: str | None) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise Refusal(f"VERSION must be three dot-separated integers (X.Y.Z), got {value!r}.")
    return value


def version_tuple(value: str) -> tuple[int, int, int]:
    """Integers, never strings: this project's releases cross 2.9.x to 2.10.0."""
    major, minor, patch = (int(part) for part in validate_version(value).split("."))
    return major, minor, patch


def validate_sdist(version: str, url: str | None, sha256: str | None) -> None:
    """The renderer writes the URL into a Ruby string unescaped, so it is checked before use."""
    expected_file = f"job_sluice-{validate_version(version)}.tar.gz"
    match = _SDIST_URL_RE.fullmatch(url or "")
    if match is None or match.group("file") != expected_file:
        raise Refusal(
            f"PyPI's sdist URL for {version} must be a files.pythonhosted.org package URL for "
            f"{expected_file}, got {url!r}."
        )
    if not _HEX64_RE.fullmatch(sha256 or ""):
        raise Refusal(f"PyPI's sdist sha256 for {version} must be 64 hex digits, got {sha256!r}.")


def pick_sdist(pypi_json: dict, version: str) -> tuple[str, str]:
    urls = pypi_json.get("urls") if isinstance(pypi_json, dict) else None
    sdists = [u for u in urls or [] if isinstance(u, dict) and u.get("packagetype") == "sdist"]
    if len(sdists) != 1:
        raise Refusal(f"expected exactly one sdist for {version} on PyPI, found {len(sdists)}.")
    url = sdists[0].get("url")
    sha256 = (sdists[0].get("digests") or {}).get("sha256")
    validate_sdist(version, url, sha256)
    return url, sha256


def tap_owner(repository_owner: str | None) -> str:
    """Lower-cased: Homebrew's `Taps/<owner>` directory is lower case, and GitHub serves release
    downloads under a lower-cased owner (spec, section 3)."""
    if not _OWNER_RE.fullmatch(repository_owner or ""):
        raise Refusal(f"repository owner {repository_owner!r} is not a GitHub account name.")
    return repository_owner.lower()


def compose_tag(version: str, run_id: str, run_attempt: str) -> str:
    """One tag per run attempt: no release asset is ever replaced (spec, section 3)."""
    validate_version(version)
    for label, value in (("run id", run_id), ("run attempt", run_attempt)):
        if not _POSITIVE_INT_RE.fullmatch(value or ""):
            raise Refusal(f"the {label} must be a positive integer, got {value!r}.")
    return f"{FORMULA_NAME}-{version}-{run_id}-{run_attempt}"


def compose_root_url(repository_owner: str, tag: str) -> str:
    return f"https://github.com/{tap_owner(repository_owner)}/{TAP_REPO}/releases/download/{tag}"


# --- the tap ------------------------------------------------------------------------------------


def parse_symref(ls_remote_output: str) -> tuple[str, str]:
    """(default branch, tip SHA) from `git ls-remote --symref <tap url> HEAD`."""
    branch = sha = None
    for line in ls_remote_output.splitlines():
        ref = re.fullmatch(r"ref: refs/heads/([A-Za-z0-9._/-]+)\tHEAD", line)
        if ref:
            branch = ref.group(1)
            continue
        tip = re.fullmatch(r"([0-9a-f]{40})\tHEAD", line)
        if tip:
            sha = tip.group(1)
    if branch is None or sha is None:
        raise Refusal(f"could not read the tap's default branch and tip from {ls_remote_output!r}.")
    return branch, sha


def formula_state_from_status(status: int) -> str:
    """Three values, never two: a rate-limit 403 read as 'absent' would push a dry run to the
    tap's default branch."""
    return {200: "present", 404: "absent"}.get(status, "error")


def resolve_target(push_target: str, formula_state: str, default_branch: str, version: str) -> str:
    validate_push_target(push_target)
    if push_target == "default":
        return default_branch
    if formula_state == "present":
        return f"bump-{validate_version(version)}"
    if formula_state == "absent":
        return default_branch
    raise Refusal(
        f"could not tell whether Formula/{FORMULA_NAME}.rb exists at the tap's base commit "
        f"(state {formula_state!r}). Refusing rather than reading it as absent, which would push a "
        f"dry run to the tap's default branch."
    )


def caller_for(push_target: str) -> str:
    return {"default": "release", "auto": DRY_RUN_CALLER}[validate_push_target(push_target)]


# --- platforms ----------------------------------------------------------------------------------


def platforms_json() -> str:
    return json.dumps([{"runner": runner, "tag": tag} for runner, tag in PLATFORMS])


def declared_tags(platforms: str) -> list[str]:
    """The bottle tags from `platforms_json()`'s output, as every job after `plan` reads them."""
    try:
        entries = json.loads(platforms)
    except (TypeError, ValueError) as err:
        raise Refusal(f"the declared platform set is not JSON: {platforms!r}.") from err
    if not isinstance(entries, list) or not entries:
        raise Refusal(f"the declared platform set is empty or not a list: {platforms!r}.")
    tags = [entry.get("tag") for entry in entries if isinstance(entry, dict)]
    if (
        len(tags) != len(entries)
        or not all(isinstance(tag, str) and tag for tag in tags)
        or len(set(tags)) != len(tags)
    ):
        raise Refusal(f"the declared platform set is malformed: {platforms!r}.")
    return tags


# --- the bottle jobs ----------------------------------------------------------------------------

# The only cellars a relocatable libexec venv bottles as. A path-valued cellar would be written
# into the formula unescaped by `brew bottle --merge` (BOTTLE_ERB), so anything else is refused.
ALLOWED_CELLARS = ("any", "any_skip_relocation")


def check_pour(info: dict, *, full_name: str, version: str, expect_built: bool = False) -> None:
    """One installed keg of THIS formula, at VERSION, poured (or, with `expect_built`, built).

    Scoped to the named formula on purpose: `brew info --json=v2 --installed` lists every installed
    formula whatever name is given, and every dependency on the runner was poured, so an unscoped
    check passes when job-sluice itself built from source (spec, section 6a). `expect_built` is the
    negative control's own mode: a `!`-inverted command never fails a `bash -e` step.
    """
    formulae = info.get("formulae") if isinstance(info, dict) else None
    if not isinstance(formulae, list) or len(formulae) != 1 or not isinstance(formulae[0], dict):
        raise Refusal(f"brew info must describe exactly one formula, got {formulae!r:.300}.")
    formula = formulae[0]
    if formula.get("full_name") != full_name:
        raise Refusal(f"brew info describes {formula.get('full_name')!r}, expected {full_name!r}.")
    installed = formula.get("installed")
    if not isinstance(installed, list) or len(installed) != 1 or not isinstance(installed[0], dict):
        raise Refusal(f"{full_name} must have exactly one installed keg, found {installed!r:.300}.")
    keg = installed[0]
    if keg.get("version") != version:
        raise Refusal(f"{full_name}'s installed keg is {keg.get('version')!r}, expected {version!r}.")
    poured = keg.get("poured_from_bottle")
    wanted = not expect_built
    if poured is not wanted:
        raise Refusal(
            f"{full_name} {version} reports poured_from_bottle={poured!r}, expected {wanted!r}. "
            + ("It was built from source: no bottle tag matched, or the bottle was not selected."
               if wanted else "The negative control expected the --build-bottle keg.")
        )


def check_produced_tag(bottle_json: dict, declared_tag: str) -> None:
    """The tag `brew bottle` produced must be the matrix entry's, never a runner-side value."""
    if not declared_tag:
        raise Refusal("the declared bottle tag is empty.")
    entries = list(bottle_json.values()) if isinstance(bottle_json, dict) else []
    if len(entries) != 1 or not isinstance(entries[0], dict):
        raise Refusal(f"a bottle JSON must describe exactly one formula, found {len(entries)}.")
    bottle = entries[0].get("bottle")
    tags = bottle.get("tags") if isinstance(bottle, dict) else None
    tags = tags if isinstance(tags, dict) else {}
    if list(tags) != [declared_tag]:
        raise Refusal(
            f"this runner produced bottle tag(s) {sorted(tags)}, but its matrix entry declares "
            f"{declared_tag!r}. The runner image's macOS no longer matches its label."
        )


class Asset(NamedTuple):
    tag: str
    remote_name: str
    local_path: Path
    sha256: str


def validate_bottle_jsons(
    json_paths: list[Path], *, version: str, root_url: str, tags: list[str]
) -> list[Asset]:
    """Every bottle JSON and bottle file, checked as data before the token exists (spec, section 3).

    The names are built here from trusted values and compared with Homebrew's, never uploaded on
    assumption: the 2026-09-07 `curl: (37)` came from an assumed name.
    """
    validate_version(version)
    if not tags:
        raise Refusal("the declared tag set is empty.")
    found: dict[str, Asset] = {}
    for path in json_paths:
        path = Path(path)
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as err:
            raise Refusal(f"{path.name} is not readable JSON: {err}.") from err
        entries = list(data.values()) if isinstance(data, dict) else []
        if len(entries) != 1 or not isinstance(entries[0], dict):
            raise Refusal(f"{path.name} must describe exactly one formula.")
        bottle = entries[0].get("bottle")
        bottle = bottle if isinstance(bottle, dict) else {}
        tag_hashes = bottle.get("tags") if isinstance(bottle.get("tags"), dict) else {}
        if len(tag_hashes) != 1:
            raise Refusal(f"{path.name} must carry exactly one tag, found {sorted(tag_hashes)}.")
        ((tag, tag_hash),) = tag_hashes.items()
        if tag not in tags:
            raise Refusal(f"{path.name} carries the undeclared tag {tag!r}; declared: {sorted(tags)}.")
        if tag in found:
            raise Refusal(f"two bottle JSONs carry the tag {tag!r}.")
        if bottle.get("root_url") != root_url:
            raise Refusal(
                f"{path.name}'s root_url is {bottle.get('root_url')!r}, expected {root_url!r}."
            )
        rebuild = bottle.get("rebuild", 0)
        if type(rebuild) is not int or rebuild != 0:
            raise Refusal(f"{path.name}'s rebuild is {rebuild!r}; brew bottle must run --no-rebuild.")
        if bottle.get("cellar") not in ALLOWED_CELLARS:
            raise Refusal(
                f"{path.name}'s cellar is {bottle.get('cellar')!r}, not one of {list(ALLOWED_CELLARS)}."
            )
        remote = f"{FORMULA_NAME}-{version}.{tag}.bottle.tar.gz"
        local = f"{FORMULA_NAME}--{version}.{tag}.bottle.tar.gz"
        if not isinstance(tag_hash, dict) or (tag_hash.get("filename"), tag_hash.get("local_filename")) != (remote, local):
            raise Refusal(
                f"{path.name}'s names are not ({remote!r}, {local!r}): "
                f"{tag_hash.get('filename') if isinstance(tag_hash, dict) else tag_hash!r}."
            )
        sha256 = tag_hash.get("sha256")
        if not isinstance(sha256, str) or not _HEX64_RE.fullmatch(sha256):
            raise Refusal(f"{path.name}'s sha256 is not 64 hex digits: {sha256!r}.")
        bottle_file = path.parent / local
        if not bottle_file.is_file():
            raise Refusal(f"{local} is missing beside {path.name}.")
        actual = hashlib.sha256(bottle_file.read_bytes()).hexdigest()
        if actual != sha256:
            raise Refusal(f"{local} hashes to {actual}, but {path.name} declares {sha256}.")
        found[tag] = Asset(tag, remote, bottle_file, sha256)
    missing = sorted(set(tags) - set(found))
    if missing:
        raise Refusal(f"the bottle JSONs do not match the declared tags: missing {missing}.")
    return [found[tag] for tag in tags]


# The block `brew bottle --merge --write` writes (dev-cmd/bottle.rb, BOTTLE_ERB): a root_url line,
# then one sha256 line per tag and nothing else, anchored at both ends. A `rebuild` line, a second
# block or any extra token fails the match.
_BLOCK_RE = re.compile(
    r"^  bottle do\n"
    r'    root_url "(?P<root_url>[^"\n]*)"\n'
    r"(?P<lines>(?:    sha256 [^\n]*\n)+)"
    r"  end\n",
    re.MULTILINE,
)
# The cellar alternation is built from ALLOWED_CELLARS, so the JSON check and the block grammar name one
# vocabulary and cannot drift apart.
_SHA_LINE_RE = re.compile(
    r"    sha256 cellar: :(?P<cellar>"
    + "|".join(re.escape(cellar) for cellar in ALLOWED_CELLARS)
    + r"), +"
    r'(?P<tag>[a-z0-9_]+): +"(?P<sha256>[0-9a-f]{64})"'
)


def parse_bottle_block(text: str) -> tuple[str, dict[str, tuple[str, str]], re.Match]:
    """(root_url, {tag: (cellar, sha256)}, the block's match) for the formula's one bottle block."""
    blocks = text.count("bottle do")
    if blocks != 1:
        raise Refusal(f"the formula must contain exactly one `bottle do` block, found {blocks}.")
    match = _BLOCK_RE.search(text)
    if match is None:
        raise Refusal(
            "the formula's bottle block is not in the shape brew bottle --merge writes: a root_url "
            "line, then only sha256 lines."
        )
    tags: dict[str, tuple[str, str]] = {}
    for line in match.group("lines").splitlines():
        line_match = _SHA_LINE_RE.fullmatch(line)
        if line_match is None:
            raise Refusal(f"unexpected line in the bottle block: {line!r}.")
        if line_match.group("tag") in tags:
            raise Refusal(f"the bottle block names {line_match.group('tag')!r} twice.")
        tags[line_match.group("tag")] = (line_match.group("cellar"), line_match.group("sha256"))
    return match.group("root_url"), tags, match


def check_merged_tags(formula_text: str, tags: list[str]) -> None:
    """The merged block's tags equal the declared set, which comes from `plan`, never from the
    block being checked (spec, section 6b)."""
    if not tags:
        raise Refusal("the declared tag set is empty.")
    _, block_tags, _ = parse_bottle_block(formula_text)
    if set(block_tags) != set(tags):
        raise Refusal(
            f"the merged bottle block carries {sorted(block_tags)}, but {sorted(tags)} are declared."
        )


def check_cache_file(data: bytes, formula_text: str, tag: str) -> None:
    """The file `brew fetch` left for TAG hashes to the block's digest. Required because `brew
    fetch --bottle-tag` exits 0 for a tag the formula does not carry (spec, section 6b)."""
    _, block_tags, _ = parse_bottle_block(formula_text)
    if tag not in block_tags:
        raise Refusal(f"the formula's bottle block carries no {tag!r} bottle.")
    actual = hashlib.sha256(data).hexdigest()
    expected = block_tags[tag][1]
    if actual != expected:
        raise Refusal(f"the fetched {tag} bottle hashes to {actual}; the formula declares {expected}.")


# --- the release on the tap ---------------------------------------------------------------------

_TAG_RE = re.compile(rf"{FORMULA_NAME}-[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*-[1-9][0-9]*")
_RUN_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/actions/runs/[1-9][0-9]*")
DRY_RUN_CALLER = "dry run"
CALLERS = ("release", DRY_RUN_CALLER)


def release_templates(*, version: str, tag: str, caller: str, run_url: str) -> tuple[str, str]:
    """The release's title and notes: fixed text from trusted values, never command output or
    generated notes. The caller is in the title so a human can tell a release's bottles from a dry
    run's before deleting anything (spec, Out of scope)."""
    validate_version(version)
    if not _TAG_RE.fullmatch(tag or ""):
        raise Refusal(f"release tag {tag!r} is not a composed bottle tag.")
    if caller not in CALLERS:
        raise Refusal(f"caller must be one of {list(CALLERS)}, got {caller!r}.")
    if not _RUN_URL_RE.fullmatch(run_url or ""):
        raise Refusal(f"run URL {run_url!r} is not a GitHub Actions run URL.")
    title = f"{FORMULA_NAME} {version} bottles ({caller})"
    notes = f"Bottles for {FORMULA_NAME} {version}, published by {run_url} ({caller}).\nRelease tag: {tag}\n"
    return title, notes


def release_decision(
    releases: list[dict], *, tag: str, base_sha: str, title: str, notes: str
) -> dict | None:
    """None when the release is absent (create it as a draft); the release when it matches; a
    refusal naming what differs otherwise. Drafts count: a partial upload leaves one behind for a
    re-run to complete (spec, section 3)."""
    matches = [r for r in releases if isinstance(r, dict) and r.get("tag_name") == tag]
    if not matches:
        return None
    if len(matches) > 1:
        raise Refusal(f"{len(matches)} releases carry the tag {tag!r}.")
    release = matches[0]
    wanted = {"target_commitish": base_sha, "name": title, "body": notes}
    differs = sorted(key for key, value in wanted.items() if release.get(key) != value)
    if differs:
        raise Refusal(
            f"a release already carries the tag {tag!r} but its {', '.join(differs)} differ from "
            f"this run's. Refusing to publish into a release this run did not create."
        )
    return release


def asset_decision(assets: list[dict], *, name: str, sha256: str) -> str:
    """'upload' when absent, 'skip' when the same bytes are already there; a refusal otherwise, so
    no asset a pushed formula might reference is ever replaced."""
    existing = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
    if not existing:
        return "upload"
    digest = existing[0].get("digest")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        # A re-run refuses this asset every time, so the refusal names the one step that clears it.
        raise Refusal(
            f"release asset {name!r} has no usable digest ({digest!r}); an interrupted upload can "
            f"leave one. Delete that asset from the draft release, then re-run the failed jobs."
        )
    if digest.removeprefix("sha256:") != sha256:
        raise Refusal(
            f"asset {name!r} already exists with digest {digest}, but this run's bottle is "
            f"sha256:{sha256}. Refusing to replace it."
        )
    return "skip"


def release_digests(assets: list[dict], *, version: str, tags: list[str]) -> dict[str, str]:
    """{tag: sha256} read from a published release's assets, for the formula validator."""
    validate_version(version)
    digests: dict[str, str] = {}
    for tag in tags:
        name = f"{FORMULA_NAME}-{version}.{tag}.bottle.tar.gz"
        matches = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
        if len(matches) != 1:
            raise Refusal(f"the release must hold exactly one {name!r}, found {len(matches)}.")
        digest = matches[0].get("digest")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise Refusal(f"asset {name!r} has no usable digest ({digest!r}).")
        digests[tag] = digest.removeprefix("sha256:")
    return digests


# --- the pushed formula -------------------------------------------------------------------------

# One resource stanza as `brew update-python-resources` writes it: four lines and a blank one.
# `#`, `{`, `}`, `"`, `;` and spaces appear in no field's character set, so a quoted value cannot
# carry Ruby interpolation or a second statement (spec, section 5). The pattern carries no `^`: the
# run must be contiguous and sit directly above `_RESOURCES_PRECEDE`, and together with the comparison
# against the renderer's text that puts every stanza at the start of a line.
_STANZA_RE = re.compile(
    r'  resource "(?P<name>[A-Za-z0-9._-]+)" do\n'
    r'    url "https://files\.pythonhosted\.org/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/'
    r'(?P<project>[A-Za-z0-9._-]+)-(?P<version>[0-9][A-Za-z0-9.+!]*)\.tar\.gz"\n'
    r'    sha256 "[0-9a-f]{64}"\n'
    r"  end\n"
    r"\n"
)

# render() emits no resource, so `brew update-python-resources` takes
# utils/ast.rb::replace_resource_stanzas' insert arm, which writes one contiguous run directly above
# `def install`. (A formula that already had resources would have its group replaced in place
# instead; this pipeline never produces one.)
_RESOURCES_PRECEDE = "  def install\n"  # measured anchor

# `brew bottle --merge --write` adds the block directly after the `license` line and a blank line
# (utils/ast.rb::add_stanza). Anywhere else, removing it still restores the renderer's text, below the
# class's closing `end` included, so its position is checked too.
_BLOCK_FOLLOWS_RE = re.compile(r'\n  license "[^"\n]+"\n\n\Z')  # measured anchor


def _normalise(name: str) -> str:
    """PEP 503 name normalisation."""
    return re.sub(r"[-_.]+", "-", name).lower()


@functools.lru_cache(maxsize=1)
def _renderer():
    """The renderer module, loaded from its file rather than imported. An import would need a directory
    on sys.path to find it, and an entry added ahead of the standard library, where an insert at the front
    puts it, lets a module file in that directory shadow the standard library for every import after it.
    Loaded once: the validator renders on every call."""
    spec = importlib.util.spec_from_file_location(
        "render_homebrew_formula", ROOT / "scripts" / "render_homebrew_formula.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render(sdist_url: str, sha256: str) -> str:
    return _renderer().render(sdist_url=sdist_url, sha256=sha256)


def _first_difference(expected: str, actual: str) -> str:
    expected_lines, actual_lines = expected.splitlines(), actual.splitlines()
    for number, (want, got) in enumerate(zip(expected_lines, actual_lines), start=1):
        if want != got:
            return f"first difference at line {number}: expected {want!r}, found {got!r}."
    return f"expected {len(expected_lines)} lines, found {len(actual_lines)}."


def validate_formula(
    text: str,
    *,
    sdist_url: str,
    sha256: str,
    root_url: str,
    tags: list[str],
    release_digests: dict[str, str],
) -> None:
    """The merged formula is exactly the renderer's text, plus strict resource stanzas, plus one
    bottle block consistent with the published release, or the push is refused (spec, section 5).

    Every expected value is an ARGUMENT, supplied from `plan`'s outputs and a releases API read,
    never parsed out of the text under test: that would compare the artifact with itself.
    """
    if not tags:
        raise Refusal("the declared tag set is empty.")
    block_root_url, block_tags, block = parse_bottle_block(text)
    if block_root_url != root_url:
        raise Refusal(f"the bottle block's root_url is {block_root_url!r}, expected {root_url!r}.")
    if set(block_tags) != set(tags):
        raise Refusal(f"the bottle block carries {sorted(block_tags)}, but {sorted(tags)} are declared.")
    for tag in tags:
        if release_digests.get(tag) != block_tags[tag][1]:
            raise Refusal(
                f"the bottle block's {tag} digest {block_tags[tag][1]} is not the published "
                f"asset's {release_digests.get(tag)!r}."
            )
    # measured separator: `brew bottle --merge --write` inserts the block after `license` and
    # leaves one blank line after its `end` (utils/ast.rb::add_stanza, confirmed by the plan's
    # first task). Removing the block and that one newline restores the pre-merge text.
    after = block.end()
    if text[after : after + 1] != "\n":  # measured separator
        raise Refusal("the bottle block is not followed by the blank line brew bottle --merge writes.")
    if not _BLOCK_FOLLOWS_RE.search(text[: block.start()]):  # measured anchor
        raise Refusal(
            "the bottle block does not directly follow the license line and its blank line, where "
            "brew bottle --merge writes it."
        )
    remainder = text[: block.start()] + text[after + 1 :]
    stanzas = list(_STANZA_RE.finditer(remainder))
    if not stanzas:
        raise Refusal("the formula carries no resource stanzas: brew update-python-resources filled none.")
    for stanza in stanzas:
        if _normalise(stanza.group("project")) != _normalise(stanza.group("name")):
            raise Refusal(
                f"resource {stanza.group('name')!r} points at an sdist for "
                f"{stanza.group('project')!r}."
            )
    for previous, current in zip(stanzas, stanzas[1:]):
        if current.start() != previous.end():
            raise Refusal("the resource stanzas do not form one contiguous run.")
    start = stanzas[0].start()
    remainder = _STANZA_RE.sub("", remainder)
    if not remainder[start:].startswith(_RESOURCES_PRECEDE):  # measured anchor
        raise Refusal(
            f"the resource stanzas are not directly above {_RESOURCES_PRECEDE.strip()!r}, where "
            "brew update-python-resources writes them."
        )
    expected = _render(sdist_url, sha256)
    if remainder != expected:
        raise Refusal(
            "the formula is not the renderer's text plus resource stanzas and one bottle block: "
            + _first_difference(expected, remainder)
        )


# --- the push -----------------------------------------------------------------------------------

# The renderer writes no `version` stanza, so the version lives only in the top-level url line: two
# spaces of indent, where a resource's url line has four.
_TOP_URL_RE = re.compile(
    r'^  url "https://[^"\n]+/job_sluice-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)\.tar\.gz"$', re.MULTILINE
)


def parse_formula_version(text: str) -> tuple[int, int, int]:
    found = _TOP_URL_RE.findall(text)
    if len(found) != 1:
        raise Refusal(
            f"could not read one release version from the tap's formula (found {len(found)} "
            f"top-level url lines)."
        )
    return version_tuple(found[0])


def push_decision(
    *,
    target_state: str,
    remote_formula: bytes | None,
    ours: bytes,
    target_is_default: bool,
    base_formula: bytes | None,
    version: str,
    dry_run: bool,
) -> str:
    """'noop' or 'push', or a refusal (spec, section 6c).

    - An absent default branch is refused; an absent scratch branch is the first dry run of a
      version.
    - A dry run on the default branch with a formula at BASE_SHA is refused: `plan` sends a dry run
      there only when the contents API reads the formula as absent, and git, read here in the token
      job, is the cross-check on that observable.
    - Identical bytes at the target are a re-run after this release's push already landed.
    - On the default branch, the formula at BASE_SHA must not be newer than VERSION: the re-run of
      an older release must not roll the tap back. A base with no formula is the bootstrap.
    """
    wanted = version_tuple(version)
    if target_state not in ("present", "absent"):
        raise Refusal(f"the target branch's state is {target_state!r}, not present or absent.")
    if target_state == "absent" and target_is_default:
        raise Refusal("the tap's default branch does not exist; refusing to create it from a publish.")
    if dry_run and target_is_default and base_formula is not None:
        raise Refusal(
            f"a dry run is targeting the tap's default branch, but Formula/{FORMULA_NAME}.rb exists at "
            "the base commit, and plan sends a dry run there only while it does not (the bootstrap). "
            "Refusing rather than making a dry run the tree of record."
        )
    if target_state == "present" and remote_formula is not None and remote_formula == ours:
        return "noop"
    if target_is_default and base_formula is not None:
        try:
            base_text = base_formula.decode("utf-8")
        except UnicodeDecodeError as err:
            raise Refusal("the tap's formula at the base commit is not UTF-8 text.") from err
        current = parse_formula_version(base_text)
        if current > wanted:
            raise Refusal(
                f"the tap's formula at the base commit is {'.'.join(map(str, current))}, newer "
                f"than {version}. Refusing to roll the tap back."
            )
    return "push"


# --- I/O ----------------------------------------------------------------------------------------

API = "https://api.github.com"
_USER_AGENT = f"sluice-{FORMULA_NAME}-bottles"


def http_request(request: urllib.request.Request) -> tuple[int, bytes]:
    """The one network call. Injected as `http` everywhere else, so tests never touch a network."""
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def github_request(
    http,
    method: str,
    url: str,
    token: str | None,
    *,
    body: dict | None = None,
    data: bytes | None = None,
    content_type: str = "application/json",
) -> tuple[int, object]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else data
    if payload is not None:
        headers["Content-Type"] = content_type
    status, raw = http(urllib.request.Request(url, data=payload, method=method, headers=headers))
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return status, parsed


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Every git command this file runs, with hooks disabled: a hook left in a clone by an artifact
    would otherwise run beside the token (spec, section 2)."""
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd, capture_output=True, check=False
    )


def _git_ok(*args: str, cwd: Path | None = None, redact: str = "") -> bytes:
    proc = git(*args, cwd=cwd)
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", "replace").strip()
        if redact:
            message = message.replace(redact, "***")
        # Named past any `-c key=value` pairs: `args[0]` reads `git -c failed` for a commit run with
        # config pairs, which names no command at all.
        position = 0
        while position < len(args) and args[position] == "-c":
            position += 2
        subcommand = args[position] if position < len(args) else ""
        raise Refusal(f"git {subcommand} failed with exit {proc.returncode}: {message[:500]}")
    return proc.stdout


def _require(env: dict, name: str) -> str:
    value = env.get(name)
    if not value:
        raise Refusal(f"the environment variable {name} is required.")
    return value


def _read_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as err:
        raise Refusal(f"{path} is not readable JSON: {err}.") from err


# A `$GITHUB_OUTPUT` name: every key `build_plan` emits has this shape, and nothing else can open a
# second entry or a `name<<DELIMITER` block.
_OUTPUT_KEY_RE = re.compile(r"[a-z_][a-z0-9_]*")


def write_outputs(outputs: dict[str, str], path: str) -> None:
    """Append `key=value` lines to $GITHUB_OUTPUT, one per output.

    GitHub reads a line as `name=value`, or as `name<<DELIMITER` opening a multi-line value, so a
    name that is not an identifier, or a value with a newline, would let one output write another.
    Both are refused before anything is written.
    """
    for key, value in outputs.items():
        if not _OUTPUT_KEY_RE.fullmatch(key):
            raise Refusal(f"the output name {key!r} is not a lower-case identifier.")
        if "\n" in value or "\r" in value:
            raise Refusal(f"the output {key} contains a newline.")
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


# --- plan ---------------------------------------------------------------------------------------


def build_plan(
    *,
    push_target: str,
    version: str,
    repository_owner: str,
    run_id: str,
    run_attempt: str,
    run_url: str,
    ls_remote_output: str,
    pypi_json: dict,
    contents_status: int | None,
) -> dict[str, str]:
    """Every value more than one job reads, derived once (spec, section 2)."""
    validate_push_target(push_target)
    validate_version(version)
    owner = tap_owner(repository_owner)
    if not _RUN_URL_RE.fullmatch(run_url or ""):
        raise Refusal(f"run URL {run_url!r} is not a GitHub Actions run URL.")
    default_branch, base_sha = parse_symref(ls_remote_output)
    sdist_url, sdist_sha256 = pick_sdist(pypi_json, version)
    formula_state = (
        formula_state_from_status(contents_status) if push_target == "auto" else "not consulted"
    )
    target_branch = resolve_target(push_target, formula_state, default_branch, version)
    tag = compose_tag(version, run_id, run_attempt)
    return {
        "tap_owner": owner,
        "default_branch": default_branch,
        "base_sha": base_sha,
        "target_branch": target_branch,
        "sdist_url": sdist_url,
        "sdist_sha256": sdist_sha256,
        "tag": tag,
        "root_url": compose_root_url(repository_owner, tag),
        "run_url": run_url,
        "caller": caller_for(push_target),
        "platforms": platforms_json(),
    }


def cmd_plan(args, env: dict, http) -> None:
    # Validated before any external command: a wrong PUSH_TARGET must be the first thing that fails.
    push_target = validate_push_target(env.get("PUSH_TARGET"))
    version = validate_version(env.get("VERSION"))
    repository_owner = _require(env, "REPOSITORY_OWNER")
    owner = tap_owner(repository_owner)
    ls_remote = _git_ok(
        "ls-remote", "--symref", f"https://github.com/{owner}/{TAP_REPO}.git", "HEAD"
    ).decode("utf-8", "replace")
    status, raw = http(
        urllib.request.Request(
            f"https://pypi.org/pypi/{FORMULA_NAME}/{version}/json",
            headers={"User-Agent": _USER_AGENT},
        )
    )
    if status != 200:
        raise Refusal(f"PyPI answered HTTP {status} for {FORMULA_NAME} {version}.")
    try:
        pypi_json = json.loads(raw)
    except ValueError as err:
        raise Refusal(f"PyPI's answer for {FORMULA_NAME} {version} is not JSON.") from err
    contents_status = None
    if push_target == "auto":
        _, base_sha = parse_symref(ls_remote)
        contents_status, _ = github_request(
            http,
            "GET",
            f"{API}/repos/{owner}/{TAP_REPO}/contents/Formula/{FORMULA_NAME}.rb?ref={base_sha}",
            env.get("GITHUB_TOKEN"),
        )
    outputs = build_plan(
        push_target=push_target,
        version=version,
        repository_owner=repository_owner,
        run_id=_require(env, "RUN_ID"),
        run_attempt=_require(env, "RUN_ATTEMPT"),
        run_url=_require(env, "RUN_URL"),
        ls_remote_output=ls_remote,
        pypi_json=pypi_json,
        contents_status=contents_status,
    )
    write_outputs(outputs, _require(env, "GITHUB_OUTPUT"))


# --- the untrusted jobs' checks -----------------------------------------------------------------


def cmd_render(args, env: dict, http) -> None:
    Path(args.out).write_text(_render(_require(env, "SDIST_URL"), _require(env, "SDIST_SHA256")))


def cmd_tags(args, env: dict, http) -> None:
    for tag in declared_tags(_require(env, "PLATFORMS")):
        print(tag)


def cmd_produced_tag(args, env: dict, http) -> None:
    check_produced_tag(_read_json(args.json), _require(env, "DECLARED_TAG"))


def cmd_pour_check(args, env: dict, http) -> None:
    owner = tap_owner(_require(env, "TAP_OWNER"))
    check_pour(
        _read_json(args.info),
        full_name=f"{owner}/tap/{FORMULA_NAME}",
        version=validate_version(env.get("VERSION")),
        expect_built=args.expect_built,
    )


def cmd_merged_tags(args, env: dict, http) -> None:
    check_merged_tags(Path(args.formula).read_text(), declared_tags(_require(env, "PLATFORMS")))


def cmd_cache_check(args, env: dict, http) -> None:
    bottle = Path(args.file)
    if not bottle.is_file():
        raise Refusal(f"no fetched bottle at {bottle}: brew fetch downloaded nothing for {args.tag}.")
    check_cache_file(bottle.read_bytes(), Path(args.formula).read_text(), args.tag)


# --- the release upload (a token job) -----------------------------------------------------------


def list_releases(http, token: str | None, owner: str) -> list[dict]:
    """Every release, drafts included when the token has push access (spec, section 3)."""
    releases: list[dict] = []
    page = 1
    while True:
        status, body = github_request(
            http, "GET", f"{API}/repos/{owner}/{TAP_REPO}/releases?per_page=100&page={page}", token
        )
        if status != 200 or not isinstance(body, list):
            raise Refusal(f"listing the tap's releases failed with HTTP {status}.")
        if not body:
            return releases
        releases.extend(body)
        page += 1


def list_assets(http, token: str | None, owner: str, release_id: int) -> list[dict]:
    assets: list[dict] = []
    page = 1
    while True:
        status, body = github_request(
            http,
            "GET",
            f"{API}/repos/{owner}/{TAP_REPO}/releases/{release_id}/assets?per_page=100&page={page}",
            token,
        )
        if status != 200 or not isinstance(body, list):
            raise Refusal(f"listing release {release_id}'s assets failed with HTTP {status}.")
        if not body:
            return assets
        assets.extend(body)
        page += 1


def _create_draft_release(http, token, owner, *, tag, base_sha, title, notes) -> dict:
    status, body = github_request(
        http,
        "POST",
        f"{API}/repos/{owner}/{TAP_REPO}/releases",
        token,
        body={"tag_name": tag, "target_commitish": base_sha, "name": title, "body": notes,
              "draft": True},
    )
    if status != 201 or not isinstance(body, dict) or "id" not in body:
        raise Refusal(f"creating the draft release {tag} failed with HTTP {status}.")
    return body


def _upload_asset(http, token, owner, release_id: int, asset: Asset) -> None:
    status, _ = github_request(
        http,
        "POST",
        f"https://uploads.github.com/repos/{owner}/{TAP_REPO}/releases/{release_id}/assets"
        f"?name={urllib.parse.quote(asset.remote_name)}",
        token,
        data=asset.local_path.read_bytes(),
        content_type="application/octet-stream",
    )
    if status != 201:
        raise Refusal(f"uploading {asset.remote_name} failed with HTTP {status}.")


def _publish_release(http, token, owner, release_id: int) -> None:
    status, _ = github_request(
        http, "PATCH", f"{API}/repos/{owner}/{TAP_REPO}/releases/{release_id}", token,
        body={"draft": False},
    )
    if status != 200:
        raise Refusal(f"publishing release {release_id} failed with HTTP {status}.")


def upload_bottles(
    *, http, token: str, owner: str, tag: str, base_sha: str, title: str, notes: str,
    assets: list[Asset],
) -> None:
    """Find or create the draft, decide every asset before uploading any, upload, publish last.

    Deciding first means a digest clash refuses with nothing written, and publishing last means
    `prove` only ever fetches from a complete release.
    """
    if not _HEX40_RE.fullmatch(base_sha or ""):
        raise Refusal(f"BASE_SHA {base_sha!r} is not a commit id.")
    release = release_decision(
        list_releases(http, token, owner), tag=tag, base_sha=base_sha, title=title, notes=notes
    )
    existing = list_assets(http, token, owner, release["id"]) if release is not None else []
    decisions = [(asset, asset_decision(existing, name=asset.remote_name, sha256=asset.sha256))
                 for asset in assets]
    print(f"release {tag}: {'absent, creating a draft' if release is None else 'found'}")
    if release is None:
        release = _create_draft_release(
            http, token, owner, tag=tag, base_sha=base_sha, title=title, notes=notes
        )
    for asset, decision in decisions:
        print(f"{asset.remote_name}: {decision}")
        if decision == "upload":
            _upload_asset(http, token, owner, release["id"], asset)
    if release.get("draft"):
        _publish_release(http, token, owner, release["id"])
        print(f"published release {tag}")


def _validated_assets(env: dict) -> list[Asset]:
    directory = Path(_require(env, "BOTTLES_DIR"))
    return validate_bottle_jsons(
        sorted(directory.glob("*.bottle.json")),
        version=validate_version(env.get("VERSION")),
        root_url=_require(env, "ROOT_URL"),
        tags=declared_tags(_require(env, "PLATFORMS")),
    )


def cmd_validate_bottles(args, env: dict, http) -> None:
    _validated_assets(env)


def cmd_upload_bottles(args, env: dict, http) -> None:
    assets = _validated_assets(env)
    tag = _require(env, "TAG")
    title, notes = release_templates(
        version=validate_version(env.get("VERSION")), tag=tag, caller=_require(env, "CALLER"),
        run_url=_require(env, "RUN_URL"),
    )
    upload_bottles(
        http=http, token=_require(env, "TAP_TOKEN"), owner=tap_owner(_require(env, "TAP_OWNER")),
        tag=tag, base_sha=_require(env, "BASE_SHA"), title=title, notes=notes, assets=assets,
    )


# --- the tap push (a token job) -----------------------------------------------------------------

_BOT_NAME = "sluice-release-please[bot]"
_BOT_EMAIL = "sluice-release-please[bot]@users.noreply.github.com"


def _read_formula(path: str) -> bytes:
    """The merged formula's exact bytes, which validate-formula and push-prepare both use.

    Never `read_text()`: universal newlines turn a lone CR into LF, so the text validated would not be
    the bytes pushed, and Ruby does not end a line at a lone CR. A CR anywhere is refused.
    """
    data = Path(path).read_bytes()
    if b"\r" in data:
        raise Refusal(f"{path} contains a carriage return; the formula must use LF line endings only.")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as err:
        raise Refusal(f"{path} is not UTF-8 text.") from err
    return data


def cmd_validate_formula(args, env: dict, http) -> None:
    owner = tap_owner(_require(env, "TAP_OWNER"))
    tag = _require(env, "TAG")
    version = validate_version(env.get("VERSION"))
    tags = declared_tags(_require(env, "PLATFORMS"))
    token = env.get("GITHUB_TOKEN")
    published = [r for r in list_releases(http, token, owner)
                 if r.get("tag_name") == tag and not r.get("draft")]
    if len(published) != 1:
        raise Refusal(f"expected one published release tagged {tag!r}, found {len(published)}.")
    digests = release_digests(list_assets(http, token, owner, published[0]["id"]), version=version,
                              tags=tags)
    validate_formula(
        _read_formula(_require(env, "MERGED_FORMULA")).decode("utf-8"),
        sdist_url=_require(env, "SDIST_URL"),
        sha256=_require(env, "SDIST_SHA256"),
        root_url=_require(env, "ROOT_URL"),
        tags=tags,
        release_digests=digests,
    )


def _formula_at_commit(clone: Path, commit: str) -> bytes | None:
    """The formula's bytes at `commit`, or None only when git lists no such file there.

    Three-valued on purpose, like `formula_state_from_status`: a failed read taken as "absent" would
    reach the bootstrap arm and skip the default branch's version refusal (spec, section 6c).
    """
    path = f"Formula/{FORMULA_NAME}.rb"
    listed = _git_ok("ls-tree", "--name-only", commit, "--", path, cwd=clone).decode("utf-8", "replace")
    if listed == "":
        return None
    if listed != f"{path}\n":
        raise Refusal(f"git listed {listed!r} for {path} at {commit}.")
    return _git_ok("show", f"{commit}:{path}", cwd=clone)


def prepare_push(
    *, remote_url: str, workdir: Path, target_branch: str, default_branch: str, base_sha: str,
    version: str, formula: bytes, dry_run: bool,
) -> str:
    """Clone the tap, decide (spec, section 6c), and commit on BASE_SHA when the decision is push.

    Writes `push-state.json` for `publish_push`. Runs no token: the push is a later step.
    """
    if not _HEX40_RE.fullmatch(base_sha or ""):
        raise Refusal(f"BASE_SHA {base_sha!r} is not a commit id.")
    workdir = Path(workdir)
    clone = workdir / "tap"
    if clone.exists():
        raise Refusal(f"{clone} already exists; push-prepare expects a fresh working directory.")
    workdir.mkdir(parents=True, exist_ok=True)
    _git_ok("clone", "--no-checkout", remote_url, str(clone))
    probe = git("ls-remote", "--exit-code", "origin", f"refs/heads/{target_branch}", cwd=clone)
    if probe.returncode == 0:
        # `ls-remote` matches its pattern against the TAIL of every ref name, so a tag stored as
        # refs/tags/refs/heads/<branch> is listed here too, and a push to the branch's name would move
        # that tag. Present means exactly one line, for exactly this branch.
        listed = probe.stdout.decode("utf-8", "replace")
        match = re.fullmatch(rf"([0-9a-f]{{40}})\trefs/heads/{re.escape(target_branch)}\n", listed)
        if match is None:
            raise Refusal(
                f"the tap lists {listed!r} for refs/heads/{target_branch}; expected exactly that one "
                "branch."
            )
        target_state, remote_sha = "present", match.group(1)
    elif probe.returncode == 2:
        target_state, remote_sha = "absent", ""
    else:
        raise Refusal(f"could not tell whether {target_branch} exists (git exit {probe.returncode}).")
    remote_formula = None
    if target_state == "present":
        _git_ok("fetch", "origin", f"+refs/heads/{target_branch}:refs/remotes/origin/{target_branch}",
                cwd=clone)
        remote_formula = _formula_at_commit(clone, f"refs/remotes/origin/{target_branch}")
    if git("cat-file", "-e", f"{base_sha}^{{commit}}", cwd=clone).returncode != 0:
        raise Refusal(f"the base commit {base_sha} is not in the tap's history.")
    base_formula = _formula_at_commit(clone, base_sha)
    target_is_default = target_branch == default_branch
    decision = push_decision(
        target_state=target_state, remote_formula=remote_formula, ours=formula,
        target_is_default=target_is_default, base_formula=base_formula, version=version,
        dry_run=dry_run,
    )
    if decision == "push":
        # Built with plumbing, never through a checked-out tree of BASE_SHA: a work tree lets the tap's
        # own content decide what is stored (a `.gitattributes` working-tree-encoding re-encodes the
        # bytes) or where they land (a symlink committed at the formula's path is followed out of the
        # clone). The index starts as BASE_SHA's tree, and only this channel's formula entry changes,
        # to a regular-file blob of exactly the validated bytes.
        path = f"Formula/{FORMULA_NAME}.rb"
        staged = workdir / "formula-to-commit.rb"
        staged.write_bytes(formula)
        # The bytes are hashed from a file outside the clone, which no tap attribute can match, so no
        # filter applies to them; `--no-filters` is a defence that keeps that true if the staged path
        # ever moves inside the clone.
        # Absolute: hash-object runs inside the clone, where a relative PUSH_WORKDIR names nothing.
        blob = _git_ok("hash-object", "-w", "--no-filters", "--", str(staged.absolute()),
                       cwd=clone).decode().strip()
        _git_ok("read-tree", base_sha, cwd=clone)
        _git_ok("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", cwd=clone)
        tree = _git_ok("write-tree", cwd=clone).decode().strip()
        if tree == _git_ok("rev-parse", f"{base_sha}^{{tree}}", cwd=clone).decode().strip():
            # Compared with the BASE, never the target: push_decision already found the target does not
            # hold these bytes, so a no-op reported here would leave the formula unpublished behind a
            # green step. Raised before push-state.json is written, so publish has nothing to push.
            raise Refusal(
                f"the formula is already byte-identical at the base commit {base_sha}, yet "
                f"{target_branch} does not hold it; refusing rather than reporting a push that did not "
                "happen."
            )
        else:
            commit = _git_ok(
                "-c", f"user.name={_BOT_NAME}", "-c", f"user.email={_BOT_EMAIL}",
                "-c", "commit.gpgsign=false", "commit-tree", tree, "-p", base_sha,
                "-m", f"{FORMULA_NAME} {version}", cwd=clone,
            ).decode().strip()
            # A detached HEAD at the new commit, which is what publish_push pushes.
            _git_ok("update-ref", "--no-deref", "HEAD", commit, cwd=clone)
    (workdir / "push-state.json").write_text(json.dumps({
        "decision": decision, "target_branch": target_branch,
        "target_is_default": target_is_default, "remote_sha": remote_sha,
    }))
    return decision


def publish_push(*, workdir: Path, push_url: str, target_branch: str, redact: str = "") -> None:
    """Push what `prepare_push` committed: fast-forward only to the default branch, and to a scratch
    branch only under a lease on the tip `prepare_push` observed."""
    state = json.loads((Path(workdir) / "push-state.json").read_text())
    if state.get("target_branch") != target_branch:
        raise Refusal(f"push-state.json was prepared for {state.get('target_branch')!r}, not {target_branch!r}.")
    if state.get("decision") == "noop":
        print(f"{target_branch} already holds this formula; nothing to push.")
        return
    if state.get("decision") != "push":
        raise Refusal(f"push-state.json holds an unknown decision {state.get('decision')!r}.")
    clone = Path(workdir) / "tap"
    refspec = f"HEAD:refs/heads/{target_branch}"
    if state["target_is_default"]:
        _git_ok("push", push_url, refspec, cwd=clone, redact=redact)
    else:
        lease = f"--force-with-lease=refs/heads/{target_branch}:{state['remote_sha']}"
        _git_ok("push", lease, push_url, refspec, cwd=clone, redact=redact)


def cmd_push_prepare(args, env: dict, http) -> None:
    owner = tap_owner(_require(env, "TAP_OWNER"))
    # Refused before the clone: a caller this file does not know could not be read as either one.
    caller = _require(env, "CALLER")
    if caller not in CALLERS:
        raise Refusal(f"CALLER must be one of {list(CALLERS)}, got {caller!r}.")
    decision = prepare_push(
        remote_url=f"https://github.com/{owner}/{TAP_REPO}.git",
        workdir=Path(_require(env, "PUSH_WORKDIR")),
        target_branch=_require(env, "TARGET_BRANCH"),
        default_branch=_require(env, "DEFAULT_BRANCH"),
        base_sha=_require(env, "BASE_SHA"),
        version=validate_version(env.get("VERSION")),
        formula=_read_formula(_require(env, "MERGED_FORMULA")),
        dry_run=caller == DRY_RUN_CALLER,
    )
    print(f"push-prepare decided: {decision}")


def cmd_push_publish(args, env: dict, http) -> None:
    owner = tap_owner(_require(env, "TAP_OWNER"))
    token = _require(env, "TAP_TOKEN")
    publish_push(
        workdir=Path(_require(env, "PUSH_WORKDIR")),
        push_url=f"https://x-access-token:{token}@github.com/{owner}/{TAP_REPO}.git",
        target_branch=_require(env, "TARGET_BRANCH"),
        redact=token,
    )


# --- main ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homebrew_bottles.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "tags", "validate-bottles", "upload-bottles", "validate-formula",
                 "push-prepare", "push-publish"):
        sub.add_parser(name)
    render = sub.add_parser("render")
    render.add_argument("--out", required=True)
    produced = sub.add_parser("produced-tag")
    produced.add_argument("--json", required=True)
    pour = sub.add_parser("pour-check")
    pour.add_argument("--info", required=True)
    pour.add_argument("--expect-built", action="store_true")
    merged = sub.add_parser("merged-tags")
    merged.add_argument("--formula", required=True)
    cache = sub.add_parser("cache-check")
    cache.add_argument("--formula", required=True)
    cache.add_argument("--tag", required=True)
    cache.add_argument("--file", required=True)
    return parser


_COMMANDS = {
    "plan": cmd_plan,
    "render": cmd_render,
    "tags": cmd_tags,
    "produced-tag": cmd_produced_tag,
    "pour-check": cmd_pour_check,
    "merged-tags": cmd_merged_tags,
    "cache-check": cmd_cache_check,
    "validate-bottles": cmd_validate_bottles,
    "upload-bottles": cmd_upload_bottles,
    "validate-formula": cmd_validate_formula,
    "push-prepare": cmd_push_prepare,
    "push-publish": cmd_push_publish,
}


def main(argv: list[str] | None = None, *, env: dict | None = None, http=http_request) -> int:
    args = _parser().parse_args(argv)
    environment = dict(os.environ) if env is None else env
    try:
        _COMMANDS[args.command](args, environment, http)
    except Refusal as err:
        # stdout, where GitHub Actions reads workflow commands.
        print(f"::error::{err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
