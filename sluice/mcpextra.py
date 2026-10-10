"""What to tell a user whose install lacks the `mcp` extra. Its own module, importing nothing,
so it can be read without loading the store and the onboard package that `sluice.mcpserver`
imports at module scope; `mcp serve` says these exact words."""

NOT_INSTALLED = "the 'mcp' package is not installed -- run `pip install job-sluice[mcp]`"
