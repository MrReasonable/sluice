# FAQ

## Why is the command `job-sluice` and not `sluice`?

The PyPI name `sluice` has been squatted since 2015 by an unrelated, dormant zfs-snapshot tool
with no console script of its own. There is no binary collision, but `pip install sluice` could
never resolve here — so the distribution and the console script are both `job-sluice`.

Three things in Python packaging are independent, and only two of them changed:

| | Name | Changed |
|---|---|---|
| Distribution | `job-sluice` | yes |
| Console script | `job-sluice` | yes |
| Import package | `sluice` | no |

The `SLUICE_*` environment variables and the `~/.config/sluice/` paths are unchanged too. Those
are invisible to a user, and renaming them would be a breaking **config** change — which this
project rates above a breaking API change — for no user-visible benefit. Only what you type at a
shell prompt is different.

Extras attach to the distribution name, so from a release it is `pip install 'job-sluice[render]'`.
Dropping the `job-` prefix resolves to that unrelated package.

## Does sluice apply to jobs for me?

No. It stages an application and a prep packet; you press send. That is one of three things
reserved to you by design — the others are logging into job boards and verifying your evidence.
Each is a decision no tool should make under your name. See
[`AI-SETUP.md`](AI-SETUP.md), which is mostly a list of things an agent is forbidden to do on your
behalf.

## Does it have opinions about which jobs are good?

None that ship. Every preference gate defaults to empty, and an empty gate passes every lead
through rather than filtering your search against a stranger's taste. What the judge looks for is
read at runtime from a note in your vault. See
[`GUARANTEES.md`](GUARANTEES.md#an-empty-setting-abstains).

## Do I have to use Obsidian?

No. The vault is a directory of plain markdown files with YAML frontmatter, which sluice reads
back on the next run. Obsidian is the nicest way to browse and hand-edit it — and `job-sluice init`
writes a Bases view so your leads render as a sortable table — but nothing requires it. Any editor
works, and hand-editing is a first-class workflow rather than something tolerated.

## Can I run it without an LLM?

Partly. `triage run --no-llm` runs the deterministic tiers only — no backend call, nothing billed —
and `ingest`, `apply` and `track` need no backend at all. The judge and the CV composer are the two
things that do. `job-sluice doctor --offline` tells you which commands your current setup can run.

## Why does it need a browser server?

Job boards are client-rendered and actively hostile to scrapers, so `ingest run` drives a
persistent, authenticated headless browser rather than fetching HTML. Sluice does not bundle one;
see [`INSTALL.md`](INSTALL.md#camofox). Without it the rest of the pipeline still works on leads
already in your vault, and `job-sluice leads add` files a job you found yourself.

## Something is wrong and I do not know what

Run `job-sluice doctor`. It reports what is missing and which commands each gap blocks, and exits
non-zero only when something you actually configured is broken — so a fresh install exits 0.
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) covers specific failures.
