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

That is the whole surface without `--write`.

## `--write` is a trust decision, made once

```bash
claude mcp add job-sluice -- job-sluice mcp serve --write
```

This additionally registers `dismiss_lead`, `apply_record`, `cv_run`, `cv_signoff`,
`create_lead` and `propose_evidence`. Each is a thin layer over one facade method rather than a
raw store write, so every invariant in [`GUARANTEES.md`](GUARANTEES.md) still holds — an agent
cannot reach past them.

`propose_evidence` is a write tool and reads like an exception to the section below, so be precise
about what it does: it only **queues** an entry for review. The entry is not citable, and
`list_evidence`'s default view cannot see it, until a human verifies it.

The decision is made **per registration, not per call**. A read-only server's `tools/list`
genuinely omits the write tools' names and schemas; it does not advertise them and refuse at call
time. An agent connected to a read-only server cannot see that a write surface exists, which is
the property that makes the flag meaningful rather than advisory.

## What nothing at any level can do

**Mark evidence verified.** The `verified:` key is what makes an evidence entry citable by the CV
fabrication gate, and it has exactly one writer, reachable only from a human at a prompt. No MCP
tool PROMOTES evidence at any `--write` level — `propose_evidence` puts an entry in the queue and
stops there — and the CLI's `verify` carries no `--all` and no `--yes`, because a bulk flag is the
same hole one level up.

That is deliberate and load-bearing. Verifying evidence is one of the three things
[`AI-SETUP.md`](AI-SETUP.md) reserves to you, alongside logging into job boards and pressing send:
each is a decision no tool should make under your name.
