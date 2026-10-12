"""Planning engine: where each unit of money should sit, what to do next, and what is quietly costing money.

Everything here is derived from data the app already holds (holdings, payslips, budget, card transactions)
plus free market data. Assumptions (rates, expected returns) live in one editable settings blob so the
numbers on screen can always be traced back to something the user can change.
"""
import json
import math
import random
import re
import time
from datetime import date, datetime, timedelta
from statistics import mean, median

from database import get_db, base_ccy as CCY
import analytics as AN
import budget as BG
import payslips as PS
import plugins
import prices as P

DEFAULTS = {
    "savings_rate": 4.0,            # what your savings account pays, % a year
    "term_rate": 4.5,               # fallback when a fixed-term holding's name has no "x%"
    "emergency_months": 6,
    "buffer_months": 1.0,           # months of spending kept in current accounts
    "assume": {"cash": [4.0, 0.0], "equity": [8.0, 16.0], "gold": [5.0, 15.0]},   # [expected %, volatility %]
    "policy": {"equity": 90, "gold": 10},                                          # long-term sleeve mix, %
    "tranches": 6,
    "fx_fee_default": 3.0,          # legacy; markup is now measured from statements
    "card_info": {},                # {card_id: {annual_fee, fx_fee_pct, benefits}}
    "alerts": [],                   # [{"symbol": "VTI", "drop_pct": 10}]: tell me when a symbol is this far below its 1-year high
    "holding_rates": {},            # {holding_id: % a year} overrides
    "auto_review": False,           # write the weekly review automatically (spends the user's API credit)
    "savings_cap": 0,               # balance above which your savings account stops paying the headline rate (0 = unknown)
    "term_maturity": "",            # ISO date your fixed-term holdings pay out; blank = unknown
    "income_since": "",             # first payslip period (YYYY-MM) of your current pay level, e.g. after a promotion; blank = last 12
    "invest_usd": 0,                # what you put into the monthly fund, in dollars (the budget line holds the app-currency equivalent)
    "invest_label": "VTI (total market index fund)",
}


def usd_rate():
    """App-currency units per US dollar: 1 for dollars, otherwise the latest saved exchange rate (kept fresh by Sync prices)."""
    if CCY() == "USD":
        return 1.0
    with get_db() as conn:
        r = conn.execute("SELECT rate_to_base FROM fx_rates WHERE currency='USD' ORDER BY as_of DESC LIMIT 1").fetchone()
    if r:
        return r["rate_to_base"]
    rate = P.fx_to_base("USD")
    return rate or 1.0


# what an emergency fund must cover: food, getting around, health, bills, education and the family commitments paid outside the cards.
# Dining counts half (it is also where you eat); travel, shopping, entertainment, flights for yourself and the rest can stop.
LEAN = {"Groceries": 1, "Dining": .5, "Transport": 1, "Health & Fitness": 1, "Bills & Utilities": 1, "Government & Fines": 1, "Education": 1, "Personal Care": 1}


def savings_name():
    """The name of your savings account holding, for advice text ("move it to ...")."""
    with get_db() as conn:
        r = conn.execute("SELECT name FROM holdings WHERE archived=0 AND asset_class='savings' ORDER BY manual_value DESC LIMIT 1").fetchone()
    return r["name"] if r else "a savings account"


# ───────────────────────── settings ─────────────────────────
def settings():
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key='plan_settings'").fetchone()
    try:
        s = json.loads(r["value"]) if r else {}
    except ValueError:
        s = {}
    for old, new in plugins.get("SETTINGS_RENAMES", {}).items():   # a plugin can bring older setting names along (SETTINGS_RENAMES)
        if old in s and new not in s:
            s[new] = s[old]
    out = {**DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS}}
    out["assume"] = {**DEFAULTS["assume"], **(s.get("assume") or {})}
    out["policy"] = {**DEFAULTS["policy"], **(s.get("policy") or {})} if "policy" in s else dict(DEFAULTS["policy"])
    return out


def save_settings(patch):
    cur = settings()
    for k in ("savings_rate", "term_rate", "emergency_months", "buffer_months", "fx_fee_default", "savings_cap", "tranches"):
        if k in patch:
            try:
                cur[k] = max(0.0, float(patch[k]))
            except (TypeError, ValueError):
                pass
    if "auto_review" in patch:
        cur["auto_review"] = bool(patch["auto_review"])
    for k in ("term_maturity", "invest_label", "income_since"):
        if k in patch:
            cur[k] = str(patch[k] or "").strip()[:40]
    if "invest_usd" in patch:
        try:
            cur["invest_usd"] = max(0.0, float(patch["invest_usd"]))
        except (TypeError, ValueError):
            pass
    for k in ("assume", "policy", "alerts", "holding_rates", "card_info"):
        if k in patch and isinstance(patch[k], type(DEFAULTS[k])):
            cur[k] = patch[k]
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('plan_settings',?)", (json.dumps(cur),))
    return cur


def _ag():
    import agent
    return agent


# ───────────────────────── market weather ─────────────────────────
_CACHE = {}
WEATHER = [("^GSPC", "S&P 500"), ("^VIX", "VIX (fear gauge)"), ("^TNX", "US 10y yield"), ("^IRX", "US 3-month yield"),
           ("GC=F", "Gold (USD/oz)"), ("DX-Y.NYB", "US dollar index"), ("BTC-USD", "Bitcoin"), ("VTI", "VTI (US total market)")]


def _hist(sym, rng="1y", ttl=1500):
    k = (sym, rng)
    if k in _CACHE and time.time() - _CACHE[k][0] < ttl:
        return _CACHE[k][1]
    h = P.history(sym, rng)
    if h and h["points"]:
        _CACHE[k] = (time.time(), h)
    return h


def _stats(sym, label):
    h = _hist(sym)
    if not h:
        return {"symbol": sym, "label": label, "error": "no data"}
    c = [p["close"] for p in h["points"]]
    last = c[-1]
    ma200 = mean(c[-200:]) if len(c) >= 150 else None
    return {"symbol": sym, "label": label, "last": round(last, 2),
            "chg_1m_pct": round((last / c[-22] - 1) * 100, 1) if len(c) > 22 else None,
            "from_1y_high_pct": round((last / max(c) - 1) * 100, 1),
            "vs_200d_pct": round((last / ma200 - 1) * 100, 1) if ma200 else None}


def market_weather():
    rows = [_stats(s, l) for s, l in WEATHER]
    by = {r["symbol"]: r for r in rows}
    vix, spx = by["^VIX"].get("last"), by["^GSPC"].get("from_1y_high_pct")
    notes = []
    if vix is not None:
        notes.append("Fear is elevated (VIX above 25): staged entries beat one big buy." if vix > 25 else
                     "Markets are calm (VIX under 16): nothing forces a rush or a wait." if vix < 16 else "Volatility is normal.")
    if spx is not None:
        notes.append(f"The S&P 500 is {abs(spx):.1f}% below its 1-year high." if spx <= -5 else "The S&P 500 is near its 1-year high.")
    return {"as_of": date.today().isoformat(), "rows": rows, "read": " ".join(notes),
            "caveat": "This describes conditions; it does not predict direction. Timing the market reliably is not possible."}


def dca_backtest(symbol, months=12, rng="5y"):
    """Rolling windows: invest a lump sum on day 1 vs the same total spread monthly. Honest about how often each wins."""
    h = _hist(symbol, rng, ttl=6 * 3600)
    if not h:
        return {"error": "no price history for " + symbol}
    seen, px = set(), []
    for p in h["points"]:
        k = p["date"][:7]
        if k not in seen:
            seen.add(k); px.append(p["close"])
    if len(px) < months + 6:
        return {"error": "not enough history for that window"}
    wins, diffs = 0, []
    for i in range(0, len(px) - months):
        end = px[i + months]
        lump = end / px[i]
        dca = mean(end / px[i + k] for k in range(months))
        diffs.append((lump / dca - 1) * 100)
        wins += lump > dca
    n = len(diffs)
    return {"symbol": symbol.upper(), "window_months": months, "windows_tested": n, "lump_sum_won_pct": round(wins / n * 100),
            "median_lump_edge_pct": round(median(diffs), 1), "worst_lump_vs_dca_pct": round(min(diffs), 1),
            "best_lump_vs_dca_pct": round(max(diffs), 1),
            "read": ("Lump sum usually wins because markets rise more often than they fall, but staged entry limits the regret "
                     "of buying right before a drop. Past windows only.")}


# ───────────────────────── Monte Carlo ─────────────────────────
def simulate(start, monthly, years, ret_pct, vol_pct, target=None, outflows=None, n=3000, seed=11):
    """Monthly steps, lognormal returns. Returns yearly p10/p50/p90 and, if target given, the odds of reaching it."""
    rng = random.Random(seed)
    months = int(years * 12)
    mu, sg = ret_pct / 100 / 12, vol_pct / 100 / math.sqrt(12)
    out = {m: a for m, a in (outflows or [])}
    paths = []
    for _ in range(n):
        v, path = start, []
        for m in range(1, months + 1):
            v = max(0.0, v * math.exp(mu - sg * sg / 2 + sg * rng.gauss(0, 1)) + monthly - out.get(m, 0))
            if m % 12 == 0:
                path.append(v)
        paths.append(path)
    def pct(q):
        return [round(sorted(p[y] for p in paths)[int(q * (n - 1))]) for y in range(len(paths[0]))] if paths and paths[0] else []
    res = {"years": list(range(1, int(years) + 1)), "p10": pct(.1), "p50": pct(.5), "p90": pct(.9),
           "inputs": {"start": round(start), "monthly": round(monthly), "return_pct": ret_pct, "volatility_pct": vol_pct}}
    if target and paths and paths[0]:
        res["prob_target_pct"] = round(sum(p[-1] >= target for p in paths) / n * 100)
    return res


# ───────────────────────── the one set of monthly numbers ─────────────────────────
def basics():
    """What comes in, what really goes out and what is left, once, for every page.
    Income: typical (median) payslip month, and the 12-month average, which spreads the annual bonus.
    Spending: usual month (per-category median) of the last 6 complete card cycles plus the budget lines paid outside the cards.
    The typed-in budget amounts are plans; these are measurements."""
    AG = _ag()
    inc, b = AN.income_basis(), BG.summary()
    net = inc.get("typical_net") or b["income"]
    net_avg = inc.get("avg_net") or net
    gross = inc.get("gross") or b["income"]
    lumpc = BG.lump_categories(b["items"])
    card = AN.typical_card_spend(exclude=lumpc)
    off = sum(i["monthly"] for i in b["items"] if i.get("off_card") and i["type"] == "expense")
    lump = sum(i["monthly"] for i in b["items"] if i.get("lump") and i["type"] == "expense")      # irregular lines count at their plan
    import bank_tx as BT
    bank = BT.usual_other()["monthly"]                  # cash, debit-card purchases, payments to people, fees: leave the bank, never on a card
    import plugins
    extra_fn = plugins.get("EXTRA_MONTHLY_SPEND")            # spending a plugin can measure that no card, budget line or bank line shows
    extra = float(extra_fn() or 0) if extra_fn else 0.0
    spend = card["avg"] + off + lump + bank + extra if card["n"] else b["expenses"]
    saved, invest = b["savings"] + b["invest"], b["invest"]
    cyc = [m for m in AN.spend_cycles()["months"] if m["complete"]][-6:]
    lean = sum(w * median(m["cats"].get(c, 0) for m in cyc) for c, w in LEAN.items() if c not in lumpc) if cyc else spend
    lean += sum(i["monthly"] for i in b["items"] if i.get("lump") and set(i["categories"]) & set(LEAN))      # e.g. tuition
    lean += sum(i["monthly"] for i in b["items"] if i.get("off_card") and i["type"] == "expense" and not i["label"].startswith(tuple(plugins.get("LEAN_EXCLUDE_PREFIXES", ["\0"]))))
    liquid = sum(h["value_base"] for h in AG.t_list_holdings({})["holdings"] if h["class"] in ("cash", "savings") and not h["liability"])
    return {"net_typical": round(net), "net_avg": round(net_avg), "gross": round(gross), "bonus_per_month": round(net_avg - net),
            "card_avg": round(card["avg"]), "card_cycles": card["n"], "off_card": round(off), "lump": round(lump), "bank_other": round(bank), "extra_spend": round(extra), "spend": round(spend), "lean_spend": round(min(lean, spend)), "planned_spend": round(b["expenses"]),
            "saved": round(saved), "invest": round(invest),
            "kept_typical": round(net - spend), "flow_typical": round(net - spend - saved), "flow_avg": round(net_avg - spend - saved),
            "liquid": round(liquid), "runway_months": round(liquid / spend, 1) if spend else None,
            "savings_rate_gross_pct": round(saved / gross * 100, 1) if gross else None,
            "income_source": inc["source"], "income_period": inc.get("period")}


# ───────────────────────── the money plan ─────────────────────────
def _rate(h, S):
    r = S["holding_rates"].get(str(h["id"]))
    if r is not None:
        return float(r)
    if h["class"] == "savings":
        return S["savings_rate"]
    if h["class"] == "bond":
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", h["name"])
        return float(m.group(1)) if m else S["term_rate"]
    return 0.0 if h["class"] == "cash" else None


def _goals():
    prof = _ag().get_profile()
    out = []
    for g in prof.get("goals") or []:
        try:
            out.append({"name": g["name"], "amount": float(g["amount"]), "date": g["target_date"]})
        except (KeyError, TypeError, ValueError):
            pass
    return sorted(out, key=lambda g: g["date"])


def _months_to(d):
    try:
        return max(0.5, (date.fromisoformat(d) - date.today()).days / 30.44)
    except ValueError:
        return 12


def money_plan():
    AG, S = _ag(), settings()
    o = AG.t_get_overview({})
    hs = AG.t_list_holdings({})["holdings"]
    B = basics()
    spend = B["spend"]                 # what the cards and transfers really cost, not the typed-in plan
    monthly_in = B["saved"]
    by = lambda *cl: [h for h in hs if h["class"] in cl and not h["liability"]]
    cur = sum(h["value_base"] for h in by("cash"))
    sav = sum(h["value_base"] for h in by("savings"))
    term = sum(h["value_base"] for h in by("bond"))
    growth = {c: sum(h["value_base"] for h in by(*k)) for c, k in {"equity": ("stock", "etf", "mutual_fund"), "gold": ("gold",), "crypto": ("crypto",)}.items()}
    gsum = sum(growth.values())
    liquid = cur + sav

    # waterfall over liquid money: emergency fund, then dated goals, then free
    emerg_t = S["emergency_months"] * B["lean_spend"]
    left = liquid
    emerg = min(left, emerg_t); left -= emerg
    goals, term_left = [], term
    mat = S["term_maturity"]
    for g in _goals():
        mo = _months_to(g["date"])
        f = min(left, g["amount"]); left -= f
        gap = g["amount"] - f
        usable = term_left if (not mat or mat <= g["date"]) else 0      # a fixed-term holding that pays out after the goal cannot fund it
        from_term = min(usable, gap); term_left -= from_term
        goals.append({**g, "months_left": round(mo, 1), "funded_liquid": round(f), "from_term_assets": round(from_term),
                      "assumes_term": bool(from_term and not mat),
                      "shortfall": round(gap - from_term), "monthly_needed": round((gap - from_term) / mo) if gap - from_term > 0 else 0})
    free = max(0, left)
    buffer = S["buffer_months"] * spend
    idle = max(0, cur - buffer)
    rate = S["savings_rate"]
    steps = []
    if idle >= 2000:
        cap = S["savings_cap"]
        steps.append({"id": "move-idle", "title": f"Move {CCY()} {round(idle, -2):,.0f} from current accounts to {savings_name()}",
                      "why": f"Current accounts earn about nothing; at {rate}% that is roughly {CCY()} {idle * rate / 100:,.0f} a year. "
                             f"Keep about {CCY()} {buffer:,.0f} ({S['buffer_months']:g} months of spending) for bills."
                             + ("" if cap else f" Check whether {savings_name()} pays the headline rate on balances this large."),
                      "amount": round(idle)})
    if emerg < emerg_t:
        steps.append({"id": "emergency", "title": f"Top up the emergency fund by {CCY()} {emerg_t - emerg:,.0f}",
                      "why": f"{S['emergency_months']:g} months of essential spending ({CCY()} {B['lean_spend']:,.0f} a month: food, transport, health, bills, family support) is {CCY()} {emerg_t:,.0f}.", "amount": round(emerg_t - emerg)})
    for g in goals:
        if g["shortfall"] > 0:
            steps.append({"id": "goal-" + g["name"], "title": f"Save {CCY()} {g['monthly_needed']:,.0f} a month for {g['name']}",
                          "why": f"{CCY()} {g['shortfall']:,.0f} still unfunded with {g['months_left']:.0f} months to go.", "amount": g["monthly_needed"]})
        elif g["assumes_term"]:
            steps.append({"id": "term-date", "title": f"Confirm when your fixed-term holding pays out: {g['name']} counts on it",
                          "why": f"{CCY()} {g['from_term_assets']:,.0f} of {g['name']} is assumed to come from your fixed-term holdings. Enter its maturity date in Your savings plan; if it is after {g['date']}, the goal is short by that amount.",
                          "amount": g["from_term_assets"]})
    # long-term sleeve: stage the free money in tranches, filling the policy mix
    sleeve_total = gsum + free + 12 * B["invest"]      # plan for the next year of investing too
    need = {k: max(0, sleeve_total * v / 100 - growth.get(k, 0)) for k, v in S["policy"].items()}
    nsum = sum(need.values()) or 1
    n_t = max(1, int(S["tranches"]))
    deploy = {"free_to_deploy": round(free), "tranches": n_t, "per_tranche": round(free / n_t) if free else 0, "monthly_contribution": round(monthly_in),
              "route": [{"class": k, "target_base": round(sleeve_total * S["policy"][k] / 100), "have_base": round(growth.get(k, 0)),
                         "share_of_new_money_pct": round(need[k] / nsum * 100)} for k in S["policy"]]}
    if free >= 5000:
        steps.append({"id": "deploy", "title": f"Invest {CCY()} {free / n_t:,.0f} a month for {n_t} months (staged)",
                      "why": f"{CCY()} {free:,.0f} is not needed for emergencies or dated goals. Staging limits the damage of buying right before a fall.",
                      "amount": round(free / n_t)})
    elif monthly_in:
        if B["flow_avg"] < 0:
            steps.append({"id": "cashflow", "title": f"Investing {CCY()} {monthly_in:,.0f} a month is more than you keep: cut {CCY()} {-B['flow_avg']:,.0f} or invest less",
                          "why": f"Take-home averages {CCY()} {B['net_avg']:,.0f} (bonus included), real spending is {CCY()} {spend:,.0f}. After investing you are {CCY()} {-B['flow_avg']:,.0f} short in an average month, paid from savings.",
                          "amount": round(-B["flow_avg"])})
        else:
            steps.append({"id": "contribute", "title": f"Keep investing {CCY()} {monthly_in:,.0f} a month",
                          "why": (f"A typical month leaves {CCY()} {B['kept_typical']:,.0f} after spending, so {CCY()} {max(0, monthly_in - B['kept_typical']):,.0f} of it comes from savings; "
                                  f"your annual bonus (about {CCY()} {B['bonus_per_month']:,.0f} a month averaged) more than covers that. Keep the bonus for this, not for spending.")
                         if B["flow_typical"] < 0 else "A typical month covers it after your real spending.", "amount": round(monthly_in)})

    # safe money vs the market, at today's rates
    eq = S["assume"]["equity"]
    hurdle = [{"savings_rate_pct": r, "equity_premium_pct": round(eq[0] - r, 1)} for r in (rate, rate - 1.5, rate - 3, rate - 4.5) if r > 0]
    concentration, flags = [], []
    if gsum:
        top = sorted(((h["value_base"], h["name"]) for h in hs if h["class"] in ("stock", "etf", "mutual_fund", "gold", "crypto")), reverse=True)[:3]
        concentration = [{"name": n, "pct_of_growth": round(v / gsum * 100)} for v, n in top]
    for h in hs:
        if "cfd" in (h["symbol"] + h["name"]).lower():
            flags.append({"holding": h["name"], "issue": "CFD: leveraged, with overnight financing charges. Check it fits your investing rules before keeping it."})
    if term > 0:
        used = term - term_left
        flags.append({"holding": "Fixed-term holdings", "issue": f"{CCY()} {term:,.0f} is locked at a fixed rate, which is an advantage while rates fall."
                      + (f" {CCY()} {used:,.0f} of it is counted toward your dated goals." if used else "")
                      + (f" It pays out on {S['term_maturity']}." if S["term_maturity"] else " Add its maturity date in Your savings plan so the plan knows when it is free.")})
    syms = {h["symbol"].upper() for h in hs}
    safe_vals = [(h["value_base"], _rate(h, S) or 0.0) for h in hs if h["class"] in ("cash", "savings", "bond") and not h["liability"]]
    blended = sum(v * r for v, r in safe_vals) / (sum(v for v, _ in safe_vals) or 1)
    return {
        "as_of": date.today().isoformat(), "assumptions": {"savings_rate_pct": rate, "emergency_months": S["emergency_months"], "monthly_spend": round(spend), "lean_spend": B["lean_spend"],
                                                           "safe_blended_rate_pct": round(blended, 2)},
        "balances": {"current": round(cur), "savings": round(sav), "term_assets": round(term), "growth": {k: round(v) for k, v in growth.items()},
                     "liquid": round(liquid), "net_worth": o["net_worth_base"]},
        "waterfall": [{"name": "Emergency fund", "amount": round(emerg), "target": round(emerg_t)}] +
                     [{"name": g["name"], "amount": g["funded_liquid"] + g["from_term_assets"], "target": round(g["amount"])} for g in goals] +
                     [{"name": "Free to invest", "amount": round(free), "target": None}] +
                     ([{"name": "Locked in fixed-term holdings (free on maturity)", "amount": round(term_left), "target": None}] if term_left > 0 else []),
        "locked_term_base": round(term_left), "goals": goals, "steps": steps, "deploy": deploy, "hurdle": hurdle, "idle_current_base": round(idle),
        "idle_annual_gain_base": round(idle * rate / 100), "concentration": concentration, "flags": flags,
        "note": f"Your safe pile earns about {blended:.1f}% blended today ({rate:g}% on the savings account), so equities only add return for money you will not need for 5+ years. "
                "Those rates are variable and usually fall when interest rates do.",
    }


def projection(years=10, target=None):
    """Net-worth fan. The safe pile compounds at its blended rate, takes in what is left of pay after real spending and
    investing (the 12-month average, so the bonus counts once a year), and pays out the dated goals; the growth sleeve is simulated."""
    S, plan, B = settings(), money_plan(), basics()
    b, free = plan["balances"], plan["deploy"]["free_to_deploy"]
    rate = plan["assumptions"]["safe_blended_rate_pct"] / 100
    outs = {}
    for g in plan["goals"]:
        m = max(1, int(round(g["months_left"])))
        outs[m] = outs.get(m, 0) + g["amount"]
    s, safe = b["liquid"] + b["term_assets"] - free, []
    for m in range(1, years * 12 + 1):
        s = max(0.0, s * (1 + rate / 12) + B["flow_avg"] + B["saved"] - B["invest"] - outs.get(m, 0))     # savings lines stay in the safe pile
        if m % 12 == 0:
            safe.append(round(s))
    r_eq, v_eq = S["assume"]["equity"]
    sim = simulate(sum(b["growth"].values()) + free, B["invest"], years, r_eq, v_eq, max(0, target - safe[-1]) if target else None)
    out = {"years": sim["years"], "safe": safe, **{k: [x + y for x, y in zip(safe, sim[k])] for k in ("p10", "p50", "p90")},
           "inputs": {"equity_return_pct": r_eq, "equity_vol_pct": v_eq, "safe_rate_pct": round(rate * 100, 1), "monthly_invested": B["invest"], "monthly_kept": B["flow_avg"] + B["saved"] - B["invest"]}}
    if target:
        out["prob_target_pct"] = sim.get("prob_target_pct")
    return out


def forecast(months=3):
    AG, S, B = _ag(), settings(), basics()
    interest = sum(h["value_base"] for h in AG.t_list_holdings({})["holdings"] if h["class"] == "savings") * S["savings_rate"] / 100 / 12
    out, v = [], B["liquid"]
    for i in range(1, months + 1):
        v += B["flow_typical"] + interest              # next months are ordinary months: no bonus in them
        out.append({"month_ahead": i, "liquid_base": round(v)})
    return {"net_income": B["net_typical"], "net_income_avg": B["net_avg"], "card_spend": B["card_avg"], "off_card_spend": B["off_card"] + B["lump"] + B["bank_other"] + B["extra_spend"],
            "invested_or_saved": B["saved"], "free_after_everything": B["flow_typical"], "free_after_everything_avg": B["flow_avg"],
            "interest_per_month": round(interest), "projection": out}


# ───────────────────────── cards: foreign spend and real value ─────────────────────────
FOREIGN_CODES = set("""DEU GBR FRA ITA ESP NLD CHE AUT BEL PRT GRC TUR THA SGP MYS IDN VNM PHL HKG CHN JPN KOR IND LKA NPL MDV EGY MAR ZAF
KEN TZA USA CAN AUS NZL SAU QAT BHR OMN KWT JOR GEO AZE ARM UZB KAZ RUS CZE HUN POL SWE NOR DNK FIN IRL ISR CYP MLT HRV SRB BIH IRL LUX""".split())
FOREIGN_WORDS = ("agoda", "booking.com", "airbnb", "klook", "expedia", "trip.com", "tripadvisor", "ryanair",
                 "easyjet", "wizz", "aliexpress", "alibaba", "shein", "temu", "openai", "anthropic", "github", "midjourney", "vercel", "aws ", "steam")


def _home_tags():
    """Country codes that mean "here" on a card line: your home country (Settings > Home country) plus any a plugin lists (HOME_COUNTRY_TAGS)."""
    import categorizer as CZ
    home = CZ.HOME()
    iso3 = next((k for k, v in CZ.ISO3.items() if v == home), "")
    return ({iso3, CZ.ISO2.get(home, "").upper()} | {t.upper() for t in plugins.get("HOME_COUNTRY_TAGS", [])}) - {""}


def _is_foreign(desc):
    d = (desc or "").strip()
    m = re.search(r"\s([A-Za-z]{3})\s*$", d)
    if m and m.group(1).upper() in FOREIGN_CODES:
        return True
    if m and m.group(1).upper() in _home_tags():
        return False                      # a line coded with your own country was billed in your own currency, whatever the merchant
    low = d.lower()
    return any(w in low for w in FOREIGN_WORDS)


PERIOD_LABELS = {"c1": "latest cycle", "all": "all complete cycles"}


def period_filter(period):
    """period: m3/m6/m12 (calendar months back), c1/c3/c6/c12 (complete statement cycles), all (every complete cycle).
    -> (since_iso or None, set of cycle months or None, label)"""
    period = period or "m12"
    if period.startswith("mon:") and len(period) == 11:                      # one calendar month, mon:2026-08
        y, m = int(period[4:8]), int(period[9:11])
        last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).isoformat()
        return f"{period[4:]}-01", None, date(y, m, 1).strftime("%B %Y"), last
    if period.startswith("cyc:"):                                           # one statement cycle, cyc:2026-08
        return None, {period[4:]}, date(int(period[4:8]), int(period[9:11]), 1).strftime("%B %Y") + " statement cycle", None
    if period.startswith("rng:") and ".." in period:                         # any dates, rng:2026-08-01..2026-08-31
        a, b = period[4:].split("..", 1)
        return a, None, f"{a} to {b}", b
    if period[0] == "m" and period[1:].isdigit():
        n = int(period[1:])
        return (date.today() - timedelta(days=round(n * 30.44))).isoformat(), None, f"last {n} months", None
    done = [m["month"] for m in AN.spend_cycles()["months"] if m["complete"]]
    if period == "all":
        return None, set(done), "all complete cycles", None
    n = int(period[1:]) if period[0] == "c" and period[1:].isdigit() else 6
    return None, set(done[-n:]), PERIOD_LABELS.get(period, f"last {n} cycles"), None


def in_period(statement_date, txn_date, pf):
    import transactions as TX
    since, cycles, _, until = pf
    if since is not None:
        return txn_date >= since and (until is None or txn_date <= until)
    return TX.cycle_month(statement_date) in cycles if statement_date else False


def _txns(period="m12"):
    pf = period_filter(period)
    with get_db() as conn:
        rows = conn.execute("""SELECT t.*, c.name card, s.statement_date sdate FROM statement_transactions t
                               JOIN credit_cards c ON c.id=t.card_id LEFT JOIN card_statements s ON s.id=t.statement_id
                               WHERE c.archived=0""").fetchall()
    return [dict(r) for r in rows if in_period(r["sdate"], r["txn_date"], pf)]


def _txns12():
    return _txns("m12")


def _mid_rate_fn(cur):
    """App-currency units per 1 unit of `cur` on a given date, from market data."""
    base = CCY()
    if cur == base:
        return lambda d: 1.0
    h = _hist(f"{cur}{base}=X", "2y", ttl=6 * 3600)
    if not h or not h["points"]:
        return None
    import bisect
    series = {p["date"]: p["close"] for p in h["points"]}
    days = sorted(series)

    def at(d):
        i = bisect.bisect_right(days, d) - 1
        return series[days[max(i, 0)]]
    return at


def _fx_rows(period="m12"):
    """Every foreign-currency card transaction of the last 12 months with what the bank charged versus the mid-market value."""
    rows = [t for t in _txns(period) if t.get("fx_currency")]
    mids = {}
    out = []
    for t in rows:
        cur = t["fx_currency"]
        if cur not in mids:
            mids[cur] = _mid_rate_fn(cur)
        fn = mids[cur]
        mid = fn(t["txn_date"]) if fn else None
        mid_base = t["fx_amount"] * mid if mid else None
        out.append({**t, "mid_rate": mid, "mid_base": mid_base,
                    "extra": (t["amount"] - mid_base) if mid_base is not None else None,
                    "markup_pct": ((t["amount"] / mid_base - 1) * 100) if mid_base else None})
    return out


def fx_report(period="m12"):
    rows = _fx_rows(period)
    S = settings()
    known = [r for r in rows if r["extra"] is not None]
    cur, card = {}, {}
    for r in known:
        c = cur.setdefault(r["fx_currency"], {"currency": r["fx_currency"], "foreign_amount": 0.0, "purchases_base": 0.0, "mid_base": 0.0, "extra_base": 0.0, "refund_cost_base": 0.0, "count": 0})
        k = card.setdefault(r["card"], {"card": r["card"], "card_id": r["card_id"], "purchases_base": 0.0, "mid_base": 0.0, "extra_base": 0.0, "refund_cost_base": 0.0, "count": 0})
        for d in (c, k):
            if r["amount"] > 0:                      # purchases define the markup the bank applies
                d["purchases_base"] += r["amount"]; d["mid_base"] += r["mid_base"]; d["extra_base"] += r["extra"]; d["count"] += 1
            else:                                    # refunds that came back at a worse rate are a separate cost
                d["refund_cost_base"] += r["extra"]
        if r["amount"] > 0:
            c["foreign_amount"] += r["fx_amount"]
    def fin(d):
        d["markup_pct"] = round(d["extra_base"] / d["mid_base"] * 100, 2) if d["mid_base"] else None
        for k in ("purchases_base", "mid_base", "foreign_amount", "extra_base", "refund_cost_base"):
            if k in d:
                d[k] = round(d[k], 2)
        return d
    by_cur = sorted((fin(d) for d in cur.values() if d["count"]), key=lambda d: -d["purchases_base"])
    by_card = sorted((fin(d) for d in card.values() if d["count"]), key=lambda d: -d["purchases_base"])
    buys = [r for r in known if r["amount"] > 0]
    charged, mid = sum(r["amount"] for r in buys), sum(r["mid_base"] for r in buys)
    markup_cost = sum(r["extra"] for r in buys)
    refund_cost = sum(r["extra"] for r in known if r["amount"] <= 0)
    # refunds: what came back versus what was originally charged for the same merchant and foreign amount
    refunds = []
    for r in known:
        if r["amount"] >= 0:
            continue
        orig = next((o for o in known if o["card_id"] == r["card_id"] and o["amount"] > 0 and o["description"] == r["description"]
                     and abs(o["fx_amount"] + r["fx_amount"]) < 0.02 and o["txn_date"] <= r["txn_date"]), None)
        refunds.append({"date": r["txn_date"], "card": r["card"], "description": " ".join(r["description"].split())[:44], "currency": r["fx_currency"],
                        "foreign_amount": abs(r["fx_amount"]), "base_refunded": round(-r["amount"], 2),
                        "base_originally_charged": round(orig["amount"], 2) if orig else None,
                        "lost_to_rates_base": round(orig["amount"] + r["amount"], 2) if orig else None})
    abroad_base = [t for t in _txns(period) if not t.get("fx_currency") and t["amount"] > 0 and t["category"] not in ("Internal", "Fees & Charges") and _is_foreign(t["description"])]
    tx = sorted(({"date": r["txn_date"], "card": r["card"], "description": " ".join(r["description"].split())[:40], "currency": r["fx_currency"],
                  "foreign_amount": r["fx_amount"], "base": round(r["amount"], 2), "rate": round(r["fx_rate"], 4), "mid_rate": round(r["mid_rate"], 4) if r["mid_rate"] else None,
                  "markup_pct": round(r["markup_pct"], 2) if r["markup_pct"] is not None else None} for r in known), key=lambda x: -abs(x["base"]))
    best = min((c for c in by_card if c["markup_pct"] is not None and c["count"] >= 2), key=lambda c: c["markup_pct"], default=None)
    saving = round(sum(max(0, c["extra_base"] - c["mid_base"] * best["markup_pct"] / 100) for c in by_card if best and c is not best)) if best else 0
    return {"window": period_filter(period)[2], "period": period, "transactions_in_foreign_currency": len(known),
            "total": {"purchases_base": round(charged, 2), "mid_market_base": round(mid, 2), "markup_cost_base": round(markup_cost, 2),
                      "refund_rate_loss_base": round(refund_cost, 2), "extra_cost_base": round(markup_cost + refund_cost, 2),
                      "effective_markup_pct": round(markup_cost / mid * 100, 2) if mid else None},
            "by_currency": by_cur, "by_card": by_card, "refunds": refunds, "transactions": tx[:30],
            "cheapest_card": best["card"] if best else None, "saving_if_all_on_cheapest_base": saving,
            "billed_in_base_by_overseas_merchants": {"count": len(abroad_base), "base": round(sum(t["amount"] for t in abroad_base)),
                                                    "note": "Charged in your own currency by overseas-looking merchants. Some banks still apply an international fee to these but print no separate line, so it cannot be measured."},
            "how_measured": "Read from the statement: currency, foreign amount and the amount the bank charged. The extra cost is the amount charged minus the foreign amount at the mid-market rate on the day. "
                            "It includes the bank's markup and the card network's conversion margin; none of these banks print a separate fee line.",
            "missing_rates": len(rows) - len(known)}


def card_usage(period="m12"):
    S = settings()
    rows = _txns(period)
    by_card = {}
    for t in rows:
        e = by_card.setdefault(t["card_id"], {"card": t["card"], "card_id": t["card_id"], "spend": 0.0, "n": 0, "cashback": 0.0, "annual_fee": 0.0,
                                              "penalties": 0.0, "foreign": 0.0, "cats": {}, "first": t["txn_date"]})
        d = (t["description"] or "").lower()
        a = t["amount"]
        e["first"] = min(e["first"], t["txn_date"])
        if a < 0 and re.search(r"cash ?back|reward|rebate", d):
            e["cashback"] += -a
        elif "annual fee" in d:
            e["annual_fee"] += a
        elif re.search(r"finance charge|interest|late (payment|fee)|overlimit|cash advance", d):
            e["penalties"] += max(a, 0)
        elif a > 0 and t["category"] not in ("Internal", "Fees & Charges"):
            e["spend"] += a; e["n"] += 1
            e["cats"][t["category"]] = e["cats"].get(t["category"], 0) + a
            if t.get("fx_currency"):
                e["foreign"] += a
    total = sum(e["spend"] for e in by_card.values()) or 1
    import rewards as RW
    rw = {c["card_id"]: c for c in RW.report(period)["cards"]}
    fx_extra = {}
    for r in _fx_rows(period):
        if r["extra"] is not None:
            fx_extra[r["card_id"]] = fx_extra.get(r["card_id"], 0) + r["extra"]       # purchases cost extra; refunds at worse rates add to it
    out = []
    for e in by_card.values():
        info = S["card_info"].get(str(e["card_id"]), {})
        fee = float(info.get("annual_fee", e["annual_fee"]))
        fx_cost = fx_extra.get(e["card_id"], 0.0)
        if e["card_id"] in rw:                       # statements print the real figure for most banks
            e["cashback"] = rw[e["card_id"]]["earned_12m"]
        net = e["cashback"] - max(fee, 0) - e["penalties"] - fx_cost
        flags = []
        if e["spend"] / total < 0.05:
            flags.append("Rarely used (under 5% of your card spend)")
        if fee > 0 and e["cashback"] < fee:
            flags.append("Earns back less than its annual fee")
        if fx_cost > 200:
            flags.append(f"Lost about {CCY()} {fx_cost:,.0f} to exchange-rate markup in 12 months")
        if e["penalties"] > 0:
            flags.append(f"{CCY()} {e['penalties']:,.0f} in interest or late fees: pay in full")
        out.append({"card": e["card"], "card_id": e["card_id"], "spend_12m": round(e["spend"]), "share_pct": round(e["spend"] / total * 100), "transactions": e["n"],
                    "cashback_earned": round(e["cashback"]), "annual_fee": round(fee), "interest_and_late_fees": round(e["penalties"]), "foreign_spend": round(e["foreign"]),
                    "est_fx_cost": round(fx_cost), "net_value": round(net), "top_categories": [c for c, _ in sorted(e["cats"].items(), key=lambda x: -x[1])[:3]],
                    "benefits": info.get("benefits", ""), "flags": flags, "data_since": e["first"]})
    out.sort(key=lambda c: -c["spend_12m"])
    return {"window": period_filter(period)[2], "period": period, "cards": out,
            "caution": "Cashback and annual fees are read from statement lines where they appear; set them per card for accuracy. "
                       "Closing a card can shorten your credit history with the credit bureau and lose fee-waiver spend thresholds."}


def recurring():
    rows = [t for t in _txns12() if t["amount"] > 0 and t["category"] not in ("Internal", "Fees & Charges")
            and not re.search(r"charit|donat", t["category"] or "", re.I)]
    groups = {}
    for t in rows:
        k = " ".join(re.sub(r"[0-9#*/_.\-]+", " ", t["description"] or "").split()[:2]).upper()
        if k:
            groups.setdefault(k, []).append(t)
    out = []
    for k, ts in groups.items():
        months = {t["txn_date"][:7] for t in ts}
        amts = [t["amount"] for t in ts]
        if len(months) >= 3 and len(ts) <= len(months) * 1.4 and max(amts) <= min(amts) * 1.25:
            out.append({"merchant": k.title(), "monthly": round(mean(amts), 2), "annual": round(mean(amts) * 12), "months_seen": len(months), "card": ts[0]["card"],
                        "last": max(t["txn_date"] for t in ts)})
    return sorted(out, key=lambda r: -r["annual"])


# ───────────────────────── the insights feed ─────────────────────────
def insights():
    """Plain rule-based findings (no model call): cheap enough to compute on every dashboard load."""
    S = settings()
    items = []
    def add(id_, sev, title, body, page=None, ask=None):
        items.append({"id": id_, "severity": sev, "title": title, "body": body, "page": page, "ask": ask})
    try:
        p = money_plan()
        if p["idle_current_base"] >= 2000:
            add("idle-cash", "medium", f"{CCY()} {p['idle_current_base']:,} is sitting in current accounts",
                f"Moving it to {savings_name()} at {S['savings_rate']}% would earn about {CCY()} {p['idle_annual_gain_base']:,} a year. Check it has no balance cap first.", "plan")
        free = p["deploy"]["free_to_deploy"]
        if free >= 20000:
            add("free-money", "low", f"About {CCY()} {free:,} is free to invest",
                f"It is not needed for emergencies or your dated goals. A staged plan is {p['deploy']['tranches']} tranches of {CCY()} {p['deploy']['per_tranche']:,}.", "plan",
                "Build me a staged plan to invest my free cash, within my investing rules.")
        for g in p["goals"]:
            if g["shortfall"] > 0:
                add("goal-" + g["name"], "high", f"{g['name']} is {CCY()} {g['shortfall']:,} short", f"You need about {CCY()} {g['monthly_needed']:,} a month for {g['months_left']:.0f} months.", "plan")
        for f in p["flags"]:
            add("flag-" + f["holding"], "low" if f["holding"].startswith("Fixed-term") else "medium", f["holding"], f["issue"], "all")
        singles = {h["name"] for h in _ag().t_list_holdings({})["holdings"] if h["class"] in ("stock", "crypto")}
        c = next((c for c in p["concentration"] if c["name"] in singles and c["pct_of_growth"] >= 40), None)   # a world ETF is the diversifier, not the problem
        if c:
            add("concentration", "medium", f"{c['name']} is {c['pct_of_growth']}% of your growth assets", "One stock dominating a small sleeve makes results depend on it.", "all")
    except Exception:
        pass
    try:
        f = forecast()
        if f["free_after_everything"] < -300:
            avg = f["free_after_everything_avg"]
            add("cashflow", "high" if avg < -300 else "medium",
                f"After investing, a typical month is {CCY()} {-f['free_after_everything']:,} short" + (" and so is the average" if avg < -300 else ""),
                f"Take-home {CCY()} {f['net_income']:,} (typical month), cards {CCY()} {f['card_spend']:,} (usual month, 6 cycles), paid outside cards {CCY()} {f['off_card_spend']:,}, invested {CCY()} {f['invested_or_saved']:,}. "
                + (f"Counting the annual bonus the average month is {CCY()} {avg:,} short." if avg < -300 else f"Your annual bonus brings the average month to {CCY()} {avg:+,}, so the bonus is what funds the investing.")
                + f" Savings profit (about {CCY()} {f['interest_per_month']:,} a month) stays in the account.", "budget",
                "My monthly cash flow looks negative. Where is the money going and what should I cut or change?")
    except Exception:
        pass
    try:
        fx = fx_report()
        t = fx["total"]
        if t["extra_cost_base"] >= 300:
            best = f" On {fx['cheapest_card']}'s rates the same spending would have cost about {CCY()} {fx['saving_if_all_on_cheapest_base']:,} less." if fx["cheapest_card"] and fx["saving_if_all_on_cheapest_base"] >= 100 else ""
            add("fx-cost", "medium", f"Exchange-rate markup cost you about {CCY()} {t['extra_cost_base']:,.0f} in 12 months",
                f"{CCY()} {t['purchases_base']:,.0f} of foreign-currency purchases cost about {t['effective_markup_pct']}% above the mid-market rate, plus {CCY()} {t['refund_rate_loss_base']:,.0f} lost on refunds.{best}", "cards",
                "Which card or account should I use abroad? Compare my card markups and the current options where I live for travel.")
        for c in card_usage()["cards"]:
            if c["annual_fee"] > 0 and c["cashback_earned"] < c["annual_fee"]:
                add("card-" + str(c["card_id"]), "low", f"{c['card']} may not be worth its fee", f"It earned {CCY()} {c['cashback_earned']:,} back against a {CCY()} {c['annual_fee']:,} fee.", "cards")
    except Exception:
        pass
    try:
        import rewards as RW
        rep = RW.report()
        if rep["unredeemed"] >= 500:
            parts = ", ".join(f"{c['card']} {CCY()} {c['balance']:,.0f}" for c in rep["cards"] if c["balance"])
            add("unredeemed", "low", f"{CCY()} {rep['unredeemed']:,.0f} of rewards are sitting unused", f"{parts}. Unused credits are easy to forget and some expire: spend or redeem them.", "cards",
                "Check the terms of my unredeemed cashback and points: do they expire, and what is the best way to use them?")
        for c in rep["cards"]:
            cap = c.get("cap")
            if cap and cap["utilisation_pct"] >= 90:
                add("cap-full", "low", f"{c['card']} is at its reward cap",
                    f"You redeemed about {CCY()} {cap['avg_reward_per_month']:,} a month against a maximum of {CCY()} {cap['max_reward_per_month']:,}. Spend beyond the cap earns little, so send the rest to your next-best card.", "cards")
            elif cap and cap["utilisation_pct"] < 60:
                add("cap-room", "medium", f"You use only {cap['utilisation_pct']}% of {c['card']}'s {cap['label']} reward",
                    f"It can pay up to {CCY()} {cap['max_reward_per_month']:,} a month and you collect about {CCY()} {cap['avg_reward_per_month']:,}. Pay up to {CCY()} {cap['cap_spend'] or 0:,.0f} a month with it first.", "cards")
        rt = RW.routing()
        if rt["abroad"]:
            top = rt["abroad"][0]
            add("abroad-best", "low", f"Best way to pay abroad: {top['card']}", f"After exchange-rate markup it nets {top['net_pct']:+.1f}%" + (f" ({top['note']})" if top.get("note") else "") + ". Then: " +
                ", ".join(f"{a['card']} {a['net_pct']:+.1f}%" for a in rt["abroad"][1:4]), "cards")
    except Exception:
        pass
    try:
        for a in S["alerts"]:
            st = _stats(a["symbol"], a["symbol"])
            if st.get("from_1y_high_pct") is not None and st["from_1y_high_pct"] <= -abs(float(a["drop_pct"])):
                add("dip-" + a["symbol"], "medium", f"{a['symbol']} is {abs(st['from_1y_high_pct']):.0f}% below its 1-year high",
                    f"Your rule: look when it falls {a['drop_pct']}%. If you have free cash, this is when a staged tranche is easier to justify.", "plan")
        w = market_weather()
        vix = next((r for r in w["rows"] if r["symbol"] == "^VIX"), {}).get("last")
        if vix and vix > 28:
            add("vix", "low", "Markets are unusually fearful", "VIX is above 28. Stay staged and avoid one large purchase.", "plan")
    except Exception:
        pass
    try:
        for r in recurring()[:3]:
            if r["annual"] >= 600:
                add("rec-" + r["merchant"], "low", f"{r['merchant']} costs about {CCY()} {r['annual']:,} a year", f"A charge of about {CCY()} {r['monthly']:,.0f} appears every month. Still using it?", "cards")
    except Exception:
        pass
    with get_db() as conn:
        gone = {r["insight_key"] for r in conn.execute("SELECT insight_key FROM dismissed_insights").fetchall()}
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted([i for i in items if i["id"] not in gone], key=lambda i: order[i["severity"]])


def dismiss(iid):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO dismissed_insights VALUES (?,?)", (iid, datetime.utcnow().isoformat()))


# ───────────────────────── your savings plan (the inputs behind every plan number) ─────────────────────────
def requirements():
    """Everything you tell the planner, in one place: what you put away each month, your goals, and the rules it follows."""
    S, b = settings(), BG.summary()
    inv = next((i for i in b["items"] if i["type"] == "invest"), None)
    sav = next((i for i in b["items"] if i["type"] == "savings"), None)
    usd = S["invest_usd"] or (round(inv["monthly"] / usd_rate(), 2) if inv else 0)
    goals = [{"name": g["name"], "amount": g["amount"], "date": g["date"]} for g in _goals()]
    others = sum(i["monthly"] for i in b["items"] if i["type"] in ("invest", "savings") and i is not inv and i is not sav)   # lines this form does not edit
    return {"other_base": round(others), "invest_usd": usd, "invest_label": S["invest_label"], "invest_base": round(usd * usd_rate()), "save_base": round(sav["monthly"]) if sav else 0,
            "goals": goals, "emergency_months": S["emergency_months"], "buffer_months": S["buffer_months"], "savings_rate": S["savings_rate"],
            "savings_cap": S["savings_cap"], "term_maturity": S["term_maturity"], "usd_base": usd_rate()}


def save_requirements(b):
    """Writes the monthly amounts to the budget lines (one source of truth), goals to the profile, rules to plan settings."""
    def num(k, lo=0.0):
        try:
            return max(lo, float(b.get(k) or 0))
        except (TypeError, ValueError):
            raise ValueError(f"{k.replace('_', ' ')} must be a number.")
    usd, save = num("invest_usd"), num("save_base")
    label = (str(b.get("invest_label") or "").strip() or DEFAULTS["invest_label"])[:40]
    goals = []
    for g in b.get("goals") or []:
        name, amt, d = str(g.get("name") or "").strip()[:40], float(g.get("amount") or 0), str(g.get("date") or "")
        if not name and not amt:
            continue
        try:
            date.fromisoformat(d)
        except ValueError:
            raise ValueError(f"Goal '{name or 'unnamed'}' needs a valid date.")
        if not name or amt <= 0:
            raise ValueError("Each goal needs a name and an amount above zero.")
        goals.append({"name": name, "amount": amt, "currency": CCY(), "target_date": d, "notes": ""})
    mat = str(b.get("term_maturity") or "")
    if mat:
        try:
            date.fromisoformat(mat)
        except ValueError:
            raise ValueError("Maturity must be a date.")
    items = BG.list_items()
    for typ, amt, lab in (("invest", round(usd * usd_rate(), 2), label), ("savings", save, "Savings")):
        cur = next((i for i in items if i["type"] == typ), None)
        if cur and not amt and typ == "savings":
            BG.delete_item(cur["id"])
        elif amt or cur:
            BG.save_item({"label": lab if typ == "invest" or not cur else cur["label"], "type": typ, "amount": amt, "frequency": "monthly"}, cur["id"] if cur else None)
    _ag().save_profile({"goals": goals})
    save_settings({"invest_usd": usd, "invest_label": label, "term_maturity": mat, "emergency_months": num("emergency_months"),
                   "buffer_months": num("buffer_months"), "savings_rate": num("savings_rate"), "savings_cap": num("savings_cap")})
    return requirements()


