"""What a `script` renderer receives, captured from today's pipeline (#364/#365/#368 §12.3).

CANONICAL_CV is a CV in today's composed-text format; GOLDEN is what
`cv/render.py::strip_citations` hands a render script for it. Task 10's `to_text` must
reproduce GOLDEN exactly for the equivalent CvDocument. Both are literals: deriving GOLDEN
from `to_text` would pin `to_text` against itself.
"""
CANONICAL_CV = """+1 555 0100

JANE ROE

PROFILE
I build reliable systems.

WORK EXPERIENCE

Example Systems
02/2023–present | Example Location A | SYNTHETIC-TITLE-1
- Shipped the platform [EF1]

Example Analytics
06/2020–01/2023 | Example Location B | SYNTHETIC-TITLE-2
- Grew team from 3 to 8 [EF1] [EF2]

CERTIFICATES
- Example Scrum Master

EDUCATION
- Example University, 09/2010–07/2014 | BSc Example

SKILLS
- Example Query
"""

GOLDEN = """+1 555 0100

JANE ROE

PROFILE
I build reliable systems.

WORK EXPERIENCE

Example Systems
02/2023–present | Example Location A | SYNTHETIC-TITLE-1
- Shipped the platform

Example Analytics
06/2020–01/2023 | Example Location B | SYNTHETIC-TITLE-2
- Grew team from 3 to 8

CERTIFICATES
- Example Scrum Master

EDUCATION
- Example University, 09/2010–07/2014 | BSc Example

SKILLS
- Example Query
"""


def test_the_golden_is_what_todays_strip_citations_hands_a_script():
    from sluice.cv.render import strip_citations
    assert strip_citations(CANONICAL_CV) == GOLDEN
