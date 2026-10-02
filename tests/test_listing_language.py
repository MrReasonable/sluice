"""#312: drop a listing whose TITLE is written in a script none of the user's languages uses.

Script, not language: a Han or Arabic letter is a fact about a title, while telling English from
German on two to six words is a guess. So the gate drops only on letters in a script NONE of the
configured languages writes -- never on Latin letters, never on one stray letter -- and keeps
everything it cannot classify. Empty config abstains entirely (672ad2a).

Every title here is synthetic. They are generic role words, written in each script, so no row
describes a real posting, employer or place.
"""
import pytest

from sluice.core.config import Config, load_config
from sluice.core.language import LANGUAGE_SCRIPTS, is_readable, parse_listing_languages

ZH = "软件工程师"            # "software engineer"
AR = "مهندس برمجيات"         # "software engineer"
RU = "Инженер-программист"  # "software engineer"
EL = "Μηχανικός λογισμικού"  # "software engineer"
TA = "மென்பொருள் பொறியாளர்"  # "software engineer"
JA_KANA = "ソフトウェアエンジニア"
KO = "소프트웨어 엔지니어"


@pytest.mark.parametrize("title", ["Platform Engineer", f"Platform Engineer {ZH}", AR, RU, ""])
def test_an_empty_configuration_keeps_every_title(title):
    assert is_readable(title, []) is True
    assert is_readable(title, None) is True


@pytest.mark.parametrize("title", [
    ZH, AR, RU, EL, TA,
    f"Platform Engineer ({ZH})",        # bilingual: the listing behind it is in the other script
    f"{ZH} Platform Engineer",
])
def test_a_title_in_an_unconfigured_script_is_dropped(title):
    assert is_readable(title, ["en"]) is False


@pytest.mark.parametrize("title", [
    "Platform Engineer (£90k-£100k + Equity)",   # currency symbols are not letters
    "Data Engineer ▪ Remote",                    # separators are not letters
    "Example Co™ Application Engineer",
    "Café Systems Engineer",                     # accented Latin is Latin
    "Engineering Manager (m/f/d)",
    "Werkstudent DevOps (m/w/d) für Plattform",  # German: same script, kept
    "ＦＵＬＬＷＩＤＴＨ Engineer",                # full-width Latin is Latin
])
def test_latin_script_titles_are_kept_for_an_english_reader(title):
    assert is_readable(title, ["en"]) is True


@pytest.mark.parametrize("title", ["μServices Engineer", "Δ Desk Engineer", "Еngineer"])
def test_a_single_stray_letter_does_not_drop_a_title(title):
    # One Greek letter, or a Cyrillic "Е" pasted for a Latin "E", is decoration or a homoglyph far
    # more often than it is the advert's language.
    assert is_readable(title, ["en"]) is True


def test_two_stray_letters_do_drop_a_title():
    assert is_readable("Еnginеer", ["en"]) is False


def test_several_languages_widen_what_is_readable():
    assert is_readable(f"Platform Engineer {ZH}", ["en", "zh"]) is True
    assert is_readable(ZH, ["en", "zh"]) is True
    assert is_readable(RU, ["en", "zh"]) is False


@pytest.mark.parametrize("languages,title", [
    (["zh"], f"Java{ZH}"),
    (["ja"], f"AWS{JA_KANA}"),
    (["ru"], "Python-разработчик"),
    (["zh"], "Platform Engineer"),
])
def test_latin_letters_never_drop_a_title(languages, title):
    # Owner's ruling: technology names are Latin in every market, so a native-language title
    # routinely carries one. Counting them would drop a non-Latin reader's own listings.
    assert is_readable(title, languages) is True


def test_japanese_reads_kanji_and_both_kana():
    for title in [JA_KANA, "ひらがな", "開発者", "ｴﾝｼﾞﾆｱ", "データー"]:
        assert is_readable(title, ["ja"]) is True, title
    assert is_readable(KO, ["ja"]) is False


def test_half_width_katakana_is_katakana():
    # Its Unicode name starts "HALFWIDTH", not "KATAKANA", so it needs its own prefix entry.
    assert is_readable("ｴﾝｼﾞﾆｱ", ["en"]) is False
    assert is_readable("ｴﾝｼﾞﾆｱ", ["ja"]) is True


def test_half_width_hangul_is_hangul():
    # Like half-width katakana, its Unicode name starts "HALFWIDTH", so it needs its own entry.
    assert is_readable("\uffa1\uffa2", ["en"]) is False
    assert is_readable("\uffa1\uffa2", ["ko"]) is True


def test_punjabi_reads_both_of_its_scripts():
    # Gurmukhi and the Arabic-script Shahmukhi: a reader of either must keep both.
    assert is_readable("\u0a38\u0a3e\u0a2b\u0a1f\u0a35\u0a47\u0a05\u0a30", ["pa"]) is True
    assert is_readable(AR, ["pa"]) is True
    assert is_readable(ZH, ["pa"]) is False


def test_korean_reads_hangul_and_han():
    assert is_readable(KO, ["ko"]) is True
    assert is_readable("工程", ["ko"]) is True


def test_script_punctuation_and_digits_are_not_letters():
    # Their Unicode NAMES start with a script ("ARABIC COMMA", "ARABIC-INDIC DIGIT TWO", "THAI
    # DIGIT ONE"), so only the is-a-letter check keeps them from counting as that script.
    assert is_readable("Platform Engineer،، Remote", ["en"]) is True
    assert is_readable("Engineer Level ٢٣", ["en"]) is True
    assert is_readable("Engineer ๑๒", ["en"]) is True


def test_letters_in_a_script_the_roster_does_not_know_are_kept():
    # Abstain, never guess: `ª` is alphabetic but belongs to no script the roster names.
    assert is_readable("Engenheiro de 1ª ª linha", ["en"]) is True


def test_a_list_naming_no_known_code_keeps_everything():
    # The loader validates codes, but a caller that bypassed it must fail toward keeping.
    assert is_readable(ZH, ["EN"]) is True
    assert is_readable(ZH, ["english"]) is True


def test_every_roster_language_names_at_least_one_script():
    # Anti-vacuity: a language mapped to no script would leave its reader's gate meaningless.
    assert LANGUAGE_SCRIPTS and all(LANGUAGE_SCRIPTS.values())
    assert "en" in LANGUAGE_SCRIPTS


# ── config ───────────────────────────────────────────────────────────────────

@pytest.fixture
def write_config(tmp_path, monkeypatch):
    def write(text):
        p = tmp_path / "c.yaml"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv("SLUICE_CONFIG", str(p))
    return write


def test_the_default_is_empty_so_the_gate_abstains(monkeypatch, tmp_path):
    # A config path that does not exist, so only the code default can answer.
    monkeypatch.setenv("SLUICE_CONFIG", str(tmp_path / "absent.yaml"))
    assert Config().listing_languages == []
    assert load_config(None).listing_languages == []


def test_several_languages_load_and_are_normalised(write_config):
    write_config("listing_languages: [EN, zh]\n")
    assert load_config(None).listing_languages == ["en", "zh"]


def test_an_unknown_language_code_raises_naming_its_position_not_its_value(write_config):
    # #176's house style: a config error names WHERE and WHAT TYPE, never the user's own text,
    # because the message reaches stderr, logs and pasted tracebacks. The valid codes are listed.
    write_config("listing_languages: [en, Private Words]\n")
    with pytest.raises(ValueError) as ei:
        load_config(None)
    msg = str(ei.value)
    assert "listing_languages" in msg and "entry 2" in msg
    assert "Private" not in msg and "private" not in msg
    assert "en" in msg and "zh" in msg


@pytest.mark.parametrize("bad", ["en", "true"])
def test_a_non_list_value_raises(bad, write_config):
    write_config(f"listing_languages: {bad}\n")
    with pytest.raises(ValueError, match="listing_languages"):
        load_config(None)


def test_a_non_string_entry_is_reported_by_position_and_type(write_config):
    write_config("listing_languages: [en, 42]\n")
    with pytest.raises(ValueError) as ei:
        load_config(None)
    msg = str(ei.value)
    assert "entry 2" in msg and "int" in msg and "42" not in msg


def test_an_unquoted_norwegian_code_says_to_quote_it(write_config):
    # YAML reads a bare `no` as false. Coercing it back silently would also accept a genuine
    # `false`; saying what happened lets the user write `'no'` and be sure.
    write_config("listing_languages: [en, no]\n")
    with pytest.raises(ValueError) as ei:
        load_config(None)
    msg = str(ei.value)
    assert "entry 2" in msg and "'no'" in msg
    write_config("listing_languages: [en, 'no']\n")
    assert load_config(None).listing_languages == ["en", "no"]


def test_the_init_wizard_validates_through_the_loaders_own_function():
    # `init` must never write a config that then fails to load.
    from sluice.onboard.questions import BadAnswer, parse_languages
    assert parse_languages("EN, zh") == ["en", "zh"]
    with pytest.raises(BadAnswer, match="entry 2"):
        parse_languages("en, english")
    assert parse_listing_languages(["EN"]) == ["en"]


def test_the_init_wizard_asks_for_listing_languages():
    from sluice.onboard.questions import catalogue, parse_languages
    q = {x.key: x for x in catalogue()}["listing_languages"]
    assert q.parse is parse_languages and q.writes_to == ("listing_languages",)


# ── the ingest run loop ──────────────────────────────────────────────────────

def _ingest(languages):
    """Run the REAL ingest loop over one source returning an English and a Chinese title, and
    return what reached the sink -- so a run loop that stopped calling the gate goes red here,
    which a test of the helper alone would not notice."""
    from sluice.ingest.base import Ctx
    from sluice.ingest.engine import run
    from tests.test_engine import FakeSource, _FakeSeen, _FakeSink

    src = FakeSource("example", [{"title": "Platform Engineer", "link": "http://x/1"},
                                 {"title": ZH, "link": "http://x/2"}])
    sink, seen = _FakeSink(), _FakeSeen()
    ctx = Ctx(camofox=None, config=Config(listing_languages=languages), sleep=lambda *_: None)

    class _Health:
        def __getattr__(self, name):
            return lambda *a, **k: None

    run([src], ctx, sink, seen, _Health(), retries=1)
    return [lead.title for lead in sink.leads], seen


def test_ingest_drops_an_unreadable_title_before_the_sink_and_the_seen_set():
    titles, seen = _ingest(["en"])
    assert titles == ["Platform Engineer"]
    # Never recorded as seen: a later run with a wider language list can still pick it up.
    assert "http://x/2" not in " ".join(map(str, seen.load()))


def test_ingest_with_no_languages_configured_keeps_every_title():
    titles, _seen = _ingest([])
    assert titles == ["Platform Engineer", ZH]
