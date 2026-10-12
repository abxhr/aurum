"""Composite financial-readiness score + auto-surfaced spending patterns —
the analytical layer behind the Dashboard's Briefing and Patterns cards.

Important constraints (from the owner):
1. Statements land on a rolling schedule across cards — some around the 5th,
   some the 15th, some the 25th — so the *real current calendar month* is
   never fully reported until it's over. Every month-over-month comparison in
   this file therefore excludes the current month entirely and only ever
   compares fully-completed months, no matter how much partial data has
   already landed for the current one.
2. Every such comparison is always the last fully-completed month vs the
   single month immediately before it — never an average across a longer
   trailing window. Two months, always.
Day-level windows (spend_load) are a separate concern — they anchor to the
latest data actually on file, not to calendar-month boundaries, so they're
unaffected by either constraint.
"""
import calendar
from datetime import date, timedelta
from statistics import median, mean
from database import get_db, base_ccy as CCY
import cards as C
import transactions as T


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def _active_cards():
    """{card_id: name} for cards that sent a statement in the last 75 days. A card that has gone quiet
    must not hold every month back, but it is reported separately as a data gap."""
    with get_db() as conn:
        rows = conn.execute("""SELECT c.id, c.name, MAX(s.statement_date) last FROM credit_cards c
                               LEFT JOIN card_statements s ON s.card_id=c.id GROUP BY c.id""").fetchall()
    cutoff = (date.today() - timedelta(days=75)).isoformat()
    return {r["id"]: r["name"] for r in rows if r["last"] and r["last"] >= cutoff}


def spend_cycles():
    """Spend per STATEMENT CYCLE (not calendar month) with a completeness verdict per cycle.
    A cycle is complete only when every active card has a statement for it (with transactions read).
    This is the one place that answers 'which months can I trust?'."""
    import transactions as TX
    active = _active_cards()
    with get_db() as conn:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id,name FROM credit_cards").fetchall()}
        rows = conn.execute("""SELECT s.id sid, s.card_id, s.statement_date, COALESCE(t.category,'') category,
                                      COALESCE(SUM(t.amount),0) total,
                                      (SELECT COUNT(*) FROM statement_transactions x WHERE x.statement_id=s.id) allc
                               FROM card_statements s
                               LEFT JOIN statement_transactions t ON t.statement_id=s.id AND t.amount>0 AND t.category!='Internal'
                               GROUP BY s.id, t.category""").fetchall()
    months = {}
    for r in rows:
        m = TX.cycle_month(r["statement_date"])
        ent = months.setdefault(m, {"cards": {}, "cats": {}, "detail": {}})
        ent["cards"][r["card_id"]] = ent["cards"].get(r["card_id"], 0) + r["total"]
        ent["detail"][r["card_id"]] = max(ent["detail"].get(r["card_id"], 0), r["allc"])
        if r["category"]:
            ent["cats"][r["category"]] = ent["cats"].get(r["category"], 0) + r["total"]
    out = []
    for m in sorted(months):
        e = months[m]
        have = {cid for cid in e["cards"] if e["detail"].get(cid, 0) > 0}
        missing = [names[cid] for cid in active if cid not in have]
        out.append({"month": m, "total": round(sum(e["cards"].values()), 2),
                    "by_card": {names[cid]: round(v, 2) for cid, v in e["cards"].items()},
                    "cats": {k: round(v, 2) for k, v in e["cats"].items()},
                    "complete": not missing and bool(have), "missing": missing})
    with get_db() as conn:
        cov = conn.execute("""SELECT c.id, c.name, MAX(s.statement_date) last_statement,
                                     (SELECT MAX(txn_date) FROM statement_transactions t WHERE t.card_id=c.id) covered_to
                              FROM credit_cards c LEFT JOIN card_statements s ON s.card_id=c.id GROUP BY c.id""").fetchall()
    coverage = [{"name": r["name"], "last_statement": r["last_statement"], "covered_to": r["covered_to"],
                 "active": r["id"] in active} for r in cov]
    return {"months": out, "active_cards": sorted(active.values()), "coverage": coverage}


def typical_card_spend(n=6, exclude=()):
    """Usual card spend per statement cycle over the last n COMPLETE cycles (sum of each category's median; "mean" keeps one-offs in). Six, not three: one trip
    (Aug-Sep here ran 19-21k against a 6-10k norm) should not set the pace. Every page that needs
    'what do I spend a month' reads this, so they cannot disagree."""
    done = [m for m in spend_cycles()["months"] if m["complete"]][-n:]
    cats = {c for m in done for c in m["cats"]} - set(exclude)
    usual = sum(median(m["cats"].get(c, 0) for m in done) for c in cats) if done else 0.0     # per-category median: a trip or a laptop is not a normal month
    return {"avg": round(usual, 2), "mean": round(sum(m["total"] for m in done) / len(done), 2) if done else 0.0,
            "cycles": [m["month"] for m in done], "n": len(done)}


def _data_as_of():
    """Most recent transaction date actually on file. Statements arrive monthly per
    card (not live), so anything framed as 'last 7 days' has to be measured from the
    latest data we actually have — not from today's wall-clock date — or it would
    read as a false lull every time between statement syncs."""
    # the date every active card's data reaches (the slowest card decides), so day-level views never
    # show a stretch where only some cards have reported
    active = _active_cards()
    with get_db() as conn:
        rows = conn.execute("SELECT card_id, MAX(txn_date) d FROM statement_transactions GROUP BY card_id").fetchall()
    ds = [r["d"] for r in rows if r["card_id"] in active and r["d"]] or [r["d"] for r in rows if r["d"]]
    return date.fromisoformat(min(ds)) if ds else date.today()


def _current_month_key():
    return date.today().strftime("%Y-%m")


def get_budget_summary():
    import budget as B
    return B.summary()


def _category_month_series(exclude_current=True):
    """category -> {cycle: total}, plus the sorted list of COMPLETE statement cycles (see spend_cycles).
    Incomplete cycles are left out entirely: comparing a cycle that is missing a card's statement
    with a full one is what produced fake 'spending down 77%' readings."""
    by_cat, months_sorted = {}, []
    for m in spend_cycles()["months"]:
        if not m["complete"]:
            continue
        months_sorted.append(m["month"])
        for cat, v in m["cats"].items():
            by_cat.setdefault(cat, {})[m["month"]] = v
    return by_cat, months_sorted


def _completed_monthly_totals(exclude_current=True):
    """[{month, total}] across all categories, oldest first, excluding the current
    real calendar month by default."""
    by_cat, months_sorted = _category_month_series(exclude_current)
    totals = {m: 0.0 for m in months_sorted}
    for series in by_cat.values():
        for m, v in series.items():
            totals[m] += v
    return [{"month": m, "total": round(totals[m], 2)} for m in months_sorted]


def financial_readiness():
    """A single 0-100 score blending budget health, spend trend, credit utilization,
    and upcoming-payment risk — with a breakdown so the number is explainable, not
    a black box."""
    import planner as PL
    B = PL.basics()
    # judged on what the money really does (typical net pay minus real spending and investing), not on the
    # typed-in budget, which can say "9k left over" while the cards say otherwise
    net = B["net_typical"] or 1
    budget_score = _clamp(50 + B["flow_typical"] / net * 250)       # breaking even -> 50, 20% of pay left -> 100, 20% short -> 0
    runway_score = _clamp(B["runway_months"] / PL.settings()["emergency_months"] * 100) if B["runway_months"] is not None else None

    stats = C.get_stats()

    dues = C.get_upcoming_dues(14)
    overdue = sum(1 for d in dues if d["urgency"] == "overdue")
    urgent = sum(1 for d in dues if d["urgency"] == "urgent")
    dues_score = _clamp(100 - overdue * 40 - urgent * 15)

    # spend trend: last FULLY COMPLETED month vs the one immediately before it —
    # always a straight two-month comparison, never an average across a longer
    # window. Deliberately never touches the current in-progress month — with
    # statements landing anywhere from the 5th to the 25th across different
    # cards, a "so far this month" figure would just be however many cards
    # happened to report first, not a real reading of this month's spend.
    monthly = _completed_monthly_totals()
    trend_score = None
    trend_note = None
    if len(monthly) >= 2:
        latest, prev = monthly[-1], monthly[-2]
        if prev["total"] > 0:
            trend_ratio = latest["total"] / prev["total"]
            trend_score = _clamp(100 - (trend_ratio - 1) * 120)
            trend_note = (f"{CCY()} {latest['total']:,.0f} in the {latest['month']} statement cycle (all cards complete) vs "
                          f"{CCY()} {prev['total']:,.0f} in {prev['month']}")

    # Components that can't be measured are left out (and the weights renormalised below),
    # instead of being scored as a flattering 100.
    components = [("Cash flow", budget_score, .30)]
    if runway_score is not None:
        components.append(("Emergency runway", runway_score, .25))
    components.append(("Bill due dates", dues_score, .15))
    if trend_score is not None:
        components.append(("Spend trend", trend_score, .30))

    total_weight = sum(w for _, _, w in components)
    score = round(sum(s * w for _, s, w in components) / total_weight)

    if score >= 80: verdict = "HEALTHY"
    elif score >= 60: verdict = "STABLE"
    elif score >= 40: verdict = "WATCH"
    else: verdict = "AT RISK"

    return {
        "score": score, "verdict": verdict,
        "breakdown": [{"label": l, "score": round(s)} for l, s, _ in components],
        "trend_available": trend_score is not None,
        "pace_note": trend_note,
        "as_of": _data_as_of().isoformat(),
    }


def spend_load():
    """Acute:chronic load, applied to spending — the most recent 7 days of statement
    data vs the expected 7-day share of the trailing 28 days. >1 means that stretch
    ran hotter than the recent baseline; <1 means quieter. Anchored to the latest
    transaction date on file (not today) since statements land monthly, not live —
    'last 7 days' means the last 7 days of data we actually have. This is a rolling
    day-based window, not a calendar-month one, so it's unaffected by the
    current-month-is-never-complete constraint the monthly comparisons have."""
    as_of = _data_as_of()
    cutoff28 = (as_of - timedelta(days=28)).isoformat()
    cutoff7 = (as_of - timedelta(days=7)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT txn_date, amount FROM statement_transactions WHERE amount>0 AND category!='Internal' AND txn_date>=?",
            (cutoff28,)).fetchall()
    last28 = sum(r["amount"] for r in rows)
    last7 = sum(r["amount"] for r in rows if r["txn_date"] >= cutoff7)
    expected7 = last28 / 4
    if expected7 <= 0:
        return {"ratio": None, "last7": round(last7, 2), "expected7": 0, "verdict": "NOT ENOUGH DATA",
                "as_of": as_of.isoformat()}
    ratio = round(last7 / expected7, 2)
    if ratio < 0.7: verdict = "QUIET WEEK"
    elif ratio <= 1.25: verdict = "STEADY"
    elif ratio <= 1.7: verdict = "ELEVATED"
    else: verdict = "HOT"
    return {"ratio": ratio, "last7": round(last7, 2), "expected7": round(expected7, 2), "verdict": verdict,
            "as_of": as_of.isoformat()}


MIN_SWING = 400          # a category must move by at least this much to be called out (12 -> 223 is not news)


def what_changed(min_pct=25.0):
    """One-line callout for whichever category moved the most, comparing the last
    fully-completed month strictly against the single month immediately before it
    (never an average across a longer window, never the current in-progress
    month — see module docstring). Monthly data is much noisier than the daily
    biometrics this is adapted from, so the bar to mention it (25%) is a lot
    higher than a daily version would use."""
    by_cat, months_sorted = _category_month_series()
    if len(months_sorted) < 2:
        return None
    latest_month, prev_month = months_sorted[-1], months_sorted[-2]
    best = None
    for cat, series in by_cat.items():
        latest_val = series.get(latest_month)
        prev_val = series.get(prev_month)
        if latest_val is None or not prev_val:
            continue
        pct = (latest_val - prev_val) / abs(prev_val) * 100
        if abs(latest_val - prev_val) < MIN_SWING:
            continue
        if best is None or abs(latest_val - prev_val) > abs(best["latest"] - best["prev"]):
            best = {"category": cat, "latest": round(latest_val, 2), "prev": round(prev_val, 2),
                    "month": latest_month, "prev_month": prev_month, "pct_change": round(pct, 1),
                    "direction": "up" if pct > 0 else "down"}
    if not best or abs(best["pct_change"]) < min_pct:
        return None
    best["summary"] = (f"{best['category']} was {best['direction']} {abs(best['pct_change']):.0f}% in {best['month']} "
                        f"vs {best['prev_month']} ({CCY()} {best['latest']:,.0f} vs {CCY()} {best['prev']:,.0f})")
    return best


def category_anomalies(min_pct=40.0):
    """Flags every category whose last fully-completed month swung more than
    min_pct against the single month immediately before it — always a straight
    two-month comparison, never a z-score against a longer trailing baseline
    (that needs several months of history to mean anything, which this owner's
    data mostly doesn't have yet). Never touches the current in-progress
    month — see module docstring."""
    by_cat, months_sorted = _category_month_series()
    if len(months_sorted) < 2:
        return []
    latest_month, prev_month = months_sorted[-1], months_sorted[-2]
    alerts = []
    for cat, series in by_cat.items():
        latest_val = series.get(latest_month)
        prev_val = series.get(prev_month)
        if latest_val is None or not prev_val:
            continue
        pct = (latest_val - prev_val) / abs(prev_val) * 100
        if abs(pct) < min_pct or abs(latest_val - prev_val) < MIN_SWING:
            continue
        alerts.append({
            "category": cat, "month": latest_month, "prev_month": prev_month,
            "value": round(latest_val, 2), "prev_value": round(prev_val, 2),
            "pct_change": round(pct, 1), "direction": "up" if pct > 0 else "down",
        })
    alerts.sort(key=lambda a: -abs(a["value"] - a["prev_value"]))
    return alerts


def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def find_patterns(min_months=9, min_abs_r=0.6, limit=8):      # r from 5 points is noise (it "found" r=1.0)
    """Auto-surfaced correlations between category pairs' monthly spend totals,
    using only fully-completed months (see module docstring) so a partial current
    month can't skew the correlation. Correlation, not causation — a starting
    point for what to look into, same spirit as any biometric-correlation
    feature: only ever shown with the sample size (n = number of shared months)
    so it can't be mistaken for certainty."""
    by_cat, months_sorted = _category_month_series()
    months_available = len(months_sorted)
    cats = sorted(c for c in by_cat if len(by_cat[c]) >= min_months)

    patterns = []
    for i in range(len(cats)):
        for j in range(i + 1, len(cats)):
            a, b = cats[i], cats[j]
            common = sorted(set(by_cat[a]) & set(by_cat[b]))
            if len(common) < min_months:
                continue
            r = _pearson([by_cat[a][m] for m in common], [by_cat[b][m] for m in common])
            if r is None or abs(r) < min_abs_r:
                continue
            patterns.append({
                "a": a, "b": b, "r": round(r, 2), "n": len(common),
                "direction": "up" if r > 0 else "down",
                "strength": "strong" if abs(r) >= 0.7 else "moderate",
            })
    patterns.sort(key=lambda p: -abs(p["r"]))
    return {"patterns": patterns[:limit], "months_available": months_available, "min_months": min_months}


def category_trends(top_n=6):
    """Monthly spend series for the top N categories by total spend, across every
    fully-completed month on file — the multi-line trend a single-month donut
    can't show. Same current-month exclusion as the rest of this module."""
    by_cat, months_sorted = _category_month_series()
    if len(months_sorted) < 2:
        return {"months": months_sorted, "series": []}
    totals = {cat: sum(series.values()) for cat, series in by_cat.items()}
    top_cats = sorted(totals, key=lambda c: -totals[c])[:top_n]
    series = [{"category": cat, "values": [round(by_cat[cat].get(m, 0), 2) for m in months_sorted]}
              for cat in top_cats]
    return {"months": months_sorted, "series": series}


WATCH_CATEGORIES = ["Bills & Utilities", "Subscriptions"]


def subscription_creep():
    """Tracks the categories that quietly grow over time — Bills & Utilities and
    Subscriptions — the same way every other comparison in this module works:
    last fully-completed month strictly against the one immediately before it."""
    by_cat, months_sorted = _category_month_series()
    totals_by_month = [round(sum(by_cat.get(cat, {}).get(m, 0) for cat in WATCH_CATEGORIES), 2)
                        for m in months_sorted]
    result = {"months": months_sorted, "totals": totals_by_month, "trend_available": False}
    if len(months_sorted) >= 2 and totals_by_month[-2] > 0:
        latest, prev = totals_by_month[-1], totals_by_month[-2]
        pct = round((latest - prev) / prev * 100, 1)
        result.update({
            "trend_available": True, "latest_month": months_sorted[-1], "prev_month": months_sorted[-2],
            "latest_total": latest, "prev_total": prev, "pct_change": pct,
            "creeping": latest > prev and pct >= 10 and latest - prev >= 100,
        })
    return result


_ASSET_CLASS_LABELS = {"stock": "Stocks", "etf": "ETFs", "mutual_fund": "Mutual Funds", "bond": "Bonds",
                        "savings": "Savings", "gold": "Gold", "crypto": "Crypto", "cash": "Cash", "other": "Other"}


def daily_plan(allocation_drift=None):
    """A short, prioritized list of what actually needs attention right now,
    synthesized from upcoming dues, budget surplus, spend load, and allocation
    drift — so opening the app answers 'what should I actually do', instead of
    leaving the reader to piece that together from five separate cards."""
    items = []
    dues = C.get_upcoming_dues(14)
    overdue = [d for d in dues if d["urgency"] == "overdue"]
    urgent = [d for d in dues if d["urgency"] == "urgent"]
    soon = [d for d in dues if d["urgency"] == "soon"]

    if overdue:
        total = sum((d["total_due"] or 0) - (d["paid_amount"] or 0) for d in overdue)
        items.append({"priority": "high", "icon": "⚠", "page": "cards",
                      "text": f"{len(overdue)} overdue payment{'s' if len(overdue) != 1 else ''} — "
                              f"{CCY()} {total:,.0f} total. Pay these first."})
    if urgent:
        total = sum((d["total_due"] or 0) - (d["paid_amount"] or 0) for d in urgent)
        items.append({"priority": "high", "icon": "⏰", "page": "cards",
                      "text": f"{len(urgent)} payment{'s' if len(urgent) != 1 else ''} due within 3 days — "
                              f"{CCY()} {total:,.0f}."})

    import planner as PL
    B = PL.basics()
    if B["flow_avg"] < 0:      # a short typical month that the bonus covers is the Plan page's call, not a daily alarm
        items.append({"priority": "high", "icon": "📉", "page": "budget",
                      "text": f"Even with your bonus you are {CCY()} {abs(B['flow_avg']):,.0f} a month short of spending and investing what you plan "
                              f"(average month, real card spend)."})

    if allocation_drift:
        worst = max(allocation_drift, key=lambda a: abs(a["drift"]))
        if abs(worst["drift"]) >= 15:
            label = _ASSET_CLASS_LABELS.get(worst["label"], worst["label"])
            verb = "over" if worst["drift"] > 0 else "under"
            items.append({"priority": "medium", "icon": "⚖", "page": "all",
                          "text": f"{label} is {abs(worst['drift']):.0f}pp {verb} your target allocation — "
                                  f"consider rebalancing."})

    if soon and not overdue and not urgent:
        total = sum((d["total_due"] or 0) - (d["paid_amount"] or 0) for d in soon)
        items.append({"priority": "low", "icon": "📅", "page": "cards",
                      "text": f"{len(soon)} payment{'s' if len(soon) != 1 else ''} due this week — "
                              f"{CCY()} {total:,.0f}."})

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: order[i["priority"]])
    if not items:
        items.append({"priority": "low", "icon": "✓", "page": None,
                      "text": "Nothing urgent: no payments are due and your average month is covered."})
    return items[:4]


def daily_spend_heatmap(weeks=12):
    """Daily spend intensity for the last N weeks, GitHub-contribution-grid style.
    Unlike the monthly comparisons above, this is a day-level window anchored to
    the latest data on file (like spend_load) — it intentionally includes
    whatever of the current month has actually landed, since a calendar heatmap
    is expected to show today, not stop at the last completed month."""
    as_of = _data_as_of()
    start = as_of - timedelta(days=weeks*7 - 1)
    start -= timedelta(days=(start.weekday() + 1) % 7)  # back up to the preceding Sunday
    with get_db() as conn:
        rows = conn.execute(
            "SELECT txn_date, SUM(amount) total FROM statement_transactions "
            "WHERE amount>0 AND category!='Internal' AND txn_date>=? GROUP BY txn_date",
            (start.isoformat(),)).fetchall()
    by_date = {r["txn_date"]: r["total"] for r in rows}
    days = []
    d = start
    while d <= as_of:
        iso = d.isoformat()
        days.append({"date": iso, "weekday": d.weekday(), "value": round(by_date.get(iso, 0), 2)})
        d += timedelta(days=1)
    return {"days": days, "as_of": as_of.isoformat()}


def _linreg(xs, ys):
    n = len(xs)
    mx, my = sum(xs)/n, sum(ys)/n
    denom = sum((x-mx)**2 for x in xs)
    if denom == 0:
        return 0.0, my
    slope = sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / denom
    return slope, my - slope*mx


def correlation_scatter(limit=2):
    """The underlying (x, y) monthly pairs behind the top correlated category
    pairs from find_patterns(), plus a fitted trendline — so the strongest
    relationships can be seen as an actual scatter, not just a text summary."""
    found = find_patterns(limit=limit)
    by_cat, months_sorted = _category_month_series()
    out = []
    for p in found["patterns"][:limit]:
        common = sorted(set(by_cat[p["a"]]) & set(by_cat[p["b"]]))
        xs = [by_cat[p["a"]][m] for m in common]
        ys = [by_cat[p["b"]][m] for m in common]
        slope, intercept = _linreg(xs, ys)
        x_lo, x_hi = min(xs), max(xs)
        out.append({
            "a": p["a"], "b": p["b"], "r": p["r"], "n": p["n"], "strength": p["strength"],
            "points": [{"x": x, "y": y, "month": m} for x, y, m in zip(xs, ys, common)],
            "trendline": [{"x": x_lo, "y": slope*x_lo+intercept}, {"x": x_hi, "y": slope*x_hi+intercept}],
        })
    return out


def category_mix(top_n=6):
    """This month's category spend mix (% of total) vs the month before it, for
    the top categories by combined spend — a shape comparison rather than a
    single-number one. (Not a comparison against an 'ideal' target: this app has
    no per-category budget targets on file, only the coarse income/expense/
    savings/invest lines in the Budget tab, which don't map onto spend
    categories like Groceries or Dining — so comparing against last month's
    real mix is the honest version of this, not a fabricated ideal.)"""
    by_cat, months_sorted = _category_month_series()
    if len(months_sorted) < 2:
        return None
    latest_month, prev_month = months_sorted[-1], months_sorted[-2]
    totals = {cat: sum(s.values()) for cat, s in by_cat.items()}
    top_cats = sorted(totals, key=lambda c: -totals[c])[:top_n]
    latest_total = sum(by_cat[c].get(latest_month, 0) for c in top_cats) or 1
    prev_total = sum(by_cat[c].get(prev_month, 0) for c in top_cats) or 1
    return {
        "categories": top_cats, "latest_month": latest_month, "prev_month": prev_month,
        "latest_pct": [round(by_cat[c].get(latest_month, 0)/latest_total*100, 1) for c in top_cats],
        "prev_pct": [round(by_cat[c].get(prev_month, 0)/prev_total*100, 1) for c in top_cats],
    }


def income_basis():
    """One answer to 'what do I earn?' for every page. Payslips win over the typed-in budget line.
    gross/net come from the most recent payslip that isn't a bonus or one-off month (gross more than
    1.5x the 12-month median), so a May bonus doesn't make every other month look like a shortfall."""
    from statistics import median, mean
    import payslips as PS
    budget = get_budget_summary()
    rows = PS.list_all()
    if not rows:
        return {"source": "budget", "gross": budget["income"], "net": budget["income"], "deductions": 0,
                "period": None, "budget_income": budget["income"]}
    last12 = rows[-12:]
    import planner as PL
    since = PL.settings()["income_since"]
    cur = [r for r in last12 if r["period"] >= since] if since else last12      # pay level you are on now (e.g. after a promotion)
    cur = cur or last12
    typical = median(PS.base_net(r) for r in cur)
    bonus = max(0.0, mean(PS.base_net(r) for r in last12) - median(PS.base_net(r) for r in last12))      # annual bonus spread over 12 months
    med_g = median(r["gross"] for r in last12)
    pick = next((r for r in reversed(rows) if r["gross"] <= 1.5 * med_g), rows[-1])
    return {"source": "payslip", "period": pick["period"], "gross": pick["gross"], "net": pick["net"],
            "deductions": pick["deductions"], "latest_period": rows[-1]["period"],
            "typical_net": round(typical, 2), "avg_net": round(typical + bonus, 2), "bonus_per_month": round(bonus, 2), "income_since": since or None,
            "mean_net_12": round(mean(r["net"] for r in last12), 2),
            "mean_gross_12": round(mean(r["gross"] for r in last12), 2),
            "budget_income": budget["income"]}
