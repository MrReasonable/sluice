"""What to tell a user whose install lacks the `mcp` extra. Its own module, importing nothing,
so a command that must say it before loading anything can -- `sluice.mcpserver` imports the
store and the onboard package at module scope -- and `mcp serve` says the identical words."""

NOT_INSTALLED = "the 'mcp' package is not installed -- run `pip install job-sluice[mcp]`"
