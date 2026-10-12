"""SQLite layer for Aurum: holdings, credit cards, statements, email-sync config, budget settings."""
import sqlite3, os, threading, contextvars, concurrent.futures
from datetime import datetime, date
from contextlib import contextmanager

DB_PATH = os.environ.get(
    "PORTFOLIO_DB",
    os.path.join(os.path.dirname(__file__), "..", "data", "portfolio.db"),
)
DEMO_PATH = os.path.join(os.path.dirname(DB_PATH), "demo.db")     # invented data for demos; a session in demo mode never opens the real file
_demo = contextvars.ContextVar("demo", default=False)


def set_demo(on):
    _demo.set(bool(on))


def in_demo():
    return _demo.get()


# worker threads started during a request must see the same database as that request, or a demo page could read (or write) real data
_Thread_init = threading.Thread.__init__
def _thread_init(self, *a, **k):
    _Thread_init(self, *a, **k)
    ctx, run = contextvars.copy_context(), self.run
    self.run = lambda: ctx.run(run)
threading.Thread.__init__ = _thread_init
_submit = concurrent.futures.ThreadPoolExecutor.submit
concurrent.futures.ThreadPoolExecutor.submit = lambda self, fn, *a, **k: _submit(self, contextvars.copy_context().run, fn, *a, **k)

ASSET_CLASSES = ["stock","etf","mutual_fund","bond","savings","gold","crypto","cash","other"]


@contextmanager
def get_db():
    conn = sqlite3.connect(DEMO_PATH if _demo.get() else DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_setting(key, default=""):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else default


def name_list(key):
    """A comma-separated setting as lowercase names (used to recognise your own name and your family's in bank and payslip text)."""
    return [x.strip().lower() for x in get_setting(key).split(",") if x.strip()]


def base_ccy():
    """The one currency every amount is stored and shown in (Settings > Currency). Columns and fields named *_base simply mean "in this currency"."""
    try:
        with get_db() as conn:
            r = conn.execute("SELECT value FROM settings WHERE key='base_currency'").fetchone()
        return (r["value"] if r else "") or "USD"
    except sqlite3.Error:
        return "USD"


@contextmanager
def real_db():
    """Always the real file: sign-in, sessions, passkeys and phone alerts are not part of any demo."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS holdings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                asset_class   TEXT NOT NULL DEFAULT 'cash',
                platform      TEXT DEFAULT '',
                geography     TEXT DEFAULT '',
                currency      TEXT NOT NULL DEFAULT 'USD',
                price_mode    TEXT NOT NULL DEFAULT 'manual',
                symbol        TEXT DEFAULT '',
                units         REAL DEFAULT 0,
                last_price    REAL DEFAULT 0,
                price_ccy     TEXT DEFAULT '',
                manual_value  REAL DEFAULT 0,
                cost_basis    REAL DEFAULT 0,
                target_pct    REAL DEFAULT 0,
                is_liability  INTEGER NOT NULL DEFAULT 0,
                notes         TEXT DEFAULT '',
                sort_order    INTEGER DEFAULT 0,
                archived      INTEGER NOT NULL DEFAULT 0,
                price_updated TEXT DEFAULT '',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                holding_id INTEGER NOT NULL,
                snap_date  TEXT NOT NULL,
                value_base  REAL NOT NULL,
                FOREIGN KEY (holding_id) REFERENCES holdings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS fx_rates (
                currency    TEXT NOT NULL,
                rate_to_base REAL NOT NULL,
                as_of       TEXT NOT NULL,
                PRIMARY KEY (currency, as_of)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );

            /* ---- credit cards ---- */
            CREATE TABLE IF NOT EXISTS credit_cards (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                name           TEXT NOT NULL,
                bank           TEXT NOT NULL DEFAULT '',
                last_four      TEXT DEFAULT '',
                currency       TEXT NOT NULL DEFAULT 'USD',
                credit_limit   REAL DEFAULT 0,
                statement_day  INTEGER DEFAULT 1,
                due_days       INTEGER DEFAULT 25,
                card_color     TEXT DEFAULT '#d9a441',
                pdf_password   TEXT DEFAULT '',
                email_sender   TEXT DEFAULT '',
                email_subject  TEXT DEFAULT '',
                amount_label   TEXT DEFAULT '',
                notes          TEXT DEFAULT '',
                archived       INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS card_statements (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id         INTEGER NOT NULL,
                statement_date  TEXT NOT NULL,
                due_date        TEXT NOT NULL,
                total_due       REAL NOT NULL DEFAULT 0,
                minimum_due     REAL DEFAULT 0,
                paid_amount     REAL DEFAULT 0,
                paid_date       TEXT DEFAULT '',
                status          TEXT DEFAULT 'unpaid',
                source          TEXT DEFAULT 'manual',
                email_subject   TEXT DEFAULT '',
                email_snippet   TEXT DEFAULT '',
                notes           TEXT DEFAULT '',
                created_at      TEXT NOT NULL,
                FOREIGN KEY (card_id) REFERENCES credit_cards(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS statement_transactions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                statement_id  INTEGER NOT NULL,
                card_id       INTEGER NOT NULL,
                txn_date      TEXT NOT NULL,
                description   TEXT NOT NULL,
                amount        REAL NOT NULL,
                category      TEXT DEFAULT 'Other',
                created_at    TEXT NOT NULL,
                FOREIGN KEY (statement_id) REFERENCES card_statements(id) ON DELETE CASCADE,
                FOREIGN KEY (card_id) REFERENCES credit_cards(id) ON DELETE CASCADE
            );

            /* ---- email sync ---- */
            CREATE TABLE IF NOT EXISTS email_sync (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                provider     TEXT DEFAULT 'gmail',
                token_json   TEXT DEFAULT '',
                last_sync    TEXT DEFAULT '',
                emails_found INTEGER DEFAULT 0,
                status       TEXT DEFAULT 'disconnected'
            );

            /* ---- budget ---- */
            CREATE TABLE IF NOT EXISTS budget_items (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                category  TEXT NOT NULL,
                label     TEXT NOT NULL,
                amount    REAL NOT NULL DEFAULT 0,
                currency  TEXT DEFAULT 'USD',
                type      TEXT DEFAULT 'expense',
                sort_order INTEGER DEFAULT 0,
                active    INTEGER DEFAULT 1
            );

            /* ---- market watchlist ---- */
            CREATE TABLE IF NOT EXISTS watchlist (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT NOT NULL UNIQUE,
                name       TEXT DEFAULT '',
                added_at   TEXT NOT NULL
            );

            /* ---- AI assistant chat history ---- */
            CREATE TABLE IF NOT EXISTS assistant_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            /* ---- insight dismissals (so dismissed cards stay hidden) ---- */
            CREATE TABLE IF NOT EXISTS dismissed_insights (
                insight_key TEXT PRIMARY KEY,
                dismissed_at TEXT NOT NULL
            );

            /* ---- learned merchant -> category overrides (from user corrections) ---- */
            CREATE TABLE IF NOT EXISTS merchant_categories (
                merchant_key TEXT PRIMARY KEY,
                category     TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            /* ---- net worth goals (target amount by a target date) ---- */
            CREATE TABLE IF NOT EXISTS goals (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                target_amount REAL NOT NULL,
                target_date   TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            /* ---- cached LLM-generated "AI Analysis" insights, one row per page scope ---- */
            CREATE TABLE IF NOT EXISTS ai_insights_cache (
                scope        TEXT PRIMARY KEY,
                content      TEXT NOT NULL,
                generated_at TEXT NOT NULL
            );
        """)
        _migrate(conn)


def _migrate(conn):
    """Add columns introduced after initial table creation, for existing DBs."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(credit_cards)").fetchall()}
    for col, ddl in [
        ("email_sender", "ALTER TABLE credit_cards ADD COLUMN email_sender TEXT DEFAULT ''"),
        ("email_subject", "ALTER TABLE credit_cards ADD COLUMN email_subject TEXT DEFAULT ''"),
        ("amount_label", "ALTER TABLE credit_cards ADD COLUMN amount_label TEXT DEFAULT ''"),
    ]:
        if col not in cols:
            conn.execute(ddl)
    _migrate_fx(conn)
    _heal_amount_columns(conn)


_AMOUNT_COLUMNS = {"snapshots": ("value_base", {"id", "holding_id", "snap_date"}), "fx_rates": ("rate_to_base", {"currency", "as_of"})}


def _heal_amount_columns(conn):
    """Amount columns used to carry a currency in their name. In an older database the one column that is not part of the known set is renamed to the neutral name."""
    for table, (new, known) in _AMOUNT_COLUMNS.items():
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and new not in cols:
            odd = [c for c in cols if c not in known]
            if len(odd) == 1:
                conn.execute(f'ALTER TABLE {table} RENAME COLUMN "{odd[0]}" TO {new}')


def _migrate_fx(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(statement_transactions)").fetchall()}
    for col, ddl in [("fx_currency", "TEXT DEFAULT ''"), ("fx_amount", "REAL DEFAULT 0"), ("fx_rate", "REAL DEFAULT 0")]:
        if col not in cols:
            conn.execute(f"ALTER TABLE statement_transactions ADD COLUMN {col} {ddl}")


def seed_if_empty():
    """A new install starts empty: only the display currency and the app name. You pick the currency on first run (setup page) or in Settings."""
    with get_db() as conn:
        if conn.execute("SELECT COUNT(*) c FROM settings WHERE key='base_currency'").fetchone()["c"]:
            return
        today = date.today().isoformat()
        for k, v in [("base_currency", "USD"), ("monthly_income", "0"), ("app_title", "Aurum")]:
            conn.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))
        conn.execute("INSERT OR IGNORE INTO fx_rates VALUES (?,?,?)", ("USD", 1.0, today))
        if not conn.execute("SELECT 1 FROM email_sync").fetchone():
            conn.execute("INSERT INTO email_sync(provider,status) VALUES ('gmail','disconnected')")


if __name__ == "__main__":
    init_db(); seed_if_empty()
    print("DB ready at", os.path.abspath(DB_PATH))
