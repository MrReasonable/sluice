"""NaukriGulf mashes company onto title with no separator when its DOM extractor's
org-name node is absent (#151). The listing URL still carries a
"...-jobs-in-<city>-in-<company>-..." seam, which `_split_mashed_title` uses to
recover the split deterministically -- see sluice/ingest/sources/naukrigulf.py's
module docstring. In tests/test_demash.py's style: positive cases, mirror-harm/
no-op cases, an abstain table, and parse/fixture-level checks."""
import json
from pathlib import Path

from sluice.ingest import sources
from sluice.ingest.base import Search
from sluice.ingest.sources.naukrigulf import _NaukrigulfSource, _recover, _slug, _split_mashed_title

FIXTURE = Path(__file__).parent / "fixtures" / "naukrigulf" / "raw.json"


def _url(role_slug: str, tail: str = "city-in-company-1") -> str:
    """A listing URL carrying exactly one "-jobs-in-" seam after `role_slug`."""
    return f"https://example.com/{role_slug}-jobs-in-{tail}"


# --- _split_mashed_title: positive cases -----------------------------------

def test_splits_a_single_word_role():
    assert _split_mashed_title("BankerAcme", _url("banker")) == ("Banker", "Acme")


def test_splits_a_multi_word_role():
    assert (_split_mashed_title("Cluster BankerMassive Dynamic", _url(_slug("Cluster Banker")))
            == ("Cluster Banker", "Massive Dynamic"))


def test_splits_a_punctuation_heavy_role():
    role = "ETIC, AI Engineer - Manager"
    assert (_split_mashed_title(f"{role}Vandelay", _url(_slug(role)))
            == (role, "Vandelay"))


def test_splits_a_parenthesised_role():
    role = "Rail Systems Banker (High Speed Rail)"
    assert (_split_mashed_title(f"{role}Praxis Corporation", _url(_slug(role)))
            == (role, "Praxis Corporation"))


def test_splits_a_role_containing_the_literal_words_jobs_in():
    # The role's own text ("Jobs In Finance") contains "jobs in", so its slugged URL
    # segment ALSO spells "-jobs-in-" -- the path below carries the seam TWICE:
    # once as an accident of the role's own wording, once as the real naukrigulf
    # separator before the city. Using only the FIRST occurrence would candidate-list
    # just "banker" (the role's own leading segment), whose boundary fails (next char
    # is a space, not the mashed company's capital letter) -- and the function would
    # wrongly abstain. Scanning ALL occurrences (re.finditer, not re.search) finds the
    # second, correct candidate "banker-jobs-in-finance" too.
    path = "/banker-jobs-in-finance-jobs-in-city-in-acme-corp-1"
    assert (_split_mashed_title("Banker Jobs In FinanceAcme Corp", "https://example.com" + path)
            == ("Banker Jobs In Finance", "Acme Corp"))


# --- _split_mashed_title: mirror-harm / no-op cases -------------------------

def test_clean_title_with_no_mashing_abstains():
    # "Banker" alone, matched by a seam encoding "banker": there is no i in
    # range(1, len(title)) that reaches the full title (the loop's upper bound is
    # exclusive), so a clean row can never accidentally be treated as mashed.
    assert _split_mashed_title("Banker", _url("banker")) is None


def test_plural_role_against_a_singular_seam_does_not_mid_word_match():
    # "BankersAcme" against a seam proving only "banker" (singular): the boundary
    # right after "Banker" lands on the plural "s", which is neither a separator
    # nor an uppercase company-opening letter, so the match is rejected mid-word.
    assert _split_mashed_title("BankersAcme", _url("banker")) is None


def test_whitespace_before_the_seam_abstains():
    # A genuine space between role and company is the OPPOSITE of the mashing
    # signature this function targets -- never touch it.
    assert _split_mashed_title("Banker Acme", _url("banker")) is None


def test_lowercase_opening_remainder_abstains():
    # No separator AND the "company" continuation does not open a fresh
    # capitalised token -- nothing proves a genuine second word started here.
    assert _split_mashed_title("Bankeracme", _url("banker")) is None


def test_no_seam_in_url_abstains():
    assert _split_mashed_title("BankerAcme", "https://example.com/banker-1") is None


def test_empty_url_abstains():
    assert _split_mashed_title("BankerAcme", "") is None


def test_never_produces_an_empty_role_or_company():
    # Guard against a pathological seam that would otherwise let `best` land at
    # index 0 or len(title) and hand back an empty half.
    assert _split_mashed_title("", _url("banker")) is None


# --- _recover: never touches a populated company, never mutates the input --

def test_recover_never_touches_a_populated_company():
    row = {"title": "BankerAcme", "company": "Wayne", "link": _url("banker")}
    assert _recover(row) is row  # untouched, same object -- not merely equal


def test_recover_rewrites_a_recoverable_row():
    row = {"title": "BankerAcme", "company": "", "link": _url("banker")}
    out = _recover(row)
    assert out["title"] == "Banker"
    assert out["company"] == "Acme"


def test_recover_leaves_an_unrecoverable_row_unchanged():
    row = {"title": "Bankeracme", "company": "", "link": _url("banker")}
    out = _recover(row)
    assert out["title"] == "Bankeracme"
    assert out["company"] == ""


def test_recover_never_mutates_the_input_row_dict():
    row = {"title": "BankerAcme", "company": "", "link": _url("banker")}
    original = dict(row)
    _recover(row)
    assert row == original


def test_recover_falls_back_to_url_key_when_link_is_absent():
    row = {"title": "BankerAcme", "company": "", "url": _url("banker")}
    out = _recover(row)
    assert (out["title"], out["company"]) == ("Banker", "Acme")


def test_recover_warns_when_the_url_seam_proves_a_split_but_recovery_finds_none(caplog):
    # Row 26's exact production shape (task-5 review finding, #151): the mashing boundary is
    # an en dash surrounded by spaces, not a camelCase letter pair, so the ORIGINAL
    # `[a-z][A-Z]`-on-the-title heuristic could never see it as "looks mashed" -- yet the URL
    # DOES carry the "-jobs-in-" seam, proving a split exists, and recovery still comes back
    # with nothing. That combination (seam present, split absent) is the corrected condition:
    # it is what should warn, regardless of what the title visually looks like.
    row = {
        "title": "Site Banker – Example Ventures",
        "company": "",
        "link": "https://example.com/site-banker-jobs-in-dubai-in-example-ventures-26",
    }
    with caplog.at_level("WARNING", logger="sluice.ingest.naukrigulf"):
        out = _recover(row)
    assert out["title"] == "Site Banker – Example Ventures"  # left mashed, per the ruling
    assert out["company"] == ""
    said = [r.getMessage() for r in caplog.records if r.name == "sluice.ingest.naukrigulf"]
    assert said, "a URL-proven seam with no recoverable split must warn"
    joined = " ".join(said)
    assert row["link"] in joined, "the warning must name the URL that proved the seam"
    assert row["title"] in joined, "the warning must name the title recovery gave up on"


# --- _NaukrigulfSource.parse -------------------------------------------------

def _search():
    return Search(label="NaukriGulf example")


def test_parse_rewrites_a_recoverable_row():
    src = _NaukrigulfSource(id="naukrigulf", extractor_js="", searches_spec=[])
    raw = {"result": [{"title": "BankerAcme", "company": "", "location": "Dubai",
                        "link": _url("banker")}]}
    leads = src.parse(raw, _search())
    assert len(leads) == 1
    assert leads[0].title == "Banker"
    assert leads[0].company == "Acme"


def test_parse_passes_an_unrecoverable_row_through_unchanged():
    src = _NaukrigulfSource(id="naukrigulf", extractor_js="", searches_spec=[])
    raw = {"result": [{"title": "Bankeracme", "company": "", "location": "Dubai",
                        "link": _url("banker")}]}
    leads = src.parse(raw, _search())
    assert len(leads) == 1
    assert leads[0].title == "Bankeracme"
    assert leads[0].company == ""


def test_parse_tolerates_empty_result():
    src = _NaukrigulfSource(id="naukrigulf", extractor_js="", searches_spec=[])
    assert src.parse({"result": []}, _search()) == []


def test_parse_tolerates_non_dict_raw():
    src = _NaukrigulfSource(id="naukrigulf", extractor_js="", searches_spec=[])
    assert src.parse(None, _search()) == []
    assert src.parse([], _search()) == []
    assert src.parse("not a dict", _search()) == []


def test_parse_never_mutates_input_rows():
    src = _NaukrigulfSource(id="naukrigulf", extractor_js="", searches_spec=[])
    row = {"title": "BankerAcme", "company": "", "location": "Dubai",
           "link": _url("banker")}
    raw = {"result": [row]}
    original = dict(row)
    src.parse(raw, _search())
    assert row == original


# --- Fixture-level: the whole golden fixture parses as expected -------------

def test_fixture_parses_expected_number_of_leads():
    raw = json.loads(FIXTURE.read_text())
    src = sources.get("naukrigulf")
    leads = src.parse(raw, src.searches()[0])
    # 25 original rows + the row-26 deliberate abstain case appended for #151.
    assert len(leads) == 26


def test_fixture_recovers_previously_mashed_rows():
    raw = json.loads(FIXTURE.read_text())
    src = sources.get("naukrigulf")
    leads = src.parse(raw, src.searches()[0])
    by_title_prefix = {lead.url: lead for lead in leads}

    def lead_at(link: str):
        return by_title_prefix[link]

    # Row 8: "BankerConfidential Company" -- single-word role, unusual company text.
    l8 = lead_at("https://example.com/banker-jobs-in-abu-dhabi-in-confidential-company-8")
    assert (l8.title, l8.company) == ("Banker", "Confidential Company")

    # Row 11: "Cluster BankerMassive Dynamic" -- multi-word role.
    l11 = lead_at("https://example.com/cluster-banker-jobs-in-dubai-in-massive-dynamic-11")
    assert (l11.title, l11.company) == ("Cluster Banker", "Massive Dynamic")

    # Row 18: "ETIC, AI Engineer - ManagerVandelay" -- punctuation-heavy role.
    l18 = lead_at("https://example.com/etic-ai-engineer-manager-jobs-in-cairo-in-vandelay-18")
    assert (l18.title, l18.company) == ("ETIC, AI Engineer - Manager", "Vandelay")

    # Row 19: parenthesised role.
    l19 = lead_at(
        "https://example.com/rail-systems-banker-high-speed-rail-jobs-in-abu-dhabi-in-praxis-corporation-19")
    assert (l19.title, l19.company) == ("Rail Systems Banker (High Speed Rail)", "Praxis Corporation")

    # Row 25: "Banker II - Financial ServicesFabrikam".
    l25 = lead_at("https://example.com/banker-ii-financial-services-jobs-in-cairo-in-fabrikam-25")
    assert (l25.title, l25.company) == ("Banker II - Financial Services", "Fabrikam")


def test_fixture_never_touches_already_clean_rows():
    raw = json.loads(FIXTURE.read_text())
    src = sources.get("naukrigulf")
    leads = src.parse(raw, src.searches()[0])
    by_url = {lead.url: lead for lead in leads}

    # Row 10: "Banker" / company "Wayne" already populated -- its link ALSO carries
    # a "-jobs-in-" seam matching "banker" (per the fixture edit rule), which pins
    # abstain-on-populated-company at the fixture level: the seam is present but
    # this row must never be rewritten.
    l10 = by_url["https://example.com/banker-jobs-in-saudi-arabia-in-wayne-10"]
    assert (l10.title, l10.company) == ("Banker", "Wayne")


def test_fixture_row_26_is_the_deliberate_unusual_boundary_abstain_case():
    # Row 26 (appended, per the plan correction -- none of the original 25 rows
    # models the real production "unusual separator" abstain case): the title
    # mashes role and company across an en dash surrounded by spaces
    # ("Site Banker – Example Ventures"), not the bare "no-space, uppercase-opens"
    # signature this fix targets. The listing URL's seam DOES prove the role half
    # ("site-banker"), but the boundary-check-failure conditions fire at every
    # candidate index the seam permits (see the module test below for the trace),
    # so the row must come through with its title left mashed and company empty.
    raw = json.loads(FIXTURE.read_text())
    src = sources.get("naukrigulf")
    leads = src.parse(raw, src.searches()[0])
    by_url = {lead.url: lead for lead in leads}
    l26 = by_url["https://example.com/site-banker-jobs-in-dubai-in-example-ventures-26"]
    assert l26.title == "Site Banker – Example Ventures"
    assert l26.company == ""


def test_row_26_abstain_is_genuinely_the_boundary_check_not_an_absent_seam():
    # Self-review evidence: the seam DOES match a prefix of row 26's title (proving
    # this is not merely "no candidate at all"), and the boundary check is what
    # rejects every one of those matches. Demonstrated two ways: (1) tracing which
    # candidate indices the seam allows and confirming each fails a boundary
    # condition, and (2) confirming that DELETING the boundary check would make
    # this exact row incorrectly split -- i.e. the check is load-bearing here, not
    # redundant.
    title = "Site Banker – Example Ventures"
    url = "https://example.com/site-banker-jobs-in-dubai-in-example-ventures-26"
    assert _split_mashed_title(title, url) is None

    import re
    from urllib.parse import urlparse

    path = urlparse(url).path
    candidates = [path[: m.start()].lstrip("/") for m in re.finditer(r"-jobs-in-", path)]
    assert candidates == ["site-banker"]  # the seam DOES match a real prefix

    # There genuinely are indices where the slug matches the seam -- so the
    # eventual None is the boundary check firing, not an absence of candidates.
    matching_indices = [i for i in range(1, len(title)) if _slug(title[:i]) in candidates]
    assert matching_indices  # at least one slug-matching prefix exists
    for i in matching_indices:
        before_is_space = title[i - 1].isspace()
        after_is_upper = title[i].isupper()
        assert before_is_space or not after_is_upper, (
            f"index {i} should fail a boundary condition, but passed both")

    # And: removing the boundary check entirely WOULD wrongly split this row --
    # confirming the check is load-bearing, not a no-op for this particular seam.
    best = max(matching_indices)
    role_without_check = title[:best].strip()
    company_without_check = title[best:].strip()
    assert role_without_check and company_without_check  # a (wrong) split would occur
