"""TotalJobs (totaljobs.com), UK board with a contract filter. Declarative extractor JS +
an example search (override via config).

2026-08-18: extractor rebound to the current DOM and moved into `_stepstone.py`, shared
with cwjobs. The two are the same StepStone product behind different brands and had
separately-rotted copies of one extractor. See that module for the markup and the
reasoning behind the selectors. `limit` stays at 20 here, as it was before the move.
"""
from sluice.ingest.base import BrowserListSource
from sluice.ingest.sources import register
from sluice.ingest.sources._stepstone import extractor_js

register(BrowserListSource(
    id="totaljobs",
    extractor_js=extractor_js(20),
    wait=4, scrolls=2, scroll_amount=600,
    extra={"job_type": "contract"},
    searches_spec=[
        ('TotalJobs example', 'https://www.totaljobs.com/jobs/software-developer/in-london?sort=date'),
    ],
))
