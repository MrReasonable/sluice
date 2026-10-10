"""`job-sluice mcp install`: register the job-sluice MCP server in the AI clients installed on
this machine, at user scope, and prove it by reading each client's config file back.

Stdlib only, and nothing from the `mcp` package. This file imports nothing, so the parser can
import `clients` for `--client`'s choices without loading the rest. `flow.py` is the entry
point; the spec is docs/superpowers/specs/2026-10-10-mcp-install-design.md."""
