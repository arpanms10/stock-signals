"""Capital-gains tax on backtest trades, lot by lot."""
import datetime as dt

import pytest

from backtest.tax_lots import TaxBook, TaxRules, fy_of

D = dt.date
CESS = 1.04


def book(**kw):
    return TaxBook(TaxRules(**kw))


def test_financial_year_starts_in_april():
    assert fy_of(D(2025, 3, 31)) == 2024 and fy_of(D(2025, 4, 1)) == 2025


def test_short_and_long_term_by_holding_period():
    b = book(use_exemption=False)
    b.buy("A", 10, 1000, D(2024, 4, 10))
    b.sell("A", 10, 2000, D(2025, 4, 10))         # 365 days: still short term
    b.buy("B", 10, 1000, D(2024, 4, 10))
    b.sell("B", 10, 2000, D(2025, 4, 11))         # 366 days: long term
    assert b.settle(2025) == pytest.approx((0.20 * 1000 + 0.125 * 1000) * CESS)


def test_lots_are_sold_first_in_first_out():
    b = book(use_exemption=False)
    b.buy("A", 10, 1000, D(2023, 1, 2))           # 100 a share, long term by the sale
    b.buy("A", 10, 1500, D(2025, 1, 2))           # 150 a share, short term
    b.sell("A", 10, 2000, D(2025, 6, 2))          # delivers the 2023 lot
    assert b.settle(2025) == pytest.approx(0.125 * 1000 * CESS)
    assert sum(l[0] for l in b.lots["A"]) == 10


def test_exemption_comes_off_long_term_gains():
    b = book()
    b.buy("A", 1, 100_000, D(2023, 1, 2))
    b.sell("A", 1, 300_000, D(2025, 6, 2))        # 2 lakh long-term gain
    assert b.settle(2025) == pytest.approx(0.125 * 75_000 * CESS)


def test_short_term_loss_offsets_short_gains_first_then_long():
    b = book(use_exemption=False)
    b.buy("L", 1, 10_000, D(2025, 5, 1)); b.sell("L", 1, 4_000, D(2025, 6, 1))   # -6k ST
    b.buy("S", 1, 10_000, D(2025, 5, 1)); b.sell("S", 1, 14_000, D(2025, 7, 1))  # +4k ST
    b.buy("T", 1, 10_000, D(2023, 5, 1)); b.sell("T", 1, 20_000, D(2025, 7, 1))  # +10k LT
    assert b.settle(2025) == pytest.approx(0.125 * 8_000 * CESS)


def test_long_term_loss_never_offsets_short_term_gains_and_carries():
    b = book(use_exemption=False)
    b.buy("L", 1, 10_000, D(2023, 5, 1)); b.sell("L", 1, 4_000, D(2025, 6, 1))   # -6k LT
    b.buy("S", 1, 10_000, D(2025, 5, 1)); b.sell("S", 1, 14_000, D(2025, 7, 1))  # +4k ST
    assert b.settle(2025) == pytest.approx(0.20 * 4_000 * CESS)
    b.buy("T", 1, 10_000, D(2024, 5, 1)); b.sell("T", 1, 20_000, D(2026, 7, 1))  # +10k LT
    assert b.settle(2026) == pytest.approx(0.125 * 4_000 * CESS)


def test_carried_losses_expire_after_eight_years():
    b = book(use_exemption=False)
    b.buy("L", 1, 10_000, D(2017, 5, 1)); b.sell("L", 1, 4_000, D(2017, 6, 1))
    b.settle(2017)
    b.buy("S", 1, 10_000, D(2026, 5, 1)); b.sell("S", 1, 14_000, D(2026, 7, 1))
    assert b.settle(2026) == pytest.approx(0.20 * 4_000 * CESS)   # 9 years on: gone


def test_historical_rates_follow_the_sale_date():
    b = book(regime="historical", use_exemption=False)
    b.buy("A", 1, 1_000, D(2024, 5, 1)); b.sell("A", 1, 2_000, D(2024, 7, 1))   # 15%
    b.buy("B", 1, 1_000, D(2024, 5, 1)); b.sell("B", 1, 2_000, D(2024, 8, 1))   # 20%
    assert b.settle(2024) == pytest.approx((150 + 200) * CESS)
    old = book(regime="historical")
    old.buy("A", 1, 1_000, D(2016, 5, 1)); old.sell("A", 1, 500_000, D(2018, 2, 1))
    assert old.settle(2017) == 0.0                # long-term gains exempt before Apr 2018


def test_due_settles_only_finished_years_once():
    b = book(use_exemption=False)
    b.buy("A", 1, 1_000, D(2025, 5, 1)); b.sell("A", 1, 2_000, D(2025, 6, 1))
    assert b.due(D(2026, 3, 31)) == 0.0
    assert b.due(D(2026, 4, 2)) == pytest.approx(200 * CESS)
    assert b.due(D(2026, 5, 2)) == 0.0


def test_liquidation_tax_does_not_touch_the_book():
    b = book(use_exemption=False)
    b.buy("A", 10, 1_000, D(2025, 5, 1))
    assert b.liquidation_tax({"A": 150}, D(2025, 9, 1)) == pytest.approx(0.2 * 500 * CESS)
    assert sum(l[0] for l in b.lots["A"]) == 10 and not b.gains
