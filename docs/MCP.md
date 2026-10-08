# The MCP server

`job-sluice mcp serve` runs sluice as a [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio, so an agent can call sluice directly instead of shelling out and parsing CLI
output.

```bash
pip install 'job-sluice[mcp]'
claude mcp add job-sluice -- job-sluice mcp serve
```

The `mcp` extra is required. It pulls in an async network stack — uvicorn, starlette, anyio,
pydantic — meaningfully heavier than the rest of sluice, which is why nothing outside this one
command ever imports it. A bare install never loads any of it.

## Read-only by default

| Tool | Returns |
|---|---|
| `list_leads` | the lead store, filterable by status |
| `get_lead` | one lead, with its frontmatter and body |
| `doctor` | the same preflight report the CLI prints, as structured data |
| `health` | per-source scrape baseline and retire state |
| `list_evidence` | your evidence corpora |
| `setup_status` | the current value of every setup change the career coach can propose, and which setup notes exist |

That is the whole surface without `--write`.

## `--write` is a trust decision, made once

```bash
claude mcp add job-sluice -- job-sluice mcp serve --write
```

This additionally registers `dismiss_lead`, `apply_record`, `cv_run`, `cv_signoff`,
`create_lead`, `propose_evidence`, `verify_evidence` and `setup_save`. Each is a thin layer over one facade method rather than a
raw store write, so every invariant in [`GUARANTEES.md`](GUARANTEES.md) still holds — an agent
cannot reach past them.

`propose_evidence` is a write tool and reads like an exception to the section below, so be precise
about what it does: it only **queues** an entry for review. The entry is not citable, and
`list_evidence`'s default view cannot see it, until a human verifies it.

The decision is made **per registration, not per call**. A read-only server's `tools/list`
genuinely omits the write tools' names and schemas; it does not advertise them and refuse at call
time. An agent connected to a read-only server cannot see that a write surface exists, which is
the property that makes the flag meaningful rather than advisory.

## The career coach

The server also offers a prompt, `career_interview`, at either privilege level. Claude Code
surfaces it as the slash command `/mcp__job-sluice__career_interview` (the middle part is the name you gave
`claude mcp add`), with one optional argument
saying what you want from the session. Your client cannot list prompts for you, so start it by
name. The coach interviews you, can research the role you choose, and agrees setup changes with
you in chat. Before saving it plays back everything the save would write, grouped by where it
goes and showing any value it replaces, and it calls `setup_save` (under `--write`) only after
you say yes. That yes is in chat, not in a form: unlike `verify_evidence`, nothing stops the
model from saving without it, so the rule lives in the coach's instructions and the playback is
your check (owner's ruling, 2026-10-08, after a real session in which the per-change form could
not hold a normal Role Brief section). `setup_save` takes the `version` `setup_status` returned
and writes nothing, reporting `stale`, when the config, a setup note or the vault changed in
between, say because you edited a note in Obsidian. A list setting (`setup_status` names them
under `list_settings`) takes its value as a list of strings, one item each, so an item holding
a comma stays one item; text sent for one is still split on commas. Each change it wrote that
replaced a value of yours comes back with `previous` (a list, for a list setting), which the
coach can send back to undo it, or with `not_restorable` when sending a value back would not
reproduce it (a hand-typed list item that is empty, is not text or carries surrounding spaces,
or a value its own setting would refuse), naming the config key to edit by hand. Every note or
config file a save replaces is first copied, exactly as it was, and kept for good: a note's copy
in `Job Applications/_setup_backups/` in your vault, the config's beside the config file, both
named with the date and time of the save; the result's `copies_kept` says where, and a file
whose copy cannot be written is not replaced. So `previous` restores within the chat, and the
copies restore in a later session. After saving, the
coach walks you through opening your vault in Obsidian: on a first run, the folder you named
for `vault_dir`; otherwise `job-sluice doctor` prints it, when run with the same `VAULT_DIR` the
server was registered with. `scripts/coach_eval/README.md`
describes the developer-only harness that scores the coach; it never runs in CI.

## What no tool can do for you

**Mark evidence verified.** The `verified:` key is what makes an evidence entry citable by the CV
fabrication gate, and it has exactly one writer, reached only through a human's approval. There
are two ways to give it: the CLI's `job-sluice <kind> verify`, which asks `[y/N]` per entry, or
`verify_evidence` under `--write`, which has your MCP client show a review form with each entry's
full text under its own checkbox, about a screen of entries at a time, and verifies only the
entries you tick and accept. An entry the form cannot show in full and faithfully -- too long,
too tall, or carrying a control or bidi character -- is left for the CLI. The tool takes no
argument that approves on your behalf, so an agent can open the form but cannot answer it.
`propose_evidence` puts an entry in the queue and stops there, and the CLI's `verify` carries no
`--all` and no `--yes`, because a bulk flag is a promotion with no human in it.

`verify_evidence` needs a client on the 2026-07-28 MCP protocol that supports form elicitation
(Claude Code does); any other client is told to use the CLI, and nothing is written. Boxes start
unticked, so an entry you could not see — a form that runs off a small terminal — can never be
approved by Accept.

That is deliberate and load-bearing. Verifying evidence is one of the three things
[`AI-SETUP.md`](AI-SETUP.md) reserves to you, alongside logging into job boards and pressing send:
each is a decision no tool should make under your name.
