"""CWJobs (cwjobs.co.uk), UK's biggest contract IT board. Declarative extractor JS + an
example search (override via config).

2026-08-18: extractor rebound to the current DOM and moved into `_stepstone.py`, shared
with totaljobs. CWJobs and TotalJobs are the same StepStone product (a CWJobs search
returns totaljobs.com links), and they had two separately-rotted copies of the same
extractor. See that module for what the markup looks like and why the selectors are
shaped the way they are.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register
from sluice.ingest.sources._stepstone import extractor_js

register(BrowserListSource(
    id="cwjobs",
    extractor_js=extractor_js(25),
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "contract"},
    searches_spec=[
        ('CWJobs example', 'https://www.cwjobs.co.uk/jobs/software-developer/in-london?sort=date'),
    ],
))
