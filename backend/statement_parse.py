"""Generic statement readers that need no knowledge of any bank.

  parse_lines(text, statement_date_iso)      card statement text -> [{"txn_date", "description", "amount"[, "fx_*"]}]   amount > 0 is spending
  parse_account_lines(text, statement_date)  bank account statement text -> [{"txn_date", "description", "debit", "credit", "balance"}]
  parse_csv(text, kind="card"|"account")     a CSV export from any bank or card, columns found by their headers

PDF text is read line by line: a line that starts with a date (one or two of them: transaction and posting) and ends with an amount is a transaction. The sign convention is
worked out per file: CR/DR markers and brackets are honoured, and otherwise the commoner sign is taken to be spending. Banks whose layout defeats this get a plugin (plugins.py).
"""
import csv
import io
import re
from datetime import date, datetime

from database import get_setting

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_D = (r"(?:\d{4}-\d{2}-\d{2}"                                   # 2026-07-31
      r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"                       # 31/07/2026, 07-31-26
      r"|\d{1,2}[/.-]\d{1,2}(?![\d/.,-])"                       # 31/07  (no year)
      rf"|\d{{1,2}}[ /-]{_MON}(?:[/-]\d{{2,4}}|[ ,]+\d{{4}})?"        # 31 Jul, 31-Jul-2026, 31 Jul 2026
      rf"|{_MON}\.? \d{{1,2}}(?:,? \d{{4}})?)")                 # Jul 31, 2026
_NUM = r"\(?-?[$€£]?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,3})?\)?"
_LINE = re.compile(rf"^\s*(?P<d1>{_D})(?:\s+(?P<d2>{_D}))?\s+(?P<rest>.+?)\s*$", re.I)
_TAIL = re.compile(rf"^(?P<desc>.*?)\s+(?P<amt>{_NUM})\s*(?P<sfx>CR|DR|-|\+)?\s*$", re.I)
_FX = re.compile(r"^(?P<desc>.*?)\s+(?P<cur>[A-Z]{3})\s+(?P<f>[\d,]+\.\d{2})$")
_SKIP = re.compile(r"\b(opening balance|closing balance|previous balance|balance (brought|carried)|total (new|for|credits|debits|purchases|payments)|minimum payment|payment due|credit limit|available (credit|balance)|page \d+)\b", re.I)


def _num(s):
    s = s.strip()
    neg = s.startswith("(") or "-" in s
    digits = re.sub(r"[^\d.,]", "", s)
    if re.search(r",\d{1,2}$", digits) and (digits.count(",") == 1 or digits.rfind(",") > digits.rfind(".")):
        digits = digits.replace(".", "").replace(",", ".")        # decimal comma: 3000,00 and 1.234,56
    v = digits.replace(",", "")
    try:
        v = float(v)
    except ValueError:
        return None
    return -v if neg else v


def parse_date(token, statement_date_iso=None, mdy=None):
    """A date token in any of the layouts above -> ISO date, or None. Missing years are taken from the statement date."""
    t = token.strip().rstrip(".,")
    ref = date.fromisoformat(statement_date_iso) if statement_date_iso else date.today()
    if mdy is None:
        mdy = get_setting("date_order", "dmy") == "mdy"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)
        if m:
            a, b, y = m.groups()
            mo, d = (int(a), int(b)) if mdy else (int(b), int(a))
            if mo > 12 and d <= 12:
                mo, d = d, mo
            y = int(y) if y else None
        else:
            m = re.fullmatch(rf"(\d{{1,2}})[ /-]({_MON})(?:[ /,-]+(\d{{2,4}}))?", t, re.I) or None
            if m:
                d, mon, y = int(m.group(1)), m.group(2), m.group(3)
            else:
                m = re.fullmatch(rf"({_MON})\.? (\d{{1,2}})(?:,? (\d{{4}}))?", t, re.I)
                if not m:
                    return None
                mon, d, y = m.group(1), int(m.group(2)), m.group(3)
            mo = MONTHS.get(mon.lower()[:3])
            y = int(y) if y else None
    if y is None:
        y = ref.year - (1 if mo - ref.month > 1 else 0)          # a December line on a January statement belongs to last year
    elif y < 100:
        y += 2000
    try:
        return date(y, mo, d).isoformat()
    except (ValueError, TypeError):
        return None


def parse_lines(text, statement_date_iso=None):
    rows = []
    for raw in (text or "").splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        iso = parse_date(m.group("d1"), statement_date_iso)
        rest = m.group("rest")
        if not iso or _SKIP.search(rest):
            continue
        fx = None
        t = _TAIL.match(rest)
        if not t:
            continue
        desc, amt, sfx = t.group("desc"), t.group("amt"), (t.group("sfx") or "").upper()
        v = _num(amt)
        if v is None or not re.search(r"\.\d{2}\)?$|\.\d{2}\s", amt + " "):
            continue                                              # whole numbers are reference digits, not money
        f = _FX.match(desc)
        if f:                                                     # "Description  USD 40.63  153.96": the foreign amount precedes the charged one
            desc, fx = f.group("desc"), (f.group("cur"), _num(f.group("f")))
        desc = re.sub(r"\s{2,}", " ", desc).strip(" -*")
        if len(desc) < 2 or not re.search(r"[A-Za-z]", desc):
            continue
        rows.append({"txn_date": iso, "description": desc, "v": v, "sfx": sfx, "fx": fx})
    # sign: CR/+ and brackets are credits, DR is a charge; otherwise the commoner sign is spending
    plain = [r for r in rows if r["sfx"] in ("", "-")]
    negatives = sum(1 for r in plain if r["v"] < 0 or r["sfx"] == "-")
    spend_is_negative = bool(plain) and negatives > len(plain) / 2
    out = []
    for r in rows:
        mag = abs(r["v"])
        if r["sfx"] in ("CR", "+"):
            amount = -mag
        elif r["sfx"] == "DR":
            amount = mag
        else:
            neg = r["v"] < 0 or r["sfx"] == "-"
            amount = mag if (neg == spend_is_negative) else -mag
        t = {"txn_date": r["txn_date"], "description": r["description"], "amount": round(amount, 2)}
        if r["fx"] and r["fx"][1]:
            t.update({"fx_currency": r["fx"][0], "fx_amount": round(abs(r["fx"][1]) * (1 if amount > 0 else -1), 2), "fx_rate": round(mag / abs(r["fx"][1]), 6)})
        out.append(t)
    return out


def parse_account_lines(text, statement_date_iso=None):
    """Account statements: a date, a description and one to three amounts (amount, or debit/credit, then a running balance). Debit or credit is decided by how the balance moved."""
    rows = []
    for raw in (text or "").splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        iso = parse_date(m.group("d1"), statement_date_iso)
        rest = m.group("rest")
        if not iso or _SKIP.search(rest):
            continue
        nums = re.findall(rf"(?<![\w.])({_NUM})(?![\w])", rest)
        money = [n for n in nums if re.search(r"\.\d{2}\)?$", n)]
        if not money:
            continue
        desc = rest[: rest.find(money[0])].strip(" -*")
        if len(desc) < 2 or not re.search(r"[A-Za-z]", desc):
            continue
        rows.append({"txn_date": iso, "description": re.sub(r"\s{2,}", " ", desc), "n": [_num(x) for x in money]})
    out, prev = [], None
    for r in rows:
        n = r["n"]
        bal = n[-1] if len(n) >= 2 else None
        amt = abs(n[0])
        if bal is not None and prev is not None:
            credit = bal > prev
        else:                                                     # no earlier balance to compare with: go by the words, then by the sign
            credit = bool(re.search(r"salary|payroll|deposit|refund|interest|credit|received", r["description"], re.I)) or (len(n) == 1 and n[0] > 0 and False)
        out.append({"txn_date": r["txn_date"], "description": r["description"], "debit": 0.0 if credit else amt, "credit": amt if credit else 0.0, "balance": bal})
        if bal is not None:
            prev = bal
    return out


# ───────────────────────── CSV ─────────────────────────
_COLS = {
    "date": ("transaction date", "trans date", "trans. date", "posting date", "post date", "booking date", "date", "value date", "posted"),
    "desc": ("description", "details", "transaction details", "narrative", "memo", "payee", "merchant", "name", "reference", "particulars"),
    "amount": ("amount", "transaction amount", "value", "amount (usd)"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "charge", "outflow"),
    "credit": ("credit", "credits", "deposit", "deposits", "money in", "paid in", "payment", "inflow"),
    "balance": ("balance", "running balance", "available balance"),
}


def _find_cols(header):
    h = [c.strip().lower() for c in header]
    found = {}
    for key, names in _COLS.items():
        for nm in names:                                          # earlier names win: "transaction date" beats "date"
            if nm in h:
                found[key] = h.index(nm)
                break
    return found


def _infer_mdy(cells):
    """Month-first or day-first? Decided by the file itself when any date has a part above 12, else by Settings > Date order."""
    first = second = 0
    for c in cells:
        m = re.match(r"\s*(\d{1,2})[/.-](\d{1,2})[/.-]\d{2,4}", c)
        if m:
            first += int(m.group(1)) > 12
            second += int(m.group(2)) > 12
    if first and not second:
        return False
    if second and not first:
        return True
    return get_setting("date_order", "dmy") == "mdy"


def parse_csv(text, kind="card", mapping=None, spend_is_negative=None):
    """-> {"rows": [...], "columns": {...}, "spend_is_negative": bool}. Raises ValueError with what was found when the columns cannot be worked out."""
    text = (text or "").lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    cols, start = {}, 0
    for i, row in enumerate(table[:15]):                          # a few banks put a title block above the header
        c = _find_cols(row)
        if "date" in c and ("amount" in c or "debit" in c or "credit" in c):
            cols, start = c, i + 1
            break
    if mapping:
        cols = {**cols, **{k: int(v) for k, v in mapping.items() if str(v).isdigit()}}
        start = start or 1
    if "date" not in cols or not ({"amount", "debit", "credit"} & cols.keys()):
        raise ValueError("Could not find the date and amount columns. First row seen: " + ", ".join(table[0][:8] if table else []))
    d_only = "desc" not in cols
    mdy = _infer_mdy([row[cols["date"]] for row in table[start:] if cols["date"] < len(row)])
    parsed = []
    for row in table[start:]:
        def cell(k):
            i = cols.get(k)
            return row[i].strip() if i is not None and i < len(row) else ""
        iso = parse_date(cell("date"), mdy=mdy) if cell("date") else None
        if not iso:
            continue
        desc = cell("desc") if not d_only else " ".join(c for j, c in enumerate(row) if j != cols["date"])[:80]
        amt, deb, cre = (_num(cell(k)) if cell(k) else None for k in ("amount", "debit", "credit"))
        parsed.append({"txn_date": iso, "description": re.sub(r"\s+", " ", desc).strip(), "amt": amt, "deb": deb, "cre": cre, "bal": _num(cell("balance")) if cell("balance") else None})
    if not parsed:
        raise ValueError("No transaction rows were found under the header.")
    singles = [r["amt"] for r in parsed if r["amt"] is not None]
    if spend_is_negative is None:
        spend_is_negative = bool(singles) and sum(1 for v in singles if v < 0) > len(singles) / 2
    rows = []
    for r in parsed:
        if r["amt"] is not None:
            signed = -r["amt"] if spend_is_negative else r["amt"]          # now: positive = money out / spending
        else:
            signed = abs(r["deb"] or 0) - abs(r["cre"] or 0)
        if kind == "account":
            rows.append({"txn_date": r["txn_date"], "description": r["description"], "debit": signed if signed > 0 else 0.0, "credit": -signed if signed < 0 else 0.0, "balance": r["bal"]})
        else:
            rows.append({"txn_date": r["txn_date"], "description": r["description"], "amount": round(signed, 2)})
    return {"rows": rows, "columns": cols, "spend_is_negative": spend_is_negative}
