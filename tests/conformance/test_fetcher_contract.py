"""The Fetcher contract's CONCURRENCY obligation, asserted against every in-process impl.

#309 gave `core/protocols.py`'s Fetcher a thread-safety obligation with two named parts:
`create_tab` must hand every caller a DISTINCT tab id with its bookkeeping synchronized,
and a tab id must be an INDEPENDENT handle -- no "current tab" on the instance, no shared
cursor that `evaluate`/`scroll` resolve against.

That obligation was stated in a docstring and checked by nothing, which is the shape this
repo keeps getting bitten by: prose describing a mechanism, with no row that falsifies it.
It matters more here than most, because the seam is what makes `dossier_concurrency > 1`
safe and the failure is silent -- two threads handed one id read each OTHER's page, so a
lead is judged on another posting's job description and the status written from it looks
entirely ordinary.

ONLY ONE of the two obligations is pinned here, and the omission is measured rather than
an oversight. A test for the DISTINCT-ID half was written and then deleted: against an
unsynchronised `create_tab` (the lock removed from `tests/harness/browser.py`) it passed
12 runs out of 12, even with twelve threads released from one barrier. CPython does not
preempt inside a counter bump that short, so the race is real but not reproducible from
Python -- `tests/harness/browser.py`'s own comment reaches the same conclusion from the
other direction and keeps its lock as correctness-by-construction. A row that cannot fail
against the defect it names asserts nothing, so there is none. The INDEPENDENT-HANDLE half
below is a different matter: it is a structural property, it fails deterministically
against a shared cursor (measured 6/6), and it is pinned.

SCOPE, stated plainly. This covers implementations that run IN PROCESS, which today means
`tests.harness.browser.ScriptedBrowserClient` alone. The shipped `camofox` Fetcher is a
thin client over an HTTP server this repo does not bundle: its own object holds immutable
config and builds a request per call (`core/camofox.py`), but the two obligations above
are ultimately discharged by that server, and nothing here can reach it. So this file
pins the contract for anything testable and the seam's docstring carries the requirement
for anything that is not -- it is not a claim that Camofox has been verified.
"""
import pytest

from tests.harness.browser import ScriptedBrowserClient

_PAGES = {f"https://example.invalid/{i}": f"<p>page {i}</p>" for i in range(24)}


def _client():
    return ScriptedBrowserClient(dict(_PAGES))


@pytest.mark.parametrize("make", [_client], ids=["ScriptedBrowserClient"])
def test_a_tab_id_is_an_independent_handle(make):
    """Nothing done for one tab may disturb another.

    Opened INTERLEAVED and read in a different order from the one they were created in,
    which is what catches a "current tab" held on the instance: with one, the second
    open would move the cursor and the first read would return the second page.
    """
    c = make()
    a_url, b_url = list(_PAGES)[0], list(_PAGES)[1]
    a = c.create_tab(a_url)
    b = c.create_tab(b_url)

    # `location.href`, not the JD probe: a fake may legitimately return one synthetic body
    # for every page, so the body cannot discriminate between tabs. Where the tab LANDED
    # is the per-tab fact, and it is also the one the drift signal reads.
    #
    # Read b FIRST, then a. A shared cursor makes one of these return the other's url.
    b_where = c.evaluate(b, "location.href")
    a_where = c.evaluate(a, "location.href")
    assert a_where["result"] == a_url and b_where["result"] == b_url, (
        f"tabs resolved to the wrong pages: a={a_where}, b={b_where} -- the handle is not "
        "independent")

    # And closing one must leave the other usable.
    c.close_tab(b)
    assert c.evaluate(a, "location.href")["result"] == a_url, (
        "closing one tab disturbed another")
