"""The suite must never resolve DNS. See tests/conftest.py for the guard itself."""
import socket

import pytest

from tests.conftest import DnsUsedInTests


def test_the_suite_cannot_resolve_dns():
    """The guard fixture below is load-bearing, so assert it is actually installed.

    Without this, the fixture could be silently broken (a typo'd name, a scope
    change) and the whole suite would go back to being able to resolve, which is
    how a forgotten `resolve_host=` wiring stayed green through a review round.

    It lives in this file rather than in conftest.py because pytest does not
    collect conftest.py -- an assertion there would itself be inert.
    """
    with pytest.raises(DnsUsedInTests):
        socket.getaddrinfo("anything.invalid", None)
