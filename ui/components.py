"""Shared rendering pieces for the dashboard."""
from __future__ import annotations

import streamlit as st

# Action -> (letter, background, text colour, tooltip)
BADGES = {
    "BUY":  ("B", "#12854a", "#ffffff", "Buy"),
    "ADD":  ("B", "#12854a", "#ffffff", "Add to the position"),
    "EXIT": ("S", "#c0392b", "#ffffff", "Sell the whole holding -- thesis broken"),
    "TRIM": ("T", "#d98324", "#ffffff", "Trim part of the holding -- sizing only"),
    "HOLD": ("H", "#6b7280", "#ffffff", "No action"),
}


# Urgency changes what the same letter means, so it changes the tooltip too.
# A ring alone told the reader that two badges differed without telling them
# how -- which is worse than no distinction at all.
URGENT_TIPS = {
    "EXIT": "SELL NOW -- a hard red flag (interest cover, pledge, or similar). "
            "Not a judgement call.",
    "TRIM": "Trim now -- the position breached a risk limit.",
}
CALM_TIPS = {
    "EXIT": "Sell the whole holding -- quality or trend no longer justifies "
            "owning it. Timing is your call.",
    "TRIM": "Trim part of the holding -- sizing only, you keep the rest.",
}


def badge(action: str, urgent: bool = False) -> str:
    """A small coloured circle: B green, T amber, S red, H grey.

    Trim and exit get different letters as well as different colours. They are
    different decisions -- trim is a sizing correction on a holding you keep,
    exit is a broken thesis -- and two amber-vs-red "S" badges made that
    distinction depend on noticing a shade.
    """
    letter, bg, fg, tip = BADGES.get(action, ("?", "#6b7280", "#fff", action))
    tip = (URGENT_TIPS if urgent else CALM_TIPS).get(action, tip)
    ring = "box-shadow:0 0 0 2px #fde047;" if urgent else ""
    mark = ("<span style='position:absolute;top:-4px;right:-5px;font-size:11px;"
            "color:#fde047;font-weight:800;'>!</span>") if urgent else ""
    return (f'<span title="{tip}" style="position:relative;display:inline-flex;'
            f'align-items:center;justify-content:center;width:22px;height:22px;'
            f'border-radius:50%;background:{bg};color:{fg};font-weight:700;'
            f'font-size:12px;font-family:system-ui;{ring}">{letter}{mark}</span>')


def pill(text: str, tone: str = "neutral") -> str:
    tones = {"neutral": ("#e5e7eb", "#374151"), "good": ("#d1fae5", "#065f46"),
             "warn": ("#fef3c7", "#92400e"), "bad": ("#fee2e2", "#991b1b"),
             "info": ("#dbeafe", "#1e40af")}
    bg, fg = tones.get(tone, tones["neutral"])
    return (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
            f'border-radius:10px;font-size:11px;font-weight:600;'
            f'white-space:nowrap;">{text}</span>')


def money(v) -> str:
    if v is None:
        return "--"
    return f"₹{v:,.0f}"


def pct(v, decimals: int = 1, signed: bool = False) -> str:
    if v is None:
        return "--"
    return f"{v:+.{decimals}f}%" if signed else f"{v:.{decimals}f}%"


def quality_tone(score) -> str:
    if score is None:
        return "neutral"
    return "good" if score >= 70 else "warn" if score >= 50 else "bad"


def trade_plan(price, stop, t1, t2, extra: str = "", label: str = "now",
               t1_hit: bool = False, t2_hit: bool = False) -> str:
    """Price / stop / targets in one line.

    A target below the current price is never shown as though it were still
    ahead. Entry targets on a position that has already run past them are
    marked achieved; anything else would read as "sell lower than you buy".
    """
    bits = [f"<b>{money(price)}</b> {label}"]
    if stop:
        risk = 100 * (stop / price - 1) if price else 0
        bits.append(f"stop <b>{money(stop)}</b> ({risk:+.1f}%)")
    for name, target, hit in (("T1", t1, t1_hit), ("T2", t2, t2_hit)):
        if not target:
            continue
        move = 100 * (target / price - 1) if price else 0
        if hit or move < 0:
            bits.append(f'<span style="color:#12854a;">{name} '
                        f'<b>{money(target)}</b> ✓ passed</span>')
        else:
            bits.append(f"{name} <b>{money(target)}</b> ({move:+.1f}%)")
    line = " &nbsp;·&nbsp; ".join(bits)
    if extra:
        line += f'<div style="color:#6b7280;font-size:12px;margin-top:3px;">{extra}</div>'
    return f'<div style="font-size:13px;line-height:1.6;">{line}</div>'


def buy_plan(price, stop, t1, t2, extra: str = "") -> str:
    """The plan for buying at TODAY's price -- the only frame that makes sense
    for an add. Entry targets from an older, cheaper purchase do not apply."""
    return trade_plan(price, stop, t1, t2, extra, label="to buy")


def trend_note(row) -> str:
    """Plain-language read of where price sits against its moving averages."""
    px, s20, s50, s200 = (row.get("price"), row.get("sma20"),
                          row.get("sma50"), row.get("sma200"))
    parts = []
    if px and s50 and s50 == s50:
        parts.append(f"{'above' if px > s50 else 'below'} 50 DMA ({s50:,.0f})")
    if px and s200 and s200 == s200:
        parts.append(f"{'above' if px > s200 else 'below'} 200 DMA ({s200:,.0f})")
    rsi = row.get("rsi")
    if rsi is not None and rsi == rsi:
        state = ("overbought" if rsi > 70 else
                 "oversold" if rsi < 30 else "neutral")
        parts.append(f"RSI {rsi:.0f} ({state})")
    return " · ".join(parts)


def header_metric(label: str, value: str, help_text: str = "") -> None:
    st.metric(label, value, help=help_text or None)


def legend() -> None:
    """Every badge variant shown explicitly.

    The urgent ring used to be described in words while the calm and urgent
    badges were never shown side by side -- so two visibly different S badges
    appeared in the same list with no way to tell what separated them.
    """
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:18px;align-items:center;"
        f"font-size:13px;'>"
        f"<span>{badge('BUY')} &nbsp;buy or add</span>"
        f"<span>{badge('TRIM')} &nbsp;trim — too big, not bad (you keep it)</span>"
        f"<span>{badge('EXIT')} &nbsp;exit — thesis broken, sell it all</span>"
        f"<span>{badge('EXIT', urgent=True)} &nbsp;<b>same, but urgent</b> — a hard "
        f"red flag, not a judgement call</span>"
        f"<span>{badge('HOLD')} &nbsp;hold</span>"
        f"</div>", unsafe_allow_html=True)
