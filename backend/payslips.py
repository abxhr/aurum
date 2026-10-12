"""Payslips: find the password-protected "Pay Advice" PDFs in Gmail, read them, keep a history.

Layout assumed (as described by the owner): page 1 has a two-column table, payments on the left and
deductions on the right, each row "label ... amount", with totals and a net pay line; page 2 only
re-summarises the deductions. We read text *with positions* (so the two columns can't be mixed up),
check that the rows add up to the printed totals, and flag any payslip where they don't instead of
silently storing a wrong number.

Mail may arrive directly, or as a bundle email containing forwarded emails (message/rfc822) that each
carry the PDF, which is how the Outlook forward came through.
"""
import io, re, json, calendar
import email as _email
from email.header import decode_header, make_header
from datetime import datetime
import vault
import plugins
from database import get_db, name_list, base_ccy as CCY

MONTHS = {}
for i in range(1, 13):
    MONTHS[calendar.month_name[i].lower()] = i
    MONTHS[calendar.month_abbr[i].lower()] = i
MONEY = re.compile(r"\(?-?\d{1,3}(?:,\d{3})+\.\d{1,2}\)?|\(?-?\d+\.\d{1,2}\)?")
PASSWORD_KEY = "payslip_pdf_password"
SECRET_KEYS = {PASSWORD_KEY}


class Locked(Exception):
    """The PDF could not be opened with the saved password."""


def ensure_tables():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS payslips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL UNIQUE,          -- YYYY-MM
                subject TEXT DEFAULT '', filename TEXT DEFAULT '',
                gross REAL DEFAULT 0, deductions REAL DEFAULT 0, net REAL DEFAULT 0,
                currency TEXT DEFAULT 'USD',
                ok INTEGER DEFAULT 1, notes TEXT DEFAULT '',
                raw_text TEXT DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payslip_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payslip_id INTEGER NOT NULL,
                kind TEXT NOT NULL,                   -- 'earning' | 'deduction'
                label TEXT NOT NULL, amount REAL NOT NULL,
                FOREIGN KEY (payslip_id) REFERENCES payslips(id) ON DELETE CASCADE
            );
        """)


def _setting(k, default=""):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return vault.unseal(r["value"]) if r else default


def _set(k, v):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, vault.seal(str(v)) if k in vault.SECRET_KEYS else str(v)))


# ───────────────────────── PDF -> positioned rows ─────────────────────────
def _fragments(page):
    frags = []

    def visit(text, cm, tm, font_dict, font_size):
        t = (text or "").strip()
        if not t:
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        frags.append((x, y, t, float(font_size or 9)))

    page.extract_text(visitor_text=visit)
    return frags


def _gutter(frags, width):
    """x position of the widest empty vertical band in the middle of the page (between the two tables)."""
    spans = sorted((x, x + len(t) * fs * 0.52) for x, _, t, fs in frags)
    lo, hi = width * 0.3, width * 0.72
    best, best_gap, cursor = width / 2, 0, lo
    for a, b in spans:
        if b < lo or a > hi:
            continue
        if a - cursor > best_gap:
            best_gap, best = a - cursor, (a + cursor) / 2
        cursor = max(cursor, b)
    if hi - cursor > best_gap:
        best = (hi + cursor) / 2
    return best


def _rows(frags, tol=3.0):
    rows, cur, cur_y = [], [], None
    for f in sorted(frags, key=lambda f: (-f[1], f[0])):
        if cur_y is None or abs(f[1] - cur_y) <= tol:
            cur.append(f)
            cur_y = f[1] if cur_y is None else (cur_y + f[1]) / 2
        else:
            rows.append(cur)
            cur, cur_y = [f], f[1]
    if cur:
        rows.append(cur)
    return rows


def _num(s):
    neg = s.startswith("(") or s.startswith("-")
    v = float(re.sub(r"[^\d.]", "", s) or 0)
    return -v if neg else v


def _side(text):
    """'Basic Salary 12,000.00 12,000.00' -> ('Basic Salary', 12000.0, [12000.0])"""
    m = list(MONEY.finditer(text))
    if not m:
        return None
    label = re.sub(r"\s+", " ", text[:m[0].start()]).strip(" :-.")
    if not label:
        return None
    vals = [_num(x.group()) for x in m]
    return label, vals[0], vals


def parse_page1(page):
    """Rows of (left_text, right_text, left_item, right_item), split at the gutter between the two tables."""
    frags = _fragments(page)
    if not frags:
        return []
    split = _gutter(frags, float(page.mediabox.width))
    out = []
    for row in _rows(frags):
        row.sort(key=lambda f: f[0])
        l = " ".join(t for x, _, t, _ in row if x < split)
        r = " ".join(t for x, _, t, _ in row if x >= split)
        out.append((l, r, _side(l), _side(r)))
    return out


def _items_region(rows):
    """Row range holding the pay items: after the 'Payments | Deductions' header, up to the first 'Total' row."""
    start = next((i for i, (l, r, _, _) in enumerate(rows)
                  if re.match(r"payments?\b|earnings?\b", l, re.I) and re.match(r"deductions?\b", r, re.I)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(rows)) if re.match(r"total\b", rows[i][0], re.I) or re.match(r"total\b", rows[i][1], re.I)), None)
    return (start + 1, end if end is not None else len(rows))


_EARN_HEAD = re.compile(r"^(earnings|payments?|income|gross pay|pay items)\b", re.I)
_DED_HEAD = re.compile(r"^(deductions?|taxes|withholdings?|contributions?)\b", re.I)
_TOTALISH = re.compile(r"^(total|net|gross|subtotal|year[- ]to[- ]date|ytd)\b", re.I)


def _flat_items(text):
    """Single-column payslips: an Earnings block then a Deductions block, one 'label amount' per line. -> (earnings, deductions)."""
    earn, ded, mode = [], [], None
    for line in (text or "").splitlines():
        line = re.sub(r"\s*\|\s*", " ", line).strip()
        if _EARN_HEAD.match(line) and not _side(line):
            mode = "e"
            continue
        if _DED_HEAD.match(line) and not _side(line):
            mode = "d"
            continue
        it = _side(line)
        if mode and it and not _TOTALISH.match(it[0]):
            (earn if mode == "e" else ded).append((it[0], abs(it[1])))
    return earn, ded


def parse_pdf(pdf_bytes, password):
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        ok = False
        for pw in ([password] if password else []) + [""]:
            try:
                if reader.decrypt(pw):
                    ok = True
                    break
            except Exception:
                continue
        if not ok:
            raise Locked()
    rows = parse_page1(reader.pages[0])
    raw = "\n".join(f"{l}  |  {r}" for l, r, _, _ in rows)
    if len(reader.pages) > 1:
        try:
            raw += "\n--- page 2 ---\n" + (reader.pages[1].extract_text() or "")
        except Exception:
            pass
    page_text = " ".join(f"{l} {r}" for l, r, _, _ in rows)

    notes = []
    reg = _items_region(rows)
    if reg:
        body = rows[reg[0]:reg[1]]
        earnings = [(it[0], it[1]) for _, _, it, _ in body if it]
        deductions = [(it[0], it[1]) for _, _, _, it in body if it]
    else:
        earnings, deductions = _flat_items(raw)
        if not earnings:
            notes.append("could not find the Payments / Deductions table, so the rows may be wrong")
            earnings = [(it[0], it[1]) for _, _, it, _ in rows if it and not _is_total(it[0])]
            deductions = [(it[0], it[1]) for _, _, _, it in rows if it and not _is_total(it[0])]
    gross = round(sum(v for _, v in earnings), 2)
    ded_total = round(sum(v for _, v in deductions), 2)

    # numbers the payslip prints itself, used only to double-check what we read
    printed = set()
    for pat in (r"\bTotal\s+(-?[\d,]+\.\d{2})", r"Amount\s+Amount\s+(-?[\d,]+\.\d{2})", r"(?:total|gross)\s+(?:pay|earnings|deductions)[:\s]+(-?[\d,]+\.\d{2})"):
        for m in re.finditer(pat, page_text):
            printed.add(round(_num(m.group(1)), 2))
    net_printed = None
    m = (re.search(r"\b[A-Z]{2}\d{2}[\dXx*]{6,}\s+(-?[\d,]+\.\d{2})", page_text)                              # an IBAN followed by the amount paid into it
         or re.search(r"(?:net\s+(?:pay|salary|amount|income|paid)|take[- ]home(?:\s+pay)?|amount\s+paid)[:\s]+\$?\s*(-?[\d,]+\.\d{2})", page_text, re.I))
    if m:
        net_printed = round(_num(m.group(1)), 2)
    net = net_printed if net_printed is not None else round(gross - ded_total, 2)

    if not earnings:
        notes.append("no payment rows found")
    if printed and not ({gross, ded_total} & printed):
        notes.append(f"payments {gross:,.2f} and deductions {ded_total:,.2f} match none of the printed totals")
    elif printed and (gross not in printed or ded_total not in printed):
        missing = "payments" if gross not in printed else "deductions"
        notes.append(f"{missing} total doesn't match a printed total")
    if net_printed is not None and abs(gross - ded_total - net_printed) > 0.06:
        notes.append(f"payments minus deductions is {gross - ded_total:,.2f} but the printed net pay is {net_printed:,.2f}")
    currency = CCY()
    m = re.search(r"\b(AED|AUD|BRL|CAD|CHF|CNY|CZK|DKK|EUR|GBP|HKD|HUF|IDR|ILS|INR|JPY|KRW|MXN|MYR|NOK|NZD|PHP|PLN|SAR|SEK|SGD|THB|TRY|USD|ZAR)\b", raw)
    if m:
        currency = m.group(1)
    return {"earnings": earnings, "deductions": deductions, "gross": gross, "ded_total": ded_total, "net": net,
            "ok": not notes, "notes": "; ".join(notes), "raw": raw, "currency": currency}


def _period(subject, filename, raw):
    for text in (re.findall(r"(?:pay advice|pay ?slip|salary slip|pay period|earnings statement)\s+(?:for|of)?\s*[A-Z]+\s+\d{4}", raw or "", re.I)[:1] + [subject or "", filename or "", raw or ""]):
        m = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
                      r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[\s_\-.]*(?:of\s+)?(\d{4})\b", text, re.I)
        if m:
            name = m.group(1).lower()
            mon = MONTHS.get(name) or MONTHS.get(name[:3])
            if mon:
                return f"{m.group(2)}-{mon:02d}"
    return None


# ───────────────────────── mail -> PDFs ─────────────────────────
def _dec(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def collect_pdfs(msg, subject=None, out=None):
    """All (subject, filename, bytes) PDFs in a message, descending into forwarded emails and
    attributing each PDF to the subject of the email that actually carries it."""
    out = [] if out is None else out
    subject = subject if subject is not None else _dec(msg.get("Subject"))
    if msg.is_multipart():
        for part in msg.get_payload():
            if part.get_content_type() == "message/rfc822":
                inner = part.get_payload()
                inner = inner[0] if isinstance(inner, list) and inner else None
                if inner is not None:
                    collect_pdfs(inner, _dec(inner.get("Subject")), out)
            else:
                collect_pdfs(part, subject, out)
    else:
        name = (msg.get_filename() or "")
        if msg.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            data = msg.get_payload(decode=True)
            if data:
                out.append((subject, _dec(name), data))
        elif msg.get_content_type() == "message/rfc822":
            inner = msg.get_payload()
            if isinstance(inner, list) and inner:
                collect_pdfs(inner[0], _dec(inner[0].get("Subject")), out)
    return out


def _looks_like_payslip(subject, filename):
    return bool(re.search(r"pay\s*advice|payslip|pay\s*slip|salary", f"{subject} {filename}", re.I))


# ───────────────────────── storage ─────────────────────────
def _store(period, subject, filename, parsed):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        old = conn.execute("SELECT id FROM payslips WHERE period=?", (period,)).fetchone()
        if old:
            conn.execute("DELETE FROM payslip_items WHERE payslip_id=?", (old["id"],))
            conn.execute("DELETE FROM payslips WHERE id=?", (old["id"],))
        cur = conn.execute(
            """INSERT INTO payslips(period,subject,filename,gross,deductions,net,currency,ok,notes,raw_text,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (period, subject, filename, parsed["gross"], parsed["ded_total"], parsed["net"], parsed["currency"],
             1 if parsed["ok"] else 0, parsed["notes"], parsed["raw"], now))
        pid = cur.lastrowid
        for kind, items in (("earning", parsed["earnings"]), ("deduction", parsed["deductions"])):
            for label, amount in items:
                conn.execute("INSERT INTO payslip_items(payslip_id,kind,label,amount) VALUES (?,?,?,?)", (pid, kind, label, amount))
    return pid


def scan(box, limit=60):
    ensure_tables()
    pw = _setting(PASSWORD_KEY)
    res = {"ok": True, "messages": 0, "pdfs": 0, "imported": 0, "needs_review": 0, "locked": 0, "skipped": 0, "errors": []}
    ids = []
    for q in ('subject:Payslips', 'subject:Payslip', 'subject:"Pay Advice"'):
        for i in box.search(q, limit):
            if i not in ids:
                ids.append(i)
    res["messages"] = len(ids)
    for mid in ids:
        try:
            msg = box.message(mid)
            if msg is None:
                continue
            for subject, filename, data in collect_pdfs(msg):
                res["pdfs"] += 1
                if not _looks_like_payslip(subject, filename):
                    res["skipped"] += 1
                    continue
                try:
                    parsed = parse_pdf(data, pw)
                except Locked:
                    res["locked"] += 1
                    continue
                period = _period(subject, filename, parsed["raw"])
                if not period:
                    res["skipped"] += 1
                    res["errors"].append(f"could not tell which month '{subject or filename}' is for")
                    continue
                _store(period, subject, filename, parsed)
                res["imported"] += 1
                if not parsed["ok"]:
                    res["needs_review"] += 1
        except Exception as e:
            res["errors"].append(str(e)[:120])
    _set("payslip_last_scan", datetime.utcnow().isoformat())
    if res["locked"] and not res["imported"]:
        res["ok"] = False
        res["error"] = ("The PDFs are password-protected and the saved password didn't open them. "
                        "Check the payslip password in Settings." if pw else "Add the payslip PDF password in Settings first.")
    _set("payslip_last_error", res.get("error", ""))
    return res


# ───────────────────────── reading back ─────────────────────────
def spend_words():
    """Payslip deduction labels that are really your own spending taken at source (a company shop, travel bought through payroll...). Settings > Payslips; a plugin may add words."""
    mine = [w.strip().lower() for w in _setting("payslip_spend_words").split(",") if w.strip()]
    return mine + [w.lower() for w in plugins.get("PAYSLIP_SPEND_WORDS", [])]


def _is_spend(label):
    return any(w in (label or "").lower() for w in spend_words())


def list_all():
    ensure_tables()
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT id,period,subject,gross,deductions,net,currency,ok,notes FROM payslips ORDER BY period").fetchall()]
        items = conn.execute("SELECT payslip_id,kind,label,amount FROM payslip_items ORDER BY id").fetchall()
    by = {}
    for it in items:
        by.setdefault(it["payslip_id"], []).append({"kind": it["kind"], "label": it["label"], "amount": it["amount"]})
    for r in rows:
        r["items"] = by.get(r["id"], [])
    return rows


def detail(pid):
    ensure_tables()
    with get_db() as conn:
        r = conn.execute("SELECT * FROM payslips WHERE id=?", (pid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["items"] = next((x["items"] for x in list_all() if x["id"] == pid), [])
    return d


def base_net(r):
    """Net pay before deductions that are really your spending (see spend_words), so they are budgeted as spending, not hidden inside a lower 'take-home'."""
    return r["net"] + sum(i["amount"] for i in r["items"] if i["kind"] == "deduction" and _is_spend(i["label"]))


def summary():
    rows = list_all()
    st = {"count": len(rows), "last_scan": _setting("payslip_last_scan"), "last_error": _setting("payslip_last_error"),
          "password_set": bool(_setting(PASSWORD_KEY)), "payslips": rows}
    if not rows:
        return st
    last12 = rows[-12:]
    latest = rows[-1]
    ded_tot, earn_tot = {}, {}
    for r in last12:
        for it in r["items"]:
            tgt = ded_tot if it["kind"] == "deduction" else earn_tot
            tgt[it["label"]] = tgt.get(it["label"], 0) + it["amount"]
    st.update({
        "latest": {k: latest[k] for k in ("id", "period", "gross", "deductions", "net", "currency", "ok")},
        "latest_items": latest["items"],
        "avg_net_12": round(sorted(r["net"] for r in last12)[len(last12) // 2] if len(last12) % 2 else sum(sorted(r["net"] for r in last12)[len(last12) // 2 - 1:len(last12) // 2 + 1]) / 2, 2),
        "typical_net_3": round(sorted(r["net"] for r in rows[-3:])[len(rows[-3:]) // 2], 2),
        "avg_gross_12": round(sum(r["gross"] for r in last12) / len(last12), 2),
        "months_12": len(last12),
        "mean_net_12": round(sum(r["net"] for r in last12) / len(last12), 2),
        "median_gross_12": round(sorted(r["gross"] for r in last12)[len(last12) // 2], 2),
        "deductions_12": sorted(({"label": k, "total": round(v, 2), "spend": _is_spend(k)} for k, v in ded_tot.items()),
                                key=lambda x: -x["total"]),
        "spend_series": [{"period": r["period"], "amount": round(sum(i["amount"] for i in r["items"]
                                                                       if i["kind"] == "deduction" and _is_spend(i["label"])), 2)} for r in rows],
        "reimbursed_series": [{"period": r["period"], "amount": round(sum(i["amount"] for i in r["items"]
                                                                       if i["kind"] == "earning" and _is_spend(i["label"])), 2)} for r in rows],
        "needs_review": sum(1 for r in rows if not r["ok"]),
    })
    return st


