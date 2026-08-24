"""Evidence corpus capture: the CLI commands and `init` wizard steps for the
Experience Library, Skills Inventory and STAR Stories.

A COMMAND package, like sluice/onboard/ -- nothing in the pipeline (ingest, triage, cv,
apply, track) imports it, and it sits beside the pipeline rather than inside it. The
wizard steps (`wizard.py`) take an INJECTED asker and import nothing from onboard.
`commands.py`'s own `verify` handler DOES import `sluice.onboard.ask` directly (lazily,
for its interactive review prompt, the same `NoInputAsker`/`TtyAsker` classes `cli.py`
imports for `cmd_init`) -- a deliberate cross-import between these two COMMAND packages,
not a boundary violation, since neither sits on the pipeline this package is beside.
"""
