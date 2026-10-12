"""Interactive Brokers sync via the Flex Web Service (read-only).

IBKR has no "log in with your account" API for local apps; the supported read-only route is
Flex Queries: you create an Activity Flex Query in Client Portal (Performance & Reports >
Flex Queries) that includes Account Information, Open Positions and Cash Report, enable the
Flex Web Service to get a token, and paste the token + query id into Settings. The token can
only read reports - it cannot trade or move money.

Flow: SendRequest(token, query) -> reference code -> GetStatement(reference) -> XML.
"""
import json, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
import vault
from database import get_db

BASE = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
UA = {"User-Agent": "wealth-tracker/1.0"}
US_EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX", "BATS", "ISLAND", "NYSEARCA", "NYSEMKT", "IEX"}
SECRET_KEYS = {"ibkr_flex_token"}
GEO_BY_CCY = {"USD": "US", "CAD": "Canada", "AUD": "Australia", "EUR": "Europe", "GBP": "Europe", "CHF": "Europe"}
CLASS_BY_CAT = {"STK": "stock", "BOND": "bond", "FUND": "mutual_fund", "CRYPTO": "crypto"}


def _get_setting(k, default=""):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return vault.unseal(r["value"]) if r else default


def _set_setting(k, v):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, vault.seal(str(v)) if k in vault.SECRET_KEYS else str(v)))


def _http(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _scrub(msg, token):
    return str(msg).replace(token, "***") if token else str(msg)


def fetch_report(token, query_id):
    """Run the Flex query and return the parsed XML root of the statement."""
    q = urllib.parse.urlencode({"t": token, "q": query_id, "v": 3})
    root = ET.fromstring(_http(f"{BASE}/SendRequest?{q}"))
    if (root.findtext("Status") or "").lower() != "success":
        raise RuntimeError(f"IBKR rejected the request: {root.findtext('ErrorMessage') or 'unknown error'} "
                           f"(code {root.findtext('ErrorCode') or '?'})")
    ref = root.findtext("ReferenceCode")
    url = f"{BASE}/GetStatement"  # IBKR still returns a retired gdcdyn host here; the base host serves it
    q2 = urllib.parse.urlencode({"t": token, "q": ref, "v": 3})
    for _ in range(10):
        body = ET.fromstring(_http(f"{url}?{q2}"))
        if body.tag == "FlexQueryResponse":
            return body
        # still generating (code 1019) - wait and retry; anything else is a real error
        if body.findtext("ErrorCode") not in ("1019", "1018"):
            raise RuntimeError(f"IBKR report error: {body.findtext('ErrorMessage') or 'unknown'} "
                               f"(code {body.findtext('ErrorCode') or '?'})")
        time.sleep(3)
    raise RuntimeError("IBKR is still generating the report - try again in a minute.")


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_report(root):
    """Normalise the XML into {profile, positions, cash}."""
    profiles, positions, cash = [], [], []
    for st in root.iter("FlexStatement"):
        acct = st.get("accountId", "")
        info = st.find("AccountInformation")
        if info is not None:
            profiles.append({"account_id": info.get("accountId") or acct, "alias": info.get("acctAlias", ""),
                             "name": info.get("name", ""), "type": info.get("accountType", ""),
                             "base_currency": info.get("currency", ""), "customer_type": info.get("customerType", "")})
        elif acct:
            profiles.append({"account_id": acct, "alias": "", "name": "", "type": "", "base_currency": "", "customer_type": ""})
        for p in st.iter("OpenPosition"):
            if p.get("levelOfDetail", "SUMMARY") not in ("SUMMARY", ""):
                continue
            cat = p.get("assetCategory", "")
            if cat == "CASH":  # forex "positions" are covered by the cash report
                continue
            is_etf = (p.get("subCategory") or "").upper() == "ETF"
            positions.append({
                "account": p.get("accountId") or acct, "symbol": p.get("symbol", ""), "name": p.get("description") or p.get("symbol", ""),
                "asset_class": "etf" if (cat == "STK" and is_etf) else CLASS_BY_CAT.get(cat, "other"),
                "currency": p.get("currency", "USD"), "units": _f(p.get("position")), "price": _f(p.get("markPrice")),
                "value": _f(p.get("positionValue")), "cost_basis": abs(_f(p.get("costBasisMoney"))),
                "exchange": (p.get("listingExchange") or "").upper(), "conid": p.get("conid", ""),
            })
        for c in st.iter("CashReportCurrency"):
            ccy = c.get("currency", "")
            if ccy and ccy != "BASE_SUMMARY" and abs(_f(c.get("endingCash"))) > 0.005:
                cash.append({"account": c.get("accountId") or acct, "currency": ccy, "amount": _f(c.get("endingCash"))})
    return {"profiles": profiles, "positions": positions, "cash": cash}


def _geo(pos):
    if pos["currency"] == "USD" and pos.get("exchange", "US") in US_EXCHANGES | {""}:
        return "US"
    return GEO_BY_CCY.get(pos["currency"], "Other")


def _upsert(items, ensure_fx):
    """Create/update holdings for the parsed items. Matches existing IBKR holdings by symbol so
    nothing is duplicated. Returns counts; holdings that disappear from IBKR are archived."""
    now = datetime.utcnow().isoformat()
    created = updated = 0
    seen_ids = []
    with get_db() as conn:
        prev = set(json.loads(_get_setting("ibkr_synced_ids", "[]") or "[]"))
        existing = conn.execute("SELECT * FROM holdings WHERE archived=0 AND LOWER(platform) LIKE '%ibkr%'").fetchall()
        by_sym = {(h["symbol"] or "").upper(): h for h in existing if h["symbol"]}
        mx = conn.execute("SELECT COALESCE(MAX(sort_order),0) s FROM holdings").fetchone()["s"]
        for it in items:
            ensure_fx(it["currency"])
            key = it["symbol"].upper()
            fields = dict(name=it["name"], asset_class=it["asset_class"], platform="IBKR", geography=it["geo"],
                          currency=it["currency"], price_mode=it["price_mode"], symbol=it["symbol"], units=it["units"],
                          last_price=it["price"], price_ccy=it["currency"], manual_value=it["value"],
                          cost_basis=it["cost_basis"], updated_at=now, price_updated=now)
            h = by_sym.get(key)
            if h:
                # keep the user's own target %, notes and class choice; refresh the numbers
                keep = {"asset_class": h["asset_class"], "name": h["name"], "geography": h["geography"]}
                upd = {**fields, **keep}
                conn.execute("UPDATE holdings SET " + ",".join(f"{k}=?" for k in upd) + " WHERE id=?", (*upd.values(), h["id"]))
                seen_ids.append(h["id"]); updated += 1
            else:
                mx += 1
                cur = conn.execute(
                    """INSERT INTO holdings (name,asset_class,platform,geography,currency,price_mode,symbol,units,last_price,price_ccy,
                       manual_value,cost_basis,target_pct,is_liability,notes,sort_order,created_at,updated_at,price_updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)""",
                    (fields["name"], fields["asset_class"], "IBKR", fields["geography"], fields["currency"], fields["price_mode"],
                     fields["symbol"], fields["units"], fields["last_price"], fields["price_ccy"], fields["manual_value"],
                     fields["cost_basis"], "Synced from Interactive Brokers", mx, now, now, now))
                seen_ids.append(cur.lastrowid); created += 1
        gone = [i for i in prev if i not in seen_ids]
        for i in gone:
            conn.execute("UPDATE holdings SET archived=1, updated_at=? WHERE id=?", (now, i))
    _set_setting("ibkr_synced_ids", json.dumps(seen_ids))
    return created, updated, len(gone)


def sync(ensure_fx=lambda ccy: None, take_snapshot=None):
    token, qid = _get_setting("ibkr_flex_token"), _get_setting("ibkr_flex_query_id")
    if not token or not qid:
        raise RuntimeError("IBKR is not configured - add your Flex token and query id in Settings.")
    try:
        data = parse_report(fetch_report(token, qid))
        if not data["profiles"] and not data["positions"] and not data["cash"]:
            raise RuntimeError("The Flex query returned no accounts, positions or cash. Include Account Information, "
                               "Open Positions and Cash Report in the query.")
        skip = set(ignored())
        items = []
        for p in data["positions"]:
            us = p["exchange"] in US_EXCHANGES or p["exchange"] == ""
            live = p["currency"] == "USD" and us and p["units"] and p["symbol"].replace(".", "").isalnum()
            items.append({**p, "geo": _geo(p), "price_mode": "ticker" if live else "manual"})
        for c in data["cash"]:
            items.append({"symbol": f"IBKR-CASH-{c['currency']}", "name": f"IBKR cash ({c['currency']})", "asset_class": "cash",
                          "currency": c["currency"], "units": 0, "price": 0, "value": c["amount"], "cost_basis": 0,
                          "geo": GEO_BY_CCY.get(c["currency"], "Other"), "price_mode": "manual", "exchange": ""})
        items = [i for i in items if i["symbol"].upper() not in skip]
        created, updated, archived = _upsert(items, ensure_fx)
        if take_snapshot:
            take_snapshot()
        profile = data["profiles"][0] if data["profiles"] else {}
        profile.update({"accounts": len(data["profiles"]), "positions": len(data["positions"]), "cash_lines": len(data["cash"])})
        _set_setting("ibkr_profile", json.dumps(profile))
        _set_setting("ibkr_last_sync", datetime.utcnow().isoformat())
        _set_setting("ibkr_last_error", "")
        return {"ok": True, "created": created, "updated": updated, "archived": archived, "profile": profile}
    except Exception as e:
        msg = _scrub(e, token)
        _set_setting("ibkr_last_error", msg)
        raise RuntimeError(msg)


def ignored():
    try:
        return sorted(json.loads(_get_setting("ibkr_ignored", "[]") or "[]"))
    except ValueError:
        return []


def ignore(symbol):
    """Remember a holding the user deleted so the next sync doesn't bring it back."""
    cur = set(ignored()) | {symbol.upper()}
    _set_setting("ibkr_ignored", json.dumps(sorted(cur)))


def clear_ignored():
    _set_setting("ibkr_ignored", "[]")


def configured():
    return bool(_get_setting("ibkr_flex_token") and _get_setting("ibkr_flex_query_id"))


def status():
    try:
        profile = json.loads(_get_setting("ibkr_profile", "{}") or "{}")
    except ValueError:
        profile = {}
    return {"configured": configured(), "token_set": bool(_get_setting("ibkr_flex_token")),
            "query_id": _get_setting("ibkr_flex_query_id"), "last_sync": _get_setting("ibkr_last_sync"),
            "last_error": _get_setting("ibkr_last_error"), "profile": profile, "ignored": ignored(),
            "interval_hours": float(_get_setting("ibkr_sync_interval_hours", "6") or 6)}


def due():
    if not configured():
        return False
    last = _get_setting("ibkr_last_sync")
    if not last:
        return True
    try:
        hours = float(_get_setting("ibkr_sync_interval_hours", "6") or 6)
        return (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() >= hours * 3600
    except ValueError:
        return True


def save_config(token, query_id, interval_hours=None):
    if token:
        _set_setting("ibkr_flex_token", token.strip())
    if query_id is not None:
        _set_setting("ibkr_flex_query_id", str(query_id).strip())
    if interval_hours:
        _set_setting("ibkr_sync_interval_hours", max(1, float(interval_hours)))


def disconnect():
    with get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('ibkr_flex_token','ibkr_flex_query_id','ibkr_last_error')")
