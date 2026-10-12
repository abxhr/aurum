"""Two answers a beginner needs and nothing else in the app gave directly:
  needs_wants   where the money goes in the only split that matters for building wealth: needs, wants, and what you keep
  leaks         money you are losing for no benefit (fees, exchange-rate markup, unused rewards, idle cash), added up per year

Merchants whose category does not decide need-versus-want (a restaurant, a shop, a gym) are tagged once by the cheap Decisions classifier and remembered forever,
so this costs a few cents the first time and nothing afterwards."""
import threading
from concurrent.futures import ThreadPoolExecutor

import budget as BG
import decide as DC
import planner as PL
import plugins
from database import get_db, base_ccy as CCY

RULE = {"Groceries": "need", "Bills & Utilities": "need", "Government & Fines": "need", "Education": "need", "Fees & Charges": "need",
        "Entertainment": "want", "Travel": "want", "Electronics": "want", "Subscriptions": "want", "Charity": "give"}
ASK = {"Dining", "Shopping", "Transport", "Health & Fitness", "Personal Care", "Home & Furniture", "Other"}
OFF_NEED = ("family", "rent", "tuition", "insurance", "school", "parent", "loan", "bill")
_LOCK = threading.Lock()
_BUSY = {"on": False}


def _ensure():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS merchant_need (mkey TEXT PRIMARY KEY, need TEXT NOT NULL, conf REAL DEFAULT 0, source TEXT DEFAULT 'ai')")


def _tags():
    _ensure()
    with get_db() as conn:
        return {r["mkey"]: r["need"] for r in conn.execute("SELECT mkey, need FROM merchant_need").fetchall()}


def set_need(mkey, need):
    """Your own correction wins over the classifier."""
    if need not in ("need", "want", "give"):
        raise ValueError("need, want or give")
    _ensure()
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO merchant_need VALUES (?,?,1,'user')", (mkey, need))


def _classify(pending):
    """pending: [(mkey, label, category)]. One cheap classification each; failures are skipped and tried again next time."""
    def one(p):
        mkey, label, cat = p
        q = [{"name": "need", "instructions": "Is this a necessary living cost for an ordinary working adult, or a discretionary want? Judge the merchant, not the amount.",
              "choices": [("need", "groceries, commuting, health care, utilities, essential services, family support"), ("want", "restaurants and cafes for pleasure, shopping for pleasure, gyms, leisure, treats"),
                          ("give", "charity or donations")]}]
        ans, _ = DC.decide(f"{label} (category: {cat})", q, "needs vs wants")
        a = (ans or {}).get("need")
        return (mkey, a["choice"], a["confidence"]) if a and a.get("choice") in ("need", "want", "give") else None
    with ThreadPoolExecutor(8) as ex:
        res = [r for r in ex.map(one, pending) if r]
    if res:
        _ensure()
        with get_db() as conn:
            conn.executemany("INSERT OR IGNORE INTO merchant_need VALUES (?,?,?, 'ai')", res)


def _tag_in_background(pending):
    with _LOCK:
        if _BUSY["on"]:
            return
        _BUSY["on"] = True

    def run():
        try:
            _classify(pending)
        finally:
            _BUSY["on"] = False
    threading.Thread(target=run, daemon=True).start()


def merchants(limit=40):
    """Largest merchants over the last six cycles with how each is sorted, so a wrong tag can be fixed."""
    import transactions as TX
    tags = _tags()
    with get_db() as conn:
        src = {r["mkey"]: r["source"] for r in conn.execute("SELECT mkey, source FROM merchant_need").fetchall()}
    agg = {}
    for t in PL._txns("c6"):
        if t["amount"] <= 0 or t["category"] == "Internal":
            continue
        k = TX.normalize_merchant(t["description"])
        e = agg.setdefault(k, {"key": k, "label": " ".join((t["description"] or "").split())[:40], "category": t["category"], "total": 0.0})
        e["total"] += t["amount"]
    out = []
    for e in sorted(agg.values(), key=lambda x: -x["total"])[:limit]:
        side = tags.get(e["key"]) or (RULE.get(e["category"]) if e["category"] not in ASK else "")
        out.append({**e, "total": round(e["total"]), "side": side or "", "by": src.get(e["key"], "rule" if side else "")})
    return out


def needs_wants():
    import transactions as TX
    S = PL.settings()
    B = PL.basics()
    tags = _tags()
    lumpc = BG.lump_categories(BG.list_items())             # irregular lines (trips, tuition) are budgeted at their plan, not averaged from the cards
    rows = [t for t in PL._txns("c6") if t["amount"] > 0 and t["category"] not in ("Internal",) and t["category"] not in lumpc]
    cycles = max(1, B["card_cycles"])
    per = {}                                                   # cycle -> side -> amount
    pend = {}
    for t in rows:
        cat = t["category"] or "Other"
        k = TX.normalize_merchant(t["description"])
        side = tags.get(k) or (RULE.get(cat) if cat not in ASK else None)
        cy = per.setdefault(TX.cycle_month(t["sdate"]) if t.get("sdate") else t["txn_date"][:7], {"need": 0.0, "want": 0.0, "give": 0.0, "unsorted": 0.0})
        if side:
            cy[side] += t["amount"]
        else:
            cy["unsorted"] += t["amount"]
            p = pend.setdefault(k, [k, " ".join((t["description"] or "").split())[:50], cat, 0.0]); p[3] += t["amount"]
    from statistics import median
    tot = {k: (median(c[k] for c in per.values()) if per else 0.0) for k in ("need", "want", "give", "unsorted")}      # a usual month, one-offs ignored
    pending = sorted(pend.values(), key=lambda p: -p[3])[:80]
    tagging = bool(pending) and bool(DC.available())
    if tagging:
        _tag_in_background([(p[0], p[1], p[2]) for p in pending])
    off = {"need": 0.0, "want": 0.0}
    for i in BG.list_items():
        if i["type"] == "expense" and i.get("off_card"):
            side = "need" if any(w in i["label"].lower() for w in OFF_NEED + tuple(plugins.get("OFF_NEED_EXTRA", []))) else "want"
            off[side] += i["monthly"]
    m = tot
    needs, wants = m["need"] + off["need"] + 0, m["want"] + off["want"]
    # the GT-style irregular lines are budgeted instead of averaged: add their plan to needs/wants by their category
    for i in BG.list_items():
        if i["type"] == "expense" and i.get("lump"):
            side = "need" if set(i["categories"]) & {"Education", "Bills & Utilities", "Groceries"} else "want"
            if side == "need":
                needs += i["monthly"]
            else:
                wants += i["monthly"]
    take = B["net_typical"] or 1
    keep = B["saved"]
    giving = m["give"]
    unsorted = m["unsorted"]
    out = {"take_home": take, "needs": round(needs), "wants": round(wants), "giving": round(giving), "unsorted": round(unsorted), "saved": round(keep),
           "left": round(take - needs - wants - giving - unsorted - keep),
           "pct": {"needs": round(needs / take * 100), "wants": round(wants / take * 100), "keep": round(keep / take * 100)},
           "guide": {"needs": 50, "wants": 30, "keep": 20}, "tagging": tagging, "pending": len(pending),
           "cycles": cycles}
    over = wants - 0.3 * take
    out["wants_over_guide_yearly"] = round(over * 12) if over > 0 else 0
    out["note"] = ("A common starting guide is 50% needs, 30% wants, 20% saved or invested. It is a guide, not a rule: in an expensive city with family support, needs run higher. "
                   "Wants are the part you can change without hurting anyone.")
    return out


def leaks():
    """Money going out for nothing, per year, with what to do about each. 'sure' ones cost you with no benefit; 'choice' ones are spending you may decide to keep."""
    items = []
    try:
        fx = PL.fx_report("m12")
        t = fx["total"]
        if t["extra_cost_base"] >= 100:
            best = f" Paying with {fx['cheapest_card']} would have cost about {CCY()} {fx['saving_if_all_on_cheapest_base']:,.0f} less." if fx.get("cheapest_card") and fx["saving_if_all_on_cheapest_base"] >= 50 else ""
            items.append({"kind": "sure", "title": "Exchange-rate markup on foreign spending", "per_year": round(t["extra_cost_base"]),
                          "do": f"Foreign purchases cost about {t['effective_markup_pct']}% above the real exchange rate.{best} See the Abroad tab for the cheapest card per currency."})
    except Exception:
        pass
    try:
        cu = PL.card_usage("m12")["cards"]
        fees = sum(c["annual_fee"] + c["interest_and_late_fees"] for c in cu)
        if fees >= 50:
            items.append({"kind": "sure", "title": "Card fees and interest", "per_year": round(fees), "do": "Pay statements in full and ask the bank to waive annual fees, or drop the card."})
    except Exception:
        pass
    try:
        import rewards as RW
        un = RW.report()["unredeemed"]
        if un >= 200:
            items.append({"kind": "sure", "title": "Rewards you have not used", "per_year": round(un), "once": True, "do": "One-off: spend or redeem the credits on your cards before they expire."})
    except Exception:
        pass
    try:
        rec = PL.recurring()
        tot = sum(r["annual"] for r in rec if r["annual"] >= 200)
        if tot >= 200:
            items.append({"kind": "choice", "title": "Recurring charges", "per_year": round(tot), "do": "Still using them? " + ", ".join(f"{r['merchant']} {CCY()} {r['annual']:,.0f}" for r in rec[:3]) + "."})
    except Exception:
        pass
    try:
        p = PL.money_plan()
        if p["idle_annual_gain_base"] >= 100:
            items.append({"kind": "sure", "title": "Cash sitting in current accounts", "per_year": p["idle_annual_gain_base"], "do": f"Move {CCY()} {p['idle_current_base']:,.0f} to your savings account; it earns about {PL.settings()['savings_rate']:g}% there."})
    except Exception:
        pass
    try:
        nw = needs_wants()
        if nw["wants_over_guide_yearly"] >= 1000 and nw["unsorted"] < nw["take_home"] * 0.1:
            items.append({"kind": "choice", "title": "Wants above the 30% guide", "per_year": nw["wants_over_guide_yearly"],
                          "do": f"Wants are {nw['pct']['wants']}% of take-home. Trimming to 30% would free this much a year to invest. Only if it suits you."})
    except Exception:
        pass
    sure = sum(i["per_year"] for i in items if i["kind"] == "sure" and not i.get("once"))
    once = sum(i["per_year"] for i in items if i.get("once"))
    choice = sum(i["per_year"] for i in items if i["kind"] == "choice")
    return {"items": sorted(items, key=lambda i: (i["kind"] != "sure", -i["per_year"])), "sure_per_year": round(sure), "once": round(once), "choice_per_year": round(choice),
            "invested_value_10y": round(sure * (((1 + 0.08 / 12) ** 120 - 1) / (0.08 / 12)) / 12) if sure else 0}
