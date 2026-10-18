"""Shared rendering pieces for the dashboard."""
from __future__ import annotations

import datetime as dt

import streamlit as st

# Action -> (word, background, text colour, tooltip). One set of words and
# colours for stocks and mutual funds alike, so a Trim reads the same on the
# Today page whichever book it came from.
BADGES = {
    "BUY":  ("Buy", "#E2F1E6", "#1D6436", "Buy"),
    "ADD":  ("Add", "#E2F1E6", "#1D6436", "Add to the position"),
    "EXIT": ("Exit", "#FBE7E4", "#9F1D14", "Sell the whole holding -- thesis broken"),
    "TRIM": ("Trim", "#FBEBD3", "#7A4300", "Trim part of the holding -- sizing only"),
    "HOLD": ("Hold", "#ECEBE7", "#4A4945", "No action"),
    "WATCH": ("Watch", "#E4EBF6", "#314A72",
              "Worth buying, but this plan cannot fund it"),
    "AVOID": ("Avoid", "#FBE7E4", "#9F1D14", "Not held; weak against its category"),
    "NEUTRAL": ("Neutral", "#ECEBE7", "#4A4945", "Not held; nothing for or against"),
    "NOT JUDGED": ("Not judged", "#ECEBE7", "#4A4945",
                   "Not in a category the engine ranks"),
    "STOP": ("Stop", "#FBE7E4", "#9F1D14", "The stop was reached"),
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
    """The verdict as a word on a tinted pill: Exit red, Trim amber, Add green,
    Watch blue, Hold grey.

    Trim and exit are different words as well as different colours. They are
    different decisions -- trim is a sizing correction on a holding you keep,
    exit is a broken thesis -- and telling them apart must not depend on
    noticing a shade. Urgent adds a solid red ring and a "!".
    """
    word, bg, fg, tip = BADGES.get(action, (action.title(), "#ECEBE7", "#4A4945", action))
    tip = (URGENT_TIPS if urgent else CALM_TIPS).get(action, tip)
    ring = "box-shadow:0 0 0 2px #9F1D14;" if urgent else ""
    mark = "<span style='margin-left:4px;'>!</span>" if urgent else ""
    return (f'<span title="{tip}" style="display:inline-block;min-width:52px;'
            f'text-align:center;padding:2px 8px;border-radius:6px;background:{bg};'
            f'color:{fg};font-weight:600;font-size:12px;white-space:nowrap;'
            f'{ring}">{word}{mark}</span>')


# Level -> (Streamlit badge colour, label). Streamlit's own colours follow
# the light or dark theme; fixed hex values went unreadable in dark mode.
EVIDENCE = {
    "tested": ("green", "Evidence: tested"),
    "weak": ("orange", "Evidence: weak"),
    "none": ("gray", "Evidence: no edge"),
    "experimental": ("gray", "Experimental"),
    "info": ("blue", "Describes, does not predict"),
}


def muted(text: str) -> str:
    """Secondary text in markdown, in the theme's grey."""
    return ":gray[" + str(text).replace("[", "(").replace("]", ")") + "]"


def evidence(level: str, text: str) -> None:
    """One line under a page title saying how far its signals were borne out
    in a backtest, so the strength of a verdict is visible before reading it."""
    colour, label = EVIDENCE[level]
    with st.container(border=True):
        st.markdown(f":{colour}-badge[{label}] &nbsp; {text}")


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


# Status of a target against how long it normally takes. Semantic, not accent:
# these say "how is this going", not "look here".
HORIZON_TONE = {"early": "info", "due": "neutral", "late": "warn",
                "stalled": "bad", "fast": "good", "normal": "good",
                "slow": "warn"}


def _short(iso: str) -> str:
    try:
        return dt.date.fromisoformat(iso).strftime("%d-%b")
    except ValueError:
        return iso


def outcome_status(r: dict) -> tuple[str, str]:
    """Status text and tone: how the entry ended, else where it stands
    against the usual time to T1."""
    if r.get("stopped_on"):
        if r.get("t1_hit_on"):
            return f"T1, then out at entry {_short(r['stopped_on'])}", "warn"
        return f"stopped {_short(r['stopped_on'])}", "bad"
    if r.get("t2_hit_on"):
        return f"T2 hit {_short(r['t2_hit_on'])}", "good"
    if r.get("t1_hit_on"):
        return f"T1 hit {_short(r['t1_hit_on'])}", "good"
    state = r.get("t1_state") or ""
    return state, HORIZON_TONE.get(state, "neutral")


def watching_table(rows: list[dict], horizon_sessions: dict | None = None) -> str:
    """The observation list as plain HTML.

    Deliberately not st.dataframe: Streamlit's virtualised grid computes a
    width of zero when it renders inside a tab that is not the active one, so
    the columns exist but have nowhere to draw. This list is a handful of names
    by design, so a static table is both more robust and easier to read.
    """
    # The typical session count lives in the header, where it is stated once,
    # rather than repeated as prose under every row.
    t1_n = horizon_sessions.get("t1", "") if horizon_sessions else ""
    t2_n = horizon_sessions.get("t2", "") if horizon_sessions else ""
    head = ("<tr>" + "".join(
        f'<th style="text-align:{a};padding:6px 10px;border-bottom:1px solid '
        f'var(--secondary-background-color,#3336);font-weight:600;'
        f'font-size:12px;letter-spacing:.03em;text-transform:uppercase;'
        f'opacity:.7;white-space:nowrap;">{h}</th>'
        for h, a in (("Stock", "left"), ("Flagged", "left"), ("Then", "right"),
                     ("Now", "right"), ("Move", "right"), ("Stop", "right"),
                     (f"T1:{t1_n}" if t1_n else "T1", "right"),
                     (f"T2:{t2_n}" if t2_n else "T2", "right"),
                     ("Status", "left"), ("Days", "right")))
        + "</tr>")

    body = []
    for r in rows:
        move = r.get("move_pct")
        move_col = ("#12854a" if (move or 0) > 0 else
                    "#c0392b" if (move or 0) < 0 else "inherit")
        state, tone = outcome_status(r)
        cells = [
            f'<b>{r["symbol"]}</b>'
            + (" &nbsp;" + pill("swing", "info") if r.get("source") == "swing" else ""),
            r.get("baseline_date") or "--",
            money(r.get("baseline_price")),
            money(r.get("price")),
            f'<span style="color:{move_col}">'
            f'{pct(move, signed=True) if move is not None else "--"}</span>',
            money(r.get("stop")), money(r.get("t1")), money(r.get("t2")),
            pill(state, tone) if state else "",
            str(r.get("days") if r.get("days") is not None else "--"),
        ]
        aligns = ["left", "left", "right", "right", "right", "right", "right",
                  "right", "left", "right"]
        body.append("<tr>" + "".join(
            f'<td style="text-align:{a};padding:7px 10px;border-bottom:1px '
            f'solid var(--secondary-background-color,#3334);font-size:13px;'
            f'font-variant-numeric:tabular-nums;white-space:nowrap;">{c}</td>'
            for c, a in zip(cells, aligns)) + "</tr>")
    return (f'<div style="overflow-x:auto;"><table style="width:100%;'
            f'border-collapse:collapse;">{head}{"".join(body)}</table></div>')
