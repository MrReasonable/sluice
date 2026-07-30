"""`sluice init` -- the setup wizard (#8).

A COMMAND package, not a sixth pipeline sub-app: nothing downstream imports it, and it sits beside
the five sub-apps rather than inside ingest -> triage -> cv -> apply -> track.

Split pure-from-impure on purpose. `questions` and `plan` are pure functions over a dict, so the
property that matters -- a run that answers nothing produces a config that expresses nothing -- is
a unit test rather than a wizard transcript. `ask` holds every prompt, every terminal read and the
one subprocess call.
"""
