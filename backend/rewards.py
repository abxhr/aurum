"""Card rewards: what each card really earns, how much is left on the table, and which card to use for what.

Banks report rewards differently. The generic reader looks for a labelled "cashback earned" / "rewards earned" / "points earned" figure on the statement, with a closing
balance if there is one; a bank that prints something else gets a plugin (plugins.py: REWARD_PARSER). Cards that print nothing use the cashback redemptions seen in their
transactions instead. You can also describe a card's earning rules yourself (assistant: card_info_set), and they drive the "which card for what" advice.
"""
import json
import re

import plugins
from datetime import date, datetime, timedelta

from database import get_db, base_ccy as CCY

# Earning rules. Yours replace these per card (card_info[card_id]["rules"]); a plugin may supply defaults for banks it knows (DEFAULT_REWARD_RULES).
#   rate_pct: % back  | applies: 'all' | 'foreign' | a spend category | cap: amount per `period`, counted on `cap_basis` ('spend' or 'reward')
DEFAULT_RULES = {}


def ensure():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS statement_rewards (
            statement_id INTEGER PRIMARY KEY, card_id INTEGER NOT NULL, kind TEXT,
            opening REAL DEFAULT 0, earned REAL DEFAULT 0, adjusted REAL DEFAULT 0, redeemed REAL DEFAULT 0, closing REAL DEFAULT 0,
            detail TEXT DEFAULT '', created_at TEXT)""")


def _n(s):
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return 0.0


# ───────────────────────── reading statements ─────────────────────────
_EARNED = re.compile(r"(?:cash ?back|rewards?|points|miles)\s+(?:earned|accrued|this (?:month|period|statement))[:\s]+(-?[\d,]+(?:\.\d+)?)", re.I)
_CLOSING = re.compile(r"(?:cash ?back|rewards?|points|miles)\s+(?:closing balance|balance|available)[:\s]+(-?[\d,]+(?:\.\d+)?)", re.I)
_OPENING = re.compile(r"(?:cash ?back|rewards?|points|miles)\s+(?:opening balance|brought forward)[:\s]+(-?[\d,]+(?:\.\d+)?)", re.I)
_REDEEMED = re.compile(r"(?:cash ?back|rewards?|points|miles)\s+(?:redeemed|used|spent)[:\s]+(-?[\d,]+(?:\.\d+)?)", re.I)


def parse(bank, text):
    if not text:
        return None
    fn = plugins.get("REWARD_PARSER")
    if fn:
        r = fn(bank, text)
        if r:
            return r
    e = _EARNED.search(text)
    if not e:
        return None
    g = lambda rx: _n(m.group(1)) if (m := rx.search(text)) else 0.0
    return {"kind": "cashback", "opening": g(_OPENING), "earned": _n(e.group(1)), "adjusted": 0.0, "redeemed": g(_REDEEMED), "closing": g(_CLOSING), "detail": []}


def save_for_statement(statement_id, card_id, bank, text):
    r = parse(bank, text)
    if not r:
        return False
    ensure()
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO statement_rewards VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (statement_id, card_id, r["kind"], r["opening"], r["earned"], r["adjusted"], r["redeemed"], r["closing"], json.dumps(r["detail"]), datetime.utcnow().isoformat()))
    return True


# ───────────────────────── analysis ─────────────────────────
def rules_for(card, card_info):
    mine = (card_info.get(str(card["id"])) or {}).get("rules")
    return mine if mine else {**DEFAULT_RULES, **plugins.get("DEFAULT_REWARD_RULES", {})}.get(card.get("bank", ""), [])


def _cap_reward(rule):
    """Most this rule can pay per period, in the app currency."""
    if not rule.get("cap"):
        return None
    return rule["cap"] * rule["rate_pct"] / 100 if rule.get("cap_basis", "spend") == "spend" else rule["cap"]


def report(period="m12"):
    fn = plugins.get("REWARD_REPORT")
    if fn:
        return fn(period)
    import planner as PL
    ensure()
    S = PL.settings()
    pf = PL.period_filter(period)
    since = (date.today() - timedelta(days=800)).isoformat()
    with get_db() as conn:
        cards = [dict(r) for r in conn.execute("SELECT * FROM credit_cards WHERE archived=0").fetchall()]
        stm = {}
        for r in conn.execute("""SELECT s.id, s.card_id, s.statement_date,
                                        COALESCE((SELECT SUM(amount) FROM statement_transactions t WHERE t.statement_id=s.id AND t.amount>0 AND t.category NOT IN ('Internal','Fees & Charges')),0) spend,
                                        COALESCE((SELECT -SUM(amount) FROM statement_transactions t WHERE t.statement_id=s.id AND t.description LIKE '%CASHBACK REDEEMED%'),0) redeemed_txn,
                                        r.kind, r.opening, r.earned, r.adjusted, r.redeemed, r.closing, r.detail
                                 FROM card_statements s LEFT JOIN statement_rewards r ON r.statement_id=s.id
                                 WHERE s.statement_date>=? ORDER BY s.statement_date""", (since,)).fetchall():
            if PL.in_period(r["statement_date"], r["statement_date"], pf):
                stm.setdefault(r["card_id"], []).append(dict(r))
    out, cats = [], {}
    for c in cards:
        rows = stm.get(c["id"], [])
        if not rows:
            continue
        explicit = [r for r in rows if r["kind"]]
        rules = rules_for(c, S["card_info"])
        if explicit:
            spend = sum(r["spend"] for r in explicit)
            earned = sum(r["earned"] + r["adjusted"] for r in explicit)
            basis, last = "earned (printed on statements)", explicit[-1]
            unit = "cashback"
            per = [{"date": r["statement_date"], "spend": round(r["spend"]), "earned": round(r["earned"] + r["adjusted"], 2)} for r in explicit]
            balance = last["closing"]
            for r in explicit:
                for d in json.loads(r["detail"] or "[]"):
                    k = cats.setdefault(d["label"], {"label": d["label"], "spend": 0.0, "earned": 0.0, "rate_pct": d["rate_pct"]})
                    k["spend"] += d["spend"]; k["earned"] += d["earned"]
        else:
            spend = sum(r["spend"] for r in rows)
            earned = sum(r["redeemed_txn"] for r in rows)
            basis, unit, balance = "redeemed (this bank does not print what you earn)", "cashback", None
            per = [{"date": r["statement_date"], "spend": round(r["spend"]), "earned": round(r["redeemed_txn"], 2)} for r in rows if r["redeemed_txn"]]
        cap_line = None
        for rule in rules:
            cr = _cap_reward(rule)
            if cr and not explicit:
                months = max(1, len(rows))
                per_month = earned / months
                cap_line = {"label": rule["label"], "rate_pct": rule["rate_pct"], "cap_spend": rule.get("cap") if rule.get("cap_basis", "spend") == "spend" else None,
                            "max_reward_per_month": round(cr), "avg_reward_per_month": round(per_month), "utilisation_pct": round(per_month / cr * 100),
                            "needs": rule.get("needs", "")}
        out.append({"card": c["name"], "card_id": c["id"], "bank": c["bank"], "unit": unit, "basis": basis, "statements": len(rows),
                    "spend_12m": round(spend), "earned_12m": round(earned, 2), "effective_rate_pct": round(earned / spend * 100, 2) if spend else None,
                    "balance": round(balance, 2) if balance is not None else None, "per_statement": per[-12:], "cap": cap_line, "rules": rules})
    out.sort(key=lambda r: -r["earned_12m"])
    return {"window": pf[2], "period": period, "cards": out, "categories": sorted(({**v, "spend": round(v["spend"]), "earned": round(v["earned"], 2)} for v in cats.values()), key=lambda x: -x["earned"]),
            "total_earned_12m": round(sum(c["earned_12m"] for c in out), 2),
            "unredeemed": round(sum(c["balance"] or 0 for c in out), 2),
            "note": "Where a card prints no earnings, its redeemed cashback is used; it lags what you actually earned."}


def routing(period="m12"):
    fn = plugins.get("REWARD_ROUTING")
    if fn:
        return fn(period)
    """Which card for what, from each card's rules (or its observed rate where it has none), net of the FX markup abroad."""
    import planner as PL
    rep = report(period)
    S = PL.settings()
    cards = {c["card_id"]: c for c in rep["cards"]}
    markup = {}
    for r in PL._fx_rows(period):
        if r["extra"] is not None and r["amount"] > 0:
            m = markup.setdefault(r["card_id"], [0.0, 0.0]); m[0] += r["extra"]; m[1] += r["mid_base"]
    mk = {cid: (v[0] / v[1] * 100 if v[1] else None) for cid, v in markup.items()}
    def rate(c, ctx):
        best = None
        for rule in c["rules"]:
            if rule.get("needs") or rule.get("cap"):
                continue                       # capped / conditional rules are listed on their own line
            if rule["applies"] == "all" or rule["applies"] == ctx or (ctx == "foreign" and rule["applies"] == "foreign"):
                best = max(best or 0, rule["rate_pct"])
        if best is None and not c["rules"] and c["effective_rate_pct"] is not None:
            return c["effective_rate_pct"], "observed"
        return (best, "rule") if best is not None else (None, "")
    rows = []
    for c in cards.values():
        for rule in c["rules"]:
            if rule.get("needs") or rule.get("cap"):
                cap = _cap_reward(rule)
                rows.append({"use": f"{rule['label']}" + (f", first {CCY()} {rule['cap']:,.0f} of spend a month" if rule.get("cap_basis", "spend") == "spend" and rule.get("cap") else ""),
                             "card": c["card"], "rate_pct": rule["rate_pct"], "net_abroad_pct": round(rule["rate_pct"] - mk[c["card_id"]], 1) if mk.get(c["card_id"]) is not None else None,
                             "note": (f"Needs {rule['needs']}. " if rule.get("needs") else "") + (f"Worth up to {CCY()} {cap:,.0f} a month." if cap else ""), "kind": "capped"})
    for ctx, label in (("Dining", "Dining"), ("all", "Everything else")):
        cand = [(rate(c, ctx), c) for c in cards.values()]
        cand = [(r, c) for r, c in cand if r[0] is not None]
        if cand:
            (r, src), c = max(cand, key=lambda x: x[0][0])
            rows.append({"use": label, "card": c["card"], "rate_pct": r, "note": "from the card's rules" if src == "rule" else "observed from statements", "kind": "base"})
    ab = []
    for c in cards.values():
        r, src = rate(c, "foreign")
        if r is not None and mk.get(c["card_id"]) is not None:
            ab.append({"card": c["card"], "reward_pct": r, "markup_pct": round(mk[c["card_id"]], 2), "net_pct": round(r - mk[c["card_id"]], 2)})
    for c in cards.values():
        for rule in c["rules"]:
            if "apple pay" in (rule.get("needs") or "").lower() and mk.get(c["card_id"]) is not None:
                cap = rule.get("cap") if rule.get("cap_basis", "spend") == "spend" else None
                ab.append({"card": f"{c['card']} with Apple Pay", "reward_pct": rule["rate_pct"], "markup_pct": round(mk[c["card_id"]], 2),
                           "net_pct": round(rule["rate_pct"] - mk[c["card_id"]], 2), "capped": True,
                           "note": f"first {CCY()} {cap:,.0f} of spend a month" if cap else "capped"})
    ab.sort(key=lambda x: -x["net_pct"])
    return {"rows": rows, "abroad": ab, "note": "Rates come from each card's rules; edit them with the assistant if a card's terms changed."}
