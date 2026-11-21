"""Portfolio › Tax & transition: tax lots from your tradebooks, this year's
gains, and when to make each trim of the Core–Satellite plan.

Estimates from config/tax.yaml, not tax advice. Nothing is written to your
holdings file and no order is placed.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from book import portfolio as pf
from book import risk_view as rv
from book import tax_view as tv
from book import tradebook as tb
from strategy import capgains as cg


@st.cache_data(ttl=300, show_spinner="Rebuilding tax lots…")
def _gather():
    g = tv.gather()
    if "error" in g:
        return g, None
    return g, rv.gather()


def _r(x) -> str:
    return "—" if x is None else f"₹{x:,.0f}"


def render() -> None:
    st.markdown("## Tax & transition")
    cfg = cg.load_config()
    r = cg.regime(pd.Timestamp.today().date(), cfg)
    st.caption(f"Estimates, not tax advice. Listed equity: short-term {r['stcg_pct']:g}%, "
               f"long-term {r['ltcg_pct']:g}% above ₹{r['ltcg_exemption']:,} a year, plus "
               f"{cfg['cess_pct']:g}% cess (rates checked against Finance Act 2026; "
               "config/tax.yaml). Demat shares are sold first-in first-out, so the only "
               "choice is when.")
    g, rg = _gather()
    if "error" in g:
        st.info(g["error"])
        return
    _tradebooks(g)
    st.divider()
    _this_year(g)
    st.divider()
    _transition(g, rg)
    st.divider()
    _harvest(g)
    st.divider()
    _lots(g)


# ------------------------------------------------------------------ files

def _tradebooks(g: dict) -> None:
    st.markdown("**Tradebooks**")
    files = [f for f in g["files"] if "error" not in f]
    if files:
        first = min(f["from"] for f in files if f["from"])
        st.caption(f"{len(g['trades'])} trades from {first} to "
                   f"{max(f['to'] for f in files if f['to'])}, in {len(files)} file(s) in "
                   "config/tradebooks/ (stays on your computer, never committed). Shares "
                   f"held before {first} are an opening lot, reconciled against your average "
                   "cost and the broker's long/short split.")
    else:
        st.caption("No tradebooks yet: every holding uses the broker's long/short split. "
                   "Add Console › Reports › Tradebook (Equity) files for dated lots.")
    for f in g["files"]:
        if "error" in f:
            st.warning(f"{f['file']}: {f['error']}")
    with st.expander("Add tradebook files"):
        ups = st.file_uploader("Zerodha tradebook (.xlsx or .csv), any date ranges; "
                               "overlaps are merged by trade ID", type=["xlsx", "csv"],
                               accept_multiple_files=True, key="tb_upload")
        if ups and st.button("Save to config/tradebooks/"):
            for u in ups:
                try:
                    st.success(f"Saved {tb.save_upload(u.name, u.getvalue())}")
                except Exception as e:                 # noqa: BLE001
                    st.error(f"{u.name}: {e}")
            _gather.clear()


# ------------------------------------------------------------------ this year

def _this_year(g: dict) -> None:
    c = tv.current_fy(g)
    st.markdown(f"**Financial year {c['fy']} so far**")
    m = st.columns(4)
    m[0].metric("Short-term gains, net", _r(c["st"]))
    m[1].metric("Long-term gains, net", _r(c["lt"]))
    m[2].metric("Estimated tax so far", _r(c["tax"]),
                help="After set-off and the long-term exemption, with cess")
    m[3].metric("Long-term exemption left", _r(c["exemption_left"]))
    if c["overridden"]:
        st.caption("From the figures you entered in config/tax.yaml (realised_override).")
    elif c["unknown"]:
        v = sum(u["value"] for u in c["unknown"])
        names = sorted({u["symbol"] for u in c["unknown"]})
        st.warning(f"Incomplete: {len(names)} sale(s) this year worth {_r(v)} "
                   f"({', '.join(names)}) have no purchase in your tradebooks (an IPO "
                   "allotment, or bought before them). Their gains aren't counted above, "
                   "and a long-term gain there uses part of the exemption. Enter the year's "
                   "totals from Console › Reports › Tax P&L under realised_override in "
                   "config/tax.yaml, or add an older tradebook.")


# ------------------------------------------------------------------ transition

def _transition(g: dict, rg: dict) -> None:
    st.markdown("**Transition: when to make each trim**")
    st.caption("Trims come from the Core–Satellite plan (Risk & plan) run over each horizon: "
               "only to bring a holding under its cap or a sector under its limit. Each "
               "trim is timed to the cheapest date in the horizon: now, when the shares "
               "FIFO delivers turn long-term, or from 1 April for a new year's exemption.")
    blank = sum(1 for i in rg["items"] if not i["position"].bucket)
    c = st.columns([1, 2])
    monthly = c[0].number_input("New money a month, ₹ (optional)", min_value=0, value=0,
                                step=5000, key="tx_monthly",
                                help="None assumed unless you enter it. New money reduces "
                                     "the trims needed.")
    opts = ["Your bucket labels", "Fill blank buckets with the scorecard's suggestion"]
    pick = c[1].radio("Roles", opts, index=1 if blank > len(rg["items"]) / 2 else 0,
                      key="tx_roles", help="A what-if: your holdings file is not changed.")
    use_sugg = pick == opts[1]
    scheds = tv.schedules(g, rg, float(monthly), use_sugg)
    rows = []
    for x in scheds:
        s = x["schedule"]
        rows.append({"Horizon": f"{x['horizon']} months", "Trims": len(s.rows),
                     "Sold ₹": round(sum(r.value for r in s.rows)),
                     "Tax, timed ₹": round(s.tax_plan), "Tax, all now ₹": round(s.tax_all_now),
                     "Saved ₹": round(s.saved), "Costs ₹": round(s.costs),
                     "Core after %": round(x["core_after"], 1)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("Tax is what the trims add to this year's (and next year's) bill, after what "
               "you've already realised. Costs: STT, exchange, SEBI, DP and GST "
               "(config/tax.yaml). Prices held at today's.")
    if not any(x["schedule"].rows for x in scheds):
        st.info("No trims: " + ("with blank buckets nothing is trimmed (set bucket = core "
                                "/ satellite, or try the suggested-role what-if)."
                                if not use_sugg else "every holding is within its cap and "
                                "every sector within its limit."))
    else:
        h = st.selectbox("Detail for", [f"{x['horizon']} months" for x in scheds], index=2,
                         key="tx_h")
        x = scheds[[f"{y['horizon']} months" for y in scheds].index(h)]
        st.dataframe(pd.DataFrame([{
            "Holding": r.symbol, "Shares": int(r.qty), "When": r.date.isoformat(),
            "Value ₹": round(r.value), "Short-term gain ₹": round(r.st_gain),
            "Long-term gain ₹": round(r.lt_gain), "Tax if sold now ₹": round(r.tax_if_now),
            "Costs ₹": round(r.costs), "Why then": r.why, "Why trim": r.reason}
            for r in x["schedule"].rows]), hide_index=True, width="stretch")
        st.dataframe(pd.DataFrame([{
            "Year": y["fy"], "Short-term, net ₹": round(y["st_gain"]),
            "Long-term, net ₹": round(y["lt_gain"]), "Exemption used ₹": round(y["exemption_used"]),
            "Added tax ₹": round(y["tax"]), "Loss carried forward ₹":
                round(y["carry_st_loss"] + y["carry_lt_loss"]) or None}
            for y in x["schedule"].by_fy]), hide_index=True, width="stretch")
    if monthly > 0:
        st.caption("Sensitivity to the monthly amount, 12 months:")
        sens = []
        for k in (0.5, 1, 2):
            trims, plan = tv.trims_for(rg, monthly * k, 12, use_sugg, g["prices"])
            fy = cg.fy_of(g["today"])
            s = tv.tr.schedule(trims, {a: b.lots for a, b in g["books"].items()}, g["today"],
                               12, {fy: g["realised"].get(fy, (0.0, 0.0))}, g["cfg"])
            b = {y["role"]: y for y in plan.buckets}
            sens.append({"A month ₹": round(monthly * k), "Trims": len(s.rows),
                         "Sold ₹": round(sum(r.value for r in s.rows)),
                         "Tax ₹": round(s.tax_plan), "Core after %":
                             round(b["core"]["after_pct"], 1)})
        st.dataframe(pd.DataFrame(sens), hide_index=True, width="stretch")


# ------------------------------------------------------------------ harvest

def _harvest(g: dict) -> None:
    h = tv.harvest(g)
    st.markdown("**Options this financial year** (not part of the plan)")
    c = st.columns(2)
    with c[0]:
        st.caption(f"Book long-term gains up to the unused exemption ({_r(h['exemption_left'])}) "
                   "by selling and buying back: no tax now, and a higher cost for the future. "
                   "The shares bought back start a new 12 months.")
        if h["gains"]:
            st.dataframe(pd.DataFrame([{
                "Holding": x["symbol"], "Shares": x["qty"], "Gain booked ₹": round(x["gain"]),
                "Future tax saved ₹": round(x["future_tax_saved"]),
                "Round-trip cost ₹": round(x["round_trip_cost"])} for x in h["gains"]]),
                hide_index=True, width="stretch")
            st.caption("Check first: this year's long-term gains from sales the tradebooks "
                       "can't cost (above) use the same exemption.")
        else:
            st.caption("Nothing to book.")
    with c[1]:
        st.caption("Unrealised losses. A short-term loss offsets any gain; a long-term loss "
                   "only long-term gains. Unused losses carry forward 8 years if you file "
                   "on time. FIFO: the oldest shares are sold first.")
        if h["losses"]:
            st.dataframe(pd.DataFrame([{
                "Holding": x["symbol"], "Short-term loss ₹": round(x["st_loss"]) or None,
                "Long-term loss ₹": round(x["lt_loss"]) or None} for x in h["losses"]]),
                hide_index=True, width="stretch")
    st.caption("Selling and buying back the same day can be questioned as a sham "
               "transaction; many investors leave a gap. Check with your tax adviser.")


# ------------------------------------------------------------------ lots

def _lots(g: dict) -> None:
    with st.expander("Tax lots by holding"):
        raw = {h["tradingsymbol"]: h for h in pf.read_csv_holdings()}
        label = {"matched": "tradebook", "opening": "tradebook + opening",
                 "no_trades": "broker split", "fallback": "broker split (mismatch)"}
        held = sorted(s for s in g["books"] if s in g["held"])
        st.dataframe(pd.DataFrame([{
            "Holding": s, "Source": label[g["books"][s].status],
            "Shares": g["books"][s].qty, "Long-term": g["books"][s].lt_qty_today,
            "Broker long-term": tv._f(raw.get(s, {}).get("lt_quantity")),
            "Notes": "; ".join(g["books"][s].notes)} for s in held]),
            hide_index=True, width="stretch")
        pick = st.selectbox("Lots for", held, key="tx_lots")
        days = g["cfg"]["long_term_after_days"]
        st.dataframe(pd.DataFrame([{
            "Bought": l.date.isoformat() if l.date else "before tradebooks",
            "Kind": l.source, "Shares": l.qty, "Cost ₹": round(l.cost, 2),
            "Long-term": "yes" if l.is_long(g["today"], days) else "no",
            "Long-term from": (l.turns_long(days).isoformat() if l.date and
                               not l.is_long(g["today"], days) else "")}
            for l in g["books"][pick].lots]), hide_index=True, width="stretch")
