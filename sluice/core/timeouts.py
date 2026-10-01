"""The shipped backend timeout, in a module that imports nothing.

It lives apart from `core/backends.py` because two config modules need it as a default, and
`core/config.py` is imported at `cli.py`'s module scope: reading it from `core/backends.py`
loaded that module, and `subprocess` with it, on every invocation, against the rule that keeps
the CLI's heavy imports inside the commands that use them.
"""

# Seconds any one backend invocation may take. ONE spelling, deliberately: this value had
# grown three independent copies (the seam, a factory-local constant, and cv's config
# default), and a factory-local one was measurably INERT -- rebinding it changed nothing,
# because the seam coalesced None before the factory ever saw it, so a maintainer raising
# it for slow composes would have got a silent no-op. Every provider class default, the
# seam, `CvConfig.compose_timeout` and the root `Config.backend_timeout` now read this name.
DEFAULT_TIMEOUT = 300
