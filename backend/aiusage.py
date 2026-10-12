"""A ledger of every AI call and every call a cache saved, so the cost of the app's intelligence is visible and can be tuned.
Tokens are recorded as the provider reports them; the Decisions classifier is counted in calls (it bills input only)."""
from datetime import datetime, timedelta
from database import get_db


def _ensure():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ai_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, feature TEXT NOT NULL, model TEXT DEFAULT '', kind TEXT NOT NULL, tin INTEGER DEFAULT 0, tout INTEGER DEFAULT 0, tcache INTEGER DEFAULT 0)")


def log(feature, model="", tin=0, tout=0, tcache=0, kind="call"):
    try:
        _ensure()
        with get_db() as conn:
            conn.execute("INSERT INTO ai_usage(at,feature,model,kind,tin,tout,tcache) VALUES (?,?,?,?,?,?,?)",
                         (datetime.utcnow().isoformat(), feature, model or "", kind, int(tin or 0), int(tout or 0), int(tcache or 0)))
    except Exception:
        pass                                    # accounting must never break the feature it measures


def hit(feature):
    log(feature, kind="hit")


def summary(days=30):
    _ensure()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_db() as conn:
        rows = conn.execute("""SELECT feature, kind, COUNT(*) n, SUM(tin) tin, SUM(tout) tout, SUM(tcache) tcache FROM ai_usage WHERE at>=? GROUP BY feature, kind""", (since,)).fetchall()
    out = {}
    for r in rows:
        f = out.setdefault(r["feature"], {"feature": r["feature"], "calls": 0, "decisions": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0})
        if r["kind"] == "hit":
            f["cache_hits"] += r["n"]
        elif r["kind"] == "decision":
            f["decisions"] += r["n"]
        else:
            f["calls"] += r["n"]; f["tokens_in"] += r["tin"] or 0; f["tokens_out"] += r["tout"] or 0; f["tokens_cached"] += r["tcache"] or 0
    rows = sorted(out.values(), key=lambda x: -(x["tokens_in"] + x["tokens_out"]))
    tot = {k: sum(r[k] for r in rows) for k in ("calls", "decisions", "cache_hits", "tokens_in", "tokens_out", "tokens_cached")}
    return {"days": days, "features": rows, "total": tot}
