"""#176: a YAML SCALAR given for a container-typed config field must be REFUSED.

The bug class, measured on the pre-fix tree rather than reasoned about:

  * `relevance_drop: senior` (root) loaded as `['s','e','n','i','o','r']` and
    `is_relevant` then returned False for EVERY title tried -- the whole scrape
    binned, at ingest, before dedup and before any note exists to notice.
  * `triage.target_locations: remote` loaded as the STRING `"remote"`, and `classify`
    then kept Remote AND London AND Berlin -- byte-identical to the unconfigured
    abstain, so a geography filter the user believes they configured does nothing.
  * `cv.fabrication_decoys: Acme` made the CV gate emit `FABRICATED: contains 'A'`,
    `'c'`, `'m'`, `'e'` and hard-block every CV.

All three are 672ad2a -- a preference gate reading as something the user did not
choose -- reached through a YAML typo instead of a shipped default.

RAISING rather than coercing is deliberate and is the decision this repo had already
made and half-applied: `core/config.py`'s `_str_list` names this exact scalar case and
says "a clear error at construction is the house style". Coercion cannot be made safe
here, because the likeliest scalar is the COMMA-SEPARATED one -- `init` asks
"comma-separated?", and a user hand-editing YAML repeats that phrasing.
`target_locations: London, Berlin` coerces to ONE token matching nothing, so every
located lead is rejected. Coercion converts "the gate abstains" into "the gate matches
nothing", which is the same bug class one step further from view.
"""
import pytest

from sluice.core.config import load_config
from sluice.cv.config import load_cv_config
from sluice.triage.config import load_triage_config


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# ── the three loaders, one representative field each ─────────────────────────────

def test_a_root_list_field_refuses_a_scalar(tmp_path):
    # The severe one: this runs at ingest, before dedup and before any LLM call.
    path = _write(tmp_path, "root.yaml", "vault_dir: ./v\nrelevance_drop: senior\n")
    with pytest.raises(ValueError) as e:
        load_config(path)
    assert "relevance_drop" in str(e.value)


def test_a_triage_list_field_refuses_a_scalar(tmp_path):
    path = _write(tmp_path, "triage.yaml", "triage:\n  target_locations: remote\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    assert "target_locations" in str(e.value)


def test_a_cv_list_field_refuses_a_scalar(tmp_path):
    path = _write(tmp_path, "cv.yaml", "cv:\n  fabrication_decoys: Acme\n")
    with pytest.raises(ValueError) as e:
        load_cv_config(path)
    assert "fabrication_decoys" in str(e.value)


# ── the comma-separated case, which is why coercion was rejected ─────────────────

def test_a_comma_separated_scalar_is_refused_rather_than_silently_one_token(tmp_path):
    # Coerced, this becomes ONE token that matches nothing, and every located lead is
    # rejected with nothing said. It is the likeliest scalar a user writes, because
    # `job-sluice init` asks for these answers comma-separated.
    path = _write(tmp_path, "csv.yaml", "triage:\n  target_locations: London, Berlin\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    assert "target_locations" in str(e.value)


# ── the message contract ─────────────────────────────────────────────────────────

def test_the_refusal_names_the_key_the_type_and_both_correct_spellings(tmp_path):
    path = _write(tmp_path, "msg.yaml", "triage:\n  reject_companies: AcmeCorp\n")
    with pytest.raises(ValueError) as e:
        load_triage_config(path)
    msg = str(e.value)
    assert "triage.reject_companies" in msg, "the block-qualified key must be named"
    assert "str" in msg, "the type actually found must be named"
    assert "[" in msg and "- " in msg, "both YAML list spellings must be shown"


@pytest.mark.parametrize("body,loader", [
    ("triage:\n  reject_companies: {v}\n", load_triage_config),
    ("cv:\n  employers: {v}\n", load_cv_config),
    ("vault_dir: ./v\nrelevance_drop: {v}\n", load_config),
    ("vault_dir: ./v\nlocation_noise_words: {v}\n", load_config),
])
def test_the_refusal_never_echoes_the_VALUE(tmp_path, body, loader):
    """`reject_companies`, `employers`, `relevance_drop` and the noise words are all
    personal -- an employer, a job title, a place.

    `load_config` already declines `_str_list` for `dossier_allow_hosts` on exactly this
    ground: "a config file is one of the few places a user's real private hostnames
    legitimately live", and `urlguard.parse_allow_hosts` prints
    `type(entries).__name__` instead. `_str_list` itself echoed `got {value!r}`, which
    made that choice inconsistent with its own reasoning; both now name the TYPE.

    All four loaders' paths are covered because the property is the same one and the
    implementations are not: two go through `refuse_wrong_container`, two through
    `_str_list`.
    """
    secret = "SomeRealEmployerLtd"
    path = _write(tmp_path, "leak.yaml", body.format(v=secret))
    with pytest.raises(ValueError) as e:
        loader(path)
    assert secret not in str(e.value), "the refusal echoed the user's private value"


# ── the other direction: correct config must still load ──────────────────────────

@pytest.mark.parametrize("body,loader,attr,expected", [
    ("vault_dir: ./v\nrelevance_drop: [senior]\n", load_config, "relevance_drop", ["senior"]),
    ("triage:\n  target_locations: [remote]\n", load_triage_config, "target_locations", ["remote"]),
    ("cv:\n  fabrication_decoys: [Acme]\n", load_cv_config, "fabrication_decoys", ["Acme"]),
])
def test_a_real_list_still_loads(tmp_path, body, loader, attr, expected):
    # Without this, refusing EVERY value would satisfy the tests above while making
    # the fields unusable.
    path = _write(tmp_path, "ok.yaml", body)
    assert getattr(loader(path), attr) == expected


@pytest.mark.parametrize("body,loader,attr", [
    ("vault_dir: ./v\n", load_config, "relevance_drop"),
    ("triage:\n  company_resolve_llm: false\n", load_triage_config, "target_locations"),
    ("cv:\n  voice_check: false\n", load_cv_config, "fabrication_decoys"),
])
def test_an_absent_key_still_abstains(tmp_path, body, loader, attr):
    # ABSENT is the abstain case, never an error: an unconfigured install must load and
    # gate nothing. Refusing here would be 672ad2a with a different trigger.
    path = _write(tmp_path, "absent.yaml", body)
    assert getattr(loader(path), attr) == []


# ── scope: the guard must reach every container field, not the ones I thought of ──

def test_every_container_field_in_every_loader_is_guarded(tmp_path):
    """The SCOPE assertion, without which this file is a hand-list that goes stale.

    A guard applied to the fields someone happened to enumerate is the half-applied
    defensive pattern this repo treats as worse than none -- and it is exactly how
    `_str_list` came to protect two root fields and none of the sub-app ones. This
    walks the real dataclasses, so a container field added later is covered the day it
    lands rather than the day someone remembers.
    """
    import dataclasses

    from sluice.core.config import Config
    from sluice.cv.config import CvConfig
    from sluice.triage.config import TriageConfig

    cases = [("", Config, load_config, "vault_dir: ./v\n"),
             ("triage", TriageConfig, load_triage_config, ""),
             ("cv", CvConfig, load_cv_config, "")]

    checked = 0
    for block, klass, loader, preamble in cases:
        for f in dataclasses.fields(klass):
            default = (f.default_factory() if f.default_factory is not dataclasses.MISSING
                       else f.default)
            if not isinstance(default, (list, dict)):
                continue
            # A scalar for this field must be refused by SOME guard -- this one, or the
            # field's own bespoke validator (dossier_allow_hosts, slop_allow).
            body = (f"{preamble}{block}:\n  {f.name}: scalar\n" if block
                    else f"{preamble}{f.name}: scalar\n")
            path = _write(tmp_path, f"scope-{block}-{f.name}.yaml", body)
            with pytest.raises(ValueError):
                loader(path)
            checked += 1

    assert checked >= 12, (
        f"the walk found only {checked} container fields across three loaders; "
        "discovery has stopped matching and this sweep is now vacuous")
