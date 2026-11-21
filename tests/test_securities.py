"""Security-type detection: nothing is assumed to be an equity share."""
from book import securities as sec


def kind(sym, isin="", sector=""):
    return sec.classify(sym, isin, sector)


def test_suffixes():
    assert kind("EMBASSY-RR").kind == sec.REIT
    assert kind("PGINVIT-IV").kind == sec.INVIT
    assert kind("SGBJUN31-GB").kind == sec.GOLD_BOND
    assert kind("SGBJUN31").kind == sec.GOLD_BOND
    assert kind("10IIFL28A-N1").kind == sec.BOND
    assert kind("XYZ-RE").kind == sec.RIGHTS and kind("XYZ-RE").needs_review
    assert kind("AIRTELPP-E1").kind == sec.PARTLY_PAID


def test_equity_series_suffix_is_still_equity_and_stripped():
    s = kind("XYZ-BE")
    assert (s.kind, s.symbol, s.base) == (sec.EQUITY, "XYZ", "XYZ")
    assert kind("BAJAJ-AUTO").kind == sec.EQUITY       # a real hyphenated symbol
    assert kind("BAJAJ-AUTO").base == "BAJAJ-AUTO"


def test_isin_type_codes():
    assert kind("EMBASSY", "INE041025011").kind == sec.REIT
    assert kind("PGINVIT", "INE0GGX23010").kind == sec.INVIT
    assert kind("TCS", "INE467B01029").kind == sec.EQUITY
    assert kind("SOMEBOND", "INE530B08102").kind == sec.BOND
    assert kind("NIFTYBEES", "INF204KB14I2").kind == sec.ETF


def test_disagreement_is_flagged_and_isin_wins():
    s = kind("EMBASSY-RR", "INE467B01029")
    assert s.kind == sec.EQUITY and s.needs_review


def test_unknown_suffix_flagged_not_guessed():
    s = kind("ABC-Q9")
    assert s.kind == sec.UNKNOWN and "unrecognised" in s.review


def test_government_isin_on_plain_symbol_is_not_equity():
    s = kind("GS2033", "IN0020230036")
    assert s.kind == sec.BOND and s.needs_review


def test_sleeves():
    assert kind("TCS").sleeve == sec.OPERATING
    assert kind("EMBASSY-RR").sleeve == sec.REAL_ASSETS
    assert kind("PGINVIT-IV").sleeve == sec.REAL_ASSETS
    assert kind("X", "INF204KB14I2").sleeve == sec.FUNDS
