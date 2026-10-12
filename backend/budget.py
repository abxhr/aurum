"""Budget: lines you plan (income, expenses, savings, investments), compared with what really happened.

- A line can repeat monthly, quarterly or yearly; everything is converted to a monthly figure.
- An income line can follow your payslips (typical month, or the 12-month average incl. bonus).
- An expense line can be linked to spending categories; its "actual" is then the average of the last
  6 COMPLETE statement cycles (see analytics.spend_cycles / typical_card_spend), so planned vs actual compares like with like.
- Payroll deductions are taken before net pay, so they are shown for information and never added again.
"""
from statistics import median, mean
from datetime import datetime
from database import get_db, base_ccy as CCY

TYPES = ("income", "expense", "savings", "invest")
FREQ = {"monthly": 1, "quarterly": 3, "semester": 4, "yearly": 12}      # semester = every 4 months (a cost per school term)
SOURCES = ("manual", "payslip_typical", "payslip_average")
MIN_UNBUDGETED = 150          # per month: smaller categories aren't worth a budget line


def ensure_columns():
    with get_db() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(budget_items)").fetchall()}
        for col, ddl in [("frequency", "ALTER TABLE budget_items ADD COLUMN frequency TEXT DEFAULT 'monthly'"),
                         ("source", "ALTER TABLE budget_items ADD COLUMN source TEXT DEFAULT 'manual'"),
                         ("categories", "ALTER TABLE budget_items ADD COLUMN categories TEXT DEFAULT ''")]:
            if col not in cols:
                conn.execute(ddl)


def _payslip_income():
    import payslips as PS
    rows = PS.list_all()[-12:]
    if not rows:
        return None
    nets = [r["net"] for r in rows]
    import planner as PL
    since = PL.settings()["income_since"]          # same pay-level window as analytics.income_basis
    cur = [PS.base_net(r) for r in rows if r["period"] >= since] if since else nets
    return {"typical": round(median(cur or nets), 2), "average": round(mean(nets), 2)}


def list_items():
    ensure_columns()
    ps = _payslip_income()
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM budget_items WHERE active=1 ORDER BY sort_order, id").fetchall()]
    for r in rows:
        r["frequency"] = r.get("frequency") or "monthly"
        r["source"] = r.get("source") or "manual"
        raw = [c for c in (r.get("categories") or "").split(",") if c]
        r["off_card"] = "__offcard" in raw          # paid outside the cards: transfers, tuition...
        r["lump"] = "__lump" in raw                 # irregular (trips, tuition): budgeted as a plan, its card spend kept out of the usual-month average
        r["categories"] = [c for c in raw if c not in ("__offcard", "__lump")]
        base = float(r["amount"] or 0)
        if r["type"] == "income" and ps and r["source"] == "payslip_typical":
            base = ps["typical"]
        elif r["type"] == "income" and ps and r["source"] == "payslip_average":
            base = ps["average"]
        r["amount_shown"] = base                     # per its own frequency
        r["monthly"] = round(base / FREQ.get(r["frequency"], 1), 2)
    return rows


def summary():
    items = list_items()
    tot = {t: round(sum(i["monthly"] for i in items if i["type"] == t), 2) for t in TYPES}
    return {"items": items, "income": tot["income"], "expenses": tot["expense"], "savings": tot["savings"],
            "invest": tot["invest"], "surplus": round(tot["income"] - tot["expense"] - tot["savings"] - tot["invest"], 2)}


def lump_categories(items):
    return {c for i in items if i["type"] == "expense" and i.get("lump") for c in i["categories"]}


def _trailing_monthly(cats, months=12):
    """What the cards spent on these categories over the last `months`, net of refunds, as a monthly figure."""
    from datetime import date, timedelta
    since = (date.today() - timedelta(days=round(months * 30.44))).isoformat()
    with get_db() as conn:
        r = conn.execute(f"SELECT COALESCE(SUM(amount),0) t FROM statement_transactions WHERE category IN ({','.join('?' * len(cats))}) AND txn_date>=?",
                         [*cats, since]).fetchone()
    return round(r["t"] / months, 2)


def actuals():
    """What really happened, per spending category and per linked budget line."""
    import analytics as AN, payslips as PS
    items = list_items()
    cyc = [m for m in AN.spend_cycles()["months"] if m["complete"]][-6:]      # same window as analytics.typical_card_spend
    n = len(cyc)
    exp = [i for i in items if i["type"] == "expense"]
    lumpc = lump_categories(items)
    cats = {c for m in cyc for c in m["cats"]} - lumpc
    by_cat = {c: round(median(m["cats"].get(c, 0) for m in cyc), 2) for c in cats} if n else {}      # usual month, one-offs ignored (same as typical_card_spend)
    linked = {c for i in exp for c in i["categories"]}
    per_item = {i["id"]: (_trailing_monthly(i["categories"]) if i["lump"] else round(sum(by_cat.get(c, 0) for c in i["categories"]), 2)) for i in exp if i["categories"]}
    lump_plan = round(sum(i["monthly"] for i in exp if i["lump"]), 2)
    import bank_tx as BT                                             # what the bank accounts say about lines paid outside the cards
    for i in exp:
        lab = i["label"].lower()
        if i["off_card"] and _is_family(lab):
            per_item[i["id"]] = BT.family()["per_month"]
        elif i["lump"] and any(w in lab for w in ("tuition", "education", "school", "college")):
            per_item[i["id"]] = round(per_item.get(i["id"], 0) + BT.monthly_kind("education"), 2)
    unsorted = [{"id": i["id"], "label": i["label"], "monthly": i["monthly"]} for i in exp if not i["categories"] and not i["off_card"]]
    unbudgeted = sorted(({"category": c, "monthly": round(v)} for c, v in by_cat.items()
                         if c not in linked and v >= MIN_UNBUDGETED), key=lambda x: -x["monthly"])
    card_total = round(sum(by_cat.values()), 2)
    off_card_plan = round(sum(i["monthly"] for i in exp if i["off_card"]), 2)
    inc = sum(i["monthly"] for i in items if i["type"] == "income")
    sav = sum(i["monthly"] for i in items if i["type"] in ("savings", "invest"))
    realistic_expenses = round(card_total + off_card_plan + lump_plan, 2)
    # only meaningful once every spending line says whether it lives on the cards; otherwise planned and
    # actual spending would be counted twice
    realistic = round(inc - realistic_expenses - sav, 2) if not unsorted and n else None
    ded = {}
    pl = PS.list_all()[-12:]          # same 12 payslips the take-home figure uses
    for r in pl:
        for it in r["items"]:
            if it["kind"] == "deduction":
                ded[it["label"]] = ded.get(it["label"], 0) + it["amount"]
    payroll = sorted(({"label": k, "monthly": round(v / len(pl))} for k, v in ded.items()), key=lambda x: -x["monthly"]) if pl else []
    return {"cycles": [m["month"] for m in cyc], "by_category": by_cat, "items": per_item, "unbudgeted": unbudgeted,
            "card_total": card_total, "off_card_plan": off_card_plan, "lump_plan": lump_plan, "realistic_expenses": realistic_expenses,
            "realistic_surplus": realistic, "unsorted": unsorted,
            "payroll": {"monthly": sum(p["monthly"] for p in payroll), "items": payroll, "payslips": len(pl)}}


def save_item(b, bid=None):
    ensure_columns()
    t = b.get("type", "expense")
    if t not in TYPES:
        raise ValueError("Pick a type: income, expense, savings or investment.")
    label = (b.get("label") or "").strip()
    if not 1 <= len(label) <= 40:
        raise ValueError("Give the line a name (up to 40 characters).")
    try:
        amount = float(b.get("amount") or 0)
    except (TypeError, ValueError):
        raise ValueError("Amount must be a number.")
    if amount < 0:
        raise ValueError("Amount can't be negative.")
    freq = b.get("frequency") or "monthly"
    if freq not in FREQ:
        raise ValueError("Pick how often: monthly, quarterly or yearly.")
    source = b.get("source") or "manual"
    if source not in SOURCES or (t != "income" and source != "manual"):
        source = "manual"
    cats_list = [c for c in (b.get("categories") or []) if c and "," not in c and c != "__offcard"]
    if t == "expense" and b.get("off_card") and not cats_list:
        cats_list = ["__offcard"]
    if t == "expense" and b.get("lump") and cats_list:
        cats_list.append("__lump")
    cats = ",".join(cats_list) if t == "expense" else ""
    with get_db() as conn:
        if bid:
            conn.execute("UPDATE budget_items SET label=?,amount=?,type=?,category=?,frequency=?,source=?,categories=? WHERE id=?",
                         (label, amount, t, t, freq, source, cats, bid))
            return bid
        mx = conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 s FROM budget_items").fetchone()["s"]
        cur = conn.execute("""INSERT INTO budget_items(category,label,amount,currency,type,sort_order,frequency,source,categories)
                              VALUES (?,?,?,?,?,?,?,?,?)""", (t, label, amount, CCY(), t, mx, freq, source, cats))
        return cur.lastrowid


def delete_item(bid):
    with get_db() as conn:
        conn.execute("DELETE FROM budget_items WHERE id=?", (bid,))


def checkup():
    """What the budget probably still misses, from the data. 'ask' items need your input; 'tip' items are worth knowing."""
    import re
    import payslips as PS
    items, act = list_items(), actuals()
    exp = [i for i in items if i["type"] == "expense"]
    words = " ".join(i["label"].lower() for i in exp)
    out = []
    add = lambda level, title, body: out.append({"level": level, "title": title, "body": body})
    latest = (PS.list_all() or [{"items": []}])[-1]["items"]
    housing = sum(i["amount"] for i in latest if i["kind"] == "earning" and "accommodation" in i["label"].lower())
    if not re.search(r"rent|housing|mortgage|accommodation", words):
        add("ask", "No rent or housing line", f"Your pay includes a housing allowance of {CCY()} {housing:,.0f}, but no budget line is rent or housing. If you pay rent or a housing cost from your bank account, add it as a line paid outside your cards; if the company houses you, ignore this." if housing else
            "No budget line is rent or housing. If you pay it from your bank account, add it as a line paid outside your cards.")
    bills = act["by_category"].get("Bills & Utilities", 0)
    if bills < 300:
        add("ask", "Phone, internet and electricity look too low", f"Your cards show only {CCY()} {bills:,.0f} a month on bills and utilities. If these are paid by direct debit from your bank account they are missing from your spending; add them as lines paid outside your cards.")
    add("ask", "Yearly costs not in the budget", "Car registration and insurance, health insurance, permit or document renewals and card annual fees. Add each as a yearly line (marked irregular) so the cost is spread over the months, or tell me which apply to you.")
    for i in exp:
        a = act["items"].get(i["id"])
        if i["lump"] and a is not None:
            add("tip", f"{i['label']}: cards show {CCY()} {a * 12:,.0f} net over the last 12 months", f"The plan is {CCY()} {i['monthly'] * 12:,.0f} a year. Irregular lines are judged over 12 months, not by one month.")
        elif a is not None and i["monthly"] and abs(a - i["monthly"]) > max(300, i["monthly"] * 0.4):
            add("tip", f"{i['label']}: you plan {CCY()} {i['monthly']:,.0f}, the cards say {CCY()} {a:,.0f}", "Change the plan to what you really spend, or decide to spend less and keep the number.")
    if act["unbudgeted"]:
        add("tip", "Card spending with no line", ", ".join(f"{u['category']} {CCY()} {u['monthly']:,}" for u in act["unbudgeted"]) + " a month.")
    import bank_tx as BT
    dd = BT.family()
    if dd["months"] and dd["extras"] >= 1000:
        add("ask", f"Family support runs above {CCY()} {dd['regular']:,.0f}", f"Your bank account shows {CCY()} {dd['total']:,} sent to family over {dd['months']} months, {CCY()} {dd['per_month']:,} a month on average, because of {CCY()} {dd['extras']:,} of extra transfers on top of the regular {CCY()} {dd['regular']:,.0f}. Decide whether that is a one-off or a yearly cost and budget it.")
    add("tip", "What is still invisible", "Your bank account statements are now read, so transfers, cash and card bills show up. Only cash you spend after withdrawing it, other banks, and any account whose statement never reaches your mailbox are still missing.")
    return out


def _is_family(label):
    """An off-card budget line that is money sent to family (matched by words in its label; a plugin may add more: FAMILY_LABEL_WORDS)."""
    import plugins
    return any(w in label for w in ("family", "parent", "support") + tuple(plugins.get("FAMILY_LABEL_WORDS", [])))


def report_card():
    """The latest complete statement cycle against the plan, line by line. Statements arrive after the cycle closes, so this is the honest 'how did last month go'."""
    import analytics as AN
    import bank_tx as BT
    cyc = [m for m in AN.spend_cycles()["months"] if m["complete"]]
    if not cyc:
        return None
    last = cyc[-1]
    items = [i for i in list_items() if i["type"] == "expense"]
    lines, plan_tot, act_tot = [], 0.0, 0.0
    for i in items:
        if i["categories"]:
            a = sum(last["cats"].get(c, 0) for c in i["categories"])
        elif i["off_card"] and _is_family(i["label"].lower()):
            a = sum(r["debit"] - r["credit"] for r in BT.rows(3, ["family"]) if r["txn_date"][:7] == last["month"])
        else:
            continue
        ln = {"label": i["label"], "planned": round(i["monthly"]), "actual": round(a), "lump": i["lump"]}
        if i["lump"] and i["categories"]:        # irregular: the allowance builds up every cycle and unspent months carry over, so judge the running year
            win = cyc[-12:]
            allowed = i["monthly"] * len(win)
            spent = sum(sum(m["cats"].get(c, 0) for c in i["categories"]) for m in win)
            ln.update(months=len(win), year_allowed=round(allowed), year_spent=round(spent), carry=round(allowed - spent))
        lines.append(ln)
        if not i["lump"]:
            plan_tot += i["monthly"]; act_tot += a
    income = sum(i["monthly"] for i in list_items() if i["type"] == "income")
    return {"cycle": last["month"], "lines": sorted(lines, key=lambda l: -(l["actual"] - l["planned"])), "planned_total": round(plan_tot), "actual_total": round(act_tot),
            "card_total": round(last["total"]), "income": round(income)}
