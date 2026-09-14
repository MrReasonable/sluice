# Homebrew Bottles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `arm64_sequoia` and `arm64_tahoe` bottles for `job-sluice` from the tap, through a release channel in which no job both runs third-party code and holds the tap's write token.

**Architecture:** One reusable workflow, `.github/workflows/homebrew.yml`, with six jobs. `plan`, `upload` and `push` are trusted: `ubuntu-latest`, and only `python3 -P scripts/homebrew_bottles.py`, which runs `git` itself; only `upload` and `push` mint the tap token. `formula`, `bottle` (a matrix) and `prove` run Homebrew on macOS and reference no secret. Every decision is a pure function in `scripts/homebrew_bottles.py`, proven offline in `tests/test_homebrew_bottles.py`; the untrusted jobs' bodies are bash 3.2 scripts under `.github/scripts/`; both callers (`release-please.yml`, `homebrew-dry-run.yml`) invoke the one workflow.

**Tech Stack:** GitHub Actions (reusable workflows, matrix, artifacts), Homebrew 6 (`brew bottle`, `brew fetch`), Python 3.12 standard library, bash 3.2, pytest, PyYAML (already a test-time dependency).

**Spec:** `docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md`, read at this plan's own commit: the spec was revised alongside the plan, and its §13 lists what the plan's reviews changed. Read it before any task: every "why" in this plan is argued there, section by section.

## Global Constraints

- Platform pairs, exactly: `("macos-15", "arm64_sequoia")` and `("macos-26", "arm64_tahoe")`. Never `macos-latest`.
- `scripts/homebrew_bottles.py` imports the Python standard library only.
- Trusted jobs (`plan`, `upload`, `push`) run on `ubuntu-latest`. Every `run:` line in them is exactly `python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" <subcommand>`. Their `uses:` steps are `actions/checkout` (all three), then `actions/download-artifact` and `actions/create-github-app-token` (`upload` and `push` only).
- Untrusted jobs (`formula`, `bottle`, `prove`) reference no `secrets.` value, run on `macos-26` (the matrix runner for `bottle`), and set up Python with `actions/setup-python` at `python-version: "3.12"`.
- Action pins, copied exactly:
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0`
  - `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1`
  - `actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1`
  - `actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0`
- No `if:` or `continue-on-error` on any job or step in `homebrew.yml`. No `${{ }}` inside any `run:`: every value reaches a script through `env:`.
- Every `git` command `scripts/homebrew_bottles.py` runs carries `-c core.hooksPath=/dev/null`. Every artifact a token job downloads lands under `${{ runner.temp }}`.
- `brew bottle --json` always carries `--no-rebuild`.
- Shell scripts under `.github/scripts/` must run under macOS bash 3.2: no `${VAR,,}`, `${VAR^^}`, `mapfile`, `readarray`, `declare -A`, `&>>`, `[[ -v`, `coproc`.
- Never cite a line number in a comment or docstring (`tests/test_citation_drift.py` fails the build); cite `file::symbol`.
- Test fixtures are synthetic: `example.invalid` URLs, fake digests, owner `ExampleOwner` / `exampleowner`. Resource-stanza URLs keep the `files.pythonhosted.org` host, because that host is what the formula grammar checks. One exception: `tests/homebrew_fixtures/merged_formula.rb` keeps the renderer's `homepage` line verbatim, which names the upstream project's real owner, because the validator compares that text with `render()`.
- Mutation witnesses (`.rulesync/rules/CLAUDE.md`, mutation-testing section): commit before each witness; mutate by DELETING or MOVING a line, never by adding one; back the file up with `cp` and restore with `cp`, never `git checkout`; run `.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts` once before the first witness.
- A witness must leave a Python file parseable. After applying a mutant to one, run `.venv/bin/python -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' <file>` before its tests: a mutant that fails to parse turns every test red for the wrong reason. Confirm the named test fails on its own assertion.
- Three kinds of mutant are not a plain delete or move, and each is named. A property that is the ABSENCE of something (no `|| :`, no owner literal, no `if:`) can only be violated by adding it: such rows are marked "(adds)". A row that swaps a value or call for the exact regression its test exists to catch (the old `read_text()`, a lenient `git(...)` in place of `_git_ok(...)`, a changed pin) is marked "(replaces)": the original is gone, so it cannot be an equivalent mutant. And the always-refuse mutant, `/tmp/always_refuse.py` (Task 2, Step 6), inserts `raise Refusal("always-refuse mutant")` as a function's first statement to witness its accept rows (spec, §9a); nothing else in the file adds that text.
- In each task's commit step, stage the task's files, run the whole suite, `.venv/bin/python -m pytest -q`, and only then commit; every commit block below is written in that order. Staging comes first because `tests/test_no_leaked_files.py` reads tracked files through `git ls-files` and `git grep`, which do not see an unstaged file. Run the whole suite, not only the files the task names: guards in other files (the fixture-tree scope in `tests/test_fixture_name_neutrality.py`, the helper signatures in `tests/test_release_publish_wiring.py`, the home-path gate in `tests/test_no_leaked_files.py`) are exactly what a narrow run misses.
- Use `.venv/bin/python`. The worktree venv has no pip: install a tool with `uv pip install --python .venv/bin/python <package>`.
- Conventional Commits. End every commit message with these two lines:

  ```text
  MrReasonable <4990954+MrReasonable@users.noreply.github.com>
  Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
  ```

---

## File Structure

| Path | Change | Responsibility |
| --- | --- | --- |
| `scripts/homebrew_bottles.py` | create | Every decision as a pure function, plus the CLI subcommands the workflow steps call |
| `tests/test_homebrew_bottles.py` | create | Offline accept and refuse rows for every function; CLI and git I/O against local repositories |
| `tests/homebrew_fixtures/merged_formula.rb` | create | Sanitised real two-tag `brew bottle --merge --write` output: the formula validator's accept fixture |
| `tests/homebrew_fixtures/bottle.json` | create | Sanitised real `brew bottle --json --no-rebuild` output, reduced to the keys the validator reads |
| `tests/homebrew_fixtures/info_poured.json` | create | Sanitised real `brew info --json=v2 <tap>/job-sluice` after a pour, reduced to the keys the pour check reads |
| `.github/scripts/homebrew_tap_checkout.sh` | create | Clone the tap into Homebrew's `Taps` directory at BASE_SHA, for the untrusted jobs |
| `.github/scripts/homebrew_formula.sh` | create | `formula` job body: render, resource fill, cooldown diagnostic, audit |
| `.github/scripts/homebrew_bottle.sh` | create | `bottle` job body: build, test, bottle, tag check, pour proof, linkage |
| `.github/scripts/homebrew_prove.sh` | create | `prove` job body: merge, style, tag-set check, fetch every tag |
| `.github/workflows/homebrew.yml` | create | The reusable workflow |
| `.github/workflows/release-please.yml` | modify | Its `homebrew` job becomes a call |
| `.github/workflows/homebrew-dry-run.yml` | rewrite | A `preflight` job plus a call |
| `.github/scripts/homebrew_verify.sh`, `.github/scripts/homebrew_push.sh` | delete | Retired |
| `tests/test_release_publish_wiring.py` | modify | Homebrew section replaced, per the spec's successor table |
| `scripts/render_homebrew_formula.py`, `tests/test_homebrew_formula.py` | modify | Comments whose "no bottles" premise this falsifies |
| `docs/INSTALL.md` | modify | Which Macs pour a bottle and which still build |

## Interface Contract

Every task implements against these names. An implementer sees only their own task; this table is
how neighbouring tasks agree. Everything below lives in `scripts/homebrew_bottles.py`.

| Name | Signature | Task |
| --- | --- | --- |
| `Refusal` | `class Refusal(Exception)` | 2 |
| `FORMULA_NAME`, `TAP_REPO`, `PLATFORMS`, `PUSH_TARGETS` | module constants | 2 |
| `validate_push_target` | `(value: str \| None) -> str` | 2 |
| `validate_version` | `(value: str \| None) -> str` | 2 |
| `version_tuple` | `(value: str) -> tuple[int, int, int]` | 2 |
| `validate_sdist` | `(version: str, url: str \| None, sha256: str \| None) -> None` | 2 |
| `pick_sdist` | `(pypi_json: dict, version: str) -> tuple[str, str]` | 2 |
| `tap_owner` | `(repository_owner: str \| None) -> str` | 2 |
| `compose_tag` | `(version: str, run_id: str, run_attempt: str) -> str` | 2 |
| `compose_root_url` | `(repository_owner: str, tag: str) -> str` | 2 |
| `parse_symref` | `(ls_remote_output: str) -> tuple[str, str]` | 2 |
| `formula_state_from_status` | `(status: int) -> str` | 2 |
| `resolve_target` | `(push_target: str, formula_state: str, default_branch: str, version: str) -> str` | 2 |
| `caller_for` | `(push_target: str) -> str` | 2 |
| `platforms_json` | `() -> str` | 2 |
| `declared_tags` | `(platforms: str) -> list[str]` | 2 |
| `check_pour` | `(info: dict, *, full_name: str, version: str, expect_built: bool = False) -> None` | 3 |
| `check_produced_tag` | `(bottle_json: dict, declared_tag: str) -> None` | 3 |
| `Asset` | `NamedTuple` with `tag: str`, `remote_name: str`, `local_path: Path`, `sha256: str` | 3 |
| `validate_bottle_jsons` | `(json_paths: list[Path], *, version: str, root_url: str, tags: list[str]) -> list[Asset]` | 3 |
| `parse_bottle_block` | `(text: str) -> tuple[str, dict[str, tuple[str, str]], re.Match]` | 3 |
| `check_merged_tags` | `(formula_text: str, tags: list[str]) -> None` | 3 |
| `check_cache_file` | `(data: bytes, formula_text: str, tag: str) -> None` | 3 |
| `release_templates` | `(*, version: str, tag: str, caller: str, run_url: str) -> tuple[str, str]` | 4 |
| `release_decision` | `(releases: list[dict], *, tag: str, base_sha: str, title: str, notes: str) -> dict \| None` | 4 |
| `asset_decision` | `(assets: list[dict], *, name: str, sha256: str) -> str` | 4 |
| `release_digests` | `(assets: list[dict], *, version: str, tags: list[str]) -> dict[str, str]` | 4 |
| `_render` | `(sdist_url: str, sha256: str) -> str` | 5 |
| `validate_formula` | `(text: str, *, sdist_url: str, sha256: str, root_url: str, tags: list[str], release_digests: dict[str, str]) -> None` | 5 |
| `parse_formula_version` | `(text: str) -> tuple[int, int, int]` | 6 |
| `push_decision` | `(*, target_state: str, remote_formula: bytes \| None, ours: bytes, target_is_default: bool, base_formula: bytes \| None, version: str) -> str` | 6 |
| `http_request` | `(request: urllib.request.Request) -> tuple[int, bytes]` | 7 |
| `github_request` | `(http, method: str, url: str, token: str \| None, *, body: dict \| None = None, data: bytes \| None = None, content_type: str = "application/json") -> tuple[int, object]` | 7 |
| `git` | `(*args: str, cwd: Path \| None = None) -> subprocess.CompletedProcess` | 7 |
| `build_plan` | `(*, push_target: str, version: str, repository_owner: str, run_id: str, run_attempt: str, run_url: str, ls_remote_output: str, pypi_json: dict, contents_status: int \| None) -> dict[str, str]` | 7 |
| `main` | `(argv: list[str] \| None = None, *, env: dict \| None = None, http=http_request) -> int` | 7 |
| `list_releases`, `list_assets` | `(http, token: str \| None, owner: str[, release_id: int]) -> list[dict]` | 8 |
| `upload_bottles` | `(*, http, token: str, owner: str, tag: str, base_sha: str, title: str, notes: str, assets: list[Asset]) -> None` | 8 |
| `prepare_push` | `(*, remote_url: str, workdir: Path, target_branch: str, default_branch: str, base_sha: str, version: str, formula: bytes) -> str` | 8 |
| `publish_push` | `(*, workdir: Path, push_url: str, target_branch: str, redact: str = "") -> None` | 8 |

CLI subcommands (`main`), each reading its values from environment variables:

| Subcommand | Job | Reads | Does |
| --- | --- | --- | --- |
| `plan` | plan | `PUSH_TARGET`, `VERSION`, `REPOSITORY_OWNER`, `RUN_ID`, `RUN_ATTEMPT`, `RUN_URL`, `GITHUB_TOKEN`, `GITHUB_OUTPUT` | validates inputs first, reads the tap, PyPI and (for `auto`) the contents API, writes the outputs below |
| `render` | formula | `SDIST_URL`, `SDIST_SHA256`; `--out PATH` | writes the rendered formula |
| `tags` | prove | `PLATFORMS` | prints the declared tags, one per line |
| `produced-tag` | bottle | `DECLARED_TAG`; `--json PATH` | `check_produced_tag` |
| `pour-check` | bottle | `TAP_OWNER`, `VERSION`; `--info PATH`, `--expect-built` | `check_pour` |
| `merged-tags` | prove | `PLATFORMS`; `--formula PATH` | `check_merged_tags` |
| `cache-check` | prove | `--formula PATH`, `--tag TAG`, `--file PATH` | `check_cache_file` |
| `validate-bottles` | upload | `BOTTLES_DIR`, `VERSION`, `ROOT_URL`, `PLATFORMS` | `validate_bottle_jsons` |
| `upload-bottles` | upload | the above plus `TAP_TOKEN`, `TAP_OWNER`, `TAG`, `BASE_SHA`, `CALLER`, `RUN_URL` | validates again, then `upload_bottles` |
| `validate-formula` | push | `MERGED_FORMULA`, `SDIST_URL`, `SDIST_SHA256`, `ROOT_URL`, `PLATFORMS`, `TAP_OWNER`, `TAG`, `VERSION`, `GITHUB_TOKEN` | reads the published release's digests, then `validate_formula` |
| `push-prepare` | push | `MERGED_FORMULA`, `PUSH_WORKDIR`, `TAP_OWNER`, `TARGET_BRANCH`, `DEFAULT_BRANCH`, `BASE_SHA`, `VERSION` | `prepare_push` |
| `push-publish` | push | `PUSH_WORKDIR`, `TAP_TOKEN`, `TAP_OWNER`, `TARGET_BRANCH` | `publish_push` |

`plan` job outputs, names exact: `tap_owner`, `default_branch`, `base_sha`, `target_branch`,
`sdist_url`, `sdist_sha256`, `tag`, `root_url`, `run_url`, `caller`, `platforms`.

Artifact names, exact: `homebrew-formula` (holding `job-sluice.rb`), `homebrew-bottle-json-<tag>`,
`homebrew-bottle-tar-<tag>`, `homebrew-merged-formula` (holding `job-sluice.rb`).

---

### Task 1: Measure a real merge and capture the fixtures

Nothing later in this plan guesses Homebrew's output. This task produces it on this machine,
records what it saw, and restores the machine. It writes no code.

The fixtures live in `tests/homebrew_fixtures/`, not under `tests/fixtures/`:
`tests/test_fixture_name_neutrality.py::test_the_corpus_sweep_actually_reads_the_fixtures` requires
every file under `tests/fixtures/` to be a captured board payload (`*/raw.json`), and narrowing that
#27 guard is not an option. Task 3 gives the new directory its own closure and content pins.

Every block below starts by exporting `HOMEBREW_NO_AUTO_UPDATE=1`, `HOMEBREW_NO_INSTALL_UPGRADE=1` and
`HOMEBREW_NO_INSTALL_CLEANUP=1` (each defined in Homebrew's `env_config.rb`), and where it needs the
worktree, setting `WT`: shell variables do not survive between separate command runs. They stop
`brew install` updating Homebrew and pruning old kegs. They do NOT stop a source build upgrading an
installed dependency that is outdated (`HOMEBREW_NO_INSTALL_UPGRADE` covers only the formula named),
which is why Step 2 stops before any build when a dependency is outdated. Run every block from the
worktree root.

**Files:**
- Create: `tests/homebrew_fixtures/merged_formula.rb`
- Create: `tests/homebrew_fixtures/bottle.json`
- Create: `tests/homebrew_fixtures/info_poured.json`

**Interfaces:**
- Produces: the three fixtures. Task 3 reads `bottle.json` and `info_poured.json`; Task 5 reads
  `merged_formula.rb`. The values below are the sanitised ones every later task's tests use:
  - top-level `url`: `https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz`
  - top-level `sha256`: `"c" * 64`
  - version: `9.9.0`
  - root URL: `https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1`
  - `arm64_tahoe` digest `"a" * 64`; `arm64_sequoia` digest `"b" * 64`
  - formula full name: `exampleowner/tap/job-sluice`

- [ ] **Step 1: Record the machine's Homebrew state, so it can be restored exactly**

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
mkdir -p /tmp/sluice-279-measure
brew tap > /tmp/sluice-279-measure/taps-before.txt
brew list --formula --versions > /tmp/sluice-279-measure/formulae-before.txt
brew trust > /tmp/sluice-279-measure/trust-before.txt 2>&1 || true
brew list --versions job-sluice || echo "job-sluice not installed"
```

Expected: the last command prints `job-sluice not installed`. If it prints a version instead, STOP
and report: this task uninstalls `job-sluice` at the end, and the owner must decide first.

- [ ] **Step 2: Tap the live tap and keep a pristine copy of its formula**

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
WT="$(git rev-parse --show-toplevel)"
M=/tmp/sluice-279-measure
brew tap mrreasonable/tap
TAP="$(brew --repository)/Library/Taps/mrreasonable/homebrew-tap"
git -C "$TAP" rev-parse HEAD
git ls-remote https://github.com/mrreasonable/homebrew-tap.git HEAD
cp "$TAP/Formula/job-sluice.rb" "$M/pristine.rb"
grep -c '^  resource "' "$M/pristine.rb"
brew deps --include-build --formula mrreasonable/tap/job-sluice | sort > "$M/deps.txt"
brew outdated --formula --quiet | sort > "$M/outdated.txt"
comm -12 "$M/deps.txt" "$M/outdated.txt"
"$WT/.venv/bin/python" -P - "$WT" "$M/pristine.rb" <<'PY'
import re, sys
sys.path.insert(0, sys.argv[1])
from scripts.render_homebrew_formula import render
text = open(sys.argv[2]).read()
url = re.search(r'^  url "([^"]+)"$', text, re.M).group(1)
sha = re.search(r'^  sha256 "([0-9a-f]{64})"$', text, re.M).group(1)
# The grammar Task 5's _STANZA_RE applies, copied exactly, so both steps strip the same stanzas.
stanza = re.compile(
    r'  resource "(?P<name>[A-Za-z0-9._-]+)" do\n'
    r'    url "https://files\.pythonhosted\.org/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/'
    r'(?P<project>[A-Za-z0-9._-]+)-(?P<version>[0-9][A-Za-z0-9.+!]*)\.tar\.gz"\n'
    r'    sha256 "[0-9a-f]{64}"\n'
    r"  end\n"
    r"\n"
)
stripped = stanza.sub("", text)
# Once this channel has published a release, the tap's formula carries a bottle block too. Strip it
# and say so, so that its presence is reported rather than read as renderer drift.
stripped, blocks = re.subn(r"  bottle do\n(?:    .*\n)+  end\n\n", "", stripped)
print(f"bottle blocks stripped: {blocks}")
assert 'resource "' not in stripped, "a resource stanza outside Task 5's grammar survived the strip"
print("skeleton matches the renderer" if stripped == render(sdist_url=url, sha256=sha) else "SKELETON DIFFERS")
PY
```

Expected: the two commit ids are identical; a resource count well above zero; `comm` prints nothing;
`bottle blocks stripped: 0` (or `1` once this channel has published a release); then
`skeleton matches the renderer`. STOP and report, before Step 3, if:
- the two commit ids differ: the tap pre-existed and its checkout is stale (auto-update is off), so
  `pristine.rb` is not the live formula. The owner decides whether to update it;
- `comm` prints any name: building would upgrade that installed dependency, and nothing here can put
  the old version back;
- the assertion reports a stanza outside the grammar: Task 5's accept row could never pass on this
  fixture;
- it prints `SKELETON DIFFERS`: the tap was last pushed by a different renderer than this branch's.

- [ ] **Step 3: Build a bottle-ready keg**

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
brew install --build-bottle mrreasonable/tap/job-sluice
```

Expected: a `🍺 .../job-sluice/<version>: ... built in ...` line, after about four minutes.

- [ ] **Step 4: Bottle it against a synthetic root URL**

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
cd /tmp/sluice-279-measure
brew bottle --json --no-rebuild --root-url=https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1 mrreasonable/tap/job-sluice
ls ./*.bottle.json ./*.bottle.tar.gz
python3 -c 'import json,glob;d=json.load(open(glob.glob("*.bottle.json")[0]));(k,v),=d.items();print(k);print(sorted(v));print(sorted(v["bottle"]));t=v["bottle"]["tags"];print(sorted(t));(h,)=t.values();print({x:h.get(x) for x in ("filename","local_filename","sha256")});print("bottle-level cellar",repr(v["bottle"].get("cellar")),"| per-tag cellar",repr(h.get("cellar")));print("rebuild",v["bottle"].get("rebuild"))'
```

Expected: exactly one `job-sluice--<version>.arm64_tahoe.bottle.json` and its `.tar.gz`. The Python
line prints the formula key, the entry's keys, the bottle keys, `['arm64_tahoe']`, a dict whose
`filename` has a single dash and `local_filename` a double dash, then the cellar, then `rebuild 0`.
`dev-cmd/bottle.rb` writes the cellar at the BOTTLE level (`"cellar" => bottle_cellar.to_s`), so the
bottle-level value should be a string such as `'any'` and the per-tag value `None`. **Write down
both printed cellar values**; Task 3 validates the bottle-level one, and Step 7 copies it.

- [ ] **Step 5: Pour it from a seeded cache while the URL does not exist, and record `brew info`**

This also executes the spec's §6a mechanism for real: the root URL above is a release that does not
exist, so a pour that succeeds used the seeded file.

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
cd /tmp/sluice-279-measure
brew bottle --merge --write --no-commit ./*.arm64_tahoe.bottle.json
brew info --json=v2 mrreasonable/tap/job-sluice > info-built.json
brew uninstall job-sluice
CACHE_PATH="$(brew --cache --bottle-tag=arm64_tahoe mrreasonable/tap/job-sluice)"
mkdir -p "$(dirname "$CACHE_PATH")"
cp ./job-sluice--*.arm64_tahoe.bottle.tar.gz "$CACHE_PATH"
brew install mrreasonable/tap/job-sluice
brew info --json=v2 mrreasonable/tap/job-sluice > info-poured.json
python3 -c 'import json;f=json.load(open("info-poured.json"))["formulae"];print(len(f),f[0]["full_name"]);k=f[0]["installed"];print(len(k),k[0]["version"],k[0]["poured_from_bottle"])'
python3 -c 'import json;k=json.load(open("info-built.json"))["formulae"][0]["installed"];print(len(k),k[0]["poured_from_bottle"])'
```

Expected: `1 mrreasonable/tap/job-sluice`, then `1 <version> True` for the pour, then `1 False` for
the built keg. If the pour prints `False`, or `brew install` tried to download and failed, STOP and
report the output: the spec's §6a mechanism is then wrong and must be revised before Task 3.

- [ ] **Step 6: Merge two tags into a pristine formula, and check style**

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
cd /tmp/sluice-279-measure
python3 - <<'PY'
import json, pathlib
src = next(pathlib.Path(".").glob("*.arm64_tahoe.bottle.json"))
data = json.loads(src.read_text())
(entry,) = data.values()
(tag_hash,) = entry["bottle"]["tags"].values()
seq = dict(tag_hash)
seq["filename"] = tag_hash["filename"].replace("arm64_tahoe", "arm64_sequoia")
seq["local_filename"] = tag_hash["local_filename"].replace("arm64_tahoe", "arm64_sequoia")
seq["sha256"] = "b" * 64
entry["bottle"]["tags"] = {"arm64_sequoia": seq}
pathlib.Path(src.name.replace("arm64_tahoe", "arm64_sequoia")).write_text(json.dumps(data, indent=2))
PY
cp /tmp/sluice-279-measure/pristine.rb "$(brew --repository)/Library/Taps/mrreasonable/homebrew-tap/Formula/job-sluice.rb"
brew bottle --merge --write --no-commit ./*.bottle.json
cp "$(brew --repository)/Library/Taps/mrreasonable/homebrew-tap/Formula/job-sluice.rb" merged-raw.rb
brew style --formula mrreasonable/tap/job-sluice
grep -n -B3 -A6 '^  bottle do$' merged-raw.rb
grep -n -B3 '^  def install$' merged-raw.rb
```

Expected: `brew style` reports no offences. The first `grep` shows the block and its neighbours.
**Write down** the two lines before `  bottle do` and the line after `  end`: Task 5's removal rule is
built from them (the spec's reading of `utils/ast.rb` predicts the block directly after
`  license "MIT"` and a blank line, followed by one blank line). The second `grep` shows what sits
directly above `  def install`: **write down** those lines too. Task 5's `_RESOURCES_PRECEDE` is built
from them (`utils/ast.rb::replace_resource_stanzas` inserts a new resource group directly above
`def install`, so the last line above it should be a stanza's `  end` followed by a blank line).

- [ ] **Step 7: Sanitise into the three fixtures**

```bash
WT="$(git rev-parse --show-toplevel)"
cd /tmp/sluice-279-measure
mkdir -p "$WT/tests/homebrew_fixtures"
python3 - "$WT/tests/homebrew_fixtures" <<'PY'
import json, pathlib, re, sys
out = pathlib.Path(sys.argv[1])
text = pathlib.Path("merged-raw.rb").read_text()
tahoe = json.loads(next(pathlib.Path(".").glob("*.arm64_tahoe.bottle.json")).read_text())
(full_name, entry), = tahoe.items()
real_version = entry["formula"]["pkg_version"]
(real_tag_hash,) = entry["bottle"]["tags"].values()
# Checked before anything is written: a path-valued cellar (Homebrew writes a non-default prefix's
# absolute path there) or a rebuild must never reach a fixture.
assert entry["bottle"]["cellar"] in ("any", "any_skip_relocation"), entry["bottle"]["cellar"]
assert entry["bottle"].get("rebuild", 0) == 0, entry["bottle"].get("rebuild")

# Top-level url and sha256: the only two-space-indented lines of each kind.
text, n_url = re.subn(r'^  url "[^"]+"$', '  url "https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz"', text, flags=re.M)
text, n_sha = re.subn(r'^  sha256 "[0-9a-f]{64}"$', '  sha256 "' + "c" * 64 + '"', text, flags=re.M)
assert (n_url, n_sha) == (1, 1), (n_url, n_sha)
assert real_tag_hash["sha256"] in text
text = text.replace(real_tag_hash["sha256"], "a" * 64)
assert text.count("job-sluice-9.9.0-1-1") == 1
(out / "merged_formula.rb").write_text(text)

# Homebrew writes the cellar at the BOTTLE level (dev-cmd/bottle.rb: "cellar" => bottle_cellar.to_s).
cellar = entry["bottle"]["cellar"]
bottle = {
    "exampleowner/tap/job-sluice": {
        "formula": {"name": "job-sluice", "pkg_version": "9.9.0"},
        "bottle": {
            "root_url": "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1",
            "cellar": cellar,
            "rebuild": 0,
            "tags": {
                "arm64_tahoe": {
                    "filename": real_tag_hash["filename"].replace(real_version, "9.9.0"),
                    "local_filename": real_tag_hash["local_filename"].replace(real_version, "9.9.0"),
                    "sha256": "a" * 64,
                }
            },
        },
    }
}
(out / "bottle.json").write_text(json.dumps(bottle, indent=2) + "\n")

poured = json.loads(pathlib.Path("info-poured.json").read_text())["formulae"][0]
keg = poured["installed"][0]
info = {"formulae": [{"full_name": "exampleowner/tap/job-sluice",
                      "installed": [{"version": "9.9.0", "poured_from_bottle": keg["poured_from_bottle"]}]}]}
(out / "info_poured.json").write_text(json.dumps(info, indent=2) + "\n")
print("cellar:", cellar, "| real version:", real_version)
PY
cd "$WT"
grep -c "exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1" tests/homebrew_fixtures/merged_formula.rb
grep -rnE '/(Users|home)/' tests/homebrew_fixtures/ || echo "no home paths"
```

Expected: `cellar: <any or any_skip_relocation> | real version: <version>`, then `1`, then
`no home paths`. The grep pattern is written with a group so that this plan does not itself contain a
home-directory prefix, which `tests/test_no_leaked_files.py` refuses in any tracked file.

- [ ] **Step 8: Restore the machine**

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
M=/tmp/sluice-279-measure
brew uninstall job-sluice
brew list --formula --versions > "$M/formulae-after.txt"
cut -d' ' -f1 "$M/formulae-before.txt" > "$M/names-before.txt"
cut -d' ' -f1 "$M/formulae-after.txt" > "$M/names-after.txt"
comm -13 "$M/names-before.txt" "$M/names-after.txt"
```

The `comm` output names the formulae this task added as dependencies. Uninstall exactly those, then
compare names AND versions with Step 1's record:

```bash
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1 HOMEBREW_NO_INSTALL_CLEANUP=1
M=/tmp/sluice-279-measure
comm -13 "$M/names-before.txt" "$M/names-after.txt" | xargs brew uninstall
brew list --formula --versions > "$M/formulae-after.txt"
diff "$M/formulae-before.txt" "$M/formulae-after.txt" && echo "formulae restored"
TAP="$(brew --repository)/Library/Taps/mrreasonable/homebrew-tap"
if grep -qx "mrreasonable/tap" "$M/taps-before.txt"; then
  cp "$M/pristine.rb" "$TAP/Formula/job-sluice.rb"
  git -C "$TAP" status --porcelain
  echo "the tap pre-existed; its formula is restored"
else
  brew untap mrreasonable/tap
fi
brew trust > "$M/trust-after.txt" 2>&1 || true
diff "$M/trust-before.txt" "$M/trust-after.txt" || echo "trust differs"
```

Expected: `formulae restored`. A `diff` line showing a formula whose VERSION changed means this task
upgraded something the machine already had: STOP and report it, since nothing here can put the old
version back. When the tap pre-existed, `git status --porcelain` prints nothing. If `trust differs`
prints, run `brew untrust --help`; if it exists, `brew untrust --formula mrreasonable/tap/job-sluice`,
else report the leftover trust entry.

- [ ] **Step 9: Commit the fixtures**

```bash
git add tests/homebrew_fixtures/merged_formula.rb tests/homebrew_fixtures/bottle.json tests/homebrew_fixtures/info_poured.json
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
test(packaging): capture a real two-tag bottle merge as fixtures (#279)

Sanitised output of brew bottle --json --no-rebuild, a two-tag
--merge --write --no-commit, and brew info after a pour from a seeded
cache, so the validators are proven against measured text.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
```

Expected: the whole suite passes after staging and before the commit. The fixtures are staged first so
that `tests/test_no_leaked_files.py`, which reads tracked files only, sweeps them.

- [ ] **Step 10: Report the measurements**

Report back: the skeleton result from Step 2; the `cellar` value from Step 4; the lines around the
block and the lines directly above `  def install` from Step 6; whether the Step 5 pour printed
`True`; the `brew style` result; and the restore result from Step 8.

---

### Task 2: The decision module's foundation

**Files:**
- Create: `scripts/homebrew_bottles.py`
- Create: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: nothing.
- Produces: every Task 2 row of the Interface Contract.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_homebrew_bottles.py`:

```python
"""Offline proof of every decision the Homebrew bottle channel makes (#279).

Design: docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md, section 9a. A green dry run
only ever takes a check's accept branch, so every refuse branch is proven here, and every function
also has an accept row: a check that always refuses would otherwise pass every refuse row.

EVERY expected value is restated here by hand. Module-level `_EXPECTED*` constants are guarded by
`test_every_expected_constant_is_built_only_from_literals`, ported from
tests/test_homebrew_formula.py for the same reason: an expectation read out of the module under
test compares that module with itself.

Fixtures are synthetic: `example.invalid` URLs where the host is not the property under test, fake
digests, owner `ExampleOwner`. Resource-stanza URLs keep the files.pythonhosted.org host because
that host is exactly what the formula grammar checks. tests/homebrew_fixtures/ holds sanitised real
Homebrew output. Its formula keeps the renderer's `homepage` line, which names the upstream project's
owner, because the validator compares that text with `render()`. A change to the renderer's template
makes that fixture stale: recapture it on a Mac with a real `brew bottle --merge`, as
docs/superpowers/plans/2026-09-14-homebrew-bottles.md's first task did, never by hand.
"""
import ast
import json
import pathlib

import pytest

from scripts import homebrew_bottles as hb
from scripts.homebrew_bottles import Refusal

ROOT = pathlib.Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "homebrew_bottles.py"
FIXTURES = ROOT / "tests" / "homebrew_fixtures"

_EXPECTED_PLATFORMS = {("macos-15", "arm64_sequoia"), ("macos-26", "arm64_tahoe")}

_PYPI_URL = "https://files.pythonhosted.org/packages/ab/cd/" + "e" * 60 + "/job_sluice-9.9.0.tar.gz"


# --- push_target and VERSION -------------------------------------------------------------------


def test_push_target_accepts_default_and_auto():
    assert hb.validate_push_target("default") == "default"
    assert hb.validate_push_target("auto") == "auto"


@pytest.mark.parametrize("value", [None, "", "bogus", "Default", "AUTO", "default "])
def test_push_target_refuses_anything_else_and_names_both_values(value):
    with pytest.raises(Refusal) as err:
        hb.validate_push_target(value)
    assert "'default'" in str(err.value) and "'auto'" in str(err.value)


@pytest.mark.parametrize("value", ["0.0.1", "2.9.7", "2.10.0"])
def test_version_accepts_three_integers(value):
    assert hb.validate_version(value) == value


@pytest.mark.parametrize(
    "value", [None, "", "2.10", "2.10.0.1", "v2.10.0", "2.10.0rc1", " 2.10.0", "2.x.0"]
)
def test_version_refuses_anything_else(value):
    with pytest.raises(Refusal):
        hb.validate_version(value)


def test_version_tuple_orders_numerically_across_a_digit_boundary():
    assert hb.version_tuple("2.9.7") < hb.version_tuple("2.10.0")
    assert hb.version_tuple("2.10.0") == (2, 10, 0)


# --- PyPI sdist ----------------------------------------------------------------------------------


def test_sdist_accepts_the_pythonhosted_url_for_this_version():
    hb.validate_sdist("9.9.0", _PYPI_URL, "d" * 64)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/packages/ab/cd/" + "e" * 60 + "/job_sluice-9.9.0.tar.gz",
        _PYPI_URL.replace("9.9.0", "9.9.1"),
        _PYPI_URL.replace("job_sluice", "other_pkg"),
        _PYPI_URL + '"; system "x"',
        None,
    ],
)
def test_sdist_refuses_a_url_off_grammar_or_for_another_file(url):
    with pytest.raises(Refusal):
        hb.validate_sdist("9.9.0", url, "d" * 64)


@pytest.mark.parametrize("sha256", [None, "", "d" * 63, "D" * 64, "g" * 64])
def test_sdist_refuses_a_malformed_digest(sha256):
    with pytest.raises(Refusal):
        hb.validate_sdist("9.9.0", _PYPI_URL, sha256)


def test_pick_sdist_returns_the_one_sdist():
    data = {
        "urls": [
            {"packagetype": "bdist_wheel", "url": "https://example.invalid/w.whl"},
            {"packagetype": "sdist", "url": _PYPI_URL, "digests": {"sha256": "d" * 64}},
        ]
    }
    assert hb.pick_sdist(data, "9.9.0") == (_PYPI_URL, "d" * 64)


@pytest.mark.parametrize("count", [0, 2])
def test_pick_sdist_refuses_zero_or_two_sdists(count):
    sdist = {"packagetype": "sdist", "url": _PYPI_URL, "digests": {"sha256": "d" * 64}}
    with pytest.raises(Refusal):
        hb.pick_sdist({"urls": [sdist] * count}, "9.9.0")


# --- owner, tag and root URL ---------------------------------------------------------------------


def test_tap_owner_lower_cases():
    assert hb.tap_owner("ExampleOwner") == "exampleowner"


@pytest.mark.parametrize("owner", [None, "", "-lead", "has space", "a" * 40, "x/y"])
def test_tap_owner_refuses_a_non_account_name(owner):
    with pytest.raises(Refusal):
        hb.tap_owner(owner)


def test_tag_is_deterministic():
    assert hb.compose_tag("9.9.0", "123", "1") == "job-sluice-9.9.0-123-1"
    assert hb.compose_tag("9.9.0", "123", "1") == hb.compose_tag("9.9.0", "123", "1")


def test_tag_changes_with_the_run_id_alone():
    assert hb.compose_tag("9.9.0", "123", "1") != hb.compose_tag("9.9.0", "124", "1")


def test_tag_changes_with_the_run_attempt_alone():
    assert hb.compose_tag("9.9.0", "123", "1") != hb.compose_tag("9.9.0", "123", "2")


@pytest.mark.parametrize("run_id, attempt", [("", "1"), ("0", "1"), ("12", "0"), ("12", "x"), ("-1", "1")])
def test_tag_refuses_a_non_positive_run_id_or_attempt(run_id, attempt):
    with pytest.raises(Refusal):
        hb.compose_tag("9.9.0", run_id, attempt)


def test_root_url_lower_cases_the_owner():
    assert hb.compose_root_url("ExampleOwner", "job-sluice-9.9.0-1-1") == (
        "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1"
    )


# --- the tap: default branch, bootstrap observable, target -------------------------------------


def test_symref_reads_the_branch_and_tip():
    output = "ref: refs/heads/main\tHEAD\n" + "f" * 40 + "\tHEAD\n"
    assert hb.parse_symref(output) == ("main", "f" * 40)


@pytest.mark.parametrize(
    "output", ["", "ref: refs/heads/main\tHEAD\n", "f" * 40 + "\tHEAD\n", "garbage\n"]
)
def test_symref_refuses_output_missing_either_half(output):
    with pytest.raises(Refusal):
        hb.parse_symref(output)


@pytest.mark.parametrize(
    "status, state",
    [(200, "present"), (404, "absent"), (403, "error"), (500, "error"), (301, "error")],
)
def test_the_formula_state_is_three_valued(status, state):
    assert hb.formula_state_from_status(status) == state


@pytest.mark.parametrize("state", ["present", "absent", "error"])
def test_default_targets_the_default_branch_whatever_the_observable_says(state):
    assert hb.resolve_target("default", state, "main", "9.9.0") == "main"


def test_auto_with_the_formula_present_targets_a_scratch_branch():
    assert hb.resolve_target("auto", "present", "main", "9.9.0") == "bump-9.9.0"


def test_auto_with_the_formula_absent_targets_the_default_branch():
    assert hb.resolve_target("auto", "absent", "main", "9.9.0") == "main"


@pytest.mark.parametrize("state", ["error", "", "unknown"])
def test_auto_with_an_unknown_state_refuses(state):
    with pytest.raises(Refusal):
        hb.resolve_target("auto", state, "main", "9.9.0")


def test_the_target_refuses_an_invalid_push_target():
    with pytest.raises(Refusal):
        hb.resolve_target("bogus", "present", "main", "9.9.0")


def test_the_caller_is_release_or_dry_run():
    assert (hb.caller_for("default"), hb.caller_for("auto")) == ("release", "dry run")


# --- platforms -------------------------------------------------------------------------------------


def test_the_emitted_platforms_are_the_two_pairs_restated_by_hand():
    emitted = json.loads(hb.platforms_json())
    assert {(entry["runner"], entry["tag"]) for entry in emitted} == _EXPECTED_PLATFORMS
    assert len(emitted) == len(_EXPECTED_PLATFORMS)


def test_declared_tags_reads_the_emitted_platforms():
    assert sorted(hb.declared_tags(hb.platforms_json())) == ["arm64_sequoia", "arm64_tahoe"]


@pytest.mark.parametrize(
    "platforms",
    [
        "[]",
        '[{"runner": "macos-15"}]',
        '[{"runner": "a", "tag": "x"}, {"runner": "b", "tag": "x"}]',
        '{"tag": "x"}',
    ],
)
def test_declared_tags_refuses_an_empty_or_malformed_set(platforms):
    with pytest.raises(Refusal):
        hb.declared_tags(platforms)


# --- the expectations stay literal -------------------------------------------------------------

_LITERAL_CONTAINERS = (ast.List, ast.Tuple, ast.Set)


def _is_literal_expression(node, already_validated: set[str]) -> bool:
    """Is `node` built purely from literals (and already-validated `_EXPECTED*` names)?"""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in already_validated
    if isinstance(node, _LITERAL_CONTAINERS):
        return all(_is_literal_expression(e, already_validated) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(
            k is not None
            and _is_literal_expression(k, already_validated)
            and _is_literal_expression(v, already_validated)
            for k, v in zip(node.keys, node.values)
        )
    return False


def test_every_expected_constant_is_built_only_from_literals():
    """Ported from tests/test_homebrew_formula.py::test_every_expected_constant_is_built_only_from_literals.

    This file imports the module under test, so `hb.PLATFORMS` is one attribute read away from an
    expectation. Refusing every `_EXPECTED*` right-hand side that is not built from literals keeps
    the restated pairs a second, independent source.
    """
    constants: dict[str, ast.expr] = {}
    for node in ast.parse(pathlib.Path(__file__).read_text()).body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id] if node.value is not None else []
        else:
            continue
        for name in names:
            if name.startswith("_EXPECTED"):
                constants[name] = node.value
    assert constants, "no module-level `_EXPECTED*` constant found; the sweep below proves nothing"
    validated: set[str] = set()
    for name, value in constants.items():
        assert _is_literal_expression(value, validated), (
            f"`{name}` is not built purely from literals: {ast.dump(value)[:300]}. Restate it."
        )
        validated.add(name)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'scripts.homebrew_bottles'`.

- [ ] **Step 3: Write the implementation**

Create `scripts/homebrew_bottles.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass.

- [ ] **Step 5: Commit, then witness three rows**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): add the Homebrew bottle channel's input and target decisions (#279)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
.venv/bin/python -m compileall -q -f --invalidation-mode checked-hash sluice tests scripts
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
```

For each mutant: apply it by deleting the named line(s) in `scripts/homebrew_bottles.py`, run the
named test, confirm it FAILS, then `cp /tmp/homebrew_bottles.py.bak scripts/homebrew_bottles.py`.

| Mutant (delete) | Test that must fail |
| --- | --- |
| the two lines `if formula_state == "absent":` / `return default_branch` in `resolve_target` | `test_auto_with_an_unknown_state_refuses` passes still; `test_auto_with_the_formula_absent_targets_the_default_branch` must fail |
| `or len(set(tags)) != len(tags)` (move the closing `):` up) | `test_declared_tags_refuses_an_empty_or_malformed_set` |
| the `if not _HEX64_RE.fullmatch(sha256 or ""):` block in `validate_sdist` | `test_sdist_refuses_a_malformed_digest` |

Run after each restore: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py` (expected: all
pass). Finish with `git status --short` (expected: clean).

- [ ] **Step 6: Write the always-refuse witness, and witness every accept row**

A green dry run only ever takes a check's accept branch, and a check that always refuses would pass
every refuse row, so each function's accept rows are witnessed by making the function refuse (spec,
§9a). Write `/tmp/always_refuse.py`:

```python
"""Witness a function's accept rows: the named tests pass, then the function is made to raise before
its first statement, then every named test must fail. The file is restored byte for byte whatever
happens. Exit 0 only if every named test passed before and failed after."""
import ast
import pathlib
import shutil
import subprocess
import sys

SCRIPT = pathlib.Path("scripts/homebrew_bottles.py")
BACKUP = pathlib.Path("/tmp/homebrew_bottles.py.always-refuse")


def run(test: str) -> subprocess.CompletedProcess:
    # --tb and --show-capture are explicit, so the output carries the mutant's message whatever the
    # repository's own pytest options say.
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
               "--show-capture=all", f"tests/test_homebrew_bottles.py::{test}"]
    return subprocess.run(command, capture_output=True, text=True)


function, tests = sys.argv[1], sys.argv[2:]
assert tests, "name at least one accept test"
if b"always-refuse mutant" in SCRIPT.read_bytes():
    sys.exit(f"{SCRIPT} still holds an earlier mutant: restore it from {BACKUP}, then rerun.")
not_green = [test for test in tests if run(test).returncode != 0]
assert not not_green, f"not passing before the mutant, so the mutant would prove nothing: {not_green}"
original = SCRIPT.read_bytes()
shutil.copyfile(SCRIPT, BACKUP)
matches = [node for node in ast.parse(original).body
           if isinstance(node, ast.FunctionDef) and node.name == function]
assert len(matches) == 1, f"expected one top-level function {function!r}, found {len(matches)}"
body = matches[0].body
has_docstring = (isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                 and isinstance(body[0].value.value, str))
first = body[1] if has_docstring else body[0]
lines = original.decode("utf-8").splitlines(keepends=True)
lines.insert(first.lineno - 1, " " * first.col_offset + 'raise Refusal("always-refuse mutant")\n')
try:
    mutated = "".join(lines)
    ast.parse(mutated)  # parseable, so a red test is red for the mutant's reason
    SCRIPT.write_text(mutated)
    # Exit 1 means tests failed, unlike a collection or usage error, and the marker shows the failure
    # came from this mutant's Refusal: main() prints it, and a traceback names it.
    unproven = []
    for test in tests:
        result = run(test)
        if result.returncode != 1 or "always-refuse mutant" not in result.stdout + result.stderr:
            unproven.append(test)
finally:
    shutil.copyfile(BACKUP, SCRIPT)
assert SCRIPT.read_bytes() == original, "the restore did not reproduce the original bytes"
BACKUP.unlink()
if unproven:
    print(f"{function}: UNPROVEN in {unproven}")
    sys.exit(1)
print(f"{function}: every accept row failed under the mutant")
```

Run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py validate_push_target test_push_target_accepts_default_and_auto
.venv/bin/python /tmp/always_refuse.py validate_version test_version_accepts_three_integers
.venv/bin/python /tmp/always_refuse.py version_tuple test_version_tuple_orders_numerically_across_a_digit_boundary
.venv/bin/python /tmp/always_refuse.py validate_sdist test_sdist_accepts_the_pythonhosted_url_for_this_version
.venv/bin/python /tmp/always_refuse.py pick_sdist test_pick_sdist_returns_the_one_sdist
.venv/bin/python /tmp/always_refuse.py tap_owner test_tap_owner_lower_cases
.venv/bin/python /tmp/always_refuse.py compose_tag test_tag_is_deterministic test_tag_changes_with_the_run_id_alone test_tag_changes_with_the_run_attempt_alone
.venv/bin/python /tmp/always_refuse.py compose_root_url test_root_url_lower_cases_the_owner
.venv/bin/python /tmp/always_refuse.py parse_symref test_symref_reads_the_branch_and_tip
.venv/bin/python /tmp/always_refuse.py formula_state_from_status test_the_formula_state_is_three_valued
.venv/bin/python /tmp/always_refuse.py resolve_target test_default_targets_the_default_branch_whatever_the_observable_says test_auto_with_the_formula_present_targets_a_scratch_branch test_auto_with_the_formula_absent_targets_the_default_branch
.venv/bin/python /tmp/always_refuse.py caller_for test_the_caller_is_release_or_dry_run
.venv/bin/python /tmp/always_refuse.py platforms_json test_the_emitted_platforms_are_the_two_pairs_restated_by_hand
.venv/bin/python /tmp/always_refuse.py declared_tags test_declared_tags_reads_the_emitted_platforms
git status --short
```

Expected: every line prints `<function>: every accept row failed under the mutant`, and
`git status --short` prints nothing. An `UNPROVEN` line names an accept row that passed under the
mutant, or failed without its message: read that test's output, and fix the test, not the script.

---

### Task 3: Bottle-side checks

**Files:**
- Modify: `scripts/homebrew_bottles.py`
- Modify: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: `Refusal`, `FORMULA_NAME`, `validate_version`, `_HEX64_RE` (Task 2); the fixtures
  `tests/homebrew_fixtures/bottle.json`, `info_poured.json`, `merged_formula.rb` (Task 1).
- Produces: `check_pour`, `check_produced_tag`, `Asset`, `ALLOWED_CELLARS`, `validate_bottle_jsons`,
  `parse_bottle_block`, `check_merged_tags`, `check_cache_file`.

Shapes read from Homebrew 6.0.22's source: `brew bottle --json` writes
`{full_name: {"formula": {...}, "bottle": {"root_url", "cellar", "rebuild", "date", "tags": {tag:
{"filename", "local_filename", "sha256", ...}}}}}` (`dev-cmd/bottle.rb`, the `--json` writer), with
`cellar` at the bottle level as a string. `brew info --json=v2 <formula>` gives each installed keg
as `{"version", "poured_from_bottle", ...}` (`formula.rb`, `to_hash`'s `installed` list). Task 1's
fixtures are sanitised measurements of both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_homebrew_bottles.py` (add `import hashlib` to the imports at the top):

```python
# --- the measured fixtures -----------------------------------------------------------------------


def test_the_fixture_directory_holds_exactly_the_three_measured_files():
    """This directory sits outside tests/fixtures/, whose scope guard admits only captured board
    payloads, so it carries its own closure: a file added here would sit in no pin below."""
    found = sorted(path.relative_to(FIXTURES).as_posix() for path in FIXTURES.rglob("*"))
    assert found == ["bottle.json", "info_poured.json", "merged_formula.rb"], found


def test_the_json_fixtures_carry_only_the_sanitised_keys_and_values():
    """Real `brew bottle --json` and `brew info` output also carry the build machine's OS, Xcode and
    CLT versions, timestamps, and the tap's remote and revision. A verbatim recapture must fail here,
    by name, rather than commit them."""
    bottle = json.loads((FIXTURES / "bottle.json").read_text())
    cellar = bottle.get("exampleowner/tap/job-sluice", {}).get("bottle", {}).pop("cellar", None)
    assert cellar in ("any", "any_skip_relocation"), cellar
    assert bottle == {
        "exampleowner/tap/job-sluice": {
            "formula": {"name": "job-sluice", "pkg_version": "9.9.0"},
            "bottle": {
                "root_url": "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1",
                "rebuild": 0,
                "tags": {
                    "arm64_tahoe": {
                        "filename": "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz",
                        "local_filename": "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz",
                        "sha256": "a" * 64,
                    }
                },
            },
        }
    }
    assert json.loads((FIXTURES / "info_poured.json").read_text()) == {
        "formulae": [{"full_name": "exampleowner/tap/job-sluice",
                      "installed": [{"version": "9.9.0", "poured_from_bottle": True}]}]
    }


# --- the pour check ---------------------------------------------------------------------------

_FULL_NAME = "exampleowner/tap/job-sluice"


def _info(*formulae):
    return {"formulae": list(formulae)}


def _formula(full_name=_FULL_NAME, *kegs):
    return {"full_name": full_name, "installed": list(kegs)}


def _keg(version="9.9.0", poured=True):
    return {"version": version, "poured_from_bottle": poured}


def test_the_measured_brew_info_passes_the_pour_check():
    info = json.loads((FIXTURES / "info_poured.json").read_text())
    hb.check_pour(info, full_name=_FULL_NAME, version="9.9.0")


def test_one_poured_keg_at_the_version_passes():
    hb.check_pour(_info(_formula(_FULL_NAME, _keg())), full_name=_FULL_NAME, version="9.9.0")


@pytest.mark.parametrize(
    "info",
    [
        # job-sluice built from source while a dependency beside it was poured: the case an
        # unscoped `--installed` check reads as success.
        _info(_formula("pango", _keg("1.0.0", True)), _formula(_FULL_NAME, _keg(poured=False))),
        _info(_formula(_FULL_NAME, _keg(poured=False))),
        _info(_formula("pango", _keg("1.0.0", True))),
        _info(_formula(_FULL_NAME)),
        _info(_formula(_FULL_NAME, _keg("9.8.0", True))),
        _info(_formula(_FULL_NAME, _keg(), _keg())),
        _info(_formula(_FULL_NAME, {"version": "9.9.0"})),
        # One poured keg at VERSION under another name: refused by the name check and nothing else.
        _info(_formula("exampleowner/tap/other", _keg())),
        _info(_formula("pango", _keg())),
        _info(),
        {},
    ],
)
def test_the_pour_check_refuses_anything_but_one_poured_keg_of_this_formula(info):
    with pytest.raises(Refusal):
        hb.check_pour(info, full_name=_FULL_NAME, version="9.9.0")


def test_expect_built_passes_a_built_keg():
    hb.check_pour(_info(_formula(_FULL_NAME, _keg(poured=False))), full_name=_FULL_NAME,
                  version="9.9.0", expect_built=True)


@pytest.mark.parametrize("poured", [True, None])
def test_expect_built_refuses_a_poured_or_unknown_keg(poured):
    keg = {"version": "9.9.0"} if poured is None else _keg(poured=poured)
    with pytest.raises(Refusal):
        hb.check_pour(_info(_formula(_FULL_NAME, keg)), full_name=_FULL_NAME, version="9.9.0",
                      expect_built=True)


# --- the produced tag -------------------------------------------------------------------------


def _fixture_bottle_json():
    return json.loads((FIXTURES / "bottle.json").read_text())


def test_the_measured_bottle_json_names_the_real_file_shapes():
    """Task 1 measured these: single dash for the download name, double dash on disk."""
    (entry,) = _fixture_bottle_json().values()
    (tag_hash,) = entry["bottle"]["tags"].values()
    assert (tag_hash["filename"], tag_hash["local_filename"]) == (
        "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz",
        "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz",
    )


def test_the_produced_tag_passes_when_it_is_the_declared_one():
    hb.check_produced_tag(_fixture_bottle_json(), "arm64_tahoe")


def _with_tags(tags):
    data = _fixture_bottle_json()
    (entry,) = data.values()
    (tag_hash,) = entry["bottle"]["tags"].values()
    entry["bottle"]["tags"] = {tag: tag_hash for tag in tags}
    return data


@pytest.mark.parametrize(
    "data, declared",
    [
        (_with_tags(["arm64_tahoe"]), "arm64_sequoia"),
        (_with_tags([]), "arm64_tahoe"),
        (_with_tags(["arm64_tahoe", "arm64_sequoia"]), "arm64_tahoe"),
        (_with_tags(["arm64_tahoe"]), ""),
        ({}, "arm64_tahoe"),
    ],
)
def test_the_produced_tag_refuses_a_mismatch_zero_two_or_an_empty_declaration(data, declared):
    with pytest.raises(Refusal):
        hb.check_produced_tag(data, declared)


# --- bottle JSONs, as upload reads them --------------------------------------------------------

_ROOT_URL = "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-1-1"
_BOTH_TAGS = ["arm64_sequoia", "arm64_tahoe"]


def _bottle_dir(tmp_path, tags=("arm64_sequoia", "arm64_tahoe"), mutate=None):
    """One JSON and one bottle file per tag, shaped like the measured fixture."""
    (entry,) = _fixture_bottle_json().values()
    (template,) = entry["bottle"]["tags"].values()
    paths = []
    for tag in tags:
        payload = f"bottle bytes for {tag}".encode()
        tag_hash = dict(
            template,
            filename=f"job-sluice-9.9.0.{tag}.bottle.tar.gz",
            local_filename=f"job-sluice--9.9.0.{tag}.bottle.tar.gz",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        doc = {_FULL_NAME: {"formula": dict(entry["formula"]),
                            "bottle": dict(entry["bottle"], tags={tag: tag_hash})}}
        if mutate is not None:
            mutate(tag, doc)
        (tmp_path / f"job-sluice--9.9.0.{tag}.bottle.tar.gz").write_bytes(payload)
        path = tmp_path / f"job-sluice--9.9.0.{tag}.bottle.json"
        path.write_text(json.dumps(doc))
        paths.append(path)
    return paths


def test_two_valid_bottle_jsons_yield_their_assets_in_declared_order(tmp_path):
    paths = _bottle_dir(tmp_path)
    assets = hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)
    assert [(a.tag, a.remote_name, a.local_path.name) for a in assets] == [
        ("arm64_sequoia", "job-sluice-9.9.0.arm64_sequoia.bottle.tar.gz",
         "job-sluice--9.9.0.arm64_sequoia.bottle.tar.gz"),
        ("arm64_tahoe", "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz",
         "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz"),
    ]
    assert assets[0].sha256 == hashlib.sha256(b"bottle bytes for arm64_sequoia").hexdigest()


def _bottle(doc):
    (entry,) = doc.values()
    return entry["bottle"]


def _only_tag_hash(doc):
    (tag_hash,) = _bottle(doc)["tags"].values()
    return tag_hash


_JSON_MUTANTS = {
    "wrong root url": lambda tag, doc: _bottle(doc).update(root_url=_ROOT_URL + "x"),
    "non-zero rebuild": lambda tag, doc: _bottle(doc).update(rebuild=1),
    "path-valued cellar": lambda tag, doc: _bottle(doc).update(cellar="/opt/homebrew/Cellar"),
    "missing cellar": lambda tag, doc: _bottle(doc).pop("cellar"),
    "download name with double dash": lambda tag, doc: _only_tag_hash(doc).update(
        filename=f"job-sluice--9.9.0.{tag}.bottle.tar.gz"),
    "path component in local name": lambda tag, doc: _only_tag_hash(doc).update(
        local_filename=f"../job-sluice--9.9.0.{tag}.bottle.tar.gz"),
    "digest mismatch": lambda tag, doc: _only_tag_hash(doc).update(sha256="0" * 64),
    "malformed digest": lambda tag, doc: _only_tag_hash(doc).update(sha256="Z" * 64),
    "second formula": lambda tag, doc: doc.update({"other/tap/x": {}}),
}


@pytest.mark.parametrize("mutant", sorted(_JSON_MUTANTS))
def test_a_bottle_json_off_contract_is_refused(tmp_path, mutant):
    paths = _bottle_dir(tmp_path, mutate=_JSON_MUTANTS[mutant])
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)


def test_a_json_whose_bottle_is_absent_is_refused(tmp_path):
    paths = _bottle_dir(tmp_path)
    (tmp_path / "job-sluice--9.9.0.arm64_tahoe.bottle.tar.gz").unlink()
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=_BOTH_TAGS)


@pytest.mark.parametrize(
    "made, declared",
    [
        (("arm64_tahoe",), _BOTH_TAGS),
        (("arm64_sequoia", "arm64_tahoe"), ["arm64_tahoe"]),
        (("arm64_sequoia", "arm64_tahoe"), []),
    ],
)
def test_the_json_set_must_equal_the_declared_tags(tmp_path, made, declared):
    paths = _bottle_dir(tmp_path, tags=made)
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=declared)


def test_two_jsons_for_one_tag_are_refused(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    paths = _bottle_dir(tmp_path / "a", tags=("arm64_tahoe",)) + _bottle_dir(
        tmp_path / "b", tags=("arm64_tahoe",))
    with pytest.raises(Refusal):
        hb.validate_bottle_jsons(paths, version="9.9.0", root_url=_ROOT_URL, tags=["arm64_tahoe"])


# --- the merged bottle block -------------------------------------------------------------------


def _merged():
    return (FIXTURES / "merged_formula.rb").read_text()


def test_the_measured_merge_parses_to_both_tags():
    root_url, tags, _ = hb.parse_bottle_block(_merged())
    assert root_url == _ROOT_URL
    assert {tag: sha for tag, (_cellar, sha) in tags.items()} == {
        "arm64_tahoe": "a" * 64,
        "arm64_sequoia": "b" * 64,
    }


def test_the_merged_tag_set_passes_when_it_equals_the_declared_one():
    hb.check_merged_tags(_merged(), _BOTH_TAGS)


@pytest.mark.parametrize("declared", [["arm64_tahoe"], _BOTH_TAGS + ["arm64_golden_gate"], []])
def test_the_merged_tag_set_refuses_a_missing_extra_or_empty_declaration(declared):
    with pytest.raises(Refusal):
        hb.check_merged_tags(_merged(), declared)


def _block_line(text, startswith):
    return next(line for line in text.splitlines() if line.startswith(startswith))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace(_block_line(t, "    root_url "), _block_line(t, "    root_url ") + "\n    rebuild 1"),
        lambda t: t + "\n  bottle do\n  end\n",
        lambda t: t.replace("  bottle do\n", "  bottle_block do\n"),
        lambda t: t.replace(_block_line(t, "    sha256 cellar:"), _block_line(t, "    sha256 cellar:") + '; system "x"'),
    ],
)
def test_a_bottle_block_off_shape_is_refused(mutate):
    with pytest.raises(Refusal):
        hb.parse_bottle_block(mutate(_merged()))


def test_the_cache_file_passes_when_its_digest_is_the_blocks():
    formula = _merged().replace("a" * 64, hashlib.sha256(b"payload").hexdigest())
    hb.check_cache_file(b"payload", formula, "arm64_tahoe")


@pytest.mark.parametrize("tag, data", [("arm64_tahoe", b"other"), ("arm64_golden_gate", b"payload")])
def test_the_cache_file_refuses_other_bytes_or_an_undeclared_tag(tag, data):
    formula = _merged().replace("a" * 64, hashlib.sha256(b"payload").hexdigest())
    with pytest.raises(Refusal):
        hb.check_cache_file(data, formula, tag)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: the new tests FAIL with `AttributeError: module 'scripts.homebrew_bottles' has no attribute
'check_pour'` (and similar), except three that read only the fixtures and pass at once:
`test_the_fixture_directory_holds_exactly_the_three_measured_files`,
`test_the_json_fixtures_carry_only_the_sanitised_keys_and_values` and
`test_the_measured_bottle_json_names_the_real_file_shapes`. Task 2's tests still pass.

- [ ] **Step 3: Write the implementation**

In `scripts/homebrew_bottles.py`, change the imports to:

```python
import hashlib
import json
import re
from pathlib import Path
from typing import NamedTuple
```

and append:

```python
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
    unexpected = sorted(set(found) - set(tags))
    if missing or unexpected:
        raise Refusal(
            f"the bottle JSONs do not match the declared tags: missing {missing}, "
            f"unexpected {unexpected}."
        )
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass. If `test_the_measured_bottle_json_names_the_real_file_shapes` or a pour row fails,
compare with Task 1's report before changing anything: the fixtures are measurements.

- [ ] **Step 5: Commit, then witness each row**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): check produced tags, pours, bottle JSONs and merged blocks as data (#279)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
cp tests/homebrew_fixtures/bottle.json /tmp/bottle.json.bak
```

| Mutant | Test that must fail |
| --- | --- |
| delete the `if formula.get("full_name") != full_name:` block in `check_pour` | `test_the_pour_check_refuses_anything_but_one_poured_keg_of_this_formula` |
| delete the `if actual != sha256:` block in `validate_bottle_jsons` | `test_a_bottle_json_off_contract_is_refused[digest mismatch]` |
| delete the `if set(block_tags) != set(tags):` block in `check_merged_tags` | `test_the_merged_tag_set_refuses_a_missing_extra_or_empty_declaration` |
| delete the `"pkg_version": "9.9.0"` entry, and the comma before it, from `tests/homebrew_fixtures/bottle.json` | `test_the_json_fixtures_carry_only_the_sanitised_keys_and_values` |
| create an empty `tests/homebrew_fixtures/extra.json` (adds) | `test_the_fixture_directory_holds_exactly_the_three_measured_files` |

Restore after each: `cp /tmp/homebrew_bottles.py.bak scripts/homebrew_bottles.py`,
`cp /tmp/bottle.json.bak tests/homebrew_fixtures/bottle.json`, or `rm tests/homebrew_fixtures/extra.json`.
Rerun the file (all pass), and finish with `git status --short` clean.

- [ ] **Step 6: Witness every accept row**

With `/tmp/always_refuse.py` from Task 2, run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py check_pour test_the_measured_brew_info_passes_the_pour_check test_one_poured_keg_at_the_version_passes test_expect_built_passes_a_built_keg
.venv/bin/python /tmp/always_refuse.py check_produced_tag test_the_produced_tag_passes_when_it_is_the_declared_one
.venv/bin/python /tmp/always_refuse.py validate_bottle_jsons test_two_valid_bottle_jsons_yield_their_assets_in_declared_order
.venv/bin/python /tmp/always_refuse.py parse_bottle_block test_the_measured_merge_parses_to_both_tags
.venv/bin/python /tmp/always_refuse.py check_merged_tags test_the_merged_tag_set_passes_when_it_equals_the_declared_one
.venv/bin/python /tmp/always_refuse.py check_cache_file test_the_cache_file_passes_when_its_digest_is_the_blocks
git status --short
```

Expected: every line prints `<function>: every accept row failed under the mutant`, and
`git status --short` prints nothing.

---

### Task 4: Release decisions

**Files:**
- Modify: `scripts/homebrew_bottles.py`
- Modify: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: `Refusal`, `FORMULA_NAME`, `validate_version` (Task 2).
- Produces: `release_templates`, `release_decision`, `asset_decision`, `release_digests`, `_TAG_RE`,
  `_RUN_URL_RE`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_homebrew_bottles.py`:

```python
# --- release templates and lifecycle --------------------------------------------------------------

_TAG = "job-sluice-9.9.0-123-1"
_RUN_URL = "https://github.com/ExampleOwner/sluice/actions/runs/123"
_BASE_SHA = "f" * 40

_EXPECTED_RELEASE_TITLE = "job-sluice 9.9.0 bottles (release)"
_EXPECTED_DRY_RUN_TITLE = "job-sluice 9.9.0 bottles (dry run)"
_EXPECTED_RELEASE_NOTES = (
    "Bottles for job-sluice 9.9.0, published by "
    "https://github.com/ExampleOwner/sluice/actions/runs/123 (release).\n"
    "Release tag: job-sluice-9.9.0-123-1\n"
)


def test_the_release_templates_are_fixed_text_for_each_caller():
    assert hb.release_templates(version="9.9.0", tag=_TAG, caller="release", run_url=_RUN_URL) == (
        _EXPECTED_RELEASE_TITLE, _EXPECTED_RELEASE_NOTES)
    title, notes = hb.release_templates(version="9.9.0", tag=_TAG, caller="dry run", run_url=_RUN_URL)
    assert title == _EXPECTED_DRY_RUN_TITLE
    assert notes == _EXPECTED_RELEASE_NOTES.replace("(release)", "(dry run)")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"caller": "pull request"},
        {"run_url": "https://example.invalid/actions/runs/123"},
        {"run_url": _RUN_URL + "\nextra"},
        {"tag": "job-sluice-9.9.0"},
        {"version": "9.9"},
    ],
)
def test_the_release_templates_refuse_unexpected_inputs(kwargs):
    arguments = {"version": "9.9.0", "tag": _TAG, "caller": "release", "run_url": _RUN_URL}
    arguments.update(kwargs)
    with pytest.raises(Refusal):
        hb.release_templates(**arguments)


def _release(**overrides):
    release = {"id": 7, "tag_name": _TAG, "target_commitish": _BASE_SHA,
               "name": _EXPECTED_RELEASE_TITLE, "body": _EXPECTED_RELEASE_NOTES, "draft": True}
    release.update(overrides)
    return release


def _decide(releases):
    return hb.release_decision(releases, tag=_TAG, base_sha=_BASE_SHA,
                               title=_EXPECTED_RELEASE_TITLE, notes=_EXPECTED_RELEASE_NOTES)


def test_an_absent_release_is_to_be_created():
    assert _decide([_release(tag_name="job-sluice-9.8.0-1-1")]) is None
    assert _decide([]) is None


@pytest.mark.parametrize("draft", [True, False])
def test_a_matching_release_is_accepted_draft_or_published(draft):
    assert _decide([_release(draft=draft)])["id"] == 7


@pytest.mark.parametrize(
    "overrides",
    [{"target_commitish": "e" * 40}, {"name": "other"}, {"body": "other"}],
)
def test_a_release_that_differs_is_refused(overrides):
    with pytest.raises(Refusal) as err:
        _decide([_release(**overrides)])
    assert next(iter(overrides)) in str(err.value)


def test_two_releases_under_one_tag_are_refused():
    with pytest.raises(Refusal):
        _decide([_release(), _release(id=8)])


def test_an_absent_asset_is_uploaded():
    assert hb.asset_decision([{"name": "other"}], name="n", sha256="a" * 64) == "upload"


def test_an_identical_asset_is_skipped():
    assert hb.asset_decision([{"name": "n", "digest": "sha256:" + "a" * 64}], name="n",
                             sha256="a" * 64) == "skip"


@pytest.mark.parametrize("digest", ["sha256:" + "b" * 64, None, "", "a" * 64, "md5:abc"])
def test_a_different_or_missing_digest_is_refused(digest):
    asset = {"name": "n"} if digest is None else {"name": "n", "digest": digest}
    with pytest.raises(Refusal):
        hb.asset_decision([asset], name="n", sha256="a" * 64)


def _assets():
    return [
        {"name": "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz", "digest": "sha256:" + "a" * 64},
        {"name": "job-sluice-9.9.0.arm64_sequoia.bottle.tar.gz", "digest": "sha256:" + "b" * 64},
    ]


def test_release_digests_map_each_declared_tag_to_its_assets_digest():
    assert hb.release_digests(_assets(), version="9.9.0", tags=_BOTH_TAGS) == {
        "arm64_sequoia": "b" * 64, "arm64_tahoe": "a" * 64}


@pytest.mark.parametrize(
    "assets",
    [
        _assets()[:1],
        [dict(_assets()[0], digest="sha256:" + "A" * 64), _assets()[1]],
        [dict(_assets()[0], digest=None), _assets()[1]],
        _assets() + _assets()[:1],
    ],
)
def test_release_digests_refuse_a_missing_malformed_or_duplicated_asset(assets):
    with pytest.raises(Refusal):
        hb.release_digests(assets, version="9.9.0", tags=_BOTH_TAGS)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: the new tests FAIL with `AttributeError` for `release_templates` and the others.

- [ ] **Step 3: Write the implementation**

Append to `scripts/homebrew_bottles.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass.

- [ ] **Step 5: Commit, then witness two rows**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): decide the tap release's lifecycle and asset uploads as data (#279)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
```

| Mutant (delete) | Test that must fail |
| --- | --- |
| the `if differs:` block in `release_decision` | `test_a_release_that_differs_is_refused` |
| the `if digest.removeprefix("sha256:") != sha256:` block in `asset_decision` | `test_a_different_or_missing_digest_is_refused` |

Restore after each, rerun (all pass), `git status --short` clean.

- [ ] **Step 6: Witness every accept row**

With `/tmp/always_refuse.py` from Task 2, run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py release_templates test_the_release_templates_are_fixed_text_for_each_caller
.venv/bin/python /tmp/always_refuse.py release_decision test_an_absent_release_is_to_be_created test_a_matching_release_is_accepted_draft_or_published
.venv/bin/python /tmp/always_refuse.py asset_decision test_an_absent_asset_is_uploaded test_an_identical_asset_is_skipped
.venv/bin/python /tmp/always_refuse.py release_digests test_release_digests_map_each_declared_tag_to_its_assets_digest
git status --short
```

Expected: every line prints `<function>: every accept row failed under the mutant`, and
`git status --short` prints nothing.

---

### Task 5: The formula validator

**Files:**
- Modify: `scripts/homebrew_bottles.py`
- Modify: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: `parse_bottle_block`, `Refusal` (Tasks 2, 3); `scripts/render_homebrew_formula.py::render`;
  `tests/homebrew_fixtures/merged_formula.rb` and Task 1's report of the lines around the block.
- Produces: `ROOT`, `_render`, `validate_formula`.

The accept fixture is the renderer's text at capture time. If a later change to
`scripts/render_homebrew_formula.py`'s template makes the accept row fail at the skeleton comparison,
recapture the fixture with Task 1's Steps 2 to 7 rather than editing it by hand. The fixture stays the
accept text, rather than one assembled at test time from `render()`: a renderer change alters what
every Mac merges, so it earns a real merge and `brew style` again.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_homebrew_bottles.py`:

```python
# --- the formula validator ---------------------------------------------------------------------

_FIXTURE_SDIST = "https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz"
_DIGESTS = {"arm64_tahoe": "a" * 64, "arm64_sequoia": "b" * 64}


def _validate(text, **overrides):
    arguments = {"sdist_url": _FIXTURE_SDIST, "sha256": "c" * 64, "root_url": _ROOT_URL,
                 "tags": _BOTH_TAGS, "release_digests": dict(_DIGESTS)}
    arguments.update(overrides)
    hb.validate_formula(text, **arguments)


def test_the_measured_merge_is_accepted():
    _validate(_merged())


def _refused(text, **overrides):
    try:
        _validate(text, **overrides)
    except Refusal:
        return True
    return False


def test_every_injection_into_every_line_of_the_measured_merge_is_refused():
    """Generated from the fixture, never hand-picked, so no line is left untested."""
    lines = _merged().split("\n")
    cases = []
    for index, line in enumerate(lines):
        before = lines[:index] + ['  system "x"'] + lines[index:]
        after = lines[: index + 1] + ['  system "x"'] + lines[index + 1 :]
        appended = lines[:index] + [line + '; system "x"'] + lines[index + 1 :]
        cases += [("statement before", index, before), ("statement after", index, after),
                  ("appended", index, appended)]
        for quote in range(0, line.count('"') // 2):
            position = [i for i, ch in enumerate(line) if ch == '"'][2 * quote]
            interpolated = line[: position + 1] + "#{1}" + line[position + 1 :]
            cases.append(("interpolation", index, lines[:index] + [interpolated] + lines[index + 1 :]))
    quoted_values = sum(line.count('"') // 2 for line in lines)
    assert len(cases) == 3 * len(lines) + quoted_values and quoted_values > 0, (
        "the generator produced the wrong number of cases; the loop below would prove less than it claims"
    )
    accepted = [(kind, index) for kind, index, candidate in cases if not _refused("\n".join(candidate))]
    assert accepted == [], f"injections the validator accepted: {accepted[:20]}"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sdist_url": _FIXTURE_SDIST.replace("9.9.0", "9.9.1")},
        {"sha256": "d" * 64},
        {"root_url": _ROOT_URL.replace("exampleowner", "someoneelse")},
        {"tags": ["arm64_tahoe"]},
        {"tags": _BOTH_TAGS + ["arm64_golden_gate"]},
        {"tags": []},
        {"release_digests": dict(_DIGESTS, arm64_tahoe="f" * 64)},
        {"release_digests": {"arm64_tahoe": "a" * 64}},
    ],
)
def test_arguments_that_disagree_with_the_text_are_refused(overrides):
    with pytest.raises(Refusal):
        _validate(_merged(), **overrides)


_STANZA_URL = "https://files.pythonhosted.org/packages/ab/cd/" + "e" * 60 + "/"


def _replace_first_stanza(text, name, filename):
    start = text.index('  resource "')
    end = text.index("  end\n", start) + len("  end\n")
    stanza = (f'  resource "{name}" do\n    url "{_STANZA_URL}{filename}"\n'
              f'    sha256 "{"9" * 64}"\n  end\n')
    return text[:start] + stanza + text[end:]


@pytest.mark.parametrize(
    "name, filename",
    [("alpha-beta", "alpha_beta-1.0.tar.gz"), ("Alpha-Beta", "alpha.beta-1.0.tar.gz")],
)
def test_a_resource_named_as_pep_503_normalises_its_project_is_accepted(name, filename):
    _validate(_replace_first_stanza(_merged(), name, filename))


@pytest.mark.parametrize(
    "name, filename",
    [("alpha", "alpha-beta-1.0.tar.gz"), ("alpha", "other-1.0.tar.gz")],
)
def test_a_resource_whose_project_is_not_its_name_is_refused(name, filename):
    with pytest.raises(Refusal):
        _validate(_replace_first_stanza(_merged(), name, filename))


def test_a_resource_on_a_foreign_host_is_refused():
    text = _merged()
    start = text.index('    url "https://files.pythonhosted.org/')
    with pytest.raises(Refusal):
        _validate(text[:start] + text[start:].replace("files.pythonhosted.org", "example.invalid", 1))


def test_a_rebuild_line_in_the_block_is_refused():
    text = _merged()
    root_line = _block_line(text, "    root_url ")
    with pytest.raises(Refusal):
        _validate(text.replace(root_line, root_line + "\n    rebuild 1"))


def _resource_run(text):
    """The measured merge's resource run: from its first stanza to the end of its last."""
    start = text.index('  resource "')
    last = text.rindex('  resource "')
    return start, text.index("  end\n\n", last) + len("  end\n\n")


def test_the_resource_run_moved_below_the_final_end_is_refused():
    """Removing a contiguous run restores the renderer's text wherever the run sits, so only its
    position refuses this: `resource` below the class's `end` is undefined when the formula loads."""
    text = _merged()
    start, end = _resource_run(text)
    with pytest.raises(Refusal):
        _validate(text[:start] + text[end:] + text[start:end])


def test_a_resource_stanza_split_from_its_run_is_refused():
    """One stanza moved above `test do`, at the start of its own line: removing every stanza still
    restores the renderer's text, so only the run's contiguity refuses this."""
    text = _merged()
    start, _ = _resource_run(text)
    first_end = text.index("  end\n\n", start) + len("  end\n\n")
    stanza, rest = text[start:first_end], text[:start] + text[first_end:]
    anchor = rest.index("  test do\n")
    with pytest.raises(Refusal):
        _validate(rest[:anchor] + stanza + rest[anchor:])


def _bottle_block(text):
    """The measured merge's bottle block with the blank line after it, and the text without them."""
    start = text.index("  bottle do\n")
    end = text.index("  end\n", start) + len("  end\n") + 1
    return text[start:end], text[:start] + text[end:]


def test_the_bottle_block_moved_below_the_final_end_is_refused():
    """Removing the block and its blank line restores the renderer's text wherever the block sits, so
    only its position refuses this: `bottle` below the class's `end` breaks every install."""
    block, rest = _bottle_block(_merged())
    with pytest.raises(Refusal):
        _validate(rest + block)


def test_the_bottle_block_moved_between_two_resource_stanzas_is_refused():
    """Removing the block leaves the stanzas contiguous again, so the run's contiguity cannot see this."""
    block, rest = _bottle_block(_merged())
    start, _ = _resource_run(rest)
    first_end = rest.index("  end\n\n", start) + len("  end\n\n")
    with pytest.raises(Refusal):
        _validate(rest[:first_end] + block + rest[first_end:])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: the new tests FAIL with `AttributeError: ... has no attribute 'validate_formula'`.

- [ ] **Step 3: Write the implementation**

In `scripts/homebrew_bottles.py`, add `import sys` to the imports, add this constant after
`TAP_REPO`:

```python
# The repository root, so this file can import the renderer when run as `python3 -P <path>`: -P
# removes the script's own directory from sys.path, and the renderer is the one thing the
# validator must reproduce rather than restate.
ROOT = Path(__file__).resolve().parent.parent
```

and append:

```python
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
```

If Task 1's report shows the block is NOT followed by exactly one blank line, change the two lines
marked `# measured separator` to remove the separator Task 1 recorded instead, and say so in the
commit body. Do not change them otherwise.

Likewise, if Task 1's report shows the resource run is NOT directly above `  def install`, change the
`_RESOURCES_PRECEDE` value marked `# measured anchor` to the line Task 1 recorded, and say so in the
commit body. The same holds for `_BLOCK_FOLLOWS_RE`: if the two lines Task 1 recorded above
`  bottle do` are not the `license` line and a blank line, change that pattern to match them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass, including the generated injection test.

- [ ] **Step 5: Commit, then witness each row**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): validate the pushed Homebrew formula as data (#279)

The merged formula must be exactly the renderer's text, strict resource
stanzas and one bottle block consistent with the published release.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
```

| Mutant (delete) | Test that must fail |
| --- | --- |
| the whole `for stanza in stanzas:` loop | `test_a_resource_whose_project_is_not_its_name_is_refused` |
| the whole `for previous, current in zip(stanzas, stanzas[1:]):` loop | `test_a_resource_stanza_split_from_its_run_is_refused` |
| the `if not remainder[start:].startswith(_RESOURCES_PRECEDE):` block | `test_the_resource_run_moved_below_the_final_end_is_refused` |
| the `if not _BLOCK_FOLLOWS_RE.search(text[: block.start()]):` block | `test_the_bottle_block_moved_below_the_final_end_is_refused` and `test_the_bottle_block_moved_between_two_resource_stanzas_is_refused` |
| the `for tag in tags:` digest loop | `test_arguments_that_disagree_with_the_text_are_refused` |
| the `if remainder != expected:` block | `test_every_injection_into_every_line_of_the_measured_merge_is_refused` |

Restore after each, rerun (all pass), `git status --short` clean.

- [ ] **Step 6: Witness every accept row**

With `/tmp/always_refuse.py` from Task 2, run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py validate_formula test_the_measured_merge_is_accepted test_a_resource_named_as_pep_503_normalises_its_project_is_accepted
git status --short
```

Expected: `validate_formula: every accept row failed under the mutant`, and `git status --short`
prints nothing.

---

### Task 6: The push decision

**Files:**
- Modify: `scripts/homebrew_bottles.py`
- Modify: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: `Refusal`, `version_tuple`, `FORMULA_NAME` (Task 2).
- Produces: `parse_formula_version`, `push_decision`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_homebrew_bottles.py`:

```python
# --- the push decision ----------------------------------------------------------------------------


def _formula_at(version):
    return _merged().replace("job_sluice-9.9.0.tar.gz", f"job_sluice-{version}.tar.gz").encode()


def test_the_version_is_read_from_the_top_level_url_as_integers():
    assert hb.parse_formula_version(_formula_at("9.10.0").decode()) == (9, 10, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        _merged().replace("job_sluice-9.9.0.tar.gz", "job_sluice-9.9.tar.gz"),
        _merged().replace('  url "', '  url  "', 1),
        _merged() + '  url "https://example.invalid/job_sluice-1.0.0.tar.gz"\n',
    ],
)
def test_an_unreadable_version_is_refused(text):
    with pytest.raises(Refusal):
        hb.parse_formula_version(text)


def _push(**overrides):
    arguments = {"target_state": "present", "remote_formula": _formula_at("9.8.0"),
                 "ours": _formula_at("9.9.0"), "target_is_default": True,
                 "base_formula": _formula_at("9.8.0"), "version": "9.9.0"}
    arguments.update(overrides)
    return hb.push_decision(**arguments)


def test_identical_bytes_at_the_target_are_a_no_op():
    assert _push(remote_formula=_formula_at("9.9.0")) == "noop"


def test_different_bytes_at_the_target_push():
    assert _push() == "push"


def test_an_absent_scratch_branch_pushes():
    assert _push(target_state="absent", remote_formula=None, target_is_default=False) == "push"


def test_a_target_without_the_formula_pushes():
    assert _push(remote_formula=None) == "push"


@pytest.mark.parametrize("base", ["9.9.0", "9.8.9", "2.9.7"])
def test_an_equal_or_older_base_version_pushes(base):
    assert _push(base_formula=_formula_at(base)) == "push"


def test_a_newer_base_version_on_the_default_branch_is_refused():
    """9.10.0 against 9.9.0: a string comparison would call 9.10.0 older and roll the tap back."""
    with pytest.raises(Refusal):
        _push(base_formula=_formula_at("9.10.0"))


def test_a_newer_base_version_on_a_scratch_branch_pushes():
    assert _push(target_is_default=False, base_formula=_formula_at("9.10.0")) == "push"


def test_the_bootstrap_with_no_formula_at_the_base_pushes():
    assert _push(target_state="present", remote_formula=None, base_formula=None) == "push"


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_formula": b'  url "https://example.invalid/unversioned.tar.gz"\n'},
        {"base_formula": b"\xff\xfe"},
        {"target_state": "absent", "remote_formula": None},
        {"target_state": "error"},
        {"version": "9.9"},
    ],
)
def test_an_unparseable_base_an_absent_default_or_a_bad_state_is_refused(overrides):
    with pytest.raises(Refusal):
        _push(**overrides)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: the new tests FAIL with `AttributeError` for `parse_formula_version` and `push_decision`.

- [ ] **Step 3: Write the implementation**

Append to `scripts/homebrew_bottles.py`:

```python
# --- the push -----------------------------------------------------------------------------------

# The renderer writes no `version` stanza, so the version lives only in the top-level url line: two
# spaces of indent, where a resource's url line has four.
_TOP_URL_RE = re.compile(
    r'^  url "https://[^"\n]+/job_sluice-(?P<version>\d+\.\d+\.\d+)\.tar\.gz"$', re.MULTILINE
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
) -> str:
    """'noop' or 'push', or a refusal (spec, section 6c).

    - An absent default branch is refused; an absent scratch branch is the first dry run of a
      version.
    - Identical bytes at the target are a re-run after this release's push already landed.
    - On the default branch, the formula at BASE_SHA must not be newer than VERSION: the re-run of
      an older release must not roll the tap back. A base with no formula is the bootstrap.
    """
    wanted = version_tuple(version)
    if target_state not in ("present", "absent"):
        raise Refusal(f"the target branch's state is {target_state!r}, not present or absent.")
    if target_state == "absent" and target_is_default:
        raise Refusal("the tap's default branch does not exist; refusing to create it from a publish.")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass.

- [ ] **Step 5: Commit, then witness two rows**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): decide the tap push from the target and base commit (#279)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
```

| Mutant | Test that must fail |
| --- | --- |
| delete `if current > wanted:` and its `raise` | `test_a_newer_base_version_on_the_default_branch_is_refused` |
| delete the line `if target_state == "present" and remote_formula is not None and remote_formula == ours:` and the `return "noop"` under it | `test_identical_bytes_at_the_target_are_a_no_op` |

Restore after each, rerun (all pass), `git status --short` clean.

- [ ] **Step 6: Witness every accept row**

With `/tmp/always_refuse.py` from Task 2, run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py parse_formula_version test_the_version_is_read_from_the_top_level_url_as_integers
.venv/bin/python /tmp/always_refuse.py push_decision test_identical_bytes_at_the_target_are_a_no_op test_different_bytes_at_the_target_push test_an_absent_scratch_branch_pushes test_a_target_without_the_formula_pushes test_an_equal_or_older_base_version_pushes test_a_newer_base_version_on_a_scratch_branch_pushes test_the_bootstrap_with_no_formula_at_the_base_pushes
git status --short
```

Expected: every line prints `<function>: every accept row failed under the mutant`, and
`git status --short` prints nothing.

---

### Task 7: The CLI, `plan`, and the untrusted jobs' checks

**Files:**
- Modify: `scripts/homebrew_bottles.py`
- Modify: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: every function from Tasks 2 to 6.
- Produces: `API`, `http_request`, `github_request`, `git`, `_git_ok`, `_require`, `_read_json`,
  `write_outputs`, `build_plan`, the subcommands `plan`, `render`, `tags`, `produced-tag`,
  `pour-check`, `merged-tags`, `cache-check`, and `main`. Task 8 replaces `_parser` and `_COMMANDS`
  with versions that add its subcommands.

- [ ] **Step 1: Write the failing tests**

In `tests/test_homebrew_bottles.py`, add `import subprocess` and `import sys` to the imports, then
append:

```python
# --- the CLI: plan -------------------------------------------------------------------------------


def test_the_plan_subcommand_refuses_a_bad_push_target_before_any_external_command():
    """Executed with an empty PATH: the refusal must come before git or the network, so a caller
    passing a bad push target fails before anything reads the tap. Every other variable `plan`
    requires before its first git call is set, so only statement order stands between the bad value
    and git."""
    proc = subprocess.run(
        [sys.executable, "-P", str(SCRIPT), "plan"],
        env={"PATH": "", "PUSH_TARGET": "bogus", "VERSION": "1.2.3", "REPOSITORY_OWNER": "ExampleOwner"},
        capture_output=True, text=True, timeout=60,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, output
    assert proc.stdout.startswith("::error::"), output
    assert "'default'" in output and "'auto'" in output


_SYMREF = "ref: refs/heads/main\tHEAD\n" + "f" * 40 + "\tHEAD\n"
_PYPI = {"urls": [{"packagetype": "sdist", "url": _PYPI_URL, "digests": {"sha256": "d" * 64}}]}

# Restated by hand; built with string repetition, so deliberately not an `_EXPECTED*` name.
_PLAN_FOR_DEFAULT = {
    "tap_owner": "exampleowner",
    "default_branch": "main",
    "base_sha": "f" * 40,
    "target_branch": "main",
    "sdist_url": "https://files.pythonhosted.org/packages/ab/cd/" + "e" * 60 + "/job_sluice-9.9.0.tar.gz",
    "sdist_sha256": "d" * 64,
    "tag": "job-sluice-9.9.0-123-1",
    "root_url": "https://github.com/exampleowner/homebrew-tap/releases/download/job-sluice-9.9.0-123-1",
    "run_url": "https://github.com/ExampleOwner/sluice/actions/runs/123",
    "caller": "release",
    "platforms": '[{"runner": "macos-15", "tag": "arm64_sequoia"}, '
                 '{"runner": "macos-26", "tag": "arm64_tahoe"}]',
}


def _plan(**overrides):
    arguments = {"push_target": "default", "version": "9.9.0", "repository_owner": "ExampleOwner",
                 "run_id": "123", "run_attempt": "1", "run_url": _RUN_URL,
                 "ls_remote_output": _SYMREF, "pypi_json": _PYPI, "contents_status": None}
    arguments.update(overrides)
    return hb.build_plan(**arguments)


def test_the_default_plan_emits_every_output():
    assert _plan() == _PLAN_FOR_DEFAULT


@pytest.mark.parametrize("status, target", [(200, "bump-9.9.0"), (404, "main")])
def test_the_auto_plan_targets_by_the_observable(status, target):
    plan = _plan(push_target="auto", contents_status=status)
    assert (plan["target_branch"], plan["caller"]) == (target, "dry run")


@pytest.mark.parametrize(
    "overrides",
    [
        {"push_target": "auto", "contents_status": 403},
        {"push_target": "auto", "contents_status": None},
        {"run_url": "https://example.invalid/runs/1"},
        {"version": "9.9"},
        {"repository_owner": "has space"},
        {"ls_remote_output": ""},
        {"pypi_json": {"urls": []}},
    ],
)
def test_a_plan_with_a_bad_input_or_an_unknown_observable_is_refused(overrides):
    with pytest.raises(Refusal):
        _plan(**overrides)


def test_outputs_are_written_one_per_line(tmp_path):
    path = tmp_path / "output"
    hb.write_outputs({"a": "1", "b": "two"}, str(path))
    assert path.read_text() == "a=1\nb=two\n"


def test_an_output_with_a_newline_is_refused(tmp_path):
    with pytest.raises(Refusal):
        hb.write_outputs({"a": "1\nb=2"}, str(tmp_path / "output"))


class _FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request):
        self.calls.append((request.get_method(), request.full_url, dict(request.header_items())))
        response = self.routes.get((request.get_method(), request.full_url))
        if response is None:
            raise AssertionError(f"unexpected request {request.get_method()} {request.full_url}")
        return response


def _fake_git(stdout):
    def fake(*args, cwd=None):
        return subprocess.CompletedProcess(["git", *args], 0, stdout.encode(), b"")
    return fake


@pytest.mark.parametrize(
    "push_target, status, target", [("default", None, "main"), ("auto", 200, "bump-9.9.0"),
                                    ("auto", 404, "main")],
)
def test_plan_writes_its_outputs_and_reads_the_contents_api_only_for_auto(
    tmp_path, monkeypatch, push_target, status, target
):
    monkeypatch.setattr(hb, "git", _fake_git(_SYMREF))
    routes = {("GET", "https://pypi.org/pypi/job-sluice/9.9.0/json"): (200, json.dumps(_PYPI).encode())}
    contents = ("https://api.github.com/repos/exampleowner/homebrew-tap/contents/Formula/"
                "job-sluice.rb?ref=" + "f" * 40)
    if status is not None:
        routes[("GET", contents)] = (status, b"{}")
    http = _FakeHttp(routes)
    output = tmp_path / "output"
    env = {"PUSH_TARGET": push_target, "VERSION": "9.9.0", "REPOSITORY_OWNER": "ExampleOwner",
           "RUN_ID": "123", "RUN_ATTEMPT": "1", "RUN_URL": _RUN_URL,
           "GITHUB_TOKEN": "workflow-token", "GITHUB_OUTPUT": str(output)}
    assert hb.main(["plan"], env=env, http=http) == 0
    outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert outputs["target_branch"] == target
    contents_calls = [call for call in http.calls if call[1] == contents]
    assert len(contents_calls) == (0 if push_target == "default" else 1)
    if contents_calls:
        assert contents_calls[0][2]["Authorization"] == "Bearer workflow-token"


# --- the CLI: the untrusted jobs' checks ------------------------------------------------------------


def test_render_writes_the_renderers_text(tmp_path):
    from scripts.render_homebrew_formula import render

    out = tmp_path / "job-sluice.rb"
    env = {"SDIST_URL": _FIXTURE_SDIST, "SDIST_SHA256": "c" * 64}
    assert hb.main(["render", "--out", str(out)], env=env) == 0
    assert out.read_text() == render(sdist_url=_FIXTURE_SDIST, sha256="c" * 64)


def test_tags_prints_one_declared_tag_per_line(capsys):
    assert hb.main(["tags"], env={"PLATFORMS": hb.platforms_json()}) == 0
    assert capsys.readouterr().out.split() == ["arm64_sequoia", "arm64_tahoe"]


def test_a_missing_environment_variable_is_a_refusal(capsys):
    assert hb.main(["tags"], env={}) == 1
    assert "PLATFORMS" in capsys.readouterr().out


def test_produced_tag_exits_by_the_check(tmp_path, capsys):
    path = tmp_path / "bottle.json"
    path.write_text((FIXTURES / "bottle.json").read_text())
    assert hb.main(["produced-tag", "--json", str(path)], env={"DECLARED_TAG": "arm64_tahoe"}) == 0
    assert hb.main(["produced-tag", "--json", str(path)], env={"DECLARED_TAG": "arm64_sequoia"}) == 1
    assert "::error::" in capsys.readouterr().out


def test_pour_check_scopes_to_the_tap_formula_and_has_an_expect_built_mode(tmp_path):
    path = tmp_path / "info.json"
    path.write_text((FIXTURES / "info_poured.json").read_text())
    env = {"TAP_OWNER": "exampleowner", "VERSION": "9.9.0"}
    assert hb.main(["pour-check", "--info", str(path)], env=env) == 0
    assert hb.main(["pour-check", "--info", str(path), "--expect-built"], env=env) == 1
    other_owner = {"TAP_OWNER": "otherowner", "VERSION": "9.9.0"}
    assert hb.main(["pour-check", "--info", str(path)], env=other_owner) == 1


def test_merged_tags_and_cache_check_exit_by_their_checks(tmp_path):
    formula = tmp_path / "job-sluice.rb"
    formula.write_text(_merged().replace("a" * 64, hashlib.sha256(b"payload").hexdigest()))
    bottle = tmp_path / "bottle"
    bottle.write_bytes(b"payload")
    platforms = {"PLATFORMS": hb.platforms_json()}
    assert hb.main(["merged-tags", "--formula", str(formula)], env=platforms) == 0
    check = ["cache-check", "--formula", str(formula), "--tag", "arm64_tahoe", "--file"]
    assert hb.main(check + [str(bottle)], env={}) == 0
    assert hb.main(check + [str(tmp_path / "missing")], env={}) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: the new tests FAIL (`AttributeError` for `build_plan`, `main`, `write_outputs`; the
subprocess test fails its assertions, because the script has no `__main__` block yet and exits 0 with
no output).

- [ ] **Step 3: Write the implementation**

In `scripts/homebrew_bottles.py`, change the imports to:

```python
import argparse
import hashlib
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
```

and append:

```python
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
        raise Refusal(f"git {args[0]} failed with exit {proc.returncode}: {message[:500]}")
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


def write_outputs(outputs: dict[str, str], path: str) -> None:
    """Append `key=value` lines to $GITHUB_OUTPUT. A value with a newline would inject a second
    output, so it is refused."""
    for key, value in outputs.items():
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


# --- main ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homebrew_bottles.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan")
    sub.add_parser("tags")
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
```

Keep `main` and the `if __name__` block at the END of the file: Task 8 inserts its functions above
`# --- main ---`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass.

- [ ] **Step 5: Commit, then witness one row**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): add the bottle channel's plan and check subcommands (#279)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
```

Mutant: MOVE the two lines `push_target = validate_push_target(env.get("PUSH_TARGET"))` and
`version = validate_version(env.get("VERSION"))` in `cmd_plan` to just below the `ls_remote = _git_ok(`
statement (the program still runs: both names are defined before their next use). Run
`.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py -k before_any_external_command`;
expected: FAIL, because another failure now comes first and its message does not name both push
targets. Restore with `cp`, rerun the
file (all pass), `git status --short` clean.

- [ ] **Step 6: Witness every accept row**

With `/tmp/always_refuse.py` from Task 2, run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py build_plan test_the_default_plan_emits_every_output test_the_auto_plan_targets_by_the_observable
.venv/bin/python /tmp/always_refuse.py write_outputs test_outputs_are_written_one_per_line
.venv/bin/python /tmp/always_refuse.py cmd_plan test_plan_writes_its_outputs_and_reads_the_contents_api_only_for_auto
.venv/bin/python /tmp/always_refuse.py cmd_render test_render_writes_the_renderers_text
.venv/bin/python /tmp/always_refuse.py cmd_tags test_tags_prints_one_declared_tag_per_line
.venv/bin/python /tmp/always_refuse.py cmd_produced_tag test_produced_tag_exits_by_the_check
.venv/bin/python /tmp/always_refuse.py cmd_pour_check test_pour_check_scopes_to_the_tap_formula_and_has_an_expect_built_mode
.venv/bin/python /tmp/always_refuse.py cmd_merged_tags test_merged_tags_and_cache_check_exit_by_their_checks
.venv/bin/python /tmp/always_refuse.py cmd_cache_check test_merged_tags_and_cache_check_exit_by_their_checks
git status --short
```

Expected: every line prints `<function>: every accept row failed under the mutant`, and
`git status --short` prints nothing.

---

### Task 8: Publishing I/O: the release upload and the tap push

**Files:**
- Modify: `scripts/homebrew_bottles.py`
- Modify: `tests/test_homebrew_bottles.py`

**Interfaces:**
- Consumes: Tasks 2 to 7.
- Produces: `list_releases`, `list_assets`, `upload_bottles`, `prepare_push`, `publish_push`,
  `_read_formula`, `_formula_at_commit`, the
  subcommands `validate-bottles`, `upload-bottles`, `validate-formula`, `push-prepare`,
  `push-publish`, and the replacement `_parser` and `_COMMANDS`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_homebrew_bottles.py`, add `import os` and `import re` to the imports, then append:

```python
# --- the release upload --------------------------------------------------------------------------

_RELEASES = "https://api.github.com/repos/exampleowner/homebrew-tap/releases"


class _FakeGitHub:
    """Just enough of the releases API for upload_bottles and validate-formula."""

    def __init__(self, releases=(), assets=(), fail=None):
        self.releases = [dict(release) for release in releases]
        self.assets = {release["id"]: [dict(a) for a in assets] for release in self.releases}
        self.fail = fail or {}
        self.calls = []

    def __call__(self, request):
        method, url = request.get_method(), request.full_url
        self.calls.append((method, url, request.data))
        if (method, url.split("?")[0]) in self.fail:
            return self.fail[(method, url.split("?")[0])], b"{}"
        if method == "GET" and url.startswith(_RELEASES + "?"):
            page = int(url.rsplit("page=", 1)[1])
            return 200, json.dumps(self.releases if page == 1 else []).encode()
        listing = re.fullmatch(re.escape(_RELEASES) + r"/(\d+)/assets\?per_page=100&page=(\d+)", url)
        if method == "GET" and listing:
            page = self.assets.get(int(listing[1]), []) if listing[2] == "1" else []
            return 200, json.dumps(page).encode()
        if method == "POST" and url == _RELEASES:
            release = dict(json.loads(request.data), id=99)
            self.releases.append(release)
            self.assets[99] = []
            return 201, json.dumps(release).encode()
        upload = re.fullmatch(
            r"https://uploads\.github\.com/repos/exampleowner/homebrew-tap/releases/(\d+)/assets"
            r"\?name=(.+)", url)
        if method == "POST" and upload:
            digest = "sha256:" + hashlib.sha256(request.data).hexdigest()
            self.assets[int(upload[1])].append({"name": upload[2], "digest": digest})
            return 201, b"{}"
        patch = re.fullmatch(re.escape(_RELEASES) + r"/(\d+)", url)
        if method == "PATCH" and patch:
            release = next(r for r in self.releases if r["id"] == int(patch[1]))
            release.update(json.loads(request.data))
            return 200, json.dumps(release).encode()
        raise AssertionError(f"unexpected request {method} {url}")

    def writes(self):
        return [(method, url) for method, url, _ in self.calls if method != "GET"]


def _assets_for(tmp_path):
    return hb.validate_bottle_jsons(_bottle_dir(tmp_path), version="9.9.0", root_url=_ROOT_URL,
                                    tags=_BOTH_TAGS)


def _upload(fake, assets):
    hb.upload_bottles(http=fake, token="tap-token", owner="exampleowner", tag=_TAG,
                      base_sha=_BASE_SHA, title=_EXPECTED_RELEASE_TITLE,
                      notes=_EXPECTED_RELEASE_NOTES, assets=assets)


def _uploaded(asset):
    return {"name": asset.remote_name, "digest": "sha256:" + asset.sha256}


def test_an_absent_release_is_created_as_a_draft_filled_then_published(tmp_path):
    fake = _FakeGitHub()
    _upload(fake, _assets_for(tmp_path))
    created = json.loads(next(data for method, url, data in fake.calls
                              if method == "POST" and url == _RELEASES))
    assert created == {"tag_name": _TAG, "target_commitish": _BASE_SHA,
                       "name": _EXPECTED_RELEASE_TITLE, "body": _EXPECTED_RELEASE_NOTES,
                       "draft": True}
    assert sorted(a["name"] for a in fake.assets[99]) == [
        "job-sluice-9.9.0.arm64_sequoia.bottle.tar.gz", "job-sluice-9.9.0.arm64_tahoe.bottle.tar.gz"]
    assert fake.writes()[-1] == ("PATCH", f"{_RELEASES}/99")
    assert fake.releases[-1]["draft"] is False


def test_a_partial_draft_is_completed_by_the_rerun(tmp_path):
    assets = _assets_for(tmp_path)
    fake = _FakeGitHub(releases=[_release(draft=True)], assets=[_uploaded(assets[0])])
    _upload(fake, assets)
    uploads = [url for method, url in fake.writes() if method == "POST"]
    assert len(uploads) == 1 and uploads[0].endswith("arm64_tahoe.bottle.tar.gz")
    assert fake.writes()[-1] == ("PATCH", f"{_RELEASES}/7")


def test_a_complete_published_release_writes_nothing(tmp_path):
    assets = _assets_for(tmp_path)
    fake = _FakeGitHub(releases=[_release(draft=False)], assets=[_uploaded(a) for a in assets])
    _upload(fake, assets)
    assert fake.writes() == []


def test_a_release_that_differs_refuses_before_any_write(tmp_path):
    fake = _FakeGitHub(releases=[_release(target_commitish="e" * 40)])
    with pytest.raises(Refusal):
        _upload(fake, _assets_for(tmp_path))
    assert fake.writes() == []


def test_a_different_asset_digest_refuses_before_any_upload(tmp_path):
    assets = _assets_for(tmp_path)
    clash = {"name": assets[1].remote_name, "digest": "sha256:" + "0" * 64}
    fake = _FakeGitHub(releases=[_release(draft=True)], assets=[clash])
    with pytest.raises(Refusal):
        _upload(fake, assets)
    assert fake.writes() == []


@pytest.mark.parametrize("fail", [{("GET", _RELEASES): 500}, {("POST", _RELEASES): 422}])
def test_a_failed_listing_or_create_is_a_refusal(tmp_path, fail):
    with pytest.raises(Refusal):
        _upload(_FakeGitHub(fail=fail), _assets_for(tmp_path))


def test_upload_bottles_from_the_cli_validates_then_publishes(tmp_path):
    _bottle_dir(tmp_path)
    fake = _FakeGitHub()
    env = {"BOTTLES_DIR": str(tmp_path), "VERSION": "9.9.0", "ROOT_URL": _ROOT_URL,
           "PLATFORMS": hb.platforms_json(), "TAP_TOKEN": "tap-token", "TAP_OWNER": "exampleowner",
           "TAG": _TAG, "BASE_SHA": _BASE_SHA, "CALLER": "release", "RUN_URL": _RUN_URL}
    assert hb.main(["validate-bottles"], env=env, http=fake) == 0
    assert fake.calls == []
    assert hb.main(["upload-bottles"], env=env, http=fake) == 0
    assert fake.releases[-1]["draft"] is False


def test_validate_formula_reads_the_published_releases_digests(tmp_path):
    formula = tmp_path / "job-sluice.rb"
    formula.write_text(_merged())
    env = {"MERGED_FORMULA": str(formula), "SDIST_URL": _FIXTURE_SDIST, "SDIST_SHA256": "c" * 64,
           "ROOT_URL": _ROOT_URL, "PLATFORMS": hb.platforms_json(), "TAP_OWNER": "exampleowner",
           "TAG": "job-sluice-9.9.0-1-1", "VERSION": "9.9.0", "GITHUB_TOKEN": "workflow-token"}
    published = _FakeGitHub(releases=[_release(tag_name="job-sluice-9.9.0-1-1", draft=False)],
                            assets=_assets())
    assert hb.main(["validate-formula"], env=env, http=published) == 0
    draft_only = _FakeGitHub(releases=[_release(tag_name="job-sluice-9.9.0-1-1", draft=True)],
                             assets=_assets())
    assert hb.main(["validate-formula"], env=env, http=draft_only) == 1


@pytest.mark.parametrize("newline", ["\r", "\r\n"])
def test_a_carriage_return_in_the_merged_formula_is_refused(tmp_path, newline):
    """`read_text()` would turn a lone CR into LF, so the text validated would not be the bytes pushed,
    and Ruby does not end a line at a lone CR. The reader both subcommands share takes the bytes and
    refuses a CR."""
    formula = tmp_path / "job-sluice.rb"
    formula.write_bytes(_merged().replace("\n", newline, 1).encode())
    env = {"MERGED_FORMULA": str(formula), "SDIST_URL": _FIXTURE_SDIST, "SDIST_SHA256": "c" * 64,
           "ROOT_URL": _ROOT_URL, "PLATFORMS": hb.platforms_json(), "TAP_OWNER": "exampleowner",
           "TAG": "job-sluice-9.9.0-1-1", "VERSION": "9.9.0", "GITHUB_TOKEN": "workflow-token"}
    published = _FakeGitHub(releases=[_release(tag_name="job-sluice-9.9.0-1-1", draft=False)],
                            assets=_assets())
    assert hb.main(["validate-formula"], env=env, http=published) == 1
    with pytest.raises(Refusal):
        hb._read_formula(str(formula))


# --- the tap push, against local repositories -------------------------------------------------------


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch):
    """Real git runs in this file. An inherited GIT_DIR or GIT_INDEX_FILE (pytest started from a git
    hook) would aim it at another repository, and a system or global `core.hooksPath` would make the
    hook test below pass without the helper's doing."""
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


def _git_run(cwd, *args):
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=Test",
         "-c", "user.email=test@example.invalid", "-c", "init.defaultBranch=main", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _make_tap(tmp_path, formula):
    """A bare 'origin' with a main branch, optionally holding Formula/job-sluice.rb."""
    origin = tmp_path / "origin.git"
    _git_run(tmp_path, "init", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git_run(tmp_path, "clone", origin.as_uri(), str(seed))
    (seed / "README.md").write_text("tap\n")
    if formula is not None:
        (seed / "Formula").mkdir()
        (seed / "Formula" / "job-sluice.rb").write_bytes(formula)
    _git_run(seed, "add", "-A")
    _git_run(seed, "commit", "-m", "seed")
    _git_run(seed, "push", "origin", "HEAD:refs/heads/main")
    return origin.as_uri(), seed, _git_run(seed, "rev-parse", "HEAD").stdout.decode().strip()


def _commit_to(seed, branch, formula):
    _git_run(seed, "checkout", "-B", branch)
    (seed / "Formula").mkdir(exist_ok=True)
    (seed / "Formula" / "job-sluice.rb").write_bytes(formula)
    _git_run(seed, "add", "-A")
    _git_run(seed, "commit", "-m", f"to {branch}")
    _git_run(seed, "push", "--force", "origin", f"HEAD:refs/heads/{branch}")


def _remote_tip(seed, branch):
    out = _git_run(seed, "ls-remote", "origin", f"refs/heads/{branch}").stdout.decode().split()
    return out[0] if out else None


def _remote_formula(seed, branch):
    _git_run(seed, "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    return _git_run(seed, "show", f"refs/remotes/origin/{branch}:Formula/job-sluice.rb").stdout


def _prepare(tmp_path, url, base_sha, **overrides):
    arguments = {"remote_url": url, "workdir": tmp_path / "work", "target_branch": "main",
                 "default_branch": "main", "base_sha": base_sha, "version": "9.9.0",
                 "formula": _formula_at("9.9.0")}
    arguments.update(overrides)
    return hb.prepare_push(**arguments)


def test_a_new_version_is_committed_on_the_base_and_pushed(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_formula(seed, "main") == _formula_at("9.9.0")
    log = _git_run(seed, "log", "-1", "--format=%an|%s", "origin/main").stdout.decode().strip()
    assert log == "sluice-release-please[bot]|job-sluice 9.9.0"


def test_identical_bytes_are_a_no_op_and_publish_pushes_nothing(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.9.0"))
    assert _prepare(tmp_path, url, base) == "noop"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_tip(seed, "main") == base


def test_a_newer_tap_version_refuses_before_committing(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.10.0"))
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base)
    assert _remote_tip(seed, "main") == base


def test_a_default_branch_that_moved_since_plan_rejects_the_push(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _commit_to(seed, "main", _formula_at("9.8.5"))
    moved = _remote_tip(seed, "main")
    assert _prepare(tmp_path, url, base) == "push"
    with pytest.raises(Refusal):
        hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_tip(seed, "main") == moved


def test_the_first_scratch_push_creates_the_branch(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_formula(seed, "bump-9.9.0") == _formula_at("9.9.0")


def test_a_stale_scratch_branch_is_replaced_under_its_lease(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _commit_to(seed, "bump-9.9.0", b"stale\n")
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_formula(seed, "bump-9.9.0") == _formula_at("9.9.0")


def test_a_scratch_branch_that_moves_after_prepare_is_not_clobbered(tmp_path):
    url, seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    _commit_to(seed, "bump-9.9.0", b"stale\n")
    assert _prepare(tmp_path, url, base, target_branch="bump-9.9.0") == "push"
    _commit_to(seed, "bump-9.9.0", b"someone else\n")
    with pytest.raises(Refusal):
        hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="bump-9.9.0")
    assert _remote_formula(seed, "bump-9.9.0") == b"someone else\n"


def test_an_absent_default_branch_refuses(tmp_path):
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base, target_branch="trunk", default_branch="trunk")


def test_a_failed_tree_read_refuses_rather_than_reading_as_the_bootstrap(tmp_path, monkeypatch):
    """With 9.8.0 at the base this would push. A failed `ls-tree` of the BASE, taken as "no formula
    there", would reach the bootstrap arm and skip the default branch's version refusal, so it must
    refuse. Only the base read fails here, so the target read cannot refuse in its place."""
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    real_git = hb.git

    def failing_ls_tree(*args, cwd=None):
        if args and args[0] == "ls-tree" and base in args:
            return subprocess.CompletedProcess(["git", *args], 128, b"", b"fatal: simulated")
        return real_git(*args, cwd=cwd)

    monkeypatch.setattr(hb, "git", failing_ls_tree)
    with pytest.raises(Refusal):
        _prepare(tmp_path, url, base)


def test_a_tap_with_no_formula_at_the_base_is_the_bootstrap_and_pushes(tmp_path):
    url, seed, base = _make_tap(tmp_path, None)
    assert _prepare(tmp_path, url, base) == "push"
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert _remote_formula(seed, "main") == _formula_at("9.9.0")


def test_a_hook_left_in_the_clone_does_not_run(tmp_path):
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "work" / "tap" / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    os.chmod(hook, 0o755)
    hb.publish_push(workdir=tmp_path / "work", push_url=url, target_branch="main")
    assert not marker.exists()


def test_the_same_hook_runs_for_a_push_without_the_helper(tmp_path):
    """The control for the test above: in the same clone, a plain push DOES run the hook, so its
    absence there is the helper's doing."""
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    marker = tmp_path / "hook-ran"
    hook = tmp_path / "work" / "tap" / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
    os.chmod(hook, 0o755)
    _git_run(tmp_path / "work" / "tap", "push", url, "HEAD:refs/heads/main")
    assert marker.exists()


def test_a_failed_push_does_not_print_the_token(tmp_path):
    url, _seed, base = _make_tap(tmp_path, _formula_at("9.8.0"))
    assert _prepare(tmp_path, url, base) == "push"
    with pytest.raises(Refusal) as err:
        hb.publish_push(workdir=tmp_path / "work", push_url=url + "-SECRET-TOKEN-VALUE",
                        target_branch="main", redact="SECRET-TOKEN-VALUE")
    assert "SECRET-TOKEN-VALUE" not in str(err.value)
    assert "***" in str(err.value), "the token never reached git's message, so its redaction is unproven"


def test_push_publish_from_the_cli_pushes_with_the_token_and_redacts_it(tmp_path, monkeypatch):
    """The token reaches the push URL and the redaction only through this subcommand, and a real push
    to the tap is the first place either would otherwise be exercised."""
    recorded = {}
    monkeypatch.setattr(hb, "publish_push", lambda **kwargs: recorded.update(kwargs))
    env = {"TAP_TOKEN": "tap-token-value", "TAP_OWNER": "ExampleOwner", "TARGET_BRANCH": "bump-9.9.0",
           "PUSH_WORKDIR": str(tmp_path / "work")}
    assert hb.main(["push-publish"], env=env) == 0
    assert recorded == {
        "workdir": tmp_path / "work",
        "push_url": "https://x-access-token:tap-token-value@github.com/exampleowner/homebrew-tap.git",
        "target_branch": "bump-9.9.0",
        "redact": "tap-token-value",
    }


def test_push_prepare_from_the_cli_maps_each_variable_to_its_argument(tmp_path, monkeypatch, capsys):
    recorded = {}

    def record(**kwargs):
        recorded.update(kwargs)
        return "noop"

    monkeypatch.setattr(hb, "prepare_push", record)
    formula = tmp_path / "job-sluice.rb"
    formula.write_bytes(b"formula bytes\n")
    env = {"TAP_OWNER": "ExampleOwner", "PUSH_WORKDIR": str(tmp_path / "work"),
           "TARGET_BRANCH": "bump-9.9.0", "DEFAULT_BRANCH": "main", "BASE_SHA": "f" * 40,
           "VERSION": "9.9.0", "MERGED_FORMULA": str(formula)}
    assert hb.main(["push-prepare"], env=env) == 0
    assert recorded == {
        "remote_url": "https://github.com/exampleowner/homebrew-tap.git",
        "workdir": tmp_path / "work",
        "target_branch": "bump-9.9.0",
        "default_branch": "main",
        "base_sha": "f" * 40,
        "version": "9.9.0",
        "formula": b"formula bytes\n",
    }
    assert capsys.readouterr().out == "push-prepare decided: noop\n"


# --- one derivation per fact, over the script's own source --------------------------------------


def _functions():
    return {node.name: node for node in ast.walk(ast.parse(SCRIPT.read_text()))
            if isinstance(node, ast.FunctionDef)}


def _body(function):
    """A function's statements without its docstring, which may mention what it must not do."""
    body = function.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return [node for statement in body for node in ast.walk(statement)]


def _callers(name):
    return {fname for fname, function in _functions().items()
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
                   for n in _body(function))}


def _holding(text):
    return {fname for fname, function in _functions().items()
            if any(isinstance(n, ast.Constant) and isinstance(n.value, str) and text in n.value
                   for n in _body(function))}


def test_render_has_exactly_two_callers():
    """formula renders to WRITE the formula; push's validator re-renders to COMPARE (spec, §2)."""
    assert _callers("_render") == {"cmd_render", "validate_formula"}


def test_the_tag_root_url_run_attempt_and_default_branch_are_derived_only_for_plan():
    assert _callers("compose_tag") == {"build_plan"}
    assert _callers("compose_root_url") == {"build_plan"}
    assert _callers("build_plan") == {"cmd_plan"}
    assert _holding("RUN_ATTEMPT") == {"cmd_plan"}
    assert _holding("--symref") == {"cmd_plan"}
    assert _holding("releases/download") == {"compose_root_url"}
    assert _holding("--exit-code") == {"prepare_push"}
    assert _holding("symbolic-ref") == set() and _holding("set-head") == set()


def test_release_writes_happen_only_inside_upload_bottles():
    calls = [(fname, n) for fname, function in _functions().items() for n in _body(function)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "github_request"]
    assert calls, "found no github_request call; the sweep below proves nothing"
    assert all(len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) for _, n in calls), (
        "every github_request call must pass its method as a literal, or this sweep cannot see it")
    writers = {fname for fname, n in calls if n.args[1].value != "GET"}
    assert writers == {"_create_draft_release", "_upload_asset", "_publish_release"}
    for writer in writers:
        assert _callers(writer) == {"upload_bottles"}
    assert _callers("upload_bottles") == {"cmd_upload_bottles"}


def test_the_network_is_reached_only_through_github_request_and_plans_pypi_read():
    """`http(...)` is the one network seam, and `http_request` behind it the one `urlopen`. A bare call
    anywhere else could write to a release without passing the sweep above; `cmd_plan`'s is a GET to
    PyPI, with no body and no method."""
    calls = {}
    for fname, function in _functions().items():
        for node in _body(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "http":
                calls.setdefault(fname, []).append(node)
    assert set(calls) == {"github_request", "cmd_plan"}, sorted(calls)
    (call,) = calls["cmd_plan"]
    (request,) = call.args
    assert isinstance(request, ast.Call) and len(request.args) == 1
    assert not any(keyword.arg in ("method", "data") for keyword in request.keywords)
    urlopens = {fname for fname, function in _functions().items() for node in _body(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "urlopen"}
    assert urlopens == {"http_request"}, sorted(urlopens)
    assert _callers("http_request") == set(), "http_request is injected as `http`, never called by name"


def test_both_formula_subcommands_read_the_same_bytes():
    assert _callers("_read_formula") == {"cmd_validate_formula", "cmd_push_prepare"}
    assert _holding("MERGED_FORMULA") == {"cmd_validate_formula", "cmd_push_prepare"}


def test_every_git_command_goes_through_the_hook_disabling_helper():
    tree = ast.parse(SCRIPT.read_text())
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert len(runs) == 1, f"expected the one subprocess call inside git(), found {len(runs)}"
    # Same parse: ast nodes compare by identity, so a node from a second parse is never `in` it.
    assert any(node is runs[0] for node in ast.walk(functions["git"])), "the subprocess call is not in git()"
    command = runs[0].args[0]
    assert [e.value for e in command.elts[:3]] == ["git", "-c", "core.hooksPath=/dev/null"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: the new tests FAIL (`AttributeError` for `upload_bottles`, `prepare_push`, `publish_push`,
`_read_formula`; the CLI tests error with `SystemExit: 2`, since argparse rejects the unknown
subcommands), except three AST pins that Tasks 5 and 7
already satisfy and that pass at once: `test_render_has_exactly_two_callers`,
`test_every_git_command_goes_through_the_hook_disabling_helper` and
`test_the_network_is_reached_only_through_github_request_and_plans_pypi_read`.

- [ ] **Step 3: Write the implementation**

In `scripts/homebrew_bottles.py`, add after `_POSITIVE_INT_RE`:

```python
_HEX40_RE = re.compile(r"[0-9a-f]{40}")
```

Insert the following ABOVE the `# --- main ---` line:

```python
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
    version: str, formula: bytes,
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
        target_state = "present"
        remote_sha = probe.stdout.decode().split()[0]
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
    )
    if decision == "push":
        _git_ok("checkout", "-B", target_branch, base_sha, cwd=clone)
        path = clone / "Formula" / f"{FORMULA_NAME}.rb"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(formula)
        _git_ok("add", f"Formula/{FORMULA_NAME}.rb", cwd=clone)
        if git("diff", "--cached", "--quiet", cwd=clone).returncode == 0:
            decision = "noop"
        else:
            _git_ok("-c", f"user.name={_BOT_NAME}", "-c", f"user.email={_BOT_EMAIL}",
                    "-c", "commit.gpgsign=false", "commit", "-m", f"{FORMULA_NAME} {version}",
                    cwd=clone)
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
    decision = prepare_push(
        remote_url=f"https://github.com/{owner}/{TAP_REPO}.git",
        workdir=Path(_require(env, "PUSH_WORKDIR")),
        target_branch=_require(env, "TARGET_BRANCH"),
        default_branch=_require(env, "DEFAULT_BRANCH"),
        base_sha=_require(env, "BASE_SHA"),
        version=validate_version(env.get("VERSION")),
        formula=_read_formula(_require(env, "MERGED_FORMULA")),
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
```

Replace `_parser` and `_COMMANDS` with:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_homebrew_bottles.py`
Expected: all pass. The git tests create repositories under pytest's `tmp_path` and touch no network.

- [ ] **Step 5: Commit, then witness each row**

```bash
git add scripts/homebrew_bottles.py tests/test_homebrew_bottles.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): upload bottles to the tap release and push the formula as data (#279)

The release is found or created as a draft, every asset is decided
before any upload, and the push commits on the base commit plan
observed, with hooks disabled.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp scripts/homebrew_bottles.py /tmp/homebrew_bottles.py.bak
```

| Mutant | Test that must fail |
| --- | --- |
| replace `"core.hooksPath=/dev/null"` with `"core.editor=true"` in `git` (replaces) | `test_a_hook_left_in_the_clone_does_not_run` and `test_every_git_command_goes_through_the_hook_disabling_helper` |
| delete `lease = ...` and change the scratch push to `_git_ok("push", "--force", push_url, refspec, ...)` | `test_a_scratch_branch_that_moves_after_prepare_is_not_clobbered` |
| delete the `decisions = [...]` statement, change the loop to `for asset in assets:`, and move the `asset_decision(existing, name=asset.remote_name, sha256=asset.sha256)` call into the loop as its first line, `decision = ...` | `test_a_different_asset_digest_refuses_before_any_upload` |
| in `cmd_validate_formula`, replace `_read_formula(_require(env, "MERGED_FORMULA")).decode("utf-8")` with `Path(_require(env, "MERGED_FORMULA")).read_text()` (replaces) | `test_a_carriage_return_in_the_merged_formula_is_refused` and `test_both_formula_subcommands_read_the_same_bytes` |
| in `_formula_at_commit`, replace `_git_ok("ls-tree", "--name-only", commit, "--", path, cwd=clone)` with `git("ls-tree", "--name-only", commit, "--", path, cwd=clone).stdout` (replaces) | `test_a_failed_tree_read_refuses_rather_than_reading_as_the_bootstrap` |
| in `prepare_push`, replace `base_formula = _formula_at_commit(clone, base_sha)` with `base_formula = _git_ok("show", f"{base_sha}:Formula/{FORMULA_NAME}.rb", cwd=clone) if git("cat-file", "-e", f"{base_sha}:Formula/{FORMULA_NAME}.rb", cwd=clone).returncode == 0 else None` (replaces) | `test_a_failed_tree_read_refuses_rather_than_reading_as_the_bootstrap` |
| in `_formula_at_commit`, delete `if listed == "":` and the `return None` under it | `test_a_tap_with_no_formula_at_the_base_is_the_bootstrap_and_pushes` |
| in `cmd_push_publish`, delete `redact=token,` | `test_push_publish_from_the_cli_pushes_with_the_token_and_redacts_it` |
| in `cmd_push_prepare`, swap the names `"TARGET_BRANCH"` and `"DEFAULT_BRANCH"` in their two `_require` calls (replaces) | `test_push_prepare_from_the_cli_maps_each_variable_to_its_argument` |
| replace `cmd_render`'s body with `pass` (replaces) | `test_render_has_exactly_two_callers` |

Restore after each, rerun the file (all pass), `git status --short` clean.

- [ ] **Step 6: Witness every accept row**

With `/tmp/always_refuse.py` from Task 2, run from the worktree root:

```bash
.venv/bin/python /tmp/always_refuse.py upload_bottles test_an_absent_release_is_created_as_a_draft_filled_then_published test_a_partial_draft_is_completed_by_the_rerun test_a_complete_published_release_writes_nothing
.venv/bin/python /tmp/always_refuse.py cmd_validate_bottles test_upload_bottles_from_the_cli_validates_then_publishes
.venv/bin/python /tmp/always_refuse.py cmd_upload_bottles test_upload_bottles_from_the_cli_validates_then_publishes
.venv/bin/python /tmp/always_refuse.py cmd_validate_formula test_validate_formula_reads_the_published_releases_digests
.venv/bin/python /tmp/always_refuse.py _read_formula test_validate_formula_reads_the_published_releases_digests
.venv/bin/python /tmp/always_refuse.py prepare_push test_a_new_version_is_committed_on_the_base_and_pushed test_identical_bytes_are_a_no_op_and_publish_pushes_nothing test_the_first_scratch_push_creates_the_branch test_a_stale_scratch_branch_is_replaced_under_its_lease
.venv/bin/python /tmp/always_refuse.py _formula_at_commit test_a_new_version_is_committed_on_the_base_and_pushed test_identical_bytes_are_a_no_op_and_publish_pushes_nothing test_a_tap_with_no_formula_at_the_base_is_the_bootstrap_and_pushes
.venv/bin/python /tmp/always_refuse.py publish_push test_a_new_version_is_committed_on_the_base_and_pushed test_the_first_scratch_push_creates_the_branch test_a_stale_scratch_branch_is_replaced_under_its_lease
.venv/bin/python /tmp/always_refuse.py cmd_push_prepare test_push_prepare_from_the_cli_maps_each_variable_to_its_argument
.venv/bin/python /tmp/always_refuse.py cmd_push_publish test_push_publish_from_the_cli_pushes_with_the_token_and_redacts_it
git status --short
```

Expected: every line prints `<function>: every accept row failed under the mutant`, and
`git status --short` prints nothing.

---

### Task 9: The untrusted jobs' scripts

**Files:**
- Create: `.github/scripts/homebrew_tap_checkout.sh`
- Create: `.github/scripts/homebrew_formula.sh`
- Create: `.github/scripts/homebrew_bottle.sh`
- Create: `.github/scripts/homebrew_prove.sh`
- Modify: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Consumes: the subcommands from Tasks 7 and 8.
- Produces: four scripts reading exactly these environment variables:
  - `homebrew_tap_checkout.sh`: `TAP_OWNER`, `BASE_SHA`
  - `homebrew_formula.sh`: `GITHUB_WORKSPACE`, `TAP_OWNER`, `VERSION`, `SDIST_URL`, `SDIST_SHA256`,
    `FORMULA_OUT`
  - `homebrew_bottle.sh`: `GITHUB_WORKSPACE`, `RUNNER_TEMP`, `TAP_OWNER`, `VERSION`, `ROOT_URL`,
    `DECLARED_TAG`, `FORMULA_IN`, `BOTTLE_OUT`
  - `homebrew_prove.sh`: `GITHUB_WORKSPACE`, `TAP_OWNER`, `PLATFORMS`, `FORMULA_IN`, `JSON_DIR`,
    `MERGED_OUT`

- [ ] **Step 1: Write the failing tests**

In `tests/test_release_publish_wiring.py`, add `"_script_lines", "_assert_in_order"` to the
`_MODULE_HELPER_NAMES` set. `test_every_module_level_helper_takes_path_first_with_no_default`
requires every `_`-prefixed module-level function in that file to be listed there and to take a
required `path` as its first parameter, which is why both helpers below take the script's path.

Then replace the line
`_MACOS_SHELL_SCRIPTS = ("homebrew_push.sh", "homebrew_verify.sh")` with:

```python
_MACOS_SHELL_SCRIPTS = ("homebrew_bottle.sh", "homebrew_formula.sh", "homebrew_prove.sh",
                        "homebrew_push.sh", "homebrew_tap_checkout.sh", "homebrew_verify.sh")
```

Then append at the end of the file:

```python
# --- #279: the untrusted jobs' scripts ----------------------------------------------------------

_CI_SCRIPTS = ROOT / ".github" / "scripts"


def _script_lines(path: Path) -> list[str]:
    """A script's commands, whitespace-trimmed, with blank and comment lines dropped."""
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]


def _assert_in_order(path: Path, sequence: list[tuple[str, str]]) -> None:
    """Each pattern must FULL-match a whole command of the script at `path`, strictly after the
    previous match.

    Whole lines and occurrence-aware, because a command can appear twice (`brew test` does in the
    bottle job) and `str.index` only ever finds the first.
    """
    lines = _script_lines(path)
    position = -1
    for label, pattern in sequence:
        for index in range(position + 1, len(lines)):
            if re.fullmatch(pattern, lines[index]):
                position = index
                break
        else:
            raise AssertionError(
                f"{path.name}: {label} is missing after command {position} (pattern {pattern!r}). "
                f"Commands:\n" + "\n".join(lines)
            )


def test_the_tap_checkout_script_clones_at_the_planned_commit_and_derives_nothing():
    script = _CI_SCRIPTS / "homebrew_tap_checkout.sh"
    _assert_in_order(script, [
        ("clone", r'git clone --no-checkout "https://github\.com/\$\{TAP_OWNER\}/homebrew-tap\.git" "\$TAP_DIR"'),
        ("checkout at BASE_SHA", r'git -C "\$TAP_DIR" checkout --detach "\$BASE_SHA"'),
    ])
    body = "\n".join(_script_lines(script))
    for forbidden in ("ls-remote", "symbolic-ref", "set-head", "tap-new"):
        assert forbidden not in body, f"homebrew_tap_checkout.sh must not run {forbidden}"


def test_the_formula_script_renders_fills_and_audits_in_order():
    script = _CI_SCRIPTS / "homebrew_formula.sh"
    _assert_in_order(script, [
        ("auto-update off", r"export HOMEBREW_NO_AUTO_UPDATE=1"),
        ("render", r'python3 -P "\$BOTTLES" render --out "\$TAP_FORMULA"'),
        ("resource fill with the cooldown bypass",
         r'if ! brew update-python-resources --version "\$VERSION" --ignore-main-package-cooldown "\$FORMULA_REF"; then'),
        ("audit", r'brew audit --strict --online "\$FORMULA_REF"'),
        ("hand-off", r'cp "\$TAP_FORMULA" "\$FORMULA_OUT/job-sluice\.rb"'),
    ])
    assert "pypi.org" not in "\n".join(_script_lines(script)), (
        "homebrew_formula.sh must use plan's SDIST_URL, never query PyPI a second time")


def test_the_bottle_script_builds_bottles_and_pours_in_order():
    _assert_in_order(_CI_SCRIPTS / "homebrew_bottle.sh", [
        ("auto-update off", r"export HOMEBREW_NO_AUTO_UPDATE=1"),
        ("formula in", r'cp "\$FORMULA_IN" "\$TAP_FORMULA"'),
        ("build", r'brew install --build-bottle "\$FORMULA_REF"'),
        ("first test", r'brew test "\$FORMULA_REF"'),
        ("bottle", r'brew bottle --json --no-rebuild --root-url="\$ROOT_URL" "\$FORMULA_REF"'),
        ("produced tag", r'python3 -P "\$BOTTLES" produced-tag --json "\$BOTTLE_JSON"'),
        ("single-tag merge", r'brew bottle --merge --write --no-commit "\$BOTTLE_JSON"'),
        ("negative control",
         r'python3 -P "\$BOTTLES" pour-check --info "\$RUNNER_TEMP/info-built\.json" --expect-built'),
        ("uninstall", r"brew uninstall job-sluice"),
        ("cache seed", r'cp "\$BOTTLE_TAR" "\$CACHE_PATH"'),
        ("pour", r'brew install "\$FORMULA_REF"'),
        ("pour check", r'python3 -P "\$BOTTLES" pour-check --info "\$RUNNER_TEMP/info-poured\.json"'),
        ("second test", r'brew test "\$FORMULA_REF"'),
        ("linkage", r"brew linkage --test job-sluice"),
    ])


def test_the_prove_script_merges_checks_and_fetches_every_tag_in_order():
    script = _CI_SCRIPTS / "homebrew_prove.sh"
    _assert_in_order(script, [
        ("auto-update off", r"export HOMEBREW_NO_AUTO_UPDATE=1"),
        ("formula in", r'cp "\$FORMULA_IN" "\$TAP_FORMULA"'),
        ("merge", r'\(cd "\$JSON_DIR" && brew bottle --merge --write --no-commit \./\*\.bottle\.json\)'),
        ("style", r'brew style --formula "\$FORMULA_REF"'),
        ("tag set", r'python3 -P "\$BOTTLES" merged-tags --formula "\$TAP_FORMULA"'),
        ("declared tags", r'TAGS="\$\(python3 -P "\$BOTTLES" tags\)"'),
        ("loop", r"for tag in \$TAGS; do"),
        ("cache path", r'cache_path="\$\(brew --cache --bottle-tag="\$tag" "\$FORMULA_REF"\)"'),
        ("removal", r'rm -f "\$cache_path"'),
        ("forced fetch", r'brew fetch --force --bottle-tag="\$tag" "\$FORMULA_REF"'),
        ("file check",
         r'python3 -P "\$BOTTLES" cache-check --formula "\$TAP_FORMULA" --tag "\$tag" --file "\$cache_path"'),
        ("loop end", r"done"),
        ("hand-off", r'cp "\$TAP_FORMULA" "\$MERGED_OUT/job-sluice\.rb"'),
    ])
    body = "\n".join(_script_lines(script))
    assert "brew install" not in body and ".bottle.tar.gz" not in body, (
        "prove fetches bottles from their URLs; it installs nothing and handles no bottle file")


def test_the_tap_new_ban_moves_to_the_tap_checkout_script():
    """`brew tap-new` writes workflows and a daily `brew bump --open-pr`: a second automated writer
    of a machine-owned formula, and an App token scoped `contents: write` cannot push workflows."""
    for name in _MACOS_SHELL_SCRIPTS:
        assert "tap-new" not in "\n".join(_script_lines(_CI_SCRIPTS / name)), f"{name} must never call brew tap-new"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py -k "tap_checkout or formula_script or bottle_script or prove_script or tap_new_ban or bash_4"`
Expected: FAIL with `FileNotFoundError` for the four new scripts (and the bash-3.2 sweep failing on
the roster).

- [ ] **Step 3: Write the scripts**

Create `.github/scripts/homebrew_tap_checkout.sh`:

```bash
#!/usr/bin/env bash
# Check out the tap for an untrusted job (#279): formula, bottle and prove.
#
# DERIVES NOTHING. TAP_OWNER and BASE_SHA arrive from `plan`, the one job that derives them
# (docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md, section 2). Checking out BASE_SHA,
# not a freshly fetched tip, keeps every job on the commit `plan` observed.
#
# THE TAP IS NOT AN actions/checkout. `brew` resolves a formula through the tap directory under
# $(brew --repository)/Library/Taps/, outside $GITHUB_WORKSPACE, where actions/checkout refuses to
# write. Cloning needs no credential: the tap is public.
#
# `brew tap-new` is never used. It unconditionally writes .github/dependabot.yml and three
# workflows, one running `brew bump --open-pr` daily: a second automated writer of a formula this
# channel owns. Independently, an App token scoped `contents: write` cannot push workflows at all.
set -euo pipefail

: "${TAP_OWNER:?}" "${BASE_SHA:?}"

TAP_DIR="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap"
if [ -e "$TAP_DIR" ]; then
  echo "::error::$TAP_DIR already exists; this job expects a fresh runner."
  exit 1
fi
git clone --no-checkout "https://github.com/${TAP_OWNER}/homebrew-tap.git" "$TAP_DIR"
git -C "$TAP_DIR" checkout --detach "$BASE_SHA"
mkdir -p "$TAP_DIR/Formula"
```

Create `.github/scripts/homebrew_formula.sh`:

```bash
#!/usr/bin/env bash
# The `formula` job's body (#279): render the formula, fill its resources, audit it.
#
# Runs third-party code (PyPI resolution, Homebrew's gems) and references no secret. Filled ONCE: two
# runners resolving minutes apart could land on different resource trees, and every bottle must be
# built from one formula text (spec, section 2). SDIST_URL and SDIST_SHA256 come from `plan`.
set -euo pipefail
# Auto-update off: a brew command that auto-updates would update the tap and, if its default branch
# has moved since `plan`, rebase this checkout off BASE_SHA and leave the formula copied into it
# stashed (Homebrew's cmd/update.sh::merge_or_rebase).
export HOMEBREW_NO_AUTO_UPDATE=1

: "${GITHUB_WORKSPACE:?}" "${TAP_OWNER:?}" "${VERSION:?}" "${SDIST_URL:?}" "${SDIST_SHA256:?}" "${FORMULA_OUT:?}"

BOTTLES="$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"
FORMULA_REF="${TAP_OWNER}/tap/job-sluice"
TAP_FORMULA="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap/Formula/job-sluice.rb"

python3 -P "$BOTTLES" render --out "$TAP_FORMULA"

# --ignore-main-package-cooldown is REQUIRED: this runs minutes after the PyPI upload, and the
# resolver otherwise refuses a package that new. It covers our own package only: a dependency floor
# naming a release younger than a day still fails, reported by Homebrew as an unhelpful "Unable to
# determine dependencies". The diagnostic below re-runs the resolution without the cooldown and says
# which case this is. Every lookup in it is guarded: under `set -e` an unguarded failing lookup
# would kill the diagnostic before it printed anything.
if ! brew update-python-resources --version "$VERSION" --ignore-main-package-cooldown "$FORMULA_REF"; then
  echo "::group::diagnosing the resource-resolution failure"
  diag_fail() {
    echo "::endgroup::"
    echo "::error::resource resolution failed, and the cooldown diagnostic could not run: $1. Read Homebrew's own output above; it is the only evidence available for this failure."
    exit 1
  }
  if ! EXTRAS="$(grep -oE 'job-sluice\[[a-z,]+\]' "$TAP_FORMULA" | head -1 | sed 's/.*\[//;s/\]//')" || [ -z "$EXTRAS" ]; then
    diag_fail "could not read the extras from the rendered formula"
  fi
  if ! brew_prefix="$(brew --prefix python@3.14)" || [ -z "$brew_prefix" ]; then
    diag_fail "could not locate the brewed python@3.14 prefix"
  fi
  brew_py="${brew_prefix}/libexec/bin/python"
  [ -x "$brew_py" ] || brew_py="${brew_prefix}/bin/python3.14"
  if [ ! -x "$brew_py" ]; then
    diag_fail "no executable python under ${brew_prefix}"
  fi
  if "$brew_py" -m pip install -q --disable-pip-version-check --dry-run --ignore-installed --report=/dev/null "job-sluice[${EXTRAS}] @ ${SDIST_URL}"; then
    echo "::endgroup::"
    echo "::error::resource resolution failed ONLY under Homebrew's dependency cooldown: a dependency floor in pyproject.toml names a release less than 24 hours old. No fix is needed. Once that release has aged past 24 hours, re-run the calling run's failed jobs (for a release, re-running the whole workflow never reaches these jobs). The pip error above names the package and the versions it could see."
  else
    echo "::endgroup::"
    echo "::error::resource resolution failed with AND without Homebrew's dependency cooldown, so the cooldown is not the cause. Read the pip output above."
  fi
  exit 1
fi

brew audit --strict --online "$FORMULA_REF"

mkdir -p "$FORMULA_OUT"
cp "$TAP_FORMULA" "$FORMULA_OUT/job-sluice.rb"
```

Create `.github/scripts/homebrew_bottle.sh`:

```bash
#!/usr/bin/env bash
# The `bottle` job's body (#279): build, bottle, and prove the bottle pours on this runner.
#
# Runs third-party code and references no secret. The steps and their reasons are the spec's sections 4
# and 6a; tests/test_release_publish_wiring.py pins their order, occurrence by occurrence.
set -euo pipefail
# Auto-update off: `brew install` would otherwise update the tap and, if its default branch has moved
# since `plan`, rebase this checkout off BASE_SHA and leave the formula copied into it stashed, so the
# bottle would be built from the tap's formula rather than this run's (cmd/update.sh::merge_or_rebase).
export HOMEBREW_NO_AUTO_UPDATE=1

: "${GITHUB_WORKSPACE:?}" "${RUNNER_TEMP:?}" "${TAP_OWNER:?}" "${VERSION:?}" "${ROOT_URL:?}" "${DECLARED_TAG:?}" "${FORMULA_IN:?}" "${BOTTLE_OUT:?}"

BOTTLES="$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"
FORMULA_REF="${TAP_OWNER}/tap/job-sluice"
TAP_FORMULA="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap/Formula/job-sluice.rb"

cp "$FORMULA_IN" "$TAP_FORMULA"
brew install --build-bottle "$FORMULA_REF"
brew test "$FORMULA_REF"

mkdir -p "$BOTTLE_OUT"
cd "$BOTTLE_OUT"
# --no-rebuild: otherwise rebuild is derived from the tap's origin/HEAD formula, and a dry run of an
# already-published version would build a differently named asset (spec, section 4).
brew bottle --json --no-rebuild --root-url="$ROOT_URL" "$FORMULA_REF"
set -- ./*.bottle.json
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "::error::expected exactly one bottle JSON in $BOTTLE_OUT, found: $*"
  exit 1
fi
BOTTLE_JSON="$1"
python3 -P "$BOTTLES" produced-tag --json "$BOTTLE_JSON"
brew bottle --merge --write --no-commit "$BOTTLE_JSON"

# The negative control: the built keg must read as built, proving the pour check can refuse.
brew info --json=v2 "$FORMULA_REF" > "$RUNNER_TEMP/info-built.json"
python3 -P "$BOTTLES" pour-check --info "$RUNNER_TEMP/info-built.json" --expect-built
brew uninstall job-sluice

# Seed Homebrew's download cache: the release asset does not exist yet, and Homebrew keeps a cached
# file when the URL cannot be resolved (spec, section 6a).
set -- ./*.bottle.tar.gz
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "::error::expected exactly one bottle in $BOTTLE_OUT, found: $*"
  exit 1
fi
BOTTLE_TAR="$1"
CACHE_PATH="$(brew --cache --bottle-tag="$DECLARED_TAG" "$FORMULA_REF")"
mkdir -p "$(dirname "$CACHE_PATH")"
cp "$BOTTLE_TAR" "$CACHE_PATH"
brew install "$FORMULA_REF"
brew info --json=v2 "$FORMULA_REF" > "$RUNNER_TEMP/info-poured.json"
python3 -P "$BOTTLES" pour-check --info "$RUNNER_TEMP/info-poured.json"
brew test "$FORMULA_REF"
brew linkage --test job-sluice
```

Create `.github/scripts/homebrew_prove.sh`:

```bash
#!/usr/bin/env bash
# The `prove` job's body (#279): merge every bottle into the formula, and fetch every tag from its
# real URL before the formula that points at it is pushed.
#
# Runs third-party code and references no secret: it evaluates the merged formula, which is why `push`
# validates that text again as data (spec, section 6b).
set -euo pipefail
# Auto-update off: a brew command that auto-updates would update the tap and, if its default branch
# has moved since `plan`, rebase this checkout off BASE_SHA and leave the formula copied into it
# stashed (Homebrew's cmd/update.sh::merge_or_rebase).
export HOMEBREW_NO_AUTO_UPDATE=1

: "${GITHUB_WORKSPACE:?}" "${TAP_OWNER:?}" "${PLATFORMS:?}" "${FORMULA_IN:?}" "${JSON_DIR:?}" "${MERGED_OUT:?}"

BOTTLES="$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"
FORMULA_REF="${TAP_OWNER}/tap/job-sluice"
TAP_FORMULA="$(brew --repository)/Library/Taps/${TAP_OWNER}/homebrew-tap/Formula/job-sluice.rb"

cp "$FORMULA_IN" "$TAP_FORMULA"
(cd "$JSON_DIR" && brew bottle --merge --write --no-commit ./*.bottle.json)
brew style --formula "$FORMULA_REF"
python3 -P "$BOTTLES" merged-tags --formula "$TAP_FORMULA"

# Read into a variable first: a failing command inside a `< <(...)` feeding the loop would leave the
# loop silently empty, and `set -e` stops on a failing command substitution in an assignment.
TAGS="$(python3 -P "$BOTTLES" tags)"
for tag in $TAGS; do
  cache_path="$(brew --cache --bottle-tag="$tag" "$FORMULA_REF")"
  rm -f "$cache_path"
  # --force clears any cached copy; the file check below is required because `brew fetch
  # --bottle-tag` exits 0 for a tag the formula does not carry.
  brew fetch --force --bottle-tag="$tag" "$FORMULA_REF"
  python3 -P "$BOTTLES" cache-check --formula "$TAP_FORMULA" --tag "$tag" --file "$cache_path"
done

mkdir -p "$MERGED_OUT"
cp "$TAP_FORMULA" "$MERGED_OUT/job-sluice.rb"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py`
Expected: all pass, including `test_the_homebrew_scripts_use_no_bash_4_only_constructs` over the
enlarged roster.

- [ ] **Step 5: Commit, then witness each row**

```bash
git add .github/scripts/homebrew_tap_checkout.sh .github/scripts/homebrew_formula.sh .github/scripts/homebrew_bottle.sh .github/scripts/homebrew_prove.sh tests/test_release_publish_wiring.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): add the untrusted Homebrew jobs' scripts (#279)

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp .github/scripts/homebrew_bottle.sh /tmp/homebrew_bottle.sh.bak
cp .github/scripts/homebrew_prove.sh /tmp/homebrew_prove.sh.bak
```

| Mutant (delete one line) | Test that must fail |
| --- | --- |
| the second `brew test "$FORMULA_REF"` in `homebrew_bottle.sh` | `test_the_bottle_script_builds_bottles_and_pours_in_order` |
| `rm -f "$cache_path"` in `homebrew_prove.sh` | `test_the_prove_script_merges_checks_and_fetches_every_tag_in_order` |
| `export HOMEBREW_NO_AUTO_UPDATE=1` in `homebrew_bottle.sh` | `test_the_bottle_script_builds_bottles_and_pours_in_order` |
| `cp "$FORMULA_IN" "$TAP_FORMULA"` in `homebrew_bottle.sh` | `test_the_bottle_script_builds_bottles_and_pours_in_order` |

Restore each with `cp` from `/tmp`, rerun `tests/test_release_publish_wiring.py` (all pass),
`git status --short` clean.

---

### Task 10: The reusable workflow

**Files:**
- Create: `.github/workflows/homebrew.yml`
- Modify: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Consumes: the subcommands (Tasks 7, 8), the scripts and the environment variables they read
  (Task 9), `_CI_SCRIPTS` and `_script_lines(path)` (Task 9), the `plan` outputs and artifact names
  in the Interface Contract, and these existing helpers in the test file: `_text(path)`,
  `_job_directives(path, name)`, `_job_names(path)`, `_roster_failure(path, expected, found)`.
- Produces: `.github/workflows/homebrew.yml` with inputs `version`, `ref`, `push_target` and secrets
  `RELEASE_PLEASE_CLIENT_ID`, `RELEASE_PLEASE_PRIVATE_KEY`; in the test file, `HOMEBREW`,
  `_APP_SECRETS`, `_workflow(path)`, `_triggers(path)`, `_steps(path, job)`,
  `_step_position(path, job, *, run=None, uses=None)`, `_env_reads(path, function)` and
  `_subcommand_function(path, subcommand)`.

Every `_`-prefixed module-level function in `tests/test_release_publish_wiring.py` must take a
required `path` as its first parameter and be listed in `_MODULE_HELPER_NAMES`
(`test_every_module_level_helper_takes_path_first_with_no_default`). The helpers below follow that
rule, and step predicates are written inline in each test rather than as module-level functions.

- [ ] **Step 1: Write the failing tests**

In `tests/test_release_publish_wiring.py`, add `import json` to the standard-library imports and
`from scripts import homebrew_bottles` after `import yaml`. Add
`"_workflow", "_triggers", "_steps", "_step_position", "_env_reads", "_subcommand_function"` to the
`_MODULE_HELPER_NAMES` set. Then append:

```python
# --- #279: the reusable workflow ----------------------------------------------------------------

HOMEBREW = ROOT / ".github" / "workflows" / "homebrew.yml"
_HOMEBREW_JOBS = ["plan", "formula", "bottle", "upload", "prove", "push"]
_HOMEBREW_NEEDS = {
    "plan": None,
    "formula": ["plan"],
    "bottle": ["plan", "formula"],
    "upload": ["plan", "bottle"],
    "prove": ["plan", "formula", "bottle", "upload"],
    "push": ["plan", "upload", "prove"],
}
_HOMEBREW_RUNS_ON = {"plan": "ubuntu-latest", "formula": "macos-26",
                     "bottle": "${{ matrix.runner }}", "upload": "ubuntu-latest",
                     "prove": "macos-26", "push": "ubuntu-latest"}
_TRUSTED_USES = {
    "plan": ["actions/checkout"],
    "upload": ["actions/checkout", "actions/download-artifact", "actions/create-github-app-token"],
    "push": ["actions/checkout", "actions/download-artifact", "actions/create-github-app-token"],
}
_ACTION_PINS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/create-github-app-token": "bcd2ba49218906704ab6c1aa796996da409d3eb1",
}
_BOTTLES_RUN = 'python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py"'
_TRUSTED_RUN = re.compile(re.escape(_BOTTLES_RUN) + r" [a-z-]+")
_JOB_KEYS = {"needs", "runs-on", "permissions", "outputs", "strategy", "steps"}
_RUN_STEP_KEYS = {"name", "id", "env", "run"}
_USES_STEP_KEYS = {"name", "id", "uses", "with"}
_APP_SECRETS = {"RELEASE_PLEASE_CLIENT_ID": "${{ secrets.RELEASE_PLEASE_CLIENT_ID }}",
                "RELEASE_PLEASE_PRIVATE_KEY": "${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}"}
_BOTTLES_SCRIPT = ROOT / "scripts" / "homebrew_bottles.py"
# Set by the runner for every step, so no step's `env:` names them.
_RUNNER_PROVIDED = {"GITHUB_WORKSPACE", "RUNNER_TEMP", "GITHUB_OUTPUT"}


def _workflow(path: Path) -> dict:
    """The workflow at `path`, parsed. This section pins structure (keys, lists, exact mappings),
    which a parse pins exactly and text matching would only approximate."""
    return yaml.safe_load(_text(path))


def _triggers(path: Path) -> dict:
    """The workflow's `on:` mapping. PyYAML reads a bare `on` key as the boolean True."""
    doc = _workflow(path)
    return doc["on"] if "on" in doc else doc[True]


def _steps(path: Path, job: str) -> list[dict]:
    return _workflow(path)["jobs"][job]["steps"]


def _step_position(path: Path, job: str, *, run: str | None = None, uses: str | None = None) -> int:
    """The index of the ONE step in `job` whose whole `run:` body is `run`, or whose action is `uses`.

    Exactly one: a second copy of a step must not be able to satisfy an order pin in the first
    copy's place.
    """
    assert (run is None) != (uses is None), "pass exactly one of run= or uses="
    matches = [index for index, step in enumerate(_steps(path, job))
               if (run is not None and step.get("run", "").strip() == run)
               or (uses is not None and step.get("uses", "").split("@")[0] == uses)]
    assert len(matches) == 1, f"{path.name} {job}: expected one step for {run or uses}, found {len(matches)}"
    return matches[0]


def _subcommand_function(path: Path, subcommand: str) -> str:
    """The name of the function the script at `path` maps `subcommand` to in its `_COMMANDS` table."""
    tree = ast.parse(_text(path))
    tables = [node for node in tree.body if isinstance(node, ast.Assign)
              and any(isinstance(target, ast.Name) and target.id == "_COMMANDS" for target in node.targets)]
    assert len(tables) == 1, f"{path.name}: expected one _COMMANDS table, found {len(tables)}"
    for key, value in zip(tables[0].value.keys, tables[0].value.values):
        if isinstance(key, ast.Constant) and key.value == subcommand:
            return value.id
    raise AssertionError(f"{path.name}: {subcommand!r} is not in _COMMANDS")


def _env_reads(path: Path, function: str) -> set[str]:
    """Every environment name `function` in the script at `path` reads through `_require(env, "X")`,
    `env.get("X")` or `env["X"]`, following calls into that script's own top-level functions.

    Fails closed: any other use of `env` in a followed function, and any `os.environ` outside `main`,
    is an assertion, because a read this sweep cannot see would otherwise leave it green."""
    tree = ast.parse(_text(path))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "_require" in functions, f"{path.name} has no top-level _require; this sweep would read nothing"
    environ = sorted({name for name, fn in functions.items() if name != "main"
                      for node in ast.walk(fn) if isinstance(node, ast.Attribute) and node.attr == "environ"})
    assert not environ, f"{path.name}: os.environ is read in {environ}, where this sweep cannot see it"
    names, seen, pending = set(), set(), [function]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        understood = set()
        for node in ast.walk(functions[current]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "_require" and len(node.args) == 2 and isinstance(node.args[1], ast.Constant):
                    names.add(node.args[1].value)
                    understood.add(id(node.args[0]))
                elif node.func.id in functions:
                    pending.append(node.func.id)
                    understood.update(id(argument) for argument in node.args)
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                  and node.func.value.id == "env" and node.args and isinstance(node.args[0], ast.Constant)):
                names.add(node.args[0].value)
                understood.add(id(node.func.value))
            elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                  and node.value.id == "env" and isinstance(node.slice, ast.Constant)):
                names.add(node.slice.value)
                understood.add(id(node.value))
        unexplained = [node.lineno for node in ast.walk(functions[current])
                       if isinstance(node, ast.Name) and node.id == "env" and isinstance(node.ctx, ast.Load)
                       and id(node) not in understood]
        assert not unexplained, (
            f"{path.name}::{current} uses env on lines {unexplained} in a way this sweep cannot read")
    return names


def test_the_homebrew_workflow_declares_exactly_its_jobs():
    found = _job_names(HOMEBREW)
    assert found == _HOMEBREW_JOBS, _roster_failure(HOMEBREW, _HOMEBREW_JOBS, found)


def test_the_homebrew_workflow_is_only_ever_called():
    """A `workflow_dispatch:` here would run the whole graph with no branch refusal, and with a
    push_target chosen by whoever dispatched it."""
    triggers = _triggers(HOMEBREW)
    assert list(triggers) == ["workflow_call"]
    call = triggers["workflow_call"]
    assert set(call) == {"inputs", "secrets"}
    assert call["inputs"] == {name: {"type": "string", "required": True}
                              for name in ("version", "ref", "push_target")}
    assert call["secrets"] == {name: {"required": True} for name in _APP_SECRETS}


def test_every_homebrew_job_reads_the_repository_and_nothing_more():
    doc = _workflow(HOMEBREW)
    assert doc["permissions"] == {"contents": "read"}
    for job in _HOMEBREW_JOBS:
        assert doc["jobs"][job].get("permissions") == {"contents": "read"}, job


def test_each_homebrew_job_needs_exactly_what_it_reads():
    jobs = _workflow(HOMEBREW)["jobs"]
    assert {job: jobs[job].get("needs") for job in _HOMEBREW_JOBS} == _HOMEBREW_NEEDS


def test_no_homebrew_job_or_step_can_run_after_a_failure():
    """Allow-lists of keys, not a probe for `if: always()`: any job- or step-level `if:` or
    `continue-on-error` lets a later step or job run past a failed check, whatever its spelling."""
    for job, body in _workflow(HOMEBREW)["jobs"].items():
        assert set(body) <= _JOB_KEYS, f"{job} carries {sorted(set(body) - _JOB_KEYS)}"
        for step in body["steps"]:
            assert ("run" in step) != ("uses" in step), f"{job}: {step}"
            allowed = _RUN_STEP_KEYS if "run" in step else _USES_STEP_KEYS
            assert set(step) <= allowed, f"{job}: {step.get('name')} carries {sorted(set(step) - allowed)}"


def test_the_homebrew_runners_are_pinned_and_the_matrix_comes_from_plan():
    """A runner label decides the bottle tag a job produces, so no job may float on `macos-latest`,
    and `bottle` reads both keys under the names `plan` actually emits."""
    jobs = _workflow(HOMEBREW)["jobs"]
    assert {job: jobs[job]["runs-on"] for job in _HOMEBREW_JOBS} == _HOMEBREW_RUNS_ON
    assert jobs["bottle"]["strategy"] == {
        "fail-fast": False, "matrix": {"include": "${{ fromJSON(needs.plan.outputs.platforms) }}"}}
    emitted = json.loads(homebrew_bottles.platforms_json())
    assert emitted and all(set(entry) == {"runner", "tag"} for entry in emitted), emitted
    body = jobs["bottle"]["steps"][_step_position(HOMEBREW, "bottle", run="bash .github/scripts/homebrew_bottle.sh")]
    assert body["env"].get("DECLARED_TAG") == "${{ matrix.tag }}", body["env"]


def test_the_trusted_jobs_run_only_the_bottles_script_and_every_action_is_pinned():
    jobs = _workflow(HOMEBREW)["jobs"]
    for job, expected in _TRUSTED_USES.items():
        steps = jobs[job]["steps"]
        assert [s["uses"].split("@")[0] for s in steps if "uses" in s] == expected, job
        for step in steps:
            if "run" in step:
                assert _TRUSTED_RUN.fullmatch(step["run"].strip()), f"{job}: {step['run']!r}"
    for job, body in jobs.items():
        for step in body["steps"]:
            if "uses" in step:
                action, _, sha = step["uses"].partition("@")
                assert _ACTION_PINS.get(action) == sha, f"{job}: {step['uses']}"


def test_the_untrusted_jobs_run_only_the_rostered_scripts():
    """Every command an untrusted job runs is one of the scripts the bash 3.2 sweep and the order pins
    read, so nothing runs there that those checks cannot see."""
    jobs = _workflow(HOMEBREW)["jobs"]
    for job in ("formula", "bottle", "prove"):
        runs = [step["run"].strip() for step in jobs[job]["steps"] if "run" in step]
        assert runs, job
        for run in runs:
            assert re.fullmatch(r"bash \.github/scripts/homebrew_[a-z_]+\.sh", run), f"{job}: {run!r}"


def test_no_workflow_restores_an_actions_cache():
    """The untrusted Homebrew jobs hold the run's Actions runtime token, which can save a cache entry,
    and GitHub scopes caches by branch rather than by workflow, so a later run on the same branch could
    restore it. No workflow in this repository restores a cache, so nothing reads what they could
    save; `actions/cache`, or a setup action's `cache:` input, anywhere would reopen that."""
    workflows = _workflow_files(ROOT / ".github" / "workflows")
    assert HOMEBREW in workflows, "the sweep does not reach homebrew.yml, so it proves nothing"
    for workflow in workflows:
        for job, body in (_workflow(workflow).get("jobs") or {}).items():
            for step in body.get("steps") or []:
                action = step.get("uses", "").split("@")[0]
                assert action.split("/")[:2] != ["actions", "cache"], f"{workflow.name} {job}: {step}"
                assert "cache" not in (step.get("with") or {}), f"{workflow.name} {job}: {step}"


def test_every_homebrew_step_supplies_every_variable_its_command_reads():
    """Deleting a variable from a step's `env:` otherwise stays green and fails mid-release, possibly
    after `upload` has published the tap release. The names a command requires are read from the
    script's own `: "${NAME:?}"` line or from the subcommand's own environment reads, never restated
    here."""
    checked = []
    for job, body in _workflow(HOMEBREW)["jobs"].items():
        for step in body["steps"]:
            if "run" not in step:
                continue
            run = step["run"].strip()
            script = re.fullmatch(r"bash \.github/scripts/([\w.-]+\.sh)", run)
            if script:
                source = "\n".join(_script_lines(_CI_SCRIPTS / script[1]))
                required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?\}", source))
                # The guard line is the script's contract, so check that it covers what the script
                # reads: every upper-case variable it references and never assigns, and every variable
                # the subcommands it invokes read.
                assigned = set(re.findall(r"(?<![\w$])([A-Z][A-Z0-9_]*)=", source))
                referenced = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]*)", source))
                invoked = re.findall(r'python3 -P "\$BOTTLES" ([a-z-]+)', source)
                reads = set().union(*(_env_reads(_BOTTLES_SCRIPT, _subcommand_function(_BOTTLES_SCRIPT, sub))
                                      for sub in invoked))
                unguarded = ((referenced - assigned) | reads) - required - _RUNNER_PROVIDED
                assert not unguarded, f"{script[1]} reads {sorted(unguarded)} without naming them on its guard line"
            else:
                assert _TRUSTED_RUN.fullmatch(run), f"{job}: {run!r}"
                subcommand = run[len(_BOTTLES_RUN) + 1:]
                required = _env_reads(_BOTTLES_SCRIPT, _subcommand_function(_BOTTLES_SCRIPT, subcommand))
            required -= _RUNNER_PROVIDED
            assert required, f"{job}: {run!r} resolved no required variable; the check below proves nothing"
            missing = required - set(step.get("env") or {})
            assert not missing, f"{job}: {run!r} reads {sorted(missing)}, which its step env does not set"
            checked.append(run)
    assert checked, "found no run step; the sweep above proves nothing"


def test_only_the_token_jobs_touch_secrets():
    secret = re.compile(r"secrets\.|secrets\[|toJSON\(secrets\)")
    assert {job for job in _HOMEBREW_JOBS
            if secret.search(_job_directives(HOMEBREW, job))} == {"upload", "push"}
    assert {job for job in _HOMEBREW_JOBS
            if "actions/create-github-app-token" in _job_directives(HOMEBREW, job)} == {"upload", "push"}


def test_the_token_jobs_keep_artifacts_in_runner_temp_and_read_only_plans_outputs():
    """An artifact downloaded into the workspace could replace a file the next step executes."""
    jobs = _workflow(HOMEBREW)["jobs"]
    for job in ("upload", "push"):
        downloads = [s for s in jobs[job]["steps"] if s.get("uses", "").startswith("actions/download-artifact@")]
        assert downloads, job
        for step in downloads:
            assert step["with"].get("path", "").startswith("${{ runner.temp }}/"), f"{job}: {step['with']}"
        assert set(re.findall(r"needs\.([\w-]+)\.outputs", _job_directives(HOMEBREW, job))) == {"plan"}


def test_every_output_reference_names_a_declared_output_and_every_output_a_real_step():
    jobs = _workflow(HOMEBREW)["jobs"]
    references = set(re.findall(r"needs\.([\w-]+)\.outputs\.([\w-]+)", _text(HOMEBREW)))
    assert references, "no needs.*.outputs reference found; the sweep below proves nothing"
    for job, key in references:
        assert key in jobs[job].get("outputs", {}), f"needs.{job}.outputs.{key} is not declared"
    for job, body in jobs.items():
        ids = {step.get("id") for step in body["steps"]}
        for key, value in body.get("outputs", {}).items():
            match = re.fullmatch(r"\$\{\{ steps\.([\w-]+)\.outputs\.([\w-]+) \}\}", value)
            assert match and match.group(1) in ids, f"{job}.outputs.{key} = {value!r}"


def test_no_expression_is_pasted_into_a_run_body():
    """`${{ }}` inside `run:` is substituted as text before bash parses it; values go through `env:`."""
    for job, body in _workflow(HOMEBREW)["jobs"].items():
        for step in body["steps"]:
            assert "${{" not in step.get("run", ""), f"{job}: {step.get('run')!r}"


def test_the_run_attempt_is_read_only_in_plan():
    """The tag carries the run attempt. Any other job reading it would compose a different tag on a
    re-run of failed jobs than the one `plan` published."""
    assert {job for job in _HOMEBREW_JOBS
            if "github.run_attempt" in _job_directives(HOMEBREW, job)} == {"plan"}


def test_the_mints_and_plan_take_the_owner_from_one_expression():
    jobs = _workflow(HOMEBREW)["jobs"]
    plan_step = jobs["plan"]["steps"][_step_position(HOMEBREW, "plan", run=f"{_BOTTLES_RUN} plan")]
    assert plan_step["env"].get("REPOSITORY_OWNER") == "${{ github.repository_owner }}"
    for job in ("upload", "push"):
        mint = jobs[job]["steps"][_step_position(HOMEBREW, job, uses="actions/create-github-app-token")]
        assert mint["with"] == {"client-id": "${{ secrets.RELEASE_PLEASE_CLIENT_ID }}",
                                "private-key": "${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}",
                                "owner": "${{ github.repository_owner }}",
                                "repositories": "homebrew-tap", "permission-contents": "write"}


def test_upload_validates_before_minting_and_publishes_after():
    order = [_step_position(HOMEBREW, "upload", run=f"{_BOTTLES_RUN} validate-bottles"),
             _step_position(HOMEBREW, "upload", uses="actions/create-github-app-token"),
             _step_position(HOMEBREW, "upload", run=f"{_BOTTLES_RUN} upload-bottles")]
    assert order == sorted(order), order


def test_push_validates_and_prepares_before_minting_and_pushes_after():
    order = [_step_position(HOMEBREW, "push", run=f"{_BOTTLES_RUN} validate-formula"),
             _step_position(HOMEBREW, "push", run=f"{_BOTTLES_RUN} push-prepare"),
             _step_position(HOMEBREW, "push", uses="actions/create-github-app-token"),
             _step_position(HOMEBREW, "push", run=f"{_BOTTLES_RUN} push-publish")]
    assert order == sorted(order), order
    holders = [index for index, step in enumerate(_steps(HOMEBREW, "push")) if "TAP_TOKEN" in json.dumps(step)]
    assert holders == [order[3]], f"TAP_TOKEN reaches steps {holders}; only push-publish may hold it"


def test_the_untrusted_jobs_check_out_the_tap_before_their_bodies():
    for job, script in (("formula", "homebrew_formula.sh"), ("bottle", "homebrew_bottle.sh"),
                        ("prove", "homebrew_prove.sh")):
        checkout = _step_position(HOMEBREW, job, run="bash .github/scripts/homebrew_tap_checkout.sh")
        body = _step_position(HOMEBREW, job, run=f"bash .github/scripts/{script}")
        assert checkout < body, job


def test_prove_downloads_the_formula_and_the_jsons_but_never_a_bottle():
    downloads = [s["with"] for s in _steps(HOMEBREW, "prove")
                 if s.get("uses", "").startswith("actions/download-artifact@")]
    assert [(d.get("name"), d.get("pattern")) for d in downloads] == [
        ("homebrew-formula", None), (None, "homebrew-bottle-json-*")]


def test_every_upload_fails_on_no_files_and_can_be_replaced_by_a_rerun():
    uploads = [(job, s["with"]) for job, body in _workflow(HOMEBREW)["jobs"].items()
               for s in body["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert uploads, "found no upload-artifact step; the sweep below proves nothing"
    for job, arguments in uploads:
        assert arguments.get("if-no-files-found") == "error", f"{job}: {arguments}"
        assert arguments.get("overwrite") is True, f"{job}: {arguments}"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py`
Expected: FAIL. Every new test from Step 1 fails with `FileNotFoundError` naming
`.github/workflows/homebrew.yml`; every test that was passing before this task still passes,
including `test_every_module_level_helper_takes_path_first_with_no_default`.

- [ ] **Step 3: Write the workflow**

Create `.github/workflows/homebrew.yml`:

```yaml
name: Homebrew publish

# Builds, bottles and publishes the job-sluice formula to the tap (#279). Called by
# release-please.yml's `homebrew` job (push_target: default) and by homebrew-dry-run.yml
# (push_target: auto); nothing else triggers it.
#
# THE TRUST RULE. This header is its maintained statement; the design that led to it is
# docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md, section 2. Step order inside one job
# is not a boundary: a step can write GITHUB_ENV, GITHUB_PATH, a git hook or an action bundle that a
# later step of the same job uses. So a job either runs third-party code and references no secret,
# or holds the tap token and runs none:
#   - plan, upload and push are trusted. They run on ubuntu-latest, their `run:` lines call only
#     scripts/homebrew_bottles.py through `python3 -P`, and their actions come from an exact roster.
#     Only upload and push mint the token, and they read job outputs from plan alone. The dry run's
#     `preflight` is trusted too, since its `version` output reaches them; it has no checkout, so its
#     two `run:` bodies are pinned whole instead.
#   - formula, bottle and prove run Homebrew, PyPI resolution and Homebrew's gems. They run on macOS
#     because a bottle is built for the macOS it runs on, and because the channel's payoff, Homebrew's
#     CPython finding cairo and pango with no DYLD_FALLBACK_LIBRARY_PATH, is a macOS mechanism. They
#     reference no secret, but they still hold a contents: read GITHUB_TOKEN and the run's artifact
#     token, which can create an artifact under any name. That is why a token job validates every
#     artifact it reads as data, and reads no output of theirs. The same token can save an Actions
#     cache entry that a later run on this branch could restore, which is why no workflow in this
#     repository restores a cache.
#
# No job or step carries `if:` or `continue-on-error`, and no `${{ }}` appears inside `run:`: every
# value reaches a script through `env:`. tests/test_release_publish_wiring.py pins each of these.
on:
  workflow_call:
    inputs:
      version:
        type: string
        required: true
      ref:
        type: string
        required: true
      push_target:
        type: string
        required: true
    secrets:
      RELEASE_PLEASE_CLIENT_ID:
        required: true
      RELEASE_PLEASE_PRIVATE_KEY:
        required: true

permissions:
  contents: read

jobs:
  plan:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    outputs:
      tap_owner: ${{ steps.plan.outputs.tap_owner }}
      default_branch: ${{ steps.plan.outputs.default_branch }}
      base_sha: ${{ steps.plan.outputs.base_sha }}
      target_branch: ${{ steps.plan.outputs.target_branch }}
      sdist_url: ${{ steps.plan.outputs.sdist_url }}
      sdist_sha256: ${{ steps.plan.outputs.sdist_sha256 }}
      tag: ${{ steps.plan.outputs.tag }}
      root_url: ${{ steps.plan.outputs.root_url }}
      run_url: ${{ steps.plan.outputs.run_url }}
      caller: ${{ steps.plan.outputs.caller }}
      platforms: ${{ steps.plan.outputs.platforms }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}
          persist-credentials: false
      - name: Plan the publish
        id: plan
        env:
          PUSH_TARGET: ${{ inputs.push_target }}
          VERSION: ${{ inputs.version }}
          REPOSITORY_OWNER: ${{ github.repository_owner }}
          RUN_ID: ${{ github.run_id }}
          RUN_ATTEMPT: ${{ github.run_attempt }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
          GITHUB_TOKEN: ${{ github.token }}
        run: python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" plan

  formula:
    needs: [plan]
    runs-on: macos-26
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - name: Check out the tap at the planned commit
        env:
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          BASE_SHA: ${{ needs.plan.outputs.base_sha }}
        run: bash .github/scripts/homebrew_tap_checkout.sh
      - name: Render, resource-fill and audit the formula
        env:
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          VERSION: ${{ inputs.version }}
          SDIST_URL: ${{ needs.plan.outputs.sdist_url }}
          SDIST_SHA256: ${{ needs.plan.outputs.sdist_sha256 }}
          FORMULA_OUT: ${{ runner.temp }}/homebrew-formula
        run: bash .github/scripts/homebrew_formula.sh
      # `overwrite: true` on every upload in this file: a re-run of a failed job uploads under the
      # name an earlier attempt may already have used. Token jobs validate every artifact they read,
      # so replacing one grants nothing.
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: homebrew-formula
          path: ${{ runner.temp }}/homebrew-formula/job-sluice.rb
          if-no-files-found: error
          retention-days: 7
          overwrite: true

  bottle:
    needs: [plan, formula]
    strategy:
      fail-fast: false
      matrix:
        include: ${{ fromJSON(needs.plan.outputs.platforms) }}
    runs-on: ${{ matrix.runner }}
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: homebrew-formula
          path: ${{ runner.temp }}/homebrew-formula
      - name: Check out the tap at the planned commit
        env:
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          BASE_SHA: ${{ needs.plan.outputs.base_sha }}
        run: bash .github/scripts/homebrew_tap_checkout.sh
      - name: Build, bottle and pour the formula
        env:
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          VERSION: ${{ inputs.version }}
          ROOT_URL: ${{ needs.plan.outputs.root_url }}
          DECLARED_TAG: ${{ matrix.tag }}
          FORMULA_IN: ${{ runner.temp }}/homebrew-formula/job-sluice.rb
          BOTTLE_OUT: ${{ runner.temp }}/homebrew-bottle
        run: bash .github/scripts/homebrew_bottle.sh
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: homebrew-bottle-json-${{ matrix.tag }}
          path: ${{ runner.temp }}/homebrew-bottle/*.bottle.json
          if-no-files-found: error
          retention-days: 7
          overwrite: true
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: homebrew-bottle-tar-${{ matrix.tag }}
          path: ${{ runner.temp }}/homebrew-bottle/*.bottle.tar.gz
          if-no-files-found: error
          retention-days: 7
          overwrite: true

  upload:
    needs: [plan, bottle]
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}
          persist-credentials: false
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          pattern: homebrew-bottle-*
          merge-multiple: true
          path: ${{ runner.temp }}/homebrew-bottles
      - name: Validate the bottles as data
        env:
          BOTTLES_DIR: ${{ runner.temp }}/homebrew-bottles
          VERSION: ${{ inputs.version }}
          ROOT_URL: ${{ needs.plan.outputs.root_url }}
          PLATFORMS: ${{ needs.plan.outputs.platforms }}
        run: python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" validate-bottles
      - uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        id: tap-token
        with:
          client-id: ${{ secrets.RELEASE_PLEASE_CLIENT_ID }}
          private-key: ${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: homebrew-tap
          permission-contents: write
      - name: Publish the bottles to the tap's release
        env:
          TAP_TOKEN: ${{ steps.tap-token.outputs.token }}
          BOTTLES_DIR: ${{ runner.temp }}/homebrew-bottles
          VERSION: ${{ inputs.version }}
          ROOT_URL: ${{ needs.plan.outputs.root_url }}
          PLATFORMS: ${{ needs.plan.outputs.platforms }}
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          TAG: ${{ needs.plan.outputs.tag }}
          BASE_SHA: ${{ needs.plan.outputs.base_sha }}
          CALLER: ${{ needs.plan.outputs.caller }}
          RUN_URL: ${{ needs.plan.outputs.run_url }}
        run: python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" upload-bottles

  prove:
    needs: [plan, formula, bottle, upload]
    runs-on: macos-26
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12"
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: homebrew-formula
          path: ${{ runner.temp }}/homebrew-formula
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          pattern: homebrew-bottle-json-*
          merge-multiple: true
          path: ${{ runner.temp }}/homebrew-bottle-json
      - name: Check out the tap at the planned commit
        env:
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          BASE_SHA: ${{ needs.plan.outputs.base_sha }}
        run: bash .github/scripts/homebrew_tap_checkout.sh
      - name: Merge the bottles and fetch every one from its release
        env:
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          PLATFORMS: ${{ needs.plan.outputs.platforms }}
          FORMULA_IN: ${{ runner.temp }}/homebrew-formula/job-sluice.rb
          JSON_DIR: ${{ runner.temp }}/homebrew-bottle-json
          MERGED_OUT: ${{ runner.temp }}/homebrew-merged
        run: bash .github/scripts/homebrew_prove.sh
      - uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: homebrew-merged-formula
          path: ${{ runner.temp }}/homebrew-merged/job-sluice.rb
          if-no-files-found: error
          retention-days: 7
          overwrite: true

  push:
    needs: [plan, upload, prove]
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}
          persist-credentials: false
      - uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          name: homebrew-merged-formula
          path: ${{ runner.temp }}/homebrew-merged
      - name: Validate the merged formula as data
        env:
          MERGED_FORMULA: ${{ runner.temp }}/homebrew-merged/job-sluice.rb
          SDIST_URL: ${{ needs.plan.outputs.sdist_url }}
          SDIST_SHA256: ${{ needs.plan.outputs.sdist_sha256 }}
          ROOT_URL: ${{ needs.plan.outputs.root_url }}
          PLATFORMS: ${{ needs.plan.outputs.platforms }}
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          TAG: ${{ needs.plan.outputs.tag }}
          VERSION: ${{ inputs.version }}
          GITHUB_TOKEN: ${{ github.token }}
        run: python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" validate-formula
      - name: Prepare the tap commit
        env:
          MERGED_FORMULA: ${{ runner.temp }}/homebrew-merged/job-sluice.rb
          PUSH_WORKDIR: ${{ runner.temp }}/homebrew-push
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          TARGET_BRANCH: ${{ needs.plan.outputs.target_branch }}
          DEFAULT_BRANCH: ${{ needs.plan.outputs.default_branch }}
          BASE_SHA: ${{ needs.plan.outputs.base_sha }}
          VERSION: ${{ inputs.version }}
        run: python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" push-prepare
      - uses: actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1 # v3.2.0
        id: tap-token
        with:
          client-id: ${{ secrets.RELEASE_PLEASE_CLIENT_ID }}
          private-key: ${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}
          owner: ${{ github.repository_owner }}
          repositories: homebrew-tap
          permission-contents: write
      - name: Push the tap commit
        env:
          TAP_TOKEN: ${{ steps.tap-token.outputs.token }}
          PUSH_WORKDIR: ${{ runner.temp }}/homebrew-push
          TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}
          TARGET_BRANCH: ${{ needs.plan.outputs.target_branch }}
        run: python3 -P "$GITHUB_WORKSPACE/scripts/homebrew_bottles.py" push-publish
```

- [ ] **Step 4: Run the tests, and audit the workflow**

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py`
Expected: all pass.

Run:
```bash
uv pip install --python .venv/bin/python --require-hashes -r .github/zizmor-requirements.txt
.venv/bin/zizmor --offline --strict-collection .github/workflows/
```
Expected: no findings for `homebrew.yml`. If zizmor reports one, fix the workflow rather than
suppressing it, rerun both commands, and rerun the tests.

- [ ] **Step 5: Commit, then witness each row**

```bash
git add .github/workflows/homebrew.yml tests/test_release_publish_wiring.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): add the reusable Homebrew publish workflow (#279)

Trusted plan, upload and push jobs; untrusted formula, bottle and prove
jobs, which reference no secret. Nothing calls it yet.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp .github/workflows/homebrew.yml /tmp/homebrew.yml.bak
cp .github/scripts/homebrew_prove.sh /tmp/homebrew_prove.sh.bak
cp .github/scripts/homebrew_bottle.sh /tmp/homebrew_bottle.sh.bak
```

For each row: apply the mutant, run
`.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py -k <test name>`, confirm that
test FAILS on its own assertion (not on a `KeyError` or YAML error), then restore the file the row
changed with `cp` from its `/tmp` backup. Rows marked (adds) or (replaces) are the exceptions Global
Constraints names.

| Mutant | Test that must fail |
| --- | --- |
| in `push`, move the whole `create-github-app-token` step above the `Prepare the tap commit` step | `test_push_validates_and_prepares_before_minting_and_pushes_after` |
| in `push`, move the `TAP_TOKEN: ...` line from `Push the tap commit`'s `env:` to `Prepare the tap commit`'s `env:` | `test_push_validates_and_prepares_before_minting_and_pushes_after` |
| in `upload`, move the whole `create-github-app-token` step above `Validate the bottles as data` | `test_upload_validates_before_minting_and_publishes_after` |
| in `upload`, move the whole `create-github-app-token` step to the end of `formula`'s steps | `test_only_the_token_jobs_touch_secrets` |
| delete `if-no-files-found: error` from the `homebrew-bottle-tar-` upload | `test_every_upload_fails_on_no_files_and_can_be_replaced_by_a_rerun` |
| delete `overwrite: true` from the `homebrew-merged-formula` upload | `test_every_upload_fails_on_no_files_and_can_be_replaced_by_a_rerun` |
| move `RUN_ATTEMPT: ${{ github.run_attempt }}` from `plan`'s step `env:` to `Publish the bottles to the tap's release`'s `env:` | `test_the_run_attempt_is_read_only_in_plan` |
| delete `path: ${{ runner.temp }}/homebrew-merged` from `push`'s download step | `test_the_token_jobs_keep_artifacts_in_runner_temp_and_read_only_plans_outputs` |
| delete `prove` from `push`'s `needs:` list, leaving `[plan, upload]` | `test_each_homebrew_job_needs_exactly_what_it_reads` |
| delete the `permissions:` and `contents: read` lines from the `bottle` job | `test_every_homebrew_job_reads_the_repository_and_nothing_more` |
| in `prove`, move the `Check out the tap at the planned commit` step below `Merge the bottles and fetch every one from its release` | `test_the_untrusted_jobs_check_out_the_tap_before_their_bodies` |
| delete `DECLARED_TAG: ${{ matrix.tag }}` from the `bottle` job | `test_the_homebrew_runners_are_pinned_and_the_matrix_comes_from_plan` |
| delete `TAP_OWNER: ${{ needs.plan.outputs.tap_owner }}` from `Push the tap commit`'s `env:` | `test_every_homebrew_step_supplies_every_variable_its_command_reads` |
| delete the `required: true` line under the `ref:` input | `test_the_homebrew_workflow_is_only_ever_called` |
| delete the `platforms: ${{ steps.plan.outputs.platforms }}` line from `plan`'s `outputs:` | `test_every_output_reference_names_a_declared_output_and_every_output_a_real_step` |
| delete `owner: ${{ github.repository_owner }}` from `push`'s `create-github-app-token` step | `test_the_mints_and_plan_take_the_owner_from_one_expression` |
| delete `json-` from `prove`'s second download pattern, leaving `homebrew-bottle-*` | `test_prove_downloads_the_formula_and_the_jsons_but_never_a_bottle` |
| change the last hex digit of the `actions/checkout` SHA in `plan` (replaces) | `test_the_trusted_jobs_run_only_the_bottles_script_and_every_action_is_pinned` |
| in `homebrew_prove.sh`, delete `"${PLATFORMS:?}"` from the guard line | `test_every_homebrew_step_supplies_every_variable_its_command_reads` |
| in `homebrew_bottle.sh`, delete `"${ROOT_URL:?}"` from the guard line | `test_every_homebrew_step_supplies_every_variable_its_command_reads` |
| add `cache: pip` to the `with:` of `formula`'s `actions/setup-python` step (adds) | `test_no_workflow_restores_an_actions_cache` |
| add `if: always()` to the `Push the tap commit` step (adds) | `test_no_homebrew_job_or_step_can_run_after_a_failure` |
| move `RUN_URL`'s value out of `plan`'s step `env:` into its `run:` line, as `RUN_URL="${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}" python3 -P ...` (adds) | `test_no_expression_is_pasted_into_a_run_body` |
| insert ` -x` after `bash` in `formula`'s `Render, resource-fill and audit the formula` step (adds) | `test_the_untrusted_jobs_run_only_the_rostered_scripts` |

After the last row, rerun the whole file (all pass) and `git status --short` (clean).

---

### Task 11: Point both callers at the workflow and retire the old scripts

**Files:**
- Modify: `.github/workflows/release-please.yml` (the `homebrew` job only)
- Rewrite: `.github/workflows/homebrew-dry-run.yml`
- Delete: `.github/scripts/homebrew_verify.sh`, `.github/scripts/homebrew_push.sh`
- Modify: `tests/test_release_publish_wiring.py`

**Interfaces:**
- Consumes: `.github/workflows/homebrew.yml`, `HOMEBREW`, `_APP_SECRETS`, `_workflow(path)`
  (Task 10); `_CI_SCRIPTS`, `_script_lines(path)` (Task 9); the existing `_text(path)`,
  `_workflow_files(path)`, `RELEASE_PLEASE`, `HOMEBREW_DRY_RUN`, `_BASH4_ONLY`.
- Produces: the finished channel.

Existing tests this task keeps unchanged, because the caller keeps what they pin:
`test_the_homebrew_job_has_no_elevated_permissions` (the caller's `permissions:` sits directly before
`with:`, which ends `_permissions_block`'s slice), `test_the_homebrew_job_is_gated_on_release_created`,
`test_the_homebrew_job_waits_for_the_pypi_upload`,
`test_the_homebrew_dry_run_triggers_only_on_workflow_dispatch` and
`test_the_homebrew_dry_run_workflow_wide_permissions_are_read_only`.

- [ ] **Step 1: Retire the old scripts' tests**

Back the file up with `cp tests/test_release_publish_wiring.py /tmp/test_release_publish_wiring.py.bak`,
then write `/tmp/retire_homebrew_tests.py`:

```python
"""Delete the Homebrew pins the spec's successor table retires (section 9b), by name, together
with any comment block directly above each. Every name must be found exactly once."""
import ast
import pathlib
import re

path = pathlib.Path("tests/test_release_publish_wiring.py")
source = path.read_text()
retire = {
    "test_the_homebrew_job_runs_on_macos",
    "test_the_homebrew_release_job_verifies_before_minting_a_token_before_pushing",
    "test_the_homebrew_dry_run_verifies_before_minting_a_token_before_pushing",
    "test_the_homebrew_verify_script_updates_audits_installs_and_tests_in_order",
    "test_the_homebrew_verify_script_reseats_the_tap_checkout_before_rendering",
    "test_the_homebrew_push_script_actually_pushes",
    "test_the_homebrew_release_verify_step_carries_no_token_and_targets_the_default_branch",
    "test_the_homebrew_release_push_step_carries_the_token",
    "_PUSH_STEP_ALLOWED_KEYS",
    "test_the_homebrew_release_push_step_carries_no_if_key",
    "test_the_homebrew_dry_run_verify_step_carries_no_token_and_targets_auto",
    "test_the_homebrew_dry_run_push_step_carries_the_token",
    "test_the_homebrew_dry_run_push_step_carries_no_if_key",
    "_VERIFY_STEP_OWNERS",
    "test_both_homebrew_verify_steps_pass_the_repository_owner",
    "test_the_homebrew_verify_script_fails_loudly_on_an_invalid_push_target",
    "test_the_homebrew_verify_script_bypasses_the_release_cooldown",
    "test_the_homebrew_scripts_never_use_tap_new",
    "test_the_homebrew_dry_run_refuses_a_non_default_branch",
    "test_the_homebrew_dry_run_has_no_elevated_permissions",
    "test_the_homebrew_dry_run_drives_the_same_verify_and_push_scripts_as_the_release_job",
    "_step_own_directive_keys",
}
lines = source.splitlines(keepends=True)
spans, found = [], []
for node in ast.parse(source).body:
    names = {getattr(node, "name", None)} | {
        target.id for target in getattr(node, "targets", []) if isinstance(target, ast.Name)}
    hit = names & retire
    if not hit:
        continue
    found.extend(hit)
    decorators = getattr(node, "decorator_list", None)
    start = (decorators[0].lineno if decorators else node.lineno) - 1
    while start > 0 and lines[start - 1].lstrip().startswith("#"):
        start -= 1
    spans.append((start, node.end_lineno))
assert sorted(found) == sorted(retire), f"not found once each: {sorted(set(retire) ^ set(found))}"
for start, end in sorted(spans, reverse=True):
    del lines[start:end]
text = "".join(lines)
# Two surviving docstrings cite retired tests, and _MODULE_HELPER_NAMES names the helper whose last
# callers were retired. Each anchor must occur exactly once.
rewrites = [
    (
        '''    """homebrew-dry-run.yml is the file holding the cross-repo write token (see
    `test_the_homebrew_dry_run_has_no_elevated_permissions`'s docstring), and until this pin
    existed nothing enumerated its job roster at all -- the same blind spot `_ROSTER_MESSAGE`
    describes for the other two files, unclosed here."""''',
        '''    """homebrew-dry-run.yml hands the tap App's secrets, by name, to the reusable workflow whose
    `upload` and `push` jobs mint the write token (`test_only_the_token_jobs_touch_secrets`), and
    until this pin existed nothing enumerated its job roster at all -- the same blind spot
    `_ROSTER_MESSAGE` describes for the other two files, unclosed here."""''',
    ),
    (
        '''    guard runs after the publish it was meant to prevent. Index order over the comment-stripped
    job block is the same idiom
    `test_the_homebrew_release_job_verifies_before_minting_a_token_before_pushing` uses for the
    workflow this design is modelled on -- the pre-split `test_the_homebrew_bump_verifies_
    before_it_pushes` this cross-reference used to name indexed a single SCRIPT body instead,
    and was deleted when #104 IMPORTANT-3 split that script in two.
    """''',
        '''    guard runs after the publish it was meant to prevent. Index order over the comment-stripped
    job block is this file's idiom for a step that must precede another.
    """''',
    ),
    ('"_step_containing", "_step_own_directive_keys",', '"_step_containing",'),
]
for old, new in rewrites:
    assert text.count(old) == 1, f"anchor found {text.count(old)} times: {old[:60]!r}"
    text = text.replace(old, new)
survivors = sorted(name for name in retire if name in text)
assert not survivors, f"still named after retirement: {survivors}"
path.write_text(re.sub(r"\n{4,}", "\n\n\n", text))
print(f"retired {len(spans)} of {len(retire)} definitions")
```

Run: `.venv/bin/python /tmp/retire_homebrew_tests.py`
Expected: `retired 22 of 22 definitions`. If the survivors assertion fires, a docstring or comment
outside the retired definitions cites a retired name: rewrite that sentence in the script's `rewrites`
list so it states the property rather than the retired test, and rerun from a fresh
`cp /tmp/test_release_publish_wiring.py.bak tests/test_release_publish_wiring.py`.

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py`
Expected: all pass (the old scripts and the old workflow shape still exist, and nothing left in the
file reads them).

- [ ] **Step 2: Write the failing successor tests**

In `tests/test_release_publish_wiring.py`, replace `_HOMEBREW_DRY_RUN_JOBS = ["dry-run"]` with:

```python
_HOMEBREW_DRY_RUN_JOBS = ["preflight", "homebrew"]
```

Replace the six-name `_MACOS_SHELL_SCRIPTS` tuple from Task 9 with:

```python
_MACOS_SHELL_SCRIPTS = ("homebrew_bottle.sh", "homebrew_formula.sh", "homebrew_prove.sh",
                        "homebrew_tap_checkout.sh")
```

In the comment block above `_BASH4_ONLY`, replace these two lines:

```python
# never shipped a newer one, so a `macos-latest` runner executes these scripts under 3.2. Both
# workflows invoke them as `bash <path>`, which BYPASSES the `#!/usr/bin/env bash` shebang and
```

with:

```python
# never shipped a newer one, so a macOS runner executes these scripts under 3.2. Every job in
# homebrew.yml invokes them as `bash <path>`, which BYPASSES the `#!/usr/bin/env bash` shebang and
```

Replace the whole `test_the_homebrew_scripts_use_no_bash_4_only_constructs` function with:

```python
def test_the_homebrew_scripts_use_no_bash_4_only_constructs():
    """Every script a macOS runner executes, and every `run:` body in homebrew.yml, must parse AND
    EXPAND under bash 3.2.

    The roster is checked from BOTH ends: every script a homebrew.yml job invokes must be in it,
    and every `homebrew_*.sh` on disk must be invoked. A glob alone cannot see a script under
    another name; a hand-list alone cannot see a script added beside it.
    """
    invoked, bodies = set(), []
    for body in _workflow(HOMEBREW)["jobs"].values():
        for step in body["steps"]:
            run = step.get("run", "")
            invoked.update(re.findall(r"bash \.github/scripts/([\w.-]+\.sh)", run))
            bodies.append(("homebrew.yml", run))
    on_disk = {p.name for p in _CI_SCRIPTS.glob("homebrew_*.sh")}
    assert invoked == set(_MACOS_SHELL_SCRIPTS) == on_disk, (
        f"invoked {sorted(invoked)}, rostered {sorted(_MACOS_SHELL_SCRIPTS)}, on disk {sorted(on_disk)}")
    bodies += [(name, "\n".join(_script_lines(_CI_SCRIPTS / name))) for name in _MACOS_SHELL_SCRIPTS]
    for where, body in bodies:
        for pattern, construct, remedy in _BASH4_ONLY:
            hit = re.search(pattern, body)
            assert hit is None, (
                f"{where} uses {construct}, which macOS bash 3.2 cannot expand: found "
                f"{hit.group(0)!r}. Use {remedy} instead. Neither `bash -n` nor shellcheck "
                f"catches this -- both were measured against it -- so this sweep is the guard."
            )
```

Append at the end of the file:

```python
# --- #279: the callers ----------------------------------------------------------------------------

# The dry run's preflight is a trusted job: its `version` output reaches the token jobs. Both of its
# run bodies are pinned whole, so nothing can be added to either.
_PREFLIGHT_REFUSAL = (
    'echo "::error::Dispatch this workflow from the default branch -- it pushes to a public tap, '
    'so an unmerged branch must not become the tree of record."\n'
    "exit 1\n"
)
_PREFLIGHT_LOOKUP = (
    "python3 -P - <<'PY' >> \"$GITHUB_OUTPUT\"\n"
    "import json, urllib.request\n"
    'with urllib.request.urlopen("https://pypi.org/pypi/job-sluice/json", timeout=60) as r:\n'
    '    print("version=" + json.load(r)["info"]["version"])\n'
    "PY\n"
)


def test_the_release_job_calls_the_homebrew_workflow():
    job = _workflow(RELEASE_PLEASE)["jobs"]["homebrew"]
    assert set(job) == {"needs", "if", "uses", "permissions", "with", "secrets"}, sorted(job)
    assert job["uses"] == "./.github/workflows/homebrew.yml"
    assert job["with"] == {"version": "${{ needs.release-please.outputs.version }}",
                           "ref": "${{ needs.release-please.outputs.sha }}",
                           "push_target": "default"}
    assert job["secrets"] == _APP_SECRETS, "pass the two secrets by name, never `secrets: inherit`"


def test_the_dry_run_calls_the_same_workflow_after_its_preflight():
    """No `if:` on the call: `if: always()` would run it after a failed refusal."""
    call = _workflow(HOMEBREW_DRY_RUN)["jobs"]["homebrew"]
    assert set(call) == {"needs", "uses", "permissions", "with", "secrets"}, sorted(call)
    assert call["needs"] == ["preflight"]
    assert call["uses"] == "./.github/workflows/homebrew.yml"
    assert call["permissions"] == {"contents": "read"}
    assert call["with"] == {"version": "${{ needs.preflight.outputs.version }}",
                            "ref": "${{ github.sha }}", "push_target": "auto"}
    assert call["secrets"] == _APP_SECRETS, "pass the two secrets by name, never `secrets: inherit`"


def test_the_dry_run_preflight_refuses_a_non_default_branch_before_its_lookup():
    """A STEP-level `if:`. A job skipped by its own `if:` reports Success, and the dry run would go
    green having done nothing."""
    preflight = _workflow(HOMEBREW_DRY_RUN)["jobs"]["preflight"]
    assert set(preflight) == {"runs-on", "permissions", "outputs", "steps"}, sorted(preflight)
    assert preflight["runs-on"] == "ubuntu-latest"
    assert preflight["permissions"] == {}
    assert preflight["outputs"] == {"version": "${{ steps.version.outputs.version }}"}
    assert preflight["steps"] == [
        {"name": "Refuse to bump the tap from a non-default branch",
         "if": "github.ref_name != github.event.repository.default_branch",
         "run": _PREFLIGHT_REFUSAL},
        {"name": "Resolve the current released version", "id": "version", "run": _PREFLIGHT_LOOKUP},
    ]


def test_the_retired_homebrew_scripts_are_gone_and_nothing_names_them():
    for name in ("homebrew_verify.sh", "homebrew_push.sh"):
        assert not (_CI_SCRIPTS / name).exists(), f"{name} is retired"
        for workflow in _workflow_files(ROOT / ".github" / "workflows"):
            assert name not in _text(workflow), f"{workflow.name} still names {name}"


def test_only_the_formula_script_fills_resources():
    """Filled once, in `formula`: two runners resolving minutes apart could fill different trees."""
    holders = [p.name for p in sorted(_CI_SCRIPTS.glob("*.sh"))
               if "update-python-resources" in "\n".join(_script_lines(p))]
    assert holders == ["homebrew_formula.sh"], holders
    assert "update-python-resources" not in _text(HOMEBREW)


# `|| true`, `|| :`, `|| exit 0` and `|| echo ...` turn a failed command into success; `set +e`,
# `set +o errexit|pipefail` and `shopt -u` switch off what makes a failure fatal; and an EXIT `trap`
# that runs `exit 0` replaces the status errexit would return. No script uses `trap`, so it is banned.
_SWALLOWED = re.compile(
    r"\|\|\s*(true\b|:(\s|;|$)|exit\s+0\b|echo\b)|\bset\s+\+[a-z]*e|\bset\s+\+o\s+(errexit|pipefail)"
    r"|\bshopt\s+-u|\btrap\b"
)


def test_every_homebrew_script_stops_at_its_first_failure():
    """homebrew.yml runs each script as `bash <path>`, a child that does not inherit the step shell's
    `-e`, so every check in these scripts is fatal only through the script's own first command. A
    `! cmd` fails a `bash -e` script only as its last command."""
    for name in _MACOS_SHELL_SCRIPTS:
        lines = _script_lines(_CI_SCRIPTS / name)
        assert lines[:1] == ["set -euo pipefail"], f"{name} starts with {lines[:1]}"
    sources = [(name, _script_lines(_CI_SCRIPTS / name)) for name in _MACOS_SHELL_SCRIPTS]
    for body in _workflow(HOMEBREW)["jobs"].values():
        for step in body["steps"]:
            sources.append(("homebrew.yml", [ln.strip() for ln in step.get("run", "").splitlines()]))
    for where, lines in sources:
        for line in lines:
            assert not line.startswith("! "), f"{where}: {line}"
            assert not _SWALLOWED.search(line), f"{where}: {line}"


def test_no_homebrew_file_names_the_owner_except_the_renderers_homepage():
    """The owner reaches every job as `github.repository_owner`, derived once in `plan`. Read here
    from pyproject.toml at test time, never typed into this test."""
    source = re.search(r'^Source = "https://github\.com/([^/"]+)/', _text(ROOT / "pyproject.toml"),
                       re.MULTILINE)
    assert source, "pyproject.toml's [project.urls] Source is not a github.com URL"
    owner = source.group(1)
    files = [HOMEBREW, ROOT / "scripts" / "homebrew_bottles.py",
             ROOT / "scripts" / "render_homebrew_formula.py",
             *(_CI_SCRIPTS / name for name in _MACOS_SHELL_SCRIPTS)]
    hits = [(path.name, line.strip()) for path in files for line in _text(path).splitlines()
            if owner.lower() in line.lower() and not line.lstrip().startswith("#")]
    # The one allowed line is the upstream project's homepage. The equality fails if a second
    # occurrence appears, and if this one moves.
    assert hits == [("render_homebrew_formula.py", f'_HOMEPAGE = "https://github.com/{owner}/sluice"')], hits
```

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py`
Expected: FAIL in `test_homebrew_dry_run_declares_exactly_the_jobs_this_file_pins`,
`test_the_release_job_calls_the_homebrew_workflow`,
`test_the_dry_run_calls_the_same_workflow_after_its_preflight`,
`test_the_dry_run_preflight_refuses_a_non_default_branch_before_its_lookup`,
`test_the_retired_homebrew_scripts_are_gone_and_nothing_names_them`,
`test_only_the_formula_script_fills_resources` and
`test_the_homebrew_scripts_use_no_bash_4_only_constructs`. The two guards
`test_every_homebrew_script_stops_at_its_first_failure` and
`test_no_homebrew_file_names_the_owner_except_the_renderers_homepage` already pass, since they sweep
only files Tasks 9 and 10 wrote; Step 5 witnesses them.

- [ ] **Step 3: Rewrite the callers and delete the old scripts**

In `.github/workflows/release-please.yml`, replace the whole `homebrew:` job, from the line
`  homebrew:` through the blank line before `  post-release:`, with:

```yaml
  homebrew:
    # `needs: pypi` is an ORDERING CONSTRAINT: the formula's `url` is the PyPI sdist, which cannot
    # resolve before that upload lands.
    #
    # The channel itself is .github/workflows/homebrew.yml, a reusable workflow the dry run calls
    # too, so the two cannot drift apart. Its header states the trust rule that shapes the job
    # graph; docs/superpowers/specs/2026-09-14-homebrew-bottles-design.md records the design.
    #
    # RECOVERY: re-run this run's FAILED JOBS. Re-running the whole workflow never reaches here:
    # release-please sees the release already cut, and `release_created` comes back false.
    #
    # Dispatching `Homebrew dry run` is NOT a recovery: once the tap holds a formula, it pushes to
    # a bump-VERSION scratch branch that nothing merges.
    needs: [release-please, pypi]
    if: success() && needs.release-please.outputs.release_created == 'true'
    uses: ./.github/workflows/homebrew.yml
    # Directly before `with:`, which bounds this block for its pin. A called workflow's jobs can
    # only narrow what this grants.
    permissions:
      contents: read
    with:
      version: ${{ needs.release-please.outputs.version }}
      ref: ${{ needs.release-please.outputs.sha }}
      push_target: default
    secrets:
      RELEASE_PLEASE_CLIENT_ID: ${{ secrets.RELEASE_PLEASE_CLIENT_ID }}
      RELEASE_PLEASE_PRIVATE_KEY: ${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}

```

Two other comments in `release-please.yml` mention the `homebrew` job, and both stay true unchanged:
`release-assets`' "`homebrew` below ALSO ends up holding `contents: write`" (it still does, inside
the called workflow) and `post-release`'s "renders, audits, installs and tests the formula before
the tap is pushed".

Replace the whole of `.github/workflows/homebrew-dry-run.yml` with:

```yaml
name: Homebrew dry run

# Proves the whole Homebrew channel against the currently released version, before a real release
# depends on it, by calling the same reusable workflow the release calls
# (.github/workflows/homebrew.yml) with push_target: auto. That pushes the tap's default branch only
# while Formula/job-sluice.rb does not exist yet (the bootstrap), and a bump-VERSION scratch branch
# after that, so a dry run never becomes the tree of record for a release it did not cut. Its
# bottles go to a tap release of their own, under a tag carrying this run's id.
#
# `workflow_dispatch` only fires for a file already on the default branch, which is why this runs
# AFTER the PR merges rather than as part of its gate.
on:
  workflow_dispatch:

permissions:
  contents: read

jobs:
  # A TRUSTED job: its `version` output reaches the token jobs. It runs the branch refusal and a
  # standard-library PyPI lookup, and nothing else.
  preflight:
    runs-on: ubuntu-latest
    permissions: {}
    outputs:
      version: ${{ steps.version.outputs.version }}
    steps:
      # A STEP-level `if:`, never a job-level one: a job skipped by its own `if:` reports "Success",
      # and everything that needs it is skipped too, so the dry run would go green having done
      # nothing.
      - name: Refuse to bump the tap from a non-default branch
        if: github.ref_name != github.event.repository.default_branch
        run: |
          echo "::error::Dispatch this workflow from the default branch -- it pushes to a public tap, so an unmerged branch must not become the tree of record."
          exit 1
      - name: Resolve the current released version
        id: version
        run: |
          python3 -P - <<'PY' >> "$GITHUB_OUTPUT"
          import json, urllib.request
          with urllib.request.urlopen("https://pypi.org/pypi/job-sluice/json", timeout=60) as r:
              print("version=" + json.load(r)["info"]["version"])
          PY

  homebrew:
    # `needs: [preflight]` is what stops a failed refusal: a job whose need failed is skipped. No
    # status-function `if:` here, since `if: always()` would run the call after a failed refusal.
    needs: [preflight]
    uses: ./.github/workflows/homebrew.yml
    permissions:
      contents: read
    with:
      version: ${{ needs.preflight.outputs.version }}
      ref: ${{ github.sha }}
      push_target: auto
    secrets:
      RELEASE_PLEASE_CLIENT_ID: ${{ secrets.RELEASE_PLEASE_CLIENT_ID }}
      RELEASE_PLEASE_PRIVATE_KEY: ${{ secrets.RELEASE_PLEASE_PRIVATE_KEY }}
```

Delete the retired scripts:

```bash
git rm .github/scripts/homebrew_verify.sh .github/scripts/homebrew_push.sh
```

- [ ] **Step 4: Run the tests and audit the workflows**

Run: `.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py tests/test_homebrew_bottles.py`
Expected: all pass.

Run: `.venv/bin/zizmor --offline --strict-collection .github/workflows/`
Expected: no findings.

- [ ] **Step 5: Commit, then witness each row**

```bash
git add .github/workflows/release-please.yml .github/workflows/homebrew-dry-run.yml tests/test_release_publish_wiring.py
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
feat(packaging): publish Homebrew bottles for arm64 macOS 15 and 26 (#279)

The release and the dry run now call the reusable Homebrew workflow.
Apple Silicon Macs on macOS 15 or later pour a prebuilt bottle; other
Macs still build from source. No job both runs third-party build code
and holds the tap's write token. Retires homebrew_verify.sh and
homebrew_push.sh, whose shared job gave a build step reach to the token.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
cp .github/workflows/homebrew-dry-run.yml /tmp/homebrew-dry-run.yml.bak
cp .github/workflows/release-please.yml /tmp/release-please.yml.bak
cp .github/scripts/homebrew_prove.sh /tmp/homebrew_prove.sh.bak
cp .github/scripts/homebrew_formula.sh /tmp/homebrew_formula.sh.bak
cp .github/scripts/homebrew_bottle.sh /tmp/homebrew_bottle.sh.bak
```

For each row: apply the mutant, run
`.venv/bin/python -m pytest -q tests/test_release_publish_wiring.py -k <test name>`, confirm that test
FAILS on its own assertion, then restore the file with `cp` from its `/tmp` backup.

| Mutant | Test that must fail |
| --- | --- |
| in `homebrew-dry-run.yml`, move the refusal step's `if:` line up into the `preflight` job, above `runs-on:` | `test_the_dry_run_preflight_refuses_a_non_default_branch_before_its_lookup` |
| in `homebrew-dry-run.yml`, delete `needs: [preflight]` | `test_the_dry_run_calls_the_same_workflow_after_its_preflight` |
| in `release-please.yml`'s `homebrew` job, delete the `secrets:` line and the two lines under it | `test_the_release_job_calls_the_homebrew_workflow` |
| in `homebrew_prove.sh`, append ` || :` to the line `mkdir -p "$MERGED_OUT"` (adds) | `test_every_homebrew_script_stops_at_its_first_failure` |
| in `homebrew_bottle.sh`, delete the line `set -euo pipefail` | `test_every_homebrew_script_stops_at_its_first_failure` |
| in `homebrew_formula.sh`, append a space and the owner segment of pyproject.toml's `Source` URL inside the quotes of the last `echo "::error::` line (adds) | `test_no_homebrew_file_names_the_owner_except_the_renderers_homepage` |

Rows marked (adds) are the absence-property exception Global Constraints names. Their target lines
match no order pin, so the named guard is the only test that can fail. The retirement and
resource-fill tests were witnessed red in Step 2, before the deletion.

After the last row, rerun both test files (all pass) and `git status --short` (clean).

---

### Task 12: Prose the bottles make false

**Files:**
- Modify: `scripts/render_homebrew_formula.py` (comments only)
- Modify: `tests/test_homebrew_formula.py` (comment only)
- Modify: `docs/INSTALL.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: no code change. `render()`'s output must be byte-identical before and after, because
  Task 5's accept fixture depends on it.

- [ ] **Step 1: Record the renderer's output before editing**

```bash
.venv/bin/python -P -c 'import sys; sys.path.insert(0, "."); from scripts.render_homebrew_formula import render; print(render(sdist_url="https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz", sha256="c" * 64), end="")' > /tmp/render-before.rb
```

- [ ] **Step 2: Edit the comments**

Make each replacement with the `Edit` tool. If an old block does not match byte for byte, STOP and
report it; do not improvise a different anchor.

In `scripts/render_homebrew_formula.py`, replace:

```python
# WHY NOT JUST VENDOR PYDANTIC. Measured, and rejected on the user's behalf: this tap publishes
# NO BOTTLES, so every `brew install` builds from source. `pydantic` stays excluded because
# homebrew-core's formula IS bottled -- dropping it means every user compiles `pydantic-core`
# from Rust and first downloads a 432MB `rust` toolchain. `typing-extensions` is pure Python
# and costs them nothing. Keep this list to packages with no build step.
```

with:

```python
# WHY NOT JUST VENDOR PYDANTIC. Measured, and rejected on the user's behalf. A user whose Mac
# matches no bottle this tap publishes (Intel, or Apple Silicon on macOS 14 and older) builds the
# vendored tree from source, and every release's bottle jobs build it too. `pydantic` stays
# excluded because homebrew-core's formula IS bottled -- vendoring it means compiling
# `pydantic-core` from Rust after downloading a 432MB `rust` toolchain. The exclusion also carries
# the prefix-path seam `cellar :any` rests on: the venv reaches homebrew-core's copy through `opt`
# paths, never a versioned Cellar. `typing-extensions` is pure Python and costs nothing. Keep this
# list to packages with no build step.
```

and replace:

```python
# build for it. `brew install --build-from-source` followed by `brew test` (both in
# homebrew_verify.sh) is what actually exercises that chain, which is why the release process
# never skips straight from audit to push.
```

with:

```python
# build for it. `brew install --build-bottle`, `brew test`, a pour and a second `brew test` (all
# in .github/scripts/homebrew_bottle.sh) are what actually exercise that chain, which is why the
# release process never skips straight from audit to push.
```

In `tests/test_homebrew_formula.py`, replace:

```python
# `import mcp` failed the 2.9.4 release. Keep this to packages with NO build step -- this tap
# publishes no bottles, so anything vendored here is compiled on every user's machine.
```

with:

```python
# `import mcp` failed the 2.9.4 release. Keep this to packages with NO build step -- every user
# whose Mac matches no bottle compiles anything vendored here, and so does every bottle job.
```

In `docs/INSTALL.md`, in the `## Homebrew (macOS)` section, after the paragraph that ends
`install and not here.`, insert a blank line and:

```markdown
`brew install` pours a prebuilt bottle when the tap's formula carries one for your Mac, and builds the
formula from source when it does not. Releases publish bottles for Apple Silicon Macs on macOS 15 or
later; an Intel Mac, or an Apple Silicon Mac on macOS 14 or earlier, always builds from source.
```

- [ ] **Step 3: Verify the output is unchanged, the suite is green, and no stale claim is left**

```bash
.venv/bin/python -P -c 'import sys; sys.path.insert(0, "."); from scripts.render_homebrew_formula import render; print(render(sdist_url="https://example.invalid/packages/ab/cd/job_sluice-9.9.0.tar.gz", sha256="c" * 64), end="")' > /tmp/render-after.rb
cmp /tmp/render-before.rb /tmp/render-after.rb && echo "render unchanged"
.venv/bin/python -m pytest -q tests/test_homebrew_formula.py tests/test_homebrew_bottles.py tests/test_release_publish_wiring.py tests/test_citation_drift.py
grep -rn -i --exclude-dir=__pycache__ 'homebrew_verify\|homebrew_push\|build-from-source\|runs-on: macos-latest\|no bottles' scripts tests .github docs/INSTALL.md README.md
```

Expected: `render unchanged`; the tests pass; the `grep` prints the
`for name in ("homebrew_verify.sh", "homebrew_push.sh"):` loop in
`test_the_retired_homebrew_scripts_are_gone_and_nothing_names_them`, which names the retired scripts
to prove they are gone. Read any other line it prints: fix it if it describes the retired channel,
and leave it if it states something still true, saying which in the commit body.

- [ ] **Step 4: Commit**

```bash
git add scripts/render_homebrew_formula.py tests/test_homebrew_formula.py docs/INSTALL.md
.venv/bin/python -m pytest -q
git commit -F - <<'EOF'
docs(packaging): say which Macs pour a Homebrew bottle (#279)

Also restates why the renderer vendors only packages with no build
step, now that the tap publishes bottles.

MrReasonable <4990954+MrReasonable@users.noreply.github.com>
Claude-Session: https://claude.ai/code/session_017tWDbFbL5fUG7ofXqJxomL
EOF
```

---

### Task 13: Verify, review, open the PR, and prove it after merge

**Files:** none new.

- [ ] **Step 1: The full quality bar**

```bash
.venv/bin/python -m pytest -q
env PATH="/usr/bin:/bin" .venv/bin/python -m pytest -q
uv pip install --python .venv/bin/python ruff==0.15.21
.venv/bin/ruff check sluice tests scripts
.venv/bin/zizmor --offline --strict-collection .github/workflows/
git status --short
```

Expected: both suite runs pass (the second catches a test that depends on this machine's `PATH`);
ruff and zizmor report nothing; the tree is clean.

- [ ] **Step 2: Review before pushing**

Run `/review-pr` against the branch `feat/homebrew-bottles-279` (no PR exists yet). Fold each accepted
finding as `git commit --fixup=<sha of the commit that introduced the code>`, rerun Step 1, then
`git rebase --autosquash origin/main` (git 2.44 and later honour `--autosquash` without `-i`).

- [ ] **Step 3: Push and open the PR**

Push `feat/homebrew-bottles-279` and open the PR against `main`. The body summarises the spec's
decisions, says `Refs #279` (the issue closes after Step 4 proves the channel), and ends with the
session link. Send a push notification that the PR is open. Then follow the merge gate
(`procedural_merge_gate` in project memory): CodeRabbit is the sole approver, every review read is
paginated, and each finding is verified before it is applied.

- [ ] **Step 4: After merge, dispatch the dry run BEFORE the next release PR merges**

The release workflow runs this graph for real on the next release. Dispatch `Homebrew dry run` from
`main` first, and wait for it to finish:

```bash
DISPATCHED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh workflow run homebrew-dry-run.yml --ref main
gh run list --workflow=homebrew-dry-run.yml --limit 5 --json databaseId,createdAt,status \
  --jq ".[] | select(.createdAt >= \"$DISPATCHED\")"
```

Repeat the last command until it prints exactly one run: a dispatched run can take several seconds to
appear, and a `--limit 1` read straight after dispatching can return the previous run. Once that run
has finished:

```bash
gh run view <run id> --json jobs --jq '.jobs[] | [.name, .conclusion, .databaseId, .startedAt] | @tsv'
```

Read every job's conclusion, not only the run's; record each job's `databaseId` and `startedAt`, and
the `TAG` value from the environment of
the `push` job's `Validate the merged formula as data` step. Then take the spec's section 8 list "The
first dry run proves these", and for each item record whether it was proven, with the job log line
that shows it. Write the record to project memory (`domain_homebrew_bottling.md`) and as a comment on
#279.

- [ ] **Step 5: Exercise a partial re-run deliberately**

Re-run that dry run's `upload` job. GitHub re-runs the jobs that depend on it, `prove` and `push`,
with it:

```bash
gh run view <run id> --json jobs --jq '.jobs[] | select(.name | endswith("upload")) | .databaseId'
RERUN="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
gh run rerun <run id> --job <that job id>
gh run view <run id> --attempt 2 --json jobs --jq '.jobs[] | [.name, .conclusion, .databaseId, .startedAt] | @tsv'
```

Expected, each read from attempt 2:
- attempt 2 ran `upload`, `prove` and `push`, and not `plan`, `formula` or `bottle`: a job ran again
  when its `databaseId` differs from Step 4's record or its `startedAt` is later than `$RERUN`, not
  merely because it appears in the list;
- `upload` prints `release <tag>: found` and `<asset>: skip` for both bottles, and no `published`
  line: GitHub returned the release's title, notes and target, and each asset's digest, exactly as
  they were sent;
- `push` prints `push-prepare decided: noop`, and its steps' `TAG` is the value recorded in Step 4,
  ending `-1`, although the run is now on attempt 2.

The `TAG` is what separates reuse from re-derivation: `plan` composes the tag from the run attempt,
so a re-derived plan would end `-2`. This proves that a job-level re-run inside a called workflow
reuses `plan`'s outputs. "Re-run failed jobs", the operation the recovery comments name, is a
different GitHub operation: record it in the section 8 note as still read, not measured. If anything
in Step 4 or 5 fails, stop before the next release PR merges and treat the failure as a finding
against this plan. Close #279 once both steps pass.

- [ ] **Step 6: After the first release that publishes bottles, correct the memory index**

The project memory index (`MEMORY.md`) says the tap publishes no bottles. Once a release has pushed a
formula with a bottle block to the tap's default branch, propose a replacement line to the owner with
`AskUserQuestion`, since the memory rules require confirming a change to an existing entry: the bottled
tags, `arm64_sequoia` and `arm64_tahoe`, and that other Macs build from source. Write it with the
`/memory` skill only after the owner agrees.
