"""Bank account (non-card) balance sync: reads the closing balance from a statement e-mail and updates the mapped holdings, so their balances chart over time.
The generic reader looks for a labelled closing / available / current balance (see feeds.find_balance). A bank whose statement needs more can add a plugin
(plugins.py: ACCOUNT_BALANCE_PARSER returning {"current", "savings"}, ACCOUNT_BALANCE_DEFAULTS giving the usual sender and subject).
For several accounts with their own labels, Settings > Account feeds (feeds.py) does the same thing per account."""
import re
from datetime import datetime, date
from database import get_db
import email_sync as ES
import vault
import plugins


def _defaults():
    return plugins.get("ACCOUNT_BALANCE_DEFAULTS", {}) or {}


def extract_balances(pdf_text):
    """-> {"current": float|None, "savings": float|None}."""
    fn = plugins.get("ACCOUNT_BALANCE_PARSER")
    if fn:
        r = fn(pdf_text)
        if r and (r.get("current") is not None or r.get("savings") is not None):
            return r
    import feeds
    return {"current": feeds.find_balance(pdf_text or "", ""), "savings": None}


def get_config():
    keys = ["bank_stmt_sender", "bank_stmt_subject", "bank_stmt_pdf_password",
            "bank_stmt_current_holding_id", "bank_stmt_savings_holding_id",
            "bank_stmt_last_sync", "bank_stmt_last_current", "bank_stmt_last_savings"]
    with get_db() as conn:
        rows = {r["key"]: r["value"] for r in
                conn.execute(f"SELECT key,value FROM settings WHERE key IN ({','.join('?'*len(keys))})", keys).fetchall()}
    return {
        "sender": rows.get("bank_stmt_sender") or _defaults().get("sender", ""),
        "subject": rows.get("bank_stmt_subject") or _defaults().get("subject", ""),
        "pdf_password_set": bool(rows.get("bank_stmt_pdf_password")),
        "current_holding_id": int(rows["bank_stmt_current_holding_id"]) if rows.get("bank_stmt_current_holding_id") else None,
        "savings_holding_id": int(rows["bank_stmt_savings_holding_id"]) if rows.get("bank_stmt_savings_holding_id") else None,
        "last_sync": rows.get("bank_stmt_last_sync") or None,
        "last_current": float(rows["bank_stmt_last_current"]) if rows.get("bank_stmt_last_current") else None,
        "last_savings": float(rows["bank_stmt_last_savings"]) if rows.get("bank_stmt_last_savings") else None,
    }


def save_config(b):
    pairs = []
    if "sender" in b: pairs.append(("bank_stmt_sender", b["sender"] or ""))
    if "subject" in b: pairs.append(("bank_stmt_subject", b["subject"] or ""))
    if "pdf_password" in b and b["pdf_password"]: pairs.append(("bank_stmt_pdf_password", vault.seal(b["pdf_password"])))
    if "current_holding_id" in b: pairs.append(("bank_stmt_current_holding_id", str(b["current_holding_id"] or "")))
    if "savings_holding_id" in b: pairs.append(("bank_stmt_savings_holding_id", str(b["savings_holding_id"] or "")))
    with get_db() as conn:
        for k, v in pairs:
            conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))


def _candidate_passwords(configured_pw):
    """Try the configured password first, then fall back to any stored credit-card
    PDF password — banks often reuse the same document password everywhere."""
    pw_list = []
    if configured_pw:
        pw_list.append(configured_pw)
    with get_db() as conn:
        for r in conn.execute("SELECT DISTINCT pdf_password FROM credit_cards WHERE pdf_password!=''").fetchall():
            p_ = vault.unseal(r["pdf_password"])
            if p_ and p_ not in pw_list:
                pw_list.append(p_)
    pw_list.append("")
    return pw_list


def sync_now(take_snapshot_fn=None):
    """Find the latest matching statement email, extract balances, update the
    mapped holdings, and (if given) take a full portfolio snapshot so history
    charts pick up the new values. Returns a result dict."""
    try:
        box = ES.open_mailbox()
    except ES.GmailAuthError as e:
        return {"ok": False, "error": str(e) if ES.imap_configured() else "Gmail access expired. Reconnect Gmail in Settings."}
    if not box:
        return {"ok": False, "error": "Not connected to Gmail"}

    cfg = get_config()
    if not cfg["current_holding_id"] and not cfg["savings_holding_id"]:
        return {"ok": False, "error": "No holdings mapped — set them in Settings first"}

    with get_db() as conn:
        pw_row = conn.execute("SELECT value FROM settings WHERE key='bank_stmt_pdf_password'").fetchone()
    configured_pw = vault.unseal(pw_row["value"]) if pw_row else ""

    if not cfg["sender"]:
        return {"ok": False, "error": "Set the statement e-mail sender in Settings first"}
    query = f'from:{cfg["sender"]}' + (f' subject:"{cfg["subject"]}"' if cfg["subject"] else "")
    try:
        messages = box.search(query, 5)
        if not messages:
            return {"ok": False, "error": "No matching statement email found"}

        msg_id = messages[0]  # newest first
        pdf_text = ""
        for pw in _candidate_passwords(configured_pw):
            pdf_text = box.pdf_text(msg_id, pw)
            if pdf_text:
                break
        if not pdf_text:
            return {"ok": False, "error": "Could not decrypt the statement PDF — check the password in Settings"}

        balances = extract_balances(pdf_text)
        if balances["current"] is None and balances["savings"] is None:
            return {"ok": False, "error": "Found the statement but could not read the balances from it"}

        now = datetime.utcnow().isoformat()
        with get_db() as conn:
            if cfg["current_holding_id"] and balances["current"] is not None:
                conn.execute("UPDATE holdings SET manual_value=?,updated_at=? WHERE id=?",
                             (balances["current"], now, cfg["current_holding_id"]))
                conn.execute("INSERT OR REPLACE INTO settings VALUES ('bank_stmt_last_current',?)",
                             (str(balances["current"]),))
            if cfg["savings_holding_id"] and balances["savings"] is not None:
                conn.execute("UPDATE holdings SET manual_value=?,updated_at=? WHERE id=?",
                             (balances["savings"], now, cfg["savings_holding_id"]))
                conn.execute("INSERT OR REPLACE INTO settings VALUES ('bank_stmt_last_savings',?)",
                             (str(balances["savings"]),))
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('bank_stmt_last_sync',?)", (now,))

        if take_snapshot_fn:
            take_snapshot_fn()

        return {"ok": True, "current": balances["current"], "savings": balances["savings"], "synced_at": now}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
