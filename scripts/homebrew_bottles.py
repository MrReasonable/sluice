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

import json
import re

FORMULA_NAME = "job-sluice"
TAP_REPO = "homebrew-tap"

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
