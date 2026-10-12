"""Phone notifications (Web Push). Deliberately rare: new statements arriving, and a market situation worth acting on. Nothing else.
The message is encrypted for the phone and travels through Apple's push service; it carries no balances, only what happened.
A phone can subscribe only from a Face ID session, so alerts can never be registered for anyone else."""
import base64
import json
import time
from datetime import date, datetime
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from database import real_db as get_db
import vault

PUSH_HOSTS = ("web.push.apple.com", "push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")   # where a phone's subscription may point
CLAIM = {"sub": "mailto:admin@example.com"}
MARKET_COOLDOWN = 7 * 86400          # the same market situation is announced once a week at most
VIX_ALERT = 30


def _init():
    with get_db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT, auth TEXT, created REAL, name TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS notif_log (key TEXT, at REAL, title TEXT, sent INTEGER)")


def _setting(k):
    with get_db() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return vault.unseal(r["value"]) if r else ""


def _put(k, v):
    with get_db() as c:
        c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, vault.seal(v) if k in vault.SECRET_KEYS else v))


def _vapid():
    from py_vapid import Vapid
    pem = _setting("vapid_private")
    if not pem:
        v = Vapid(); v.generate_keys()
        pem = v.private_pem().decode()
        pub = base64.urlsafe_b64encode(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
        _put("vapid_private", pem); _put("vapid_public", pub)
    return Vapid.from_pem(pem.encode())


def public_key():
    _vapid()
    return _setting("vapid_public")


def subscribe(sub, name=""):
    _init()
    ep = (sub or {}).get("endpoint", "")
    k = (sub or {}).get("keys") or {}
    host = urlparse(ep).hostname or ""
    if not ep.startswith("https://") or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS) or not k.get("p256dh") or not k.get("auth"):
        raise ValueError("not a valid phone subscription")
    with get_db() as c:
        c.execute("INSERT OR REPLACE INTO push_subs VALUES (?,?,?,?,?)", (ep, k["p256dh"], k["auth"], time.time(), (name or "")[:40]))


def unsubscribe(ep):
    _init()
    with get_db() as c:
        c.execute("DELETE FROM push_subs WHERE endpoint=?", (ep or "",))


def count():
    _init()
    with get_db() as c:
        return c.execute("SELECT COUNT(*) n FROM push_subs").fetchone()["n"]


def send(key, title, body, cooldown=0, url="/"):
    """Push to every subscribed phone. `key` makes it once-only: the same key is not sent again within `cooldown` seconds (0 = every time)."""
    _init()
    now = time.time()
    with get_db() as c:
        if cooldown and c.execute("SELECT 1 FROM notif_log WHERE key=? AND at>? AND sent>0", (key, now - cooldown)).fetchone():
            return 0
        subs = [dict(r) for r in c.execute("SELECT * FROM push_subs")]
    sent = 0
    if subs:
        from pywebpush import webpush, WebPushException
        v = _vapid()
        for s in subs:
            try:
                webpush({"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}, json.dumps({"title": title, "body": body, "url": url, "tag": key}),
                        vapid_private_key=v, vapid_claims=dict(CLAIM), ttl=6 * 3600, timeout=15)
                sent += 1
            except WebPushException as e:
                if getattr(e.response, "status_code", 0) in (404, 410):      # the phone removed the app or turned alerts off
                    unsubscribe(s["endpoint"])
            except Exception:
                pass
    with get_db() as c:
        c.execute("INSERT INTO notif_log VALUES (?,?,?,?)", (key, now, title, sent))
        c.execute("DELETE FROM notif_log WHERE at<?", (now - 90 * 86400,))
    return sent


def new_data(before, after):
    """Called after the mail jobs: one notification per kind of thing that arrived. `before`/`after` are counts from snapshot()."""
    stm, pay, bank = (after[k] - before[k] for k in ("statements", "payslips", "bank"))
    if stm > 0:
        send(f"stm:{after['statements']}", "New card statement" + ("s" if stm > 1 else ""), f"{stm} new statement{'s' if stm > 1 else ''} read from your email.")
    if pay > 0:
        send(f"pay:{after['payslips']}", "Payslip received", "Your latest payslip is in.")
    if bank > 25:        # a monthly bank statement brings dozens of rows; a trickle of rows is not news
        send(f"bank:{date.today().isocalendar()[:2]}", "Bank statement read", f"{bank} new bank transactions added.")


def snapshot():
    with get_db() as c:
        n = lambda t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
        return {"statements": n("card_statements"), "payslips": n("payslips"), "bank": n("bank_transactions")}


def market_check():
    """Only a genuinely unusual, actionable moment: a fear spike (stagger buys) or your core fund well below its high (cheap entry)."""
    import planner as PL
    out = []
    vix = PL._stats("^VIX", "VIX").get("last")
    if vix and vix >= VIX_ALERT:
        out.append(("mkt:vix", "Markets are in fear mode", f"VIX is {vix:.0f}. Prices are swinging hard: this is when staged buying pays off. Open the briefing."))
    for al in PL.settings().get("alerts", []):                 # dip alerts you set: {"symbol": "VTI", "drop_pct": 10}
        sym = str(al.get("symbol", "")).upper()
        d = PL._stats(sym, sym).get("from_1y_high_pct") if sym else None
        if d is not None and d <= -abs(float(al.get("drop_pct", 10))):
            out.append((f"mkt:{sym}", f"{sym} is well below its high", f"{sym} is {abs(d):.0f}% under its 1-year high. A cheaper entry than usual: see the briefing."))
    for key, title, body in out:          # at most one market alert per check, and each is repeated at most weekly
        if send(key, title, body, cooldown=MARKET_COOLDOWN, url="/#market"):
            break


def price_alerts():
    try:
        return json.loads(_setting("price_alerts") or "[]")
    except ValueError:
        return []


def set_price_alert(symbol, price, direction):
    sym, d = symbol.strip().upper(), ("below" if direction == "below" else "above")
    _put("price_alerts", json.dumps([a for a in price_alerts() if not (a["symbol"] == sym and a["direction"] == d)] + [{"symbol": sym, "price": float(price), "direction": d}]))


def delete_price_alert(symbol):
    _put("price_alerts", json.dumps([a for a in price_alerts() if a["symbol"] != symbol.strip().upper()]))


def price_check():
    """Push once when a symbol you asked about crosses your level, then drop that alert. Uses the live quote, checked every 30 minutes."""
    import prices
    keep = []
    for a in price_alerts():
        q = prices.quote(a["symbol"]) or {}
        px = q.get("price")
        hit = px is not None and (px >= a["price"] if a["direction"] == "above" else px <= a["price"])
        if hit:
            send(f"px:{a['symbol']}:{a['direction']}:{a['price']}:{time.time()}", f"{a['symbol']} crossed {a['price']:g}", f"{a['symbol']} is {px:g} {q.get('currency', 'USD')}, {a['direction']} your {a['price']:g} level.", url="/#market")
        else:
            keep.append(a)
    if len(keep) != len(price_alerts()):
        _put("price_alerts", json.dumps(keep))
