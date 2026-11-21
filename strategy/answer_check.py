"""Check a research answer against the pack it claims to cite.

The spec's rules for the assistant (§14.1), made mechanical where they can be:

  invent figures         every evidence path must exist in the pack and hold
                         the value cited (to its rounding); a derived figure is
                         recomputed; every number in a non-opinion claim must
                         match one of its evidence values (as %, lakh or crore)
  opinion as fact        an 'opinion' finding may carry no figures
  old data as current    a cited figure marked stale must be listed in
                         stale_data_used
  ignore governance      a cited holding with governance flags must have them
                         in governance_flags
  certainty / orders     phrases claiming certain returns or telling you to
                         buy or sell are rejected
  insufficient data      status INSUFFICIENT DATA must say what is missing

Errors fail the check; warnings are shown beside the answer. Pure: no I/O.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "config" / "research_answer.schema.json"

CERTAINTY = [r"\bwill (rise|fall|outperform|underperform|double|go up|go down|deliver)\b",
             r"\bguarantee", r"\bcertain(ly)? to\b", r"\bsure to\b", r"\btarget price\b",
             r"\brisk[- ]free\b"]
ORDERS = [r"\byou should (buy|sell|exit|add|trim)\b", r"\b(buy|sell) now\b",
          r"\bplace (an|the) order\b", r"\bstrong buy\b"]
RANKING = [r"\b(best|worst|top|strongest|weakest)\b[^.]{0,30}\b(in|of) (the|your) (book|portfolio)\b",
           r"\branked?\b"]
NUM = re.compile(r"(?<![\w/])[-−]?₹?\s?\d[\d,]*(?:\.\d+)?\s?(?:%|x|×|lakh|crore|cr|pts)?",
                 re.IGNORECASE)


def resolve(pack, path: str):
    """JSON pointer into the pack. Raises KeyError if absent."""
    node = pack
    for part in path.strip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        elif isinstance(node, dict):
            if part not in node:
                raise KeyError(path)
            node = node[part]
        else:
            raise KeyError(path)
    return node


def _decimals(x) -> int:
    s = repr(x) if isinstance(x, float) else str(x)
    return len(s.split(".")[1]) if "." in s else 0


def same(actual, cited) -> bool:
    if isinstance(actual, bool) or isinstance(cited, bool):
        return actual == cited
    if isinstance(actual, (int, float)) and isinstance(cited, (int, float)):
        if actual is None or (isinstance(actual, float) and math.isnan(actual)):
            return False
        if math.isclose(actual, cited, rel_tol=5e-3, abs_tol=1e-9):
            return True
        return round(actual, _decimals(cited)) == cited
    if isinstance(actual, str) and isinstance(cited, str):
        return actual.strip().lower() == cited.strip().lower()
    return actual == cited


def derive(op: str, vals: list) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if op == "count":
        return float(len(vals[0]) if len(vals) == 1 and isinstance(vals[0], (list, dict))
                     else len(vals))
    if len(nums) != len(vals) or not nums:
        return None
    if op == "sum":
        return sum(nums)
    if op == "mean":
        return sum(nums) / len(nums)
    if op == "max":
        return max(nums)
    if op == "min":
        return min(nums)
    if len(nums) != 2:
        return None
    a, b = nums
    if op == "difference":
        return a - b
    if op == "ratio":
        return a / b if b else None
    if op == "pct_change":
        return (a / b - 1) * 100 if b else None
    return None


def numbers(text: str) -> list[float]:
    """Figures in a sentence, as plain numbers. Skips years, small counts
    (months, years, holdings ≤ 12), ratios like 70:30 and horizons like 3/6/12."""
    text = re.sub(r"\b\d+\s*[:/]\s*\d+(?:\s*/\s*\d+)*\b", " ", text)   # 70:30, 3/6/12
    text = re.sub(r"\bFY\s?\d{2,4}(?:-\d{2})?\b", " ", text, flags=re.I)
    text = re.sub(r"\b(19|20)\d{2}-\d{2}(-\d{2})?\b", " ", text)       # dates, FY 2026-27
    out = []
    for m in NUM.finditer(text):
        raw = m.group(0)
        s = re.sub(r"[^\d.\-−]", "", raw.replace("−", "-"))
        try:
            v = float(s)
        except ValueError:
            continue
        unit = raw.lower()
        if "lakh" in unit:
            v *= 1e5
        elif "crore" in unit or unit.rstrip().endswith("cr"):
            v *= 1e7
        if re.fullmatch(r"-?\d+", s) and ("%" not in raw and "₹" not in raw):
            if 1900 <= abs(v) <= 2100 or abs(v) <= 12:
                continue
        out.append(v)
    return out


def _matches(v: float, cands: list[float]) -> bool:
    for c in cands:
        for scale in (1, 100, 0.01, 1e-5, 1e-7, 1e5, 1e7):
            x = c * scale
            for w in (x, abs(x)):
                if math.isclose(abs(v), abs(w), rel_tol=0.01, abs_tol=0.051):
                    return True
                # a cited figure may be rounded to a whole number or one decimal
                if abs(abs(v) - round(abs(w))) < 1e-9 or abs(abs(v) - round(abs(w), 1)) < 1e-9:
                    return True
    return False


def _freshness(pack, path: str) -> str | None:
    parts = path.strip("/").split("/")
    for i in range(len(parts), 0, -1):
        try:
            node = resolve(pack, "/" + "/".join(parts[:i]))
        except (KeyError, IndexError, ValueError):
            continue
        if isinstance(node, dict) and "freshness" in node:
            return node["freshness"]
    return None


def check(answer: dict, pack: dict, schema: dict | None = None) -> dict:
    errors, warnings = [], []
    try:
        import jsonschema
        schema = schema or json.loads(SCHEMA.read_text())
        for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(answer),
                        key=lambda e: list(e.path)):
            errors.append("schema: " + ("/".join(map(str, e.path)) or "answer") + ": "
                          + e.message)
    except ImportError:
        warnings.append("jsonschema not installed: structure not validated")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings, "checked": 0}

    if answer["pack_generated_at"] != pack.get("generated_at"):
        errors.append(f"cites the pack of {answer['pack_generated_at']}, but the pack is "
                      f"now {pack.get('generated_at')}: re-run against the current pack")
    if answer["status"] == "INSUFFICIENT DATA" and not answer.get("missing"):
        errors.append("INSUFFICIENT DATA must list what is missing")

    stale_ok = answer.get("stale_data_used", [])
    gov_named = {(g["symbol"].upper()) for g in answer.get("governance_flags", [])}
    symbols, checked = set(), 0
    every_value: list[float] = []
    for i, f in enumerate(answer["findings"], 1):
        vals: list[float] = []
        for ev in f["evidence"]:
            checked += 1
            if "path" in ev:
                p = ev["path"]
                try:
                    actual = resolve(pack, p)
                except (KeyError, IndexError, ValueError):
                    errors.append(f"finding {i}: {p} is not in the pack")
                    continue
                if not same(actual, ev["value"]):
                    errors.append(f"finding {i}: {p} is {actual!r} in the pack, cited as "
                                  f"{ev['value']!r}")
                if _freshness(pack, p) == "stale" and not any(p.startswith(s)
                                                              for s in stale_ok):
                    errors.append(f"finding {i}: {p} is stale in the pack: list it in "
                                  "stale_data_used and say so")
                m = re.match(r"^/holdings/([^/]+)", p)
                if m:
                    symbols.add(m.group(1))
                if isinstance(ev["value"], (int, float)) and not isinstance(ev["value"], bool):
                    vals.append(float(ev["value"]))
            else:
                d = ev["derived"]
                try:
                    src = [resolve(pack, p) for p in d["of"]]
                except (KeyError, IndexError, ValueError) as e:
                    errors.append(f"finding {i}: derived from {e} which is not in the pack")
                    continue
                got = derive(d["op"], src)
                if got is None or not same(got, ev["value"]):
                    errors.append(f"finding {i}: {d['op']} of {len(src)} value(s) is "
                                  f"{got!r}, cited as {ev['value']!r}")
                for p in d["of"]:
                    m = re.match(r"^/holdings/([^/]+)", p)
                    if m:
                        symbols.add(m.group(1))
                vals.append(float(ev["value"]))
        every_value += vals
        nums = numbers(f["claim"])
        if f["kind"] == "opinion":
            if nums:
                errors.append(f"finding {i}: an opinion carries no figures ({nums[0]:g}); "
                              "make it a cited finding")
        else:
            if not f["evidence"]:
                errors.append(f"finding {i}: a {f['kind']} finding needs evidence")
            for v in nums:
                if not _matches(v, vals):
                    errors.append(f"finding {i}: the figure {v:g} in the claim matches none "
                                  "of its evidence")
    for v in numbers(answer["summary"]):
        if not _matches(v, every_value):
            errors.append(f"summary: the figure {v:g} is not in any finding's evidence")

    text = " ".join([answer["summary"]] + [f["claim"] for f in answer["findings"]])
    for pat in CERTAINTY:
        if re.search(pat, text, re.I):
            errors.append(f"claims certainty about the future ('{re.search(pat, text, re.I).group(0)}')")
    for pat in ORDERS:
        if re.search(pat, text, re.I):
            errors.append(f"reads as an order ('{re.search(pat, text, re.I).group(0)}'): "
                          "describe what the rules and plan say instead")

    for pat in RANKING:
        if re.search(pat, text, re.I):
            warnings.append("ranks holdings: the scorecard is descriptive and not backtested; "
                            "say what the figures show, not which is better")
            break
    for s in sorted(symbols):
        sc = (pack.get("holdings", {}).get(s) or {}).get("scorecard") or {}
        if sc.get("flags") and s.upper() not in gov_named:
            errors.append(f"{s} has governance flags in the pack "
                          f"({'; '.join(x.get('text', '') for x in sc['flags'])}): "
                          "name them in governance_flags")
        if (pack.get("holdings", {}).get(s) or {}).get("data_freshness") == "stale":
            warnings.append(f"{s}: some of its data is stale in the pack")
    if answer["status"] == "answered" and not answer["findings"]:
        errors.append("an answer needs at least one finding")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "checked": checked}
