"""NSE fundamentals extraction.

Network-free: the XBRL shapes below are reduced from real NSE filings, keeping
the structures that actually broke the parser.
"""
import pytest

from data.sources import nse_fundamentals as nf

NS = 'xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin"'


def shp_xml(flag: str, pledged: list[str], shares: list[str]) -> str:
    """Shareholding filing. Real ones repeat these tags across ~240 contexts --
    per promoter entity, once for the group, then padded with zeros."""
    body = [f'<in-bse-fin:WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged'
            f'ForPromoterAndPromoterGroup>{flag}'
            f'</in-bse-fin:WhetherAnySharesHeldByPromotersAreEncumberedUnderPledged'
            f'ForPromoterAndPromoterGroup>']
    for v in pledged:
        body.append(f"<in-bse-fin:NumberOfSharesEncumberedUnderPledged>{v}"
                    f"</in-bse-fin:NumberOfSharesEncumberedUnderPledged>")
    for v in shares:
        body.append(f"<in-bse-fin:NumberOfEquitySharesHeldInDematerializedForm>{v}"
                    f"</in-bse-fin:NumberOfEquitySharesHeldInDematerializedForm>")
    return f"<root {NS}>{''.join(body)}</root>"


def patch_get(monkeypatch, text):
    class R:
        def __init__(self, t): self.text = t
    monkeypatch.setattr(nf.nse, "get", lambda *a, **k: R(text))


def test_pledge_ignores_trailing_zero_contexts(monkeypatch):
    """Taking the LAST occurrence returned 0 on a company that genuinely has a
    pledge -- the real JSWCEMENT filing ends in ~200 zero rows."""
    xml = shp_xml("true", ["800000", "0", "800000"] + ["0"] * 200,
                  ["10101916", "971744724", "981846640", "1363364936"] + ["0"] * 50)
    patch_get(monkeypatch, xml)
    out = nf.pledge_from_shp("http://x", promoter_pct=72.02)
    assert out["pledged"] is True
    assert out["pledged_shares"] == 800000
    # 800000 / (1363364936 * 0.7202) = 0.0815%
    assert out["pledge_pct"] == pytest.approx(0.081, abs=0.005)


def test_explicit_no_encumbrance_is_a_confident_zero(monkeypatch):
    patch_get(monkeypatch, shp_xml("false", ["0"], ["100", "1000"]))
    out = nf.pledge_from_shp("http://x", promoter_pct=50.0)
    assert out["pledged"] is False and out["pledge_pct"] == 0.0


def test_unreadable_filing_is_none_not_zero(monkeypatch):
    """'No pledge' and 'we don't know' are different facts about a holding."""
    patch_get(monkeypatch, f"<root {NS}></root>")
    out = nf.pledge_from_shp("http://x", promoter_pct=50.0)
    assert out["pledged"] is None
    assert out["pledge_pct"] is None


def test_pledge_pct_is_of_promoter_holding_not_total_shares(monkeypatch):
    """India quotes pledge against PROMOTER holding. Using total shares as the
    denominator understates it by roughly the promoter stake."""
    xml = shp_xml("true", ["500000"], ["1000000"])
    patch_get(monkeypatch, xml)
    out = nf.pledge_from_shp("http://x", promoter_pct=50.0)
    # 500000 / (1000000 * 0.5) = 100% of promoter holding, not 50% of total.
    assert out["pledge_pct"] == pytest.approx(100.0)


def test_pledge_pct_none_without_promoter_pct(monkeypatch):
    patch_get(monkeypatch, shp_xml("true", ["800000"], ["1000000"]))
    out = nf.pledge_from_shp("http://x", promoter_pct=None)
    assert out["pledged"] is True and out["pledge_pct"] is None


def test_results_parser_takes_first_context_per_tag():
    """Filings repeat tags across quarter, year-to-date and prior year. The
    first is the current period; a max or sum would blend them."""
    xml = (f"<root {NS}>"
           "<in-bse-fin:RevenueFromOperations>153910600000.00</in-bse-fin:RevenueFromOperations>"
           "<in-bse-fin:RevenueFromOperations>999999999999.00</in-bse-fin:RevenueFromOperations>"
           "<in-bse-fin:ProfitBeforeTax>15440800000.00</in-bse-fin:ProfitBeforeTax>"
           "</root>")
    out = nf._parse_xbrl_numbers(xml, nf.RESULT_TAGS)
    assert out["revenue"] == pytest.approx(153910600000.0)
    assert out["pbt"] == pytest.approx(15440800000.0)


# ------------------------------------------------- integrated filing (2025+)

OLD_ROWS = [  # corporates-financial-results: stops at the Dec 2024 quarter
    {"toDate": "31-Dec-2024", "fromDate": "01-Oct-2024",
     "consolidated": "Consolidated", "xbrl": "old-dec24-c"},
    {"toDate": "31-Dec-2024", "fromDate": "01-Oct-2024",
     "consolidated": "Non-Consolidated", "xbrl": "old-dec24-s"},
    {"toDate": "31-Mar-2025", "fromDate": "01-Jan-2025",
     "consolidated": "Consolidated", "xbrl": "old-mar25-c"},
]
INTEGRATED = {"data": [  # integrated-filing-results, shape as NSE returns it
    {"qe_Date": "30-JUN-2026", "consolidated": "Consolidated",
     "broadcast_Date": "24-Jul-2026 17:40:11", "revised_Date": None,
     "xbrl": "int-jun26-c"},
    {"qe_Date": "30-JUN-2026", "consolidated": "Standalone",
     "broadcast_Date": "24-Jul-2026 17:40:11", "revised_Date": None,
     "xbrl": "int-jun26-s"},
    {"qe_Date": "31-MAR-2025", "consolidated": "Consolidated",
     "broadcast_Date": "28-May-2025 09:31:37", "revised_Date": None,
     "xbrl": "int-mar25-c"},
    {"qe_Date": "31-MAR-2025", "consolidated": "Consolidated",
     "broadcast_Date": "28-May-2025 09:31:37", "revised_Date": "02-Jun-2025 10:00:00",
     "xbrl": "int-mar25-c-revised"},
], "totalCount": 4}


def patch_endpoints(monkeypatch, old=OLD_ROWS, new=INTEGRATED, fail=()):
    class R:
        def __init__(self, j): self._j = j
        def json(self): return self._j
    def get(url, **k):
        if url in fail:
            raise RuntimeError("NSE down")
        return R(new if url == nf.INTEGRATED_URL else old)
    monkeypatch.setattr(nf.nse, "get", get)


def test_result_filings_reach_past_the_old_endpoint(monkeypatch):
    """The old endpoint stops at Dec 2024; reading it alone scored every
    company on results two years stale."""
    patch_endpoints(monkeypatch)
    rows = nf.result_filings("X")
    assert rows[0]["toDate"] == "30-Jun-2026"
    assert [r["toDate"] for r in rows] == sorted(
        [r["toDate"] for r in rows], key=lambda d: -nf._date_key(d))
    assert {r["consolidated"] for r in rows if r["toDate"] == "30-Jun-2026"} == \
        {"Consolidated", "Non-Consolidated"}        # "Standalone" normalised


def test_integrated_wins_overlap_and_latest_revision_wins(monkeypatch):
    patch_endpoints(monkeypatch)
    mar = [r for r in nf.result_filings("X")
           if r["toDate"] == "31-Mar-2025" and r["consolidated"] == "Consolidated"]
    assert [r["xbrl"] for r in mar] == ["int-mar25-c-revised"]


def test_one_endpoint_down_is_a_gap_not_nothing(monkeypatch):
    patch_endpoints(monkeypatch, fail=(nf.INTEGRATED_URL,))
    assert nf.result_filings("X")[0]["toDate"] == "31-Mar-2025"
    patch_endpoints(monkeypatch, fail=(nf.INTEGRATED_URL, nf.RESULTS_URL))
    with pytest.raises(RuntimeError):
        nf.result_filings("X")
