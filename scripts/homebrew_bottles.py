#!/usr/bin/env python3
"""Decisions, and the thin I/O around them, for publishing the Homebrew bottles (#279).

Design: docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md. Every decision the release
channel makes lives here as a pure function over plain data, so its refuse branches are proven
offline (tests/test_homebrew_bottles.py) rather than by a dry run that only ever takes the accept
branch. The CLI at the bottom is the layer the workflow steps call.

THE TRUST RULE THIS FILE SERVES. A job either runs third-party code and references no secret, or
holds the tap token and runs only this file's code, which runs `git` itself. The token jobs invoke it as
`python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"`: `-P` keeps the working directory off
`sys.path`, and every artifact they read is untrusted data this file validates before anything is
published. Standard library only, for that reason: a dependency would be third-party code running
beside the token.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

FORMULA_NAME = "job-sluice"
TAP_REPO = "homebrew-tap"

# The repository root, so this file can import the renderer when run as `python3 -P <path>`: -P
# removes the script's own directory from sys.path, and the renderer is the one thing the
# validator must reproduce rather than restate.
ROOT = Path(__file__).resolve().parent.parent

# The (runner label, bottle tag) pairs, declared ONCE. `plan` emits them for the bottle matrix, and
# tests/test_homebrew_bottles.py restates them by hand rather than importing them: an expectation
# read from here would compare this constant with itself. A runner's macOS decides the tag it
# builds, so a label that moves (`macos-latest`) would silently change which users get a bottle.
PLATFORMS = (("macos-15", "arm64_sequoia"), ("macos-26", "arm64_tahoe"))

PUSH_TARGETS = ("default", "auto")

_VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_POSITIVE_INT_RE = re.compile(r"[1-9][0-9]*")
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
    return {"default": "release", "auto": "dry run"}[validate_push_target(push_target)]


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
_SHA_LINE_RE = re.compile(
    r"    sha256 cellar: :(?P<cellar>any|any_skip_relocation), +"
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

_TAG_RE = re.compile(rf"{FORMULA_NAME}-\d+\.\d+\.\d+-[1-9][0-9]*-[1-9][0-9]*")
_RUN_URL_RE = re.compile(r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/actions/runs/[1-9][0-9]*")
CALLERS = ("release", "dry run")


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
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise Refusal(f"asset {name!r} already exists with no usable digest ({digest!r}).")
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
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
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


def _render(sdist_url: str, sha256: str) -> str:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.render_homebrew_formula import render

    return render(sdist_url=sdist_url, sha256=sha256)


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
