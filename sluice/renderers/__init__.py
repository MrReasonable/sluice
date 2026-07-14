"""Renderer plugins: composed CV text -> a PDF on disk.

A renderer is only ever reached AFTER the fabrication gate has passed. No renderer
validates, and no renderer may be called with outstanding violations -- the gate is
`cv/validate.py`'s job and putting any of it here would be a way to route around it.
"""
from sluice.core import plugins

SEAM = "renderer"


def register(name: str, factory) -> None:
    plugins.register(SEAM, name, factory)


plugins.autoload(__import__(__name__, fromlist=["_"]))
