"""Account balance feeds: keep a holding's balance current from the statement email the bank sends.

A feed says "emails from <sender> (optionally with <subject>) carry the balance for holding X after the
text <label>". We open the newest matching email, read the PDF (or the email body) and take the first
amount after the label. Works for any bank that emails statements.
"""
import re
from datetime import datetime
import vault
from database import get_db

DEFAULT_LABELS = ["closing balance", "available balance", "ledger balance", "current balance", "balance"]
AMOUNT = r"(-?\(?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|-?\(?\d+\.\d{1,2})\)?"


def ensure_table():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS account_feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT DEFAULT '', holding_id INTEGER NOT NULL,
            sender TEXT NOT NULL, subject TEXT DEFAULT '', pdf_password TEXT DEFAULT '', label TEXT DEFAULT '',
            last_sync TEXT DEFAULT '', last_value REAL, last_error TEXT DEFAULT '', created_at TEXT NOT NULL)""")


def list_feeds():
    ensure_table()
    with get_db() as conn:
        rows = conn.execute("""SELECT f.id,f.name,f.holding_id,f.sender,f.subject,f.label,f.last_sync,f.last_value,f.last_error,
                                      (f.pdf_password!='') pw_set, h.name holding_name, h.currency
                               FROM account_feeds f LEFT JOIN holdings h ON h.id=f.holding_id ORDER BY f.id""").fetchall()
    return [dict(r) for r in rows]


def save_feed(b, fid=None):
    ensure_table()
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        if fid:
            conn.execute("UPDATE account_feeds SET name=?,holding_id=?,sender=?,subject=?,label=? WHERE id=?",
                         (b.get("name", ""), b["holding_id"], b["sender"].strip(), b.get("subject", "").strip(), b.get("label", "").strip(), fid))
            if b.get("pdf_password"):
                conn.execute("UPDATE account_feeds SET pdf_password=? WHERE id=?", (vault.seal(b["pdf_password"]), fid))
            return fid
        cur = conn.execute("""INSERT INTO account_feeds(name,holding_id,sender,subject,pdf_password,label,created_at)
                              VALUES (?,?,?,?,?,?,?)""",
                           (b.get("name", ""), b["holding_id"], b["sender"].strip(), b.get("subject", "").strip(),
                            vault.seal(b.get("pdf_password", "")), b.get("label", "").strip(), now))
        return cur.lastrowid


def delete_feed(fid):
    ensure_table()
    with get_db() as conn:
        conn.execute("DELETE FROM account_feeds WHERE id=?", (fid,))


def _get(fid):
    ensure_table()
    with get_db() as conn:
        r = conn.execute("SELECT * FROM account_feeds WHERE id=?", (fid,)).fetchone()
    return dict(r) if r else None


def _amount(s):
    neg = s.strip().startswith(("-", "("))
    v = float(re.sub(r"[^\d.]", "", s) or 0)
    return -v if neg else v


_AMT = r"(-?[\d,]+\.\d{2})"


def _signed(amount, suffix):
    v = _amount(amount)
    return -abs(v) if (suffix or "").upper() == "DR" else v


def reconcile_statement(flat):
    """Statements with a running-balance column (many banks): there is no 'closing balance'
    label, so compute it. closing = opening - total debits + total credits, and it must equal the last
    running balance printed in the table. Returns (value, snippet), or None if this isn't that format.
    Raises ValueError when the numbers disagree (better to say so than store a wrong balance)."""
    m_open = re.search(r"Balance\s+Brought\s+(?:FWD|Forward)\s*" + _AMT + r"\s*(DR|CR)?", flat, re.I)
    if not m_open:
        return None
    opening = _signed(m_open.group(1), m_open.group(2))
    m_turn = re.search(r"TURN\s*OVER\s*:?\s*" + _AMT + r"\s+" + _AMT, flat, re.I)
    m_last = re.search(_AMT + r"\s*(DR|CR)?\s*ITEM\s+COUNT", flat, re.I)
    last = _signed(m_last.group(1), m_last.group(2)) if m_last else None
    if m_turn:
        formula = round(opening - _amount(m_turn.group(1)) + _amount(m_turn.group(2)), 2)
        if last is not None and abs(formula - last) > 0.011:
            raise ValueError(f"The statement doesn't reconcile: opening {opening:,.2f} - debits + credits = {formula:,.2f} "
                             f"but the last running balance is {last:,.2f}.")
        return formula, f"opening {opening:,.2f}, turnover {m_turn.group(1)} / {m_turn.group(2)}, closing {formula:,.2f}"
    if last is not None:
        return last, f"last running balance {last:,.2f}"
    return opening, f"no transactions this period; balance brought forward {opening:,.2f}"


def find_balance(text, label=""):
    """(value, snippet). Running-balance statements are reconciled; otherwise the first amount after
    the label (or after common balance labels). A bare label of just 'balance' is treated as 'not set'
    because it would match the opening balance line."""
    flat = re.sub(r"\s+", " ", text or "")
    if label.strip().lower() in ("", "balance"):
        rec = reconcile_statement(flat)
        if rec:
            return rec
    labels = [label] if label else DEFAULT_LABELS
    for lab in labels:
        m = re.search(re.escape(lab) + r"[^0-9\-(]{0,60}" + AMOUNT, flat, re.I)
        if m:
            return _amount(m.group(1)), flat[max(0, m.start() - 30):m.end() + 30]
    return None, ""


def _query(feed):
    q = f"from:{feed['sender']}"
    if feed.get("subject"):
        q += f' subject:"{feed["subject"]}"'
    return q


def _read(box, feed, mid):
    text = box.pdf_text(mid, feed.get("pdf_password") or "")
    return text or box.body(mid)


def test_feed(box, feed):
    """Dry run: what would be read, without touching any holding."""
    ids = box.search(_query(feed), 3)
    if not ids:
        return {"ok": False, "error": "No email found from that sender (and subject). Check the sender address."}
    h = box.headers(ids[0])
    text = _read(box, feed, ids[0])
    if not text:
        return {"ok": False, "error": "Found the email but could not read any text. If the PDF is locked, add its password.",
                "email": {"subject": h.get("Subject", ""), "date": h.get("Date", "")}}
    try:
        val, snip = find_balance(text, feed.get("label", ""))
    except ValueError as e:
        return {"ok": False, "error": str(e), "email": {"subject": h.get("Subject", ""), "date": h.get("Date", "")}}
    if val is None:
        lines = [l.strip() for l in text.splitlines() if re.search(r"balance", l, re.I)][:6]
        return {"ok": False, "error": "Read the statement but found no balance. Set the label to the exact words just before the balance.",
                "email": {"subject": h.get("Subject", ""), "date": h.get("Date", "")}, "balance_lines": lines}
    return {"ok": True, "value": val, "snippet": snip, "email": {"subject": h.get("Subject", ""), "date": h.get("Date", "")}}


def sync_feed(box, fid, take_snapshot=None):
    feed = _get(fid)
    if not feed:
        return {"ok": False, "error": "Unknown feed"}
    now = datetime.utcnow().isoformat()
    result = test_feed(box, feed)
    with get_db() as conn:
        if result["ok"]:
            conn.execute("UPDATE holdings SET manual_value=?,price_mode='manual',updated_at=? WHERE id=?",
                         (result["value"], now, feed["holding_id"]))
            conn.execute("UPDATE account_feeds SET last_sync=?,last_value=?,last_error='' WHERE id=?", (now, result["value"], fid))
        else:
            conn.execute("UPDATE account_feeds SET last_error=? WHERE id=?", (result["error"], fid))
    if result["ok"] and take_snapshot:
        take_snapshot()
    return result


def sync_all(box, take_snapshot=None):
    out = []
    for f in list_feeds():
        try:
            out.append({"id": f["id"], **sync_feed(box, f["id"], None)})
        except Exception as e:
            out.append({"id": f["id"], "ok": False, "error": str(e)[:120]})
    if take_snapshot and any(o.get("ok") for o in out):
        take_snapshot()
    return out


def discover(box, text, limit=12):
    """Help the user find the right sender: recent emails matching some words, with who sent them."""
    ids = box.search(text, limit)
    hits = []
    for mid in ids:
        h = box.headers(mid)
        hits.append({"from": h.get("From", ""), "subject": h.get("Subject", ""), "date": h.get("Date", "")[:25]})
    return hits
