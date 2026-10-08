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


@dataclass
class OptionCostModel:
    """Index/stock option charges, per Zerodha's F&O schedule (rates as of
    2025-26 -- check zerodha.com/charges if they change).

    Premium is what is traded, so most charges are a % of premium. Two that
    are easy to miss: STT on the SELL side was raised to 0.1% of premium from
    October 2024, and an in-the-money option held to expiry and exercised
    pays STT of 0.125% of its intrinsic value -- on a cheap deep-ITM option
    that can cost more than the premium-based charges combined.

    Slippage: history only has closing prices, not quotes. A fill is assumed
    `slippage_pct` of premium worse than the close on each side, never less
    than one tick (0.05) -- liquid index options trade tighter, thin stock
    options wider, so this is a middle assumption.
    """
    brokerage_per_order: float = 20.0
    stt_sell_pct: float = 0.1          # of premium, sell side
    stt_exercise_pct: float = 0.125    # of intrinsic, ITM option exercised at expiry
    exchange_pct: float = 0.03503      # NSE, of premium
    sebi_pct: float = 0.0001
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    slippage_pct: float = 1.0
    tick: float = 0.05

    def _fees(self, turnover: float) -> float:
        exch = turnover * self.exchange_pct / 100
        sebi = turnover * self.sebi_pct / 100
        gst = (self.brokerage_per_order + exch + sebi) * self.gst_pct / 100
        return self.brokerage_per_order + exch + sebi + gst

    def buy_cost(self, premium: float, qty: float) -> float:
        t = premium * qty
        return self._fees(t) + t * self.stamp_buy_pct / 100

    def sell_cost(self, premium: float, qty: float) -> float:
        t = premium * qty
        return self._fees(t) + t * self.stt_sell_pct / 100

    def exercise_cost(self, intrinsic: float, qty: float) -> float:
        """Held to expiry in the money: exercised automatically. No brokerage
        on a cash-settled index exercise; STT on the intrinsic value."""
        return max(intrinsic, 0.0) * qty * self.stt_exercise_pct / 100

    def fill(self, premium: float, side: str) -> float:
        """Buy a little above the close, sell a little below."""
        s = max(premium * self.slippage_pct / 100, self.tick)
        return premium + s if side == "buy" else max(premium - s, 0.0)
