"""Indian delivery-equity transaction costs.

Costs are the difference between a strategy that looks good and one that is
good. A rule that trades often can show a healthy gross return and still lose
money net -- STT alone is 0.1% on both legs, before slippage. Defaults follow
Zerodha's delivery schedule; adjust in config if your broker differs.
"""
from __future__ import annotations

from dataclasses import dataclass

DP_CHARGE_PER_SELL = 15.93        # per scrip per day, incl. GST


@dataclass
class CostModel:
    brokerage_pct: float = 0.0        # delivery equity is typically free
    brokerage_cap: float = 20.0
    stt_pct: float = 0.1             # both legs, delivery
    exchange_pct: float = 0.00297    # NSE
    sebi_pct: float = 0.0001
    stamp_duty_pct: float = 0.015    # buy side only
    gst_pct: float = 18.0
    dp_charge: float = DP_CHARGE_PER_SELL
    slippage_pct: float = 0.05

    def _brokerage(self, turnover: float) -> float:
        return min(turnover * self.brokerage_pct / 100, self.brokerage_cap)

    def buy_cost(self, price: float, qty: float) -> float:
        turnover = price * qty
        brokerage = self._brokerage(turnover)
        stt = turnover * self.stt_pct / 100
        exch = turnover * self.exchange_pct / 100
        sebi = turnover * self.sebi_pct / 100
        stamp = turnover * self.stamp_duty_pct / 100
        gst = (brokerage + exch + sebi) * self.gst_pct / 100
        return brokerage + stt + exch + sebi + stamp + gst

    def sell_cost(self, price: float, qty: float) -> float:
        turnover = price * qty
        brokerage = self._brokerage(turnover)
        stt = turnover * self.stt_pct / 100
        exch = turnover * self.exchange_pct / 100
        sebi = turnover * self.sebi_pct / 100
        gst = (brokerage + exch + sebi) * self.gst_pct / 100
        return brokerage + stt + exch + sebi + gst + self.dp_charge

    def fill_price(self, price: float, side: str) -> float:
        """Slippage: you buy a little above and sell a little below the close."""
        s = self.slippage_pct / 100
        return price * (1 + s) if side == "buy" else price * (1 - s)

    def round_trip_pct(self, price: float, qty: float) -> float:
        """Total cost of a round trip as a percent of turnover -- the hurdle
        every trade must clear before it makes you anything."""
        turnover = price * qty
        if turnover <= 0:
            return 0.0
        total = self.buy_cost(price, qty) + self.sell_cost(price, qty)
        total += turnover * 2 * self.slippage_pct / 100
        return 100 * total / turnover
