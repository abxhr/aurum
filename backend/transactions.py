"""Statement line-item extraction and spend categorization."""
import re
from datetime import datetime, date, timedelta
import plugins
import statement_parse
from database import get_db
from cards import MONTH_NAMES

CATEGORIES = [
    "Groceries", "Dining", "Shopping", "Transport", "Travel",
    "Bills & Utilities", "Subscriptions", "Entertainment", "Health & Fitness",
    "Personal Care", "Education", "Home & Furniture", "Electronics",
    "Government & Fines", "Cash & ATM", "Fees & Charges", "Payments & Transfers", "Other",
    "Internal",   # money moved between your own accounts, or profit netting: never counted as spending
]


# ─── merchant normalization ────────────────────────────────────────────────
_NOISE_PREFIX = re.compile(r"^(POS\*|POS |ECOM\*|ECOM |WEB\*|WEB |E-COM\*|E-COM |PURCHASE |SALE )", re.IGNORECASE)
_LEADING_REF = re.compile(r"^\d{3,}\s+")
_LOCATION_TOKENS = {"usa", "us", "gbr", "uk", "can", "aus", "sgp", "sg"}      # trailing country codes on card lines; your plugins may add more (LOCATION_TOKENS)


def _light_clean(description):
    """Strip POS/reference-number prefixes and collapse whitespace (some statement PDFs
    pad columns with long runs of spaces) — keeps city/country words intact, since some
    merchant names that include a place rely on them."""
    s = _LEADING_REF.sub("", _NOISE_PREFIX.sub("", (description or "").strip()))
    return re.sub(r"\s+", " ", s).strip()


def normalize_merchant(description):
    """Merchant grouping key: light-cleaned description with trailing city/country/ref
    noise also stripped, so the same merchant produces the same key across statements
    regardless of branch/city (used for learned-override lookup, not keyword matching).
    Also strips a glued (no-space) trailing city — some banks' PDF text runs the city
    straight into the merchant name — so a correction still carries over next month
    even if that statement's extraction glued/truncated things slightly differently."""
    tokens = _light_clean(description).split()
    while tokens:
        last = tokens[-1].strip(".,-").lower()
        if last.isdigit() or last in _location_tokens():
            tokens.pop()
        else:
            break
    key = re.sub(r"\s+", " ", " ".join(tokens)).strip()
    key = _strip_trailing_location(key) or key
    return (key or description or "").strip().upper()


def _location_tokens():
    return _LOCATION_TOKENS | {t.lower() for t in plugins.get("LOCATION_TOKENS", [])}


_glued_cache = {}


def _trailing_loc_glued():
    pat = plugins.get("LOCATION_GLUED_PATTERN")                         # some statement PDFs glue the city straight onto the merchant name
    if not pat:
        return None
    if pat not in _glued_cache:
        _glued_cache[pat] = re.compile(pat, re.IGNORECASE)
    return _glued_cache[pat]


def _strip_trailing_location(text):
    rx = _trailing_loc_glued()
    if not rx:
        return text.strip()
    prev, t = None, text
    while prev != t:
        prev = t
        t = rx.sub("", t)
    return t.strip()


# ── user-defined categories (Charity, Gifts, ...). Optional keywords auto-file matching merchants. ──
_CUSTOM = None


def _ensure_custom_table():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS custom_categories (
            name TEXT PRIMARY KEY, keywords TEXT DEFAULT '', created_at TEXT NOT NULL)""")


def custom_categories():
    """[{name, keywords:[...]}] in creation order."""
    global _CUSTOM
    if _CUSTOM is None:
        _ensure_custom_table()
        with get_db() as conn:
            rows = conn.execute("SELECT name, keywords FROM custom_categories ORDER BY created_at, name").fetchall()
        _CUSTOM = [{"name": r["name"], "keywords": [k.strip().lower() for k in (r["keywords"] or "").split(",") if k.strip()]} for r in rows]
    return _CUSTOM


def get_categories():
    """Built-in + custom categories. 'Internal' stays last: it is not spending."""
    base = [c for c in CATEGORIES if c != "Internal"]
    return base + [c["name"] for c in custom_categories()] + ["Internal"]


def add_category(name, keywords=""):
    global _CUSTOM
    name = re.sub(r"\s+", " ", (name or "")).strip()
    if not 2 <= len(name) <= 30:
        raise ValueError("Give the category a name between 2 and 30 characters.")
    if name.lower() in {c.lower() for c in get_categories()}:
        raise ValueError(f'"{name}" already exists.')
    _ensure_custom_table()
    with get_db() as conn:
        conn.execute("INSERT INTO custom_categories(name,keywords,created_at) VALUES (?,?,?)",
                     (name, keywords or "", datetime.utcnow().isoformat()))
    _CUSTOM = None
    moved = apply_keywords(name)
    return {"name": name, "moved": moved}


def apply_keywords(name):
    """File existing 'Other' transactions that match this category's keywords. Only touches 'Other',
    so nothing already categorised (by a rule or by you) is changed."""
    cat = next((c for c in custom_categories() if c["name"] == name), None)
    if not cat or not cat["keywords"]:
        return 0
    with get_db() as conn:
        rows = conn.execute("SELECT id, description FROM statement_transactions WHERE category='Other'").fetchall()
        ids = [r["id"] for r in rows if any(k in r["description"].lower() for k in cat["keywords"])]
        conn.executemany("UPDATE statement_transactions SET category=? WHERE id=?", [(name, i) for i in ids])
    return len(ids)


def delete_category(name):
    global _CUSTOM
    if name not in {c["name"] for c in custom_categories()}:
        raise ValueError("Only categories you added can be removed.")
    with get_db() as conn:
        n = conn.execute("UPDATE statement_transactions SET category='Other' WHERE category=?", (name,)).rowcount
        conn.execute("DELETE FROM merchant_categories WHERE category=?", (name,))
        conn.execute("DELETE FROM custom_categories WHERE name=?", (name,))
    _CUSTOM = None
    return n


def keyword_categorize(text):
    import categorizer as CZ
    for c in custom_categories():
        if any(k in (text or "").lower() for k in c["keywords"]):
            return c["name"]
    return CZ.classify(text)[0]


def _load_overrides(conn):
    return {r["merchant_key"]: r["category"] for r in
            conn.execute("SELECT merchant_key, category FROM merchant_categories").fetchall()}


def categorize(description, overrides=None):
    """Classify a transaction description: a learned merchant override (from a past
    user correction) takes precedence over keyword matching."""
    key = normalize_merchant(description)
    if overrides is not None:
        cat = overrides.get(key)
    else:
        with get_db() as conn:
            row = conn.execute(
                "SELECT category FROM merchant_categories WHERE merchant_key=?", (key,)).fetchone()
            cat = row["category"] if row else None
    return cat or keyword_categorize(_light_clean(description))


def _to_float(s):
    try:
        return float(s.replace(",", ""))
    except Exception:
        return 0.0


def extract_transactions(bank, pdf_text, stmt_date_iso):
    """Transactions read from a statement's text: a plugin for this bank if you have one (plugins.py), else the generic line reader."""
    if not pdf_text:
        return []
    parser = plugins.get("CARD_TXN_PARSERS", {}).get(bank)
    if parser:
        rows = parser(pdf_text, stmt_date_iso)
        if rows:
            return rows
    return statement_parse.parse_lines(pdf_text, stmt_date_iso)


def save_transactions(statement_id, card_id, txns):
    custom_categories()          # creates and loads your categories first: doing it while the write below is open would lock the database
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        overrides = _load_overrides(conn)
        conn.execute("DELETE FROM statement_transactions WHERE statement_id=?", (statement_id,))
        for t in txns:
            conn.execute(
                """INSERT INTO statement_transactions
                   (statement_id,card_id,txn_date,description,amount,category,created_at,fx_currency,fx_amount,fx_rate)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (statement_id, card_id, t["txn_date"], t["description"], t["amount"],
                 categorize(t["description"], overrides), now, t.get("fx_currency", ""), t.get("fx_amount", 0), t.get("fx_rate", 0)))


def get_transactions(statement_id):
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM statement_transactions WHERE statement_id=? ORDER BY txn_date, id",
            (statement_id,)).fetchall()]


def update_category(txn_id, category, sub=""):
    """Recategorize one transaction and remember the merchant -> category mapping so
    future imports (and other existing transactions from the same merchant) inherit
    the correction. Returns how many rows were updated in total."""
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT description FROM statement_transactions WHERE id=?", (txn_id,)).fetchone()
        if not row:
            return 0
        desc = row["description"]
    import categorizer as CZ
    key = CZ.set_merchant(desc, category, sub)
    CZ.recategorize()
    with get_db() as conn:
        return sum(1 for r in conn.execute("SELECT description FROM statement_transactions").fetchall() if normalize_merchant(r["description"]) == key)


def recategorize_all():
    import categorizer as CZ
    return CZ.recategorize()["changed"]


def _recategorize_all_old():
    """Re-run classification (keyword engine + learned overrides) over every stored
    transaction. Use this after improving the category rules so existing imports
    benefit too, not just new ones."""
    with get_db() as conn:
        overrides = _load_overrides(conn)
        rows = conn.execute("SELECT id, description, category FROM statement_transactions").fetchall()
        recomputed = [(categorize(r["description"], overrides), r["id"], r["category"]) for r in rows]
        updates = [(new_cat, tid) for new_cat, tid, old_cat in recomputed if new_cat != old_cat]
        conn.executemany("UPDATE statement_transactions SET category=? WHERE id=?", updates)
    return len(updates)


def spend_by_category(days_back=180):
    """Aggregate spend (positive amounts only, i.e. purchases not payments) by category."""
    # anchored to the latest transaction on file, not today: statements arrive late, so "the last 35
    # days from today" can be an almost empty window even though plenty of data exists.
    with get_db() as conn:
        latest = conn.execute("SELECT MAX(txn_date) d FROM statement_transactions").fetchone()["d"]
    anchor = date.fromisoformat(latest) if latest else date.today()
    cutoff_date = (anchor - timedelta(days=days_back)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT category, SUM(amount) total FROM statement_transactions
               WHERE amount > 0 AND category != 'Internal' AND txn_date >= ? GROUP BY category ORDER BY total DESC""",
            (cutoff_date,)).fetchall()
    return [{"category": r["category"], "total": r["total"]} for r in rows]


def monthly_spend(months_back=6):
    """Total spend per calendar month across all cards, most recent last."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT substr(txn_date,1,7) month, SUM(amount) total
               FROM statement_transactions WHERE amount > 0 AND category != 'Internal'
               GROUP BY month ORDER BY month DESC LIMIT ?""",
            (months_back,)).fetchall()
    return list(reversed([{"month": r["month"], "total": r["total"]} for r in rows]))


def cycle_month(statement_date_iso):
    """The statement cycle a statement belongs to, as YYYY-MM. Banks close cycles on different days
    (5th, 14th, 21st...), so spending is grouped by *statement cycle*, not calendar month: every
    card's cycle is a complete billing month by construction. A statement that arrives in the first
    days of a month closes the previous month's cycle."""
    d = date.fromisoformat(statement_date_iso)
    if d.day <= 5:
        d = (d.replace(day=1) - timedelta(days=1))
    return d.strftime("%Y-%m")


def get_spend_transactions(days_back=180):
    """Raw purchase transactions (amount > 0) with card context, for client-side
    cross-filtering between the category and monthly-spend charts."""
    import categorizer as CZ
    CZ.ensure_columns()
    cutoff_date = (date.today() - timedelta(days=days_back)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """SELECT t.id, t.statement_id, t.card_id, t.txn_date, t.description, t.amount, t.category, t.subcategory, t.trip, t.trip_manual, t.fx_currency, t.fx_amount,
                      c.name card_name, c.card_color, s.statement_date
               FROM statement_transactions t JOIN credit_cards c ON t.card_id=c.id
               LEFT JOIN card_statements s ON s.id=t.statement_id
               WHERE t.amount > 0 AND t.category != 'Internal' AND t.txn_date >= ?
               ORDER BY t.txn_date DESC""",
            (cutoff_date,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["cycle"] = cycle_month(d["statement_date"]) if d.get("statement_date") else d["txn_date"][:7]
        out.append(d)
    return out
