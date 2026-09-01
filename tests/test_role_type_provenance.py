"""#223 §2.1: where a lead's `role_type` came from, stamped at each origin.

`role_type` records which SEARCH found a lead, not what the posting says, and the
relevance gate consumes it as though it were a fact about the job. The fix is not to
delete the value -- sometimes it IS the user's own assertion -- but to record which of
three origins produced it, so the gate can decline to trust the tool's own guess.

The load-bearing case is the fourth one below. A user who configures
`sources.<id>.searches` but sets no `job_type` param inherits the SOURCE's shipped
guess, and if provenance were decided from the search alone -- "this search is
configured, therefore its job_type is the user's" -- that guess would be laundered into
`declared` and the gate would go on trusting it for every source shipping a non-empty
`extra`. Provenance is therefore computed PER KEY and BEFORE the `{**extra, **params}`
merge, which is the only point at which the two dicts are still distinguishable.
"""
from types import SimpleNamespace

import pytest

from sluice.core.roletype import ASSUMED, DECLARED, OBSERVED
from sluice.ingest.base import BrowserListSource, Search, searches_for


class _FakeConfig:
    def __init__(self, overrides):
        self._o = overrides

    def source(self, id):
        return SimpleNamespace(searches=self._o.get(id, []))


_ROW = {"result": [{"title": "Analyst", "link": "https://example.invalid/1"}]}


def _lead(*, extra=None, spec=("Example", "https://example.invalid/s"), config=None):
    src = BrowserListSource(id="demo", extractor_js="JS", extra=extra,
                            searches_spec=[spec])
    search = searches_for(src, config)[0]
    return src.parse(_ROW, search)[0]


def test_a_sources_own_extra_is_the_tools_guess():
    lead = _lead(extra={"job_type": "contract"})
    assert (lead.job_type, lead.job_type_source) == ("contract", ASSUMED)


def test_a_shipped_example_searchs_params_are_the_tools_guess():
    lead = _lead(spec=("Example", "https://example.invalid/s", {"job_type": "contract"}))
    assert (lead.job_type, lead.job_type_source) == ("contract", ASSUMED)


def test_a_user_configured_searchs_params_are_the_users_assertion():
    cfg = _FakeConfig({"demo": [["Mine", "https://example.invalid/q",
                                {"job_type": "contract"}]]})
    lead = _lead(config=cfg)
    assert (lead.job_type, lead.job_type_source) == ("contract", DECLARED)


def test_a_configured_search_that_sets_no_job_type_still_inherits_a_GUESS():
    # The reason provenance is per-KEY. The search is the user's; the VALUE is the
    # source's shipped `extra`. Deciding from the search alone would stamp `declared`
    # here and hand the tool's own guess the user's authority.
    cfg = _FakeConfig({"demo": [["Mine", "https://example.invalid/q"]]})
    lead = _lead(extra={"job_type": "contract"}, config=cfg)
    assert (lead.job_type, lead.job_type_source) == ("contract", ASSUMED)


def test_a_configured_search_overriding_the_sources_guess_is_declared():
    cfg = _FakeConfig({"demo": [["Mine", "https://example.invalid/q",
                                {"job_type": "perm"}]]})
    lead = _lead(extra={"job_type": "contract"}, config=cfg)
    assert (lead.job_type, lead.job_type_source) == ("permanent", DECLARED)


def test_no_job_type_anywhere_leaves_both_fields_blank():
    lead = _lead()
    assert (lead.job_type, lead.job_type_source) == ("", "")


def test_a_blank_job_type_is_not_provenance_worth_recording():
    # A source declaring `extra={"job_type": ""}` has said nothing, so there is no
    # origin to stamp. Recording `assumed` here would make the sweep in §4 read as
    # though every such lead carried a guess.
    lead = _lead(extra={"job_type": ""})
    assert (lead.job_type, lead.job_type_source) == ("", "")


def test_the_stored_value_is_folded_to_the_closed_set():
    lead = _lead(extra={"job_type": "Perm"})
    assert lead.job_type == "permanent"


def test_an_unrecognised_job_type_is_blanked_rather_than_stored():
    lead = _lead(extra={"job_type": "contract-to-perm"})
    # Blanked, so nothing downstream substring-matches it -- but the ORIGIN still
    # stands: the source did assert something, and it was the tool's guess.
    assert (lead.job_type, lead.job_type_source) == ("", ASSUMED)


def test_a_sources_extra_cannot_forge_its_own_provenance():
    # `extra` is code, not config, so `validate_search_entry`'s `_PARAMS_KEY_CLASH`
    # check never sees it. The stamp therefore happens AFTER `_row_to_lead`'s verbatim
    # `setattr` loop, which is what makes a source unable to claim `observed`.
    lead = _lead(extra={"job_type": "contract", "job_type_source": "observed"})
    assert lead.job_type_source == ASSUMED


def test_a_hand_built_search_cannot_forge_provenance_either():
    # The last route in. A `Search` built directly is a plain dataclass, so it goes
    # through neither `load_config` nor `_mk_search` and `_PARAMS_KEY_CLASH` never runs
    # -- the params dict reaches the `setattr` loop verbatim. Placing the stamp AFTER
    # that loop is what closes it; a stamp before the loop would let this through.
    src = BrowserListSource(id="demo", extractor_js="JS",
                            searches_spec=[("Example", "https://example.invalid/s")])
    forged = Search("Hand", "https://example.invalid/h",
                    {"job_type": "contract", "job_type_source": "observed"},
                    configured=True)
    assert src.parse(_ROW, forged)[0].job_type_source == DECLARED


def test_config_load_refuses_a_params_key_that_collides_with_the_new_field():
    # `_PARAMS_KEY_CLASH` is DERIVED from `Lead`'s fields, so adding `job_type_source`
    # to the dataclass closes the config route without anyone listing it anywhere.
    # Asserted because that derivation is the whole protection: a hand-list would have
    # gone stale on this very commit.
    from sluice.core.config import validate_search_entry
    with pytest.raises(ValueError, match="job_type_source"):
        validate_search_entry("a search entry", 0,
                              ["Mine", "https://example.invalid/q",
                               {"job_type_source": "observed"}])


def test_a_search_object_built_by_hand_is_treated_as_the_tools_guess():
    # `Search.configured` defaults False, so a future search-producing path that never
    # thinks about provenance produces `assumed` rather than `declared`. The direction
    # matters: a wrong `declared` is a shipped preference wearing the user's authority.
    src = BrowserListSource(id="demo", extractor_js="JS",
                            searches_spec=[("Example", "https://example.invalid/s")])
    lead = src.parse(_ROW, Search("Hand", "https://example.invalid/h",
                                  {"job_type": "contract"}))[0]
    assert lead.job_type_source == ASSUMED


# ── §2.1: the note carries the provenance, so nothing has to re-derive it ────
def _vault(tmp_path):
    from sluice.core.vault import Vault
    return Vault(str(tmp_path))


def _app(tmp_path):
    from sluice.core.app import Sluice
    from sluice.core.config import Config
    return Sluice(Config(), store=_vault(tmp_path))


def _note_text(tmp_path):
    return (tmp_path / "Job Applications" / "Job Leads" / "Acme - Analyst.md").read_text()


def test_a_scraped_leads_provenance_reaches_the_note(tmp_path):
    from sluice.core.leads import Lead
    v = _vault(tmp_path)
    v.upsert(Lead(source="demo", search="s", title="Analyst", company="Acme",
                  url="https://example.invalid/1", job_type="contract",
                  job_type_source=ASSUMED))
    assert 'role_type_source: "assumed"' in _note_text(tmp_path)


def test_a_lead_with_no_provenance_writes_the_key_blank_rather_than_omitting_it(tmp_path):
    from sluice.core.leads import Lead
    v = _vault(tmp_path)
    v.upsert(Lead(source="demo", search="s", title="Analyst", company="Acme",
                  url="https://example.invalid/1"))
    # Present-and-blank, not absent. A missing key and a blank one both read as
    # `assumed` (§2.1), so this is not a correctness difference -- it is that a note
    # whose schema silently varies by lead is one a human cannot scan.
    assert 'role_type_source: ""' in _note_text(tmp_path)


def test_a_forged_provenance_cannot_break_out_of_the_frontmatter_scalar(tmp_path):
    from sluice.core.leads import Lead
    v = _vault(tmp_path)
    # `Vault.upsert` is public and takes a `Lead` built by any caller, so the render
    # boundary applies decision 7's abstain-and-blank to this field exactly as it does
    # to the five beside it -- a `"` here would otherwise open a second frontmatter key.
    v.upsert(Lead(source="demo", search="s", title="Analyst", company="Acme",
                  url="https://example.invalid/1",
                  job_type_source='x"\nstatus: applied'))
    txt = _note_text(tmp_path)
    assert 'role_type_source: ""' in txt
    assert "status: applied" not in txt


def test_a_hand_created_lead_is_the_users_own_assertion(tmp_path):
    # `Sluice.create_lead` is the MCP write tool and the manual-entry path: the user
    # typed this job_type, so it is `declared`. It never reaches `_row_to_lead`, which
    # is why the stamp has to be repeated here rather than centralised there.
    app = _app(tmp_path)
    res = app.create_lead(title="Analyst", company="Acme",
                          url="https://example.invalid/1", job_type="contract")
    assert res.outcome == "created"
    txt = _note_text(tmp_path)
    assert 'role_type: "contract"' in txt
    assert 'role_type_source: "declared"' in txt


def test_a_hand_created_lead_with_no_job_type_declares_nothing(tmp_path):
    app = _app(tmp_path)
    app.create_lead(title="Analyst", company="Acme", url="https://example.invalid/1")
    assert 'role_type_source: ""' in _note_text(tmp_path)


def test_a_hand_created_leads_job_type_is_folded_to_the_closed_set(tmp_path):
    app = _app(tmp_path)
    app.create_lead(title="Analyst", company="Acme",
                    url="https://example.invalid/1", job_type="Perm")
    assert 'role_type: "permanent"' in _note_text(tmp_path)


def test_a_rescrape_cannot_downgrade_an_observed_role_type(tmp_path):
    """Never-clobber, on the field #223 adds.

    `role_type_source` is the ONE field in the note whose value ranks: an ingest run
    writes `assumed` for every lead a contract-labelled search returns, and the note may
    already carry an `observed` value read off the posting itself. If a re-scrape
    rewrote it, the gate would go back to judging that lead on the search label -- the
    exact defect this issue is about -- and it would happen on the ordinary nightly run,
    silently, on every lead the observation had already fixed.

    Nothing in `Vault.upsert` special-cases this: the update path is a bare `last_seen`
    bump, so the property falls out of never-clobber. Asserted anyway, because the
    property is what matters and a future "fill in missing fields on update" change
    would break it without touching a line of #223's own code.
    """
    from sluice.core.leads import Lead
    from sluice.core.vault import Vault
    v = Vault(str(tmp_path))
    seed = dict(source="demo", search="s", title="Analyst", company="Acme",
                url="https://example.invalid/1")
    assert v.upsert(Lead(**seed, job_type="contract",
                         job_type_source=OBSERVED)).outcome == "created"
    assert v.upsert(Lead(**seed, job_type="permanent",
                         job_type_source=ASSUMED)).outcome == "updated"
    note = (tmp_path / "Job Applications" / "Job Leads" / "Acme - Analyst.md").read_text()
    assert 'role_type: "contract"' in note
    assert f'role_type_source: "{OBSERVED}"' in note
