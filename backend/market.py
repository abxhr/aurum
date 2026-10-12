"""Watchlist CRUD — market quote/search/history live in prices.py."""
from datetime import datetime
from database import get_db


def get_watchlist():
    with get_db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM watchlist ORDER BY added_at DESC").fetchall()]


def add_watch(symbol, name=""):
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO watchlist (symbol, name, added_at) VALUES (?,?,?)",
            (symbol.upper(), name, now))


def remove_watch(symbol):
    with get_db() as conn:
        conn.execute("DELETE FROM watchlist WHERE symbol=?", (symbol.upper(),))
