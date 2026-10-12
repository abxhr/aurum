"""eToro sync via the public API (read-only keys): positions -> holdings, cash balance -> a cash holding.

Needs the two keys from eToro: Settings > Trading > API Key Management (Real environment, Read permission):
a public API key (x-api-key) and a user key (x-user-key). They can read your portfolio only; Read keys
cannot trade. Keys are stored locally and never sent back to the browser.

Only plain long, unleveraged positions are imported (those are real shares). Leveraged / short / CFD
positions and copy-trading mirrors are skipped and reported, so they can't distort net worth.
"""
import json, uuid, urllib.parse, urllib.request, urllib.error
from datetime import datetime
import vault
from database import get_db
import prices as P

BASE = "https://public-api.etoro.com/api/v1"
BASE_V2 = "https://public-api.etoro.com/api/v2"
SECRET_KEYS = {"etoro_api_key", "etoro_user_key"}
TYPE_TO_CLASS = {"stocks": "stock", "etf": "etf", "crypto": "crypto", "bonds": "bond", "trustfunds": "mutual_fund"}
LIVE_PRICED = {"stock", "etf"}   # Yahoo can price these from the ticker; others use eToro's own value


def _s(k, default=""):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return vault.unseal(r["value"]) if r else default


def _set(k, v):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, vault.seal(str(v)) if k in vault.SECRET_KEYS else str(v)))


def configured():
    return bool(_s("etoro_api_key") and _s("etoro_user_key"))


def _get(url, params=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={
        "x-api-key": _s("etoro_api_key"), "x-user-key": _s("etoro_user_key"),
        "x-request-id": str(uuid.uuid4()), "Accept": "application/json", "User-Agent": "wealth-tracker/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("eToro rejected the keys (HTTP %d). Check both keys, that the user key is for the Real "
                               "environment with Read permission, and that it has no IP restriction." % e.code)
        if e.code == 429:
            raise RuntimeError("eToro rate limit reached. It will retry on the next sync.")
        raise RuntimeError(f"eToro returned HTTP {e.code}.")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach eToro: {e.reason}")


def fetch_portfolio():
    d = _get(f"{BASE}/trading/info/portfolio")
    cp = d.get("clientPortfolio", d) if isinstance(d, dict) else {}
    return cp.get("positions", []) or [], float(cp.get("credit") or 0) + float(cp.get("bonusCredit") or 0), cp.get("mirrors", []) or []


def fetch_instruments(ids):
    """{instrumentId: {symbol, name, type}}. One request per id: eToro returns only part of the list
    when several ids are sent together (rate limit is 120/min, far above a personal portfolio)."""
    out = {}
    for iid in sorted(set(int(i) for i in ids)):
        res = _get(f"{BASE_V2}/market-data/instruments", {"instrumentsIds": [iid]})
        for it in (res or {}).get("results", []) or (res or {}).get("items", []) or []:
            got = it.get("instrumentId") or it.get("instrumentID")
            if got is not None and int(got) == iid:
                out[iid] = {"symbol": (it.get("symbol") or it.get("internalSymbolFull") or "").strip(),
                            "name": it.get("displayName") or it.get("instrumentDisplayName") or "",
                            "type": (it.get("type") or "").lower()}
    return out


def aggregate(positions):
    """Group plain long, unleveraged positions by instrument. Everything else is returned separately."""
    agg, other = {}, []
    for p in positions:
        if not p.get("isBuy", True) or float(p.get("leverage") or 1) != 1 or int(p.get("mirrorID") or 0):
            other.append(p)
            continue
        k = int(p.get("instrumentID"))
        a = agg.setdefault(k, {"units": 0.0, "value": 0.0, "cost": 0.0})
        a["units"] += float(p.get("units") or 0)
        a["value"] += float(p.get("unitsBaseValueDollars") or p.get("amount") or 0)
        a["cost"] += float(p.get("initialAmountInDollars") or p.get("amount") or 0)
    return agg, other


def cfd_equity(p, price):
    """Equity (what closing it would return) of a leveraged/short position: margin + P&L, never below 0."""
    amount = float(p.get("amount") or 0)
    units = float(p.get("units") or 0)
    open_rate = float(p.get("openRate") or 0)
    if not price or not open_rate:
        return amount
    pnl = units * (price - open_rate) * (1 if p.get("isBuy", True) else -1)
    return max(0.0, amount + pnl)


def ignored():
    try:
        return sorted(json.loads(_s("etoro_ignored", "[]") or "[]"))
    except ValueError:
        return []


def ignore(symbol):
    _set("etoro_ignored", json.dumps(sorted(set(ignored()) | {symbol.upper()})))


def clear_ignored():
    _set("etoro_ignored", "[]")


def sync(take_snapshot=None):
    if not configured():
        raise RuntimeError("eToro is not configured. Add both keys in Settings.")
    try:
        positions, credit, mirrors = fetch_portfolio()
        agg, other = aggregate(positions)
        meta = fetch_instruments(list(agg) + [int(p["instrumentID"]) for p in other]) if (agg or other) else {}
        skip_syms = set(ignored())
        items, unresolved, cfds = [], 0, []
        for iid, a in agg.items():
            m = meta.get(iid)
            if not m or not m["symbol"]:
                unresolved += 1
                continue
            cls = TYPE_TO_CLASS.get(m["type"], "other")
            if m["symbol"].upper() in skip_syms:
                continue
            price = a["value"] / a["units"] if a["units"] else 0   # eToro's value is cost-based; fixed below for live-priced classes
            if cls in LIVE_PRICED:
                q = P.quote(m["symbol"])
                if q and q.get("price"):
                    price = float(q["price"])
            value = price * a["units"] if cls in LIVE_PRICED else a["value"]
            items.append({"symbol": m["symbol"], "name": m["name"] or m["symbol"], "asset_class": cls,
                          "units": a["units"], "price": price, "value": value, "cost": a["cost"],
                          "mode": "ticker" if cls in LIVE_PRICED else "manual"})
        include_cfd = _s("etoro_include_cfd", "0") == "1"
        for p in other:
            m = meta.get(int(p["instrumentID"])) or {}
            sym = (m.get("symbol") or "").strip()
            if not sym or int(p.get("mirrorID") or 0):
                continue
            q = P.quote(sym)
            eq = cfd_equity(p, float(q["price"]) if q and q.get("price") else None)
            cfds.append({"symbol": sym, "leverage": float(p.get("leverage") or 1), "short": not p.get("isBuy", True), "equity": round(eq, 2)})
            if include_cfd and f"{sym}-CFD".upper() not in skip_syms:
                kind = "short" if not p.get("isBuy", True) else f"{p.get('leverage')}x"
                items.append({"symbol": f"{sym}-CFD", "name": f"{m.get('name') or sym} (CFD {kind})", "asset_class": TYPE_TO_CLASS.get(m.get("type", ""), "other"),
                              "units": float(p.get("units") or 0), "price": 0, "value": eq, "cost": float(p.get("amount") or 0), "mode": "manual"})
        if credit > 0.005 and "ETORO-CASH-USD" not in skip_syms:
            items.append({"symbol": "ETORO-CASH-USD", "name": "eToro cash (USD)", "asset_class": "cash",
                          "units": 0, "price": 0, "value": credit, "cost": 0, "mode": "manual"})
        if unresolved and not items:
            raise RuntimeError("Fetched your positions but could not look up their tickers from eToro.")
        created, updated, archived = _upsert(items)
        if take_snapshot:
            take_snapshot()
        info = {"positions": len(positions), "instruments": len(agg), "skipped_leveraged_or_short": 0 if include_cfd else len(cfds),
                "cfds": cfds, "include_cfd": include_cfd, "copy_trading_mirrors": len(mirrors), "unresolved": unresolved,
                "cash_usd": round(credit, 2)}
        _set("etoro_info", json.dumps(info))
        _set("etoro_last_sync", datetime.utcnow().isoformat())
        _set("etoro_last_error", "")
        return {"ok": True, "created": created, "updated": updated, "archived": archived, "info": info}
    except Exception as e:
        msg = str(e)
        for k in SECRET_KEYS:
            if _s(k):
                msg = msg.replace(_s(k), "***")
        _set("etoro_last_error", msg)
        raise RuntimeError(msg)


def _upsert(items):
    now = datetime.utcnow().isoformat()
    created = updated = 0
    seen = []
    with get_db() as conn:
        prev = set(json.loads(_s("etoro_synced_ids", "[]") or "[]"))
        existing = conn.execute("SELECT * FROM holdings WHERE archived=0 AND LOWER(platform) LIKE '%etoro%'").fetchall()
        by_sym = {(h["symbol"] or "").upper(): h for h in existing if h["symbol"]}
        mx = conn.execute("SELECT COALESCE(MAX(sort_order),0) s FROM holdings").fetchone()["s"]
        for it in items:
            h = by_sym.get(it["symbol"].upper())
            vals = dict(price_mode=it["mode"], symbol=it["symbol"], units=it["units"], last_price=it["price"],
                        price_ccy="USD", currency="USD", manual_value=it["value"], cost_basis=it["cost"],
                        updated_at=now, price_updated=now)
            if h:
                conn.execute("UPDATE holdings SET " + ",".join(f"{k}=?" for k in vals) + " WHERE id=?", (*vals.values(), h["id"]))
                seen.append(h["id"]); updated += 1
            else:
                mx += 1
                cur = conn.execute(
                    """INSERT INTO holdings (name,asset_class,platform,geography,currency,price_mode,symbol,units,last_price,price_ccy,
                       manual_value,cost_basis,target_pct,is_liability,notes,sort_order,created_at,updated_at,price_updated)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?)""",
                    (it["name"], it["asset_class"], "eToro", "US", "USD", it["mode"], it["symbol"], it["units"], it["price"], "USD",
                     it["value"], it["cost"], "Synced from eToro", mx, now, now, now))
                seen.append(cur.lastrowid); created += 1
        gone = [i for i in prev if i not in seen]
        for i in gone:
            conn.execute("UPDATE holdings SET archived=1, updated_at=? WHERE id=?", (now, i))
    _set("etoro_synced_ids", json.dumps(seen))
    return created, updated, len(gone)


def status():
    try:
        info = json.loads(_s("etoro_info", "{}") or "{}")
    except ValueError:
        info = {}
    return {"configured": configured(), "last_sync": _s("etoro_last_sync"), "last_error": _s("etoro_last_error"),
            "info": info, "ignored": ignored(), "include_cfd": _s("etoro_include_cfd", "0") == "1", "interval_hours": float(_s("etoro_sync_interval_hours", "6") or 6)}


def due():
    if not configured():
        return False
    last = _s("etoro_last_sync")
    if not last:
        return True
    try:
        return (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() >= float(_s("etoro_sync_interval_hours", "6") or 6) * 3600
    except ValueError:
        return True


def set_include_cfd(on):
    _set("etoro_include_cfd", "1" if on else "0")


def save_config(api_key, user_key):
    if api_key:
        _set("etoro_api_key", api_key.strip())
    if user_key:
        _set("etoro_user_key", user_key.strip())


def disconnect():
    with get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('etoro_api_key','etoro_user_key','etoro_last_error')")
