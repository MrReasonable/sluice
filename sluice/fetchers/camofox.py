"""The Camofox headless-browser server, registered as the `camofox` fetcher.

Constructed lazily by the caller, not at import: importing this module must not open a
connection, or the offline commands that merely populate the registry would start
touching a browser.
"""
from sluice.fetchers import register


def _make(config):
    # Imported inside the factory for the same reason cli.py imports it inside command
    # functions: registering the plugin must stay free of browser I/O.
    from sluice.core.camofox import Camofox
    return Camofox()


register("camofox", _make)
