"""Credit card management: CRUD, due dates, statement tracking, payment status."""
import re
from datetime import datetime, date, timedelta
import vault
import plugins
from database import get_db, get_setting, base_ccy as CCY

# Labels found on almost every statement. A card can override the "total" label (amount_label); banks with unusual layouts get a plugin (see plugins.py).
_AMT = r"(?:[a-z]{3}\s*|[$\u20ac\u00a3]\s*)?([\d,]+\.\d{2}|[\d,]+)"
_DATE = r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}[ -][a-z]{3,9}[ ,-]+\d{4}|[a-z]{3,9}\.? \d{1,2},? \d{4})"
GENERIC_PATTERNS = {
    "total": [r"(?:total|new|statement|closing)\s+(?:amount\s+|payment\s+)?(?:due|balance)[:\s]+" + _AMT, r"amount\s+due[:\s]+" + _AMT, r"outstanding\s+(?:amount|balance)[:\s]+" + _AMT],
    "minimum": [r"minimum\s+(?:payment\s+|amount\s+)?(?:due)?[:\s]+" + _AMT, r"min(?:imum)?\.?\s+payment[:\s]+" + _AMT],
    "due_date": [r"(?:payment\s+)?due\s+(?:date|by|on)[:\s]+" + _DATE, r"pay\s+by[:\s]+" + _DATE, r"payment\s+due[:\s]+" + _DATE],
}
_PALETTE = ["#4a7c8c", "#7a5c9c", "#c8973a", "#2d7a4f", "#c0503e", "#3b82f6", "#7b5ea7", "#e67e22"]


def bank_color(bank):
    c = plugins.get("BANK_COLORS", {}).get(bank)
    return c or _PALETTE[sum(map(ord, bank or "x")) % len(_PALETTE)]


def known_banks():
    """Names offered in the card form: whatever your plugins know about, plus the banks of cards you already added."""
    with get_db() as conn:
        mine = [r["bank"] for r in conn.execute("SELECT DISTINCT bank FROM credit_cards WHERE bank!=''").fetchall()]
    return sorted(set(mine) | set(plugins.get("BANK_EMAIL_SENDERS", {})))


MONTH_NAMES = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
               "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
               "january":1,"february":2,"march":3,"april":4,"june":6,
               "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}


def _parse_amount(text):
    text = text.replace(",","").strip()
    try: return float(text)
    except: return 0.0


def _parse_date_str(ds):
    """A statement date in any common layout. Numeric dates follow Settings > Date order (day first by default)."""
    ds = (ds or "").strip().rstrip(".")
    mdy = get_setting("date_order", "dmy") == "mdy"
    numeric = ["%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y"] if mdy else ["%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"]
    for fmt in numeric + ["%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d/%b/%Y", "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y", "%d-%B-%Y"]:
        try:
            return datetime.strptime(ds, fmt).date().isoformat()
        except ValueError:
            pass
    return None


DEFAULT_SUBJECT_KEYWORDS = ["statement", "credit card", "amount due", "payment due", "outstanding"]


def card_senders(card):
    """Sender addresses/domains to match for this card: custom override, else bank default."""
    custom = (card.get("email_sender") or "").strip()
    if custom:
        return [s.strip().lower() for s in custom.split(",") if s.strip()]
    return [s.lower() for s in plugins.get("BANK_EMAIL_SENDERS", {}).get(card.get("bank", ""), [])]


def card_subject_keywords(card):
    """Subject keywords to match for this card: custom override, else generic defaults."""
    custom = (card.get("email_subject") or "").strip()
    if custom:
        return [s.strip().lower() for s in custom.split(",") if s.strip()]
    return DEFAULT_SUBJECT_KEYWORDS


def extract_statement_from_email(card, subject, body):
    """Extract statement data from an email/PDF text using a card's config.

    `card` is a credit_cards row (dict-like). If the card has an `amount_label`
    set (e.g. "Total Amount Due"), that exact label is used to locate the
    number to fetch. Otherwise falls back to the plugin's patterns for that bank, then the generic labels.
    """
    bank = card.get("bank", "")
    patterns = plugins.get("CARD_SUMMARY_PATTERNS", {}).get(bank) or GENERIC_PATTERNS
    text = (subject + " " + body).lower()

    custom = plugins.get("CARD_SUMMARY_PARSERS", {}).get(bank)
    if custom:
        result = custom(text)
        if result:
            return result

    total = None
    amount_label = (card.get("amount_label") or "").strip()
    if amount_label:
        pat = re.escape(amount_label.lower()) + r"[:\s]+" + _AMT
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            total = _parse_amount(m.group(1))
    if total is None:
        for p in patterns.get("total", []):
            m = re.search(p, text, re.IGNORECASE | re.DOTALL)
            if m:
                total = _parse_amount(m.group(1))
                break

    minimum = None
    for p in patterns.get("minimum", []):
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            minimum = _parse_amount(m.group(1))
            break

    due_date = None
    for p in patterns.get("due_date", []):
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            due_date = _parse_date_str(m.group(1))
            if due_date: break

    if total is None and due_date is None:
        return None

    return {"total_due": total or 0, "minimum_due": minimum or 0, "due_date": due_date}


def get_all_cards():
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM credit_cards WHERE archived=0 ORDER BY id").fetchall()]


def get_stats():
    """Aggregate stats across all cards: limits, outstanding, paid-this-month, counts."""
    cards = get_all_cards()
    with get_db() as conn:
        latest_by_card = {}
        for c in cards:
            r = conn.execute(
                "SELECT * FROM card_statements WHERE card_id=? ORDER BY statement_date DESC LIMIT 1",
                (c["id"],)).fetchone()
            if r:
                latest_by_card[c["id"]] = dict(r)

        month_start = date.today().replace(day=1).isoformat()
        paid_this_month = conn.execute(
            "SELECT COALESCE(SUM(paid_amount),0) t FROM card_statements WHERE status='paid' AND paid_date>=?",
            (month_start,)).fetchone()["t"]

    total_limit = sum(c["credit_limit"] for c in cards)
    total_outstanding = sum(
        s["total_due"] for s in latest_by_card.values() if s["status"] != "paid")
    unpaid_count = sum(1 for s in latest_by_card.values() if s["status"] != "paid")
    paid_count = sum(1 for s in latest_by_card.values() if s["status"] == "paid")

    return {
        "card_count": len(cards),
        "total_limit": total_limit,
        "total_outstanding": total_outstanding,
        "total_available": max(total_limit - total_outstanding, 0),
        "utilization_pct": round(total_outstanding / total_limit * 100, 1) if total_limit else 0,
        "paid_this_month": paid_this_month,
        "unpaid_count": unpaid_count,
        "paid_count": paid_count,
    }


def get_card(card_id):
    with get_db() as conn:
        r = conn.execute("SELECT * FROM credit_cards WHERE id=?", (card_id,)).fetchone()
        return dict(r) if r else None


def get_statements(card_id, limit=24):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM card_statements WHERE card_id=? ORDER BY statement_date DESC LIMIT ?",
            (card_id, limit)).fetchall()]


def statement_pattern(card, stmts):
    """When this card's statements usually arrive, learned from its history (falls back to the
    statement day set on the card when there are fewer than 2 statements). Also the next expected
    date and the usual gap to the due date, and whether this month's statement is overdue."""
    import calendar
    from statistics import median
    dates = sorted({date.fromisoformat(x["statement_date"]) for x in stmts if x.get("statement_date")})[-8:]
    if not dates and not card.get("statement_day"):
        return None
    if len(dates) >= 2:
        day = int(round(median(d.day for d in dates)))
        source = "history"
    elif dates:
        day, source = dates[0].day, "history"
    else:
        day, source = int(card.get("statement_day") or 1), "setting"
    gaps = [(date.fromisoformat(x["due_date"]) - date.fromisoformat(x["statement_date"])).days
            for x in stmts if x.get("due_date") and x.get("statement_date")][:8]
    gap = int(round(median(gaps))) if gaps else int(card.get("due_days") or 0) or None

    def on(y, m):
        return date(y, m, min(day, calendar.monthrange(y, m)[1]))
    today = date.today()
    if dates:
        last = dates[-1]
        y, m = (last.year + (last.month == 12), last.month % 12 + 1)
        nxt = on(y, m)
    else:
        nxt = on(today.year, today.month)
        if nxt < today:
            nxt = on(today.year + (today.month == 12), today.month % 12 + 1)
    late_days = (today - nxt).days if nxt < today - timedelta(days=3) else 0
    return {"typical_day": day, "due_gap_days": gap, "next_expected": nxt.isoformat(),
            "late_days": late_days, "samples": len(dates), "source": source}


def get_upcoming_dues(days_ahead=30):
    """Get unpaid/partial statements due in the next N days."""
    today = date.today()
    cutoff = (today + timedelta(days=days_ahead)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.*, c.name card_name, c.bank, c.last_four, c.card_color, c.currency
               FROM card_statements s JOIN credit_cards c ON s.card_id=c.id
               WHERE s.status IN ('unpaid','partial') AND s.due_date<=? AND c.archived=0
               ORDER BY s.due_date""",
            (cutoff,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        due = date.fromisoformat(d["due_date"])
        days_left = (due - today).days
        d["days_until_due"] = days_left
        d["urgency"] = "overdue" if days_left < 0 else ("urgent" if days_left <= 3 else ("soon" if days_left <= 7 else "upcoming"))
        result.append(d)
    return result


def add_card(data):
    now = datetime.utcnow().isoformat()
    bank = data.get("bank","")
    color = data.get("card_color") or bank_color(bank)
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO credit_cards
               (name,bank,last_four,currency,credit_limit,statement_day,due_days,card_color,pdf_password,
                email_sender,email_subject,amount_label,notes,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data.get("name","New Card"), bank, data.get("last_four",""),
             data.get("currency",CCY()), data.get("credit_limit",0) or 0,
             data.get("statement_day",1), data.get("due_days",25),
             color, vault.seal(data.get("pdf_password","")),
             data.get("email_sender",""), data.get("email_subject",""), data.get("amount_label",""),
             data.get("notes",""), now, now))
        return cur.lastrowid


def update_card(card_id, data):
    fields = ["name","bank","last_four","currency","credit_limit","statement_day","due_days","card_color",
              "pdf_password","email_sender","email_subject","amount_label","notes"]
    sets, vals = [], []
    for f in fields:
        if f in data: sets.append(f"{f}=?"); vals.append(vault.seal(data[f]) if f == "pdf_password" else data[f])
    if sets:
        sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat()); vals.append(card_id)
        with get_db() as conn:
            conn.execute(f"UPDATE credit_cards SET {', '.join(sets)} WHERE id=?", vals)


def delete_card(card_id):
    with get_db() as conn:
        conn.execute("UPDATE credit_cards SET archived=1 WHERE id=?", (card_id,))


def add_statement(card_id, data):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO card_statements
               (card_id,statement_date,due_date,total_due,minimum_due,status,source,email_subject,email_snippet,notes,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (card_id, data.get("statement_date", date.today().isoformat()),
             data.get("due_date",""), data.get("total_due",0) or 0,
             data.get("minimum_due",0) or 0, data.get("status","unpaid"),
             data.get("source","manual"), data.get("email_subject",""),
             data.get("email_snippet",""), data.get("notes",""), now))
        return cur.lastrowid


def mark_paid(statement_id, amount=None, paid_date=None):
    with get_db() as conn:
        stmt = conn.execute("SELECT * FROM card_statements WHERE id=?", (statement_id,)).fetchone()
        if not stmt: return
        total = stmt["total_due"]
        paid = amount if amount is not None else total
        status = "paid" if paid >= total else "partial"
        conn.execute(
            "UPDATE card_statements SET paid_amount=?, paid_date=?, status=? WHERE id=?",
            (paid, paid_date or date.today().isoformat(), status, statement_id))


def _cycle_end(d, day):
    """The statement date a transaction on `d` belongs to: the next occurrence of the card's statement day on or after it."""
    import calendar
    day = max(1, min(int(day or 1), 28))
    end = date(d.year, d.month, day)
    if d > end:
        y, m = (d.year + (d.month == 12), d.month % 12 + 1)
        end = date(y, m, min(day, calendar.monthrange(y, m)[1]))
    return end


def import_csv(card_id, text, mapping=None, spend_is_negative=None):
    """A CSV export from the card issuer: the rows become statements (one per cycle, by the card's statement day) with categorised transactions. Re-importing replaces a cycle's lines."""
    import statement_parse
    import transactions as TX
    card = get_card(card_id)
    if not card:
        raise ValueError("Unknown card")
    parsed = statement_parse.parse_csv(text, "card", mapping, spend_is_negative)
    cycles = {}
    for r in parsed["rows"]:
        cycles.setdefault(_cycle_end(date.fromisoformat(r["txn_date"]), card.get("statement_day")), []).append(r)
    today, made = date.today(), 0
    for end, rows in sorted(cycles.items()):
        with get_db() as conn:
            prior = conn.execute("SELECT id FROM card_statements WHERE card_id=? AND statement_date=?", (card_id, end.isoformat())).fetchone()
        total = round(sum(x["amount"] for x in rows), 2)
        if prior:
            sid = prior["id"]
            with get_db() as conn:
                conn.execute("UPDATE card_statements SET total_due=? WHERE id=?", (max(total, 0), sid))
        else:
            due = end + timedelta(days=int(card.get("due_days") or 25))
            sid = add_statement(card_id, {"statement_date": end.isoformat(), "due_date": due.isoformat(), "total_due": max(total, 0), "source": "csv",
                                          "status": "paid" if end < today - timedelta(days=30) else "unpaid", "notes": "Imported from a CSV file."})
        TX.save_transactions(sid, card_id, rows)
        made += 1
    return {"ok": True, "statements": made, "transactions": len(parsed["rows"]), "columns": parsed["columns"], "spend_is_negative": parsed["spend_is_negative"]}
