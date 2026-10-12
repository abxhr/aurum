"""Bank account transactions, from any bank.

Two ways in, both generic:
  * statement e-mails: Settings > Bank accounts lists the sender and subject of each statement e-mail; the PDF text is read by statement_parse.parse_account_lines
    (a date, a description and amounts, with debit or credit decided by how the running balance moved);
  * CSV exports: import_csv() finds the columns by their headers.
A bank whose PDF layout defeats the generic reader can get a plugin (plugins.py: ACCOUNT_PARSERS).

Each line is then given a kind (salary, own transfer, family, cash, fee, investing...) so money moving between your own accounts is not counted as spending. Change any line's kind
yourself and it stays changed. Kinds can be taught to recognise your own name and family names in Settings > Your names."""
import json
import re
from datetime import date, datetime, timedelta

import plugins
import statement_parse as SP
import vault
import email_sync as ES
from database import get_db, get_setting, name_list

def ensure():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS bank_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, bank TEXT NOT NULL, account TEXT DEFAULT '', txn_date TEXT NOT NULL, description TEXT NOT NULL,
            debit REAL DEFAULT 0, credit REAL DEFAULT 0, balance REAL DEFAULT 0, kind TEXT DEFAULT '', counterparty TEXT DEFAULT '', user_kind TEXT DEFAULT '',
            UNIQUE(bank, account, txn_date, description, debit, credit, balance))""")
        for old, new in plugins.get("BANK_KIND_RENAMES", {}).items():       # a plugin can bring older kind names along (BANK_KIND_RENAMES)
            conn.execute("UPDATE bank_transactions SET kind=? WHERE kind=?", (new, old))
            conn.execute("UPDATE bank_transactions SET user_kind=? WHERE user_kind=?", (new, old))


# ───────────────────────── where statements come from ─────────────────────────
def sources():
    """[{name, sender, subject, parse}]: plugin-provided banks first, then the ones listed in Settings > Bank accounts."""
    out = [{"name": n, "sender": v["sender"], "subject": v["subject"], "parse": v.get("parse")} for n, v in plugins.get("ACCOUNT_PARSERS", {}).items()]
    try:
        mine = json.loads(get_setting("bank_sources", "[]"))
    except ValueError:
        mine = []
    for s in mine if isinstance(mine, list) else []:
        if isinstance(s, dict) and s.get("sender"):
            out.append({"name": str(s.get("name") or s["sender"])[:60], "sender": str(s["sender"])[:200], "subject": str(s.get("subject") or "")[:200], "parse": None})
    return out


def list_sources():
    """What the settings page edits: only the sources you added yourself (plugin ones are not editable here)."""
    try:
        mine = json.loads(get_setting("bank_sources", "[]"))
    except ValueError:
        mine = []
    return [{"name": s.get("name", ""), "sender": s.get("sender", ""), "subject": s.get("subject", "")} for s in mine if isinstance(s, dict)]


def save_sources(items):
    clean = []
    for s in (items or [])[:20]:
        sender = str((s or {}).get("sender", "")).strip()[:200]
        if sender:
            clean.append({"name": str(s.get("name", "")).strip()[:60] or sender, "sender": sender, "subject": str(s.get("subject", "")).strip()[:200]})
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('bank_sources',?)", (json.dumps(clean),))
    return clean


def _store(rows, bank, account=""):
    added = 0
    with get_db() as conn:
        for r in rows:
            cur = conn.execute("INSERT OR IGNORE INTO bank_transactions(bank,account,txn_date,description,debit,credit,balance) VALUES (?,?,?,?,?,?,?)",
                               (r.get("bank") or bank, r.get("account") or account, r["txn_date"], r["description"][:200], r["debit"], r["credit"], r.get("balance") or 0))
            added += cur.rowcount
    return added


def sync(months=14):
    """Reads every matching statement e-mail from the last `months` and stores new transactions. Safe to repeat: duplicates are ignored."""
    import bank_statements as BS
    ensure()
    srcs = sources()
    if not srcs:
        return {"ok": False, "error": "No bank accounts to read yet. Add the statement e-mail sender in Settings > Bank accounts, or import a CSV."}
    try:
        box = ES.open_mailbox()
    except ES.GmailAuthError as e:
        return {"ok": False, "error": str(e)}
    if not box:
        return {"ok": False, "error": "Not connected to e-mail"}
    pws = BS._candidate_passwords(vault.unseal(get_setting("bank_stmt_pdf_password", "")))
    added, seen, errors = 0, 0, []
    for src in srcs:
        q = f'from:{src["sender"]}' + (f' subject:"{src["subject"]}"' if src["subject"] else "") + f" newer_than:{months * 31}d"
        for uid in box.search(q, 40):
            text = ""
            for p in pws:
                text = box.pdf_text(uid, p)
                if text:
                    break
            if not text:
                errors.append(f"{src['name']}: could not open one statement")
                continue
            rows = (src["parse"](text) if src["parse"] else None) or SP.parse_account_lines(text)
            seen += len(rows)
            added += _store(rows, src["name"])
    classify()
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('bank_tx_last_sync',?)", (datetime.utcnow().isoformat(),))
    return {"ok": True, "read": seen, "added": added, "errors": errors[:3]}


def import_csv(text, bank="CSV import", mapping=None, spend_is_negative=None):
    ensure()
    r = SP.parse_csv(text, "account", mapping, spend_is_negative)
    added = _store(r["rows"], (bank or "CSV import")[:60])
    classify()
    return {"ok": True, "read": len(r["rows"]), "added": added, "columns": r["columns"], "spend_is_negative": r["spend_is_negative"]}


# ───────────────────────── understanding each line ─────────────────────────
INTERNAL = ("credit card", "card payment", "card pmt", "autopay", "payment thank you")
FEES = ("fee", "charge", "commission", "service chg", "overdraft", "levy", "tax on")
BROKERS = ("brokerage", "securities", "broker", "etf", "invest", "interactive brokers", "etoro", "schwab", "fidelity", "vanguard", "robinhood", "trading 212", "degiro", "wealthfront", "betterment")
PEOPLE = ("transfer", "zelle", "venmo", "paypal", "cash app", "p2p", "peer", "remittance", "wire", "faster payment", "instant payment", "sepa")


def _counterparty(desc):
    rx = plugins.get("COUNTERPARTY_REGEX")
    if rx:
        m = re.search(rx, desc or "")
        if m:
            return m.group(1).title()
    m = re.search(r"\b(?:to|from)\s+([A-Za-z][A-Za-z .'-]{2,28}?)(?:\s{2,}|\s\d|$|\s(?:ref|via|on)\b)", desc or "", re.I)
    return m.group(1).strip().title() if m else ""


def kind_of(desc, debit, credit, family=(), me=()):
    """family / me: names from Settings > Your names. A plugin's TRANSFER_RULES (plugins.py) decides first when you have one."""
    d = (desc or "").lower()
    rules = plugins.get("TRANSFER_RULES")
    if rules:
        k = rules(d, debit, credit, tuple(family), tuple(me))
        if k:
            return k
    flat = d.replace(" ", "")
    if any(x in d or x.replace(" ", "") in flat for x in family):
        return "family"
    if any(x in d for x in INTERNAL):
        return "own"                                          # card bills paid from this account
    if re.search(r"salary|payroll|direct dep|wages|pay run", d):
        return "salary"
    if any(x in d for x in BROKERS) and debit:
        return "investing"
    if re.search(r"tuition|university|college|school fee|education|course fee", d) and debit:
        return "education"
    if any(m.replace(" ", "") in flat for m in me) and any(p in d for p in PEOPLE):
        return "own"                                          # money moved between your own accounts
    if re.search(r"interest|dividend", d) and credit:
        return "interest"
    if re.search(r"\batm\b|cash withdrawal|cash advance", d) and debit and "fee" not in d:
        return "cash"
    if re.search(r"debit card|pos purchase|point of sale|card purchase|purchase", d) and debit:
        return "debit_spend"
    if any(w in d for w in FEES) and debit:
        return "fee"
    if any(p in d for p in PEOPLE):
        return "from_person" if credit else "to_person"
    if re.search(r"cashback|bonus|reward|welcome", d):
        return "reward"
    return "other_in" if credit else "other_out"


def classify():
    """Sets each line's kind (unless you set it yourself). A fee that shares a reference with an investing transfer travels with it (plugin FEE_REF_REGEX)."""
    ensure()
    family, me = name_list("family_names"), name_list("my_name")
    with get_db() as conn:
        for r in conn.execute("SELECT id, description, debit, credit, user_kind FROM bank_transactions").fetchall():
            k = r["user_kind"] or kind_of(r["description"], r["debit"], r["credit"], family, me)
            conn.execute("UPDATE bank_transactions SET kind=?, counterparty=? WHERE id=?", (k, _counterparty(r["description"]), r["id"]))
        rx = plugins.get("FEE_REF_REGEX")
        if rx:
            for r in conn.execute("SELECT id, description, txn_date FROM bank_transactions WHERE kind='fee'").fetchall():
                ref = re.search(rx, r["description"])
                if ref and conn.execute("SELECT 1 FROM bank_transactions WHERE kind='investing' AND description LIKE ? AND txn_date=?", (f"%{ref.group(1)}%", r["txn_date"])).fetchone():
                    conn.execute("UPDATE bank_transactions SET kind='investing_fee' WHERE id=?", (r["id"],))


# ───────────────────────── what it adds up to ─────────────────────────
ALL_KINDS = {"own", "salary", "family", "education", "cash", "debit_spend", "to_person", "from_person", "fee", "investing", "investing_fee", "interest", "reward", "other_in", "other_out"}
SPEND_KINDS = ("family", "education", "cash", "debit_spend", "to_person", "fee", "other_out")
OTHER_KINDS = ("cash", "debit_spend", "to_person", "fee")      # leaves your account without ever appearing on a card or a budget line


def _since(months):
    d = date.today().replace(day=1)
    for _ in range(months - 1):
        d = (d - timedelta(days=1)).replace(day=1)
    return d.isoformat()


def _month_span(months):
    """Months covered by data, capped at `months`: a new install should not divide by 12."""
    ensure()
    with get_db() as conn:
        r = conn.execute("SELECT MIN(txn_date) a, MAX(txn_date) b FROM bank_transactions").fetchone()
    if not r["a"]:
        return 0
    a, b = datetime.fromisoformat(r["a"]), datetime.fromisoformat(r["b"])
    return max(1, min(months, (b.year - a.year) * 12 + b.month - a.month + 1))


def rows(months=12, kinds=None):
    ensure()
    q = "SELECT * FROM bank_transactions WHERE txn_date>=?" + (f" AND kind IN ({','.join('?' * len(kinds))})" if kinds else "") + " ORDER BY txn_date DESC, id DESC"
    with get_db() as conn:
        return [dict(r) for r in conn.execute(q, [_since(months), *(kinds or [])]).fetchall()]


def usual_other(months=12):
    """Monthly average of the small things that leave your bank account but never reach a card or a budget line: cash, debit-card purchases, payments to people, fees."""
    n = _month_span(months)
    if not n:
        return {"monthly": 0.0, "months": 0, "by_kind": {}}
    by = {}
    for r in rows(months, OTHER_KINDS):
        by[r["kind"]] = by.get(r["kind"], 0) + r["debit"] - r["credit"]
    return {"monthly": round(sum(by.values()) / n, 2), "months": n, "by_kind": {k: round(v / n, 2) for k, v in by.items()}}


def monthly_kind(kind, months=12):
    n = _month_span(months)
    return round(sum(r["debit"] - r["credit"] for r in rows(months, [kind])) / n, 2) if n else 0.0


def investing_log(months=14):
    allr = rows(months)
    out = []
    for r in sorted([x for x in allr if x["kind"] == "investing"], key=lambda x: x["txn_date"]):
        fees = sum(x["debit"] for x in allr if x["kind"] == "investing_fee" and x["txn_date"] == r["txn_date"])
        out.append({"date": r["txn_date"], "amount": round(r["debit"] - r["credit"], 2), "fees": round(fees, 2), "what": r["counterparty"] or "Investment transfer"})
    return out


def family(months=12):
    mine = rows(months, ["family"])
    by = {}
    for r in mine:
        by[r["txn_date"][:7]] = by.get(r["txn_date"][:7], 0) + r["debit"] - r["credit"]
    n = _month_span(months)
    total = sum(by.values())
    amounts = [r["debit"] for r in mine if r["debit"]]
    regular = max(set(amounts), key=amounts.count) if amounts else 0
    extras = sum(a for a in amounts if a != regular)
    return {"total": round(total), "per_month": round(total / n) if n else 0, "regular": regular, "regular_months": sum(1 for a in amounts if a == regular),
            "extras": round(extras), "by_month": [{"month": k, "amount": round(v)} for k, v in sorted(by.items())], "months": n}


def summary(months=12):
    ensure()
    n = _month_span(months)
    rs = rows(months)
    by = {}
    for r in rs:
        m = by.setdefault(r["txn_date"][:7], {})
        m[r["kind"]] = m.get(r["kind"], 0) + r["debit"] - r["credit"]
    series = [{"month": k, **{kk: round(v) for kk, v in sorted(m.items())}} for k, m in sorted(by.items())]
    unsorted = [r for r in rs if r["kind"] in ("other_in", "other_out") and (r["debit"] + r["credit"]) >= 100]
    with get_db() as conn:
        last = (conn.execute("SELECT value FROM settings WHERE key='bank_tx_last_sync'").fetchone() or {"value": None})["value"]
        cnt = conn.execute("SELECT COUNT(*) c FROM bank_transactions").fetchone()["c"]
    return {"months": n, "count": cnt, "last_sync": last, "series": series, "other": usual_other(months), "family": family(months), "education_per_month": monthly_kind("education", months),
            "investing": investing_log(), "sources": [s["name"] for s in sources()],
            "unsorted": [{"id": r["id"], "date": r["txn_date"], "amount": r["debit"] or -r["credit"], "text": r["description"][:80]} for r in unsorted[:12]],
            "totals": {k: round(sum(r["debit"] - r["credit"] for r in rs if r["kind"] == k)) for k in sorted({r["kind"] for r in rs})}}


def habit(months=6):
    """Did the monthly investing actually happen? What the budget plans to invest against what left the bank for investing, month by month."""
    import budget as BG
    planned = round(sum(i["monthly"] for i in BG.list_items() if i["type"] == "invest"), 2)
    sent = {}
    for x in investing_log(14):
        sent[x["date"][:7]] = sent.get(x["date"][:7], 0) + x["amount"]
    ms, d = [], date.today().replace(day=1)
    for _ in range(months):
        ms.append(d.strftime("%Y-%m"))
        d = (d - timedelta(days=1)).replace(day=1)
    ms.reverse()
    rows_ = [{"month": m, "sent": round(sent.get(m, 0)), "hit": sent.get(m, 0) >= planned * 0.95 if planned else False} for m in ms]
    streak = 0
    for r in reversed(rows_[:-1] if not rows_[-1]["hit"] else rows_):      # the current month is not a miss until it is over
        if r["hit"]:
            streak += 1
        else:
            break
    return {"planned": planned, "months": rows_, "streak": streak}
