"""E-mail sync (Gmail OAuth or IMAP) for bank statements.

Requires credentials.json in the app root from Google Cloud Console.
See GMAIL-SETUP.md for setup instructions.

Uses gmail.readonly scope - read-only, cannot modify your emails.
"""
import os, json, base64, re, imaplib, io
import email as _email
from email.header import decode_header, make_header
from datetime import datetime, date, timedelta
import vault
from database import get_db
from cards import extract_statement_from_email, add_statement, get_all_cards, card_senders, card_subject_keywords
from transactions import extract_transactions, save_transactions
import rewards as RW

CRED_FILE = os.path.join(os.path.dirname(__file__), "..", "credentials.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
REDIRECT_URI = "http://localhost:5000/api/email/callback"

# Try to import Google libraries
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")  # safe for localhost
    GMAIL_LIBS = True
except ImportError:
    GMAIL_LIBS = False


def gmail_available():
    return GMAIL_LIBS and os.path.exists(CRED_FILE)


def get_auth_url():
    """Return the OAuth URL the user should visit."""
    if not gmail_available():
        return None, "Gmail libraries or credentials.json not found. See GMAIL-SETUP.md."
    try:
        flow = Flow.from_client_secrets_file(CRED_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true",
                                             prompt="consent")
        # store state for callback verification
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('oauth_state',?)", (state,))
        return url, None
    except Exception as e:
        return None, str(e)


def exchange_code(code, state=None):
    """Exchange auth code for token, store in DB."""
    if not gmail_available():
        return False, "Gmail not configured"
    import hmac
    with get_db() as conn:                                  # the callback is reachable without a session, so the one-time state made at connect time is what proves it is ours
        row = conn.execute("SELECT value FROM settings WHERE key='oauth_state'").fetchone()
        conn.execute("DELETE FROM settings WHERE key='oauth_state'")
    if not row or not state or not hmac.compare_digest(row["value"], state):
        return False, "The sign-in link was not started from this app."
    try:
        flow = Flow.from_client_secrets_file(CRED_FILE, scopes=SCOPES, redirect_uri=REDIRECT_URI)
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_json = creds.to_json()
        now = datetime.utcnow().isoformat()
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM email_sync WHERE provider='gmail'").fetchone()
            if existing:
                conn.execute("UPDATE email_sync SET token_json=?,status='connected',last_sync='' WHERE provider='gmail'", (vault.seal(token_json),))
            else:
                conn.execute("INSERT INTO email_sync(provider,token_json,status) VALUES ('gmail',?,'connected')", (vault.seal(token_json),))
        return True, None
    except Exception as e:
        return False, str(e)


class GmailAuthError(Exception):
    """The stored Gmail authorization no longer works and the user must reconnect."""


def _mark_expired():
    with get_db() as conn:
        conn.execute("UPDATE email_sync SET status='expired' WHERE provider='gmail'")


def get_service():
    """Authenticated Gmail service, refreshing the token if needed.
    Returns None when never connected; raises GmailAuthError when the saved authorization
    was revoked/expired (Google 'invalid_grant', which happens every 7 days while the OAuth
    app is in 'Testing' mode) so callers can tell the user instead of failing silently."""
    if not gmail_available():
        return None
    with get_db() as conn:
        row = conn.execute("SELECT token_json FROM email_sync WHERE provider='gmail'").fetchone()
    if not row or not row["token_json"]:
        return None
    try:
        creds = Credentials.from_authorized_user_info(json.loads(vault.unseal(row["token_json"])), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with get_db() as conn:
                conn.execute("UPDATE email_sync SET token_json=?,status='connected' WHERE provider='gmail'", (vault.seal(creds.to_json()),))
        elif creds.expired:
            raise GmailAuthError("Gmail authorization has no refresh token")
        return build("gmail", "v1", credentials=creds)
    except GmailAuthError:
        _mark_expired()
        raise
    except Exception as e:
        if "invalid_grant" in str(e) or e.__class__.__name__ == "RefreshError":
            _mark_expired()
            raise GmailAuthError(str(e))
        raise



# ───────── mailbox adapters: the sync logic is the same whether mail comes from the Gmail API
# (OAuth) or plain IMAP (Gmail address + App Password; never expires, no Google Cloud project) ─────────
def _setting(k, default=""):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return vault.unseal(r["value"]) if r else default


def imap_configured():
    return bool(_setting("gmail_imap_user") and _setting("gmail_imap_password"))


def _pdf_bytes_to_text(pdf_bytes, password=""):
    password = vault.unseal(password)
    try:
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
                return ""
        out = ""
        for page in reader.pages:
            try:
                out += page.extract_text() or ""
            except Exception:
                continue
        return out
    except Exception:
        return ""


class GmailApiBox:
    def __init__(self, service):
        self.s = service

    def search(self, query, limit=100):
        r = self.s.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        return [m["id"] for m in r.get("messages", [])]  # newest first

    def headers(self, mid):
        m = self.s.users().messages().get(userId="me", id=mid, format="metadata",
                                          metadataHeaders=["From", "Subject", "Date"]).execute()
        return {h["name"]: h["value"] for h in m.get("payload", {}).get("headers", [])}

    def body(self, mid):
        return _get_email_body(self.s, mid)

    def pdf_text(self, mid, password=""):
        return _extract_pdf_attachment(self.s, mid, password)

    def message(self, mid):
        """The whole email as a parsed email.message.Message (raw RFC 822)."""
        r = self.s.users().messages().get(userId="me", id=mid, format="raw").execute()
        return _email.message_from_bytes(base64.urlsafe_b64decode(r["raw"] + "=="))


class ImapBox:
    """Gmail over IMAP. Gmail's X-GM-RAW lets us reuse the exact same search query syntax."""
    def __init__(self, conn):
        self.c = conn
        self._msgs = {}
        for box in ('"[Gmail]/All Mail"', "INBOX"):
            typ, _ = self.c.select(box, readonly=True)
            if typ == "OK":
                break

    def search(self, query, limit=100):
        q = '"' + query.replace("\\", "\\\\").replace('"', '\\"') + '"'
        typ, data = self.c.uid("SEARCH", None, "X-GM-RAW", q)
        if typ != "OK" or not data or not data[0]:
            return []
        return [u.decode() for u in reversed(data[0].split())][:limit]  # newest first

    def headers(self, uid):
        typ, d = self.c.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
        if typ != "OK" or not d or not d[0]:
            return {}
        msg = _email.message_from_bytes(d[0][1])
        def dec(v):
            try:
                return str(make_header(decode_header(v or "")))
            except Exception:
                return v or ""
        return {"From": dec(msg.get("From")), "Subject": dec(msg.get("Subject")), "Date": msg.get("Date", "")}

    def _msg(self, uid):
        if uid not in self._msgs:
            typ, d = self.c.uid("FETCH", uid, "(BODY.PEEK[])")
            self._msgs[uid] = _email.message_from_bytes(d[0][1]) if typ == "OK" and d and d[0] else None
        return self._msgs[uid]

    def body(self, uid):
        msg = self._msg(uid)
        if not msg:
            return ""
        plain = html = ""
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                raw = part.get_payload(decode=True) or b""
                text = raw.decode(part.get_content_charset() or "utf-8", errors="ignore")
                if ct == "text/plain" and not plain:
                    plain = text
                elif ct == "text/html" and not html:
                    html = _strip_html(text)
        return plain or html

    def message(self, uid):
        return self._msg(uid)

    def pdf_text(self, uid, password=""):
        msg = self._msg(uid)
        if not msg:
            return ""
        texts = []
        n = 0
        for part in msg.walk():
            name = (part.get_filename() or "").lower()
            if part.get_content_type() == "application/pdf" or name.endswith(".pdf"):
                data = part.get_payload(decode=True)
                if data:
                    t = _pdf_bytes_to_text(data, password)
                    if t:
                        texts.append(t)
                n += 1
                if n >= 3:
                    break
        return "\n".join(texts)


def _imap_open(user=None, password=None):
    user = user or _setting("gmail_imap_user")
    password = (password or _setting("gmail_imap_password")).replace(" ", "")
    try:
        c = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=40)
        c.login(user, password)
        return c
    except imaplib.IMAP4.error as e:
        raise GmailAuthError("Gmail rejected the address or App Password: " + str(e)[:120])


def save_imap(user, password):
    """Validate the App Password by logging in, then store it."""
    c = _imap_open(user.strip(), password)
    try:
        c.logout()
    except Exception:
        pass
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('gmail_imap_user',?)", (user.strip(),))
        conn.execute("INSERT OR REPLACE INTO settings VALUES ('gmail_imap_password',?)", (vault.seal(password.replace(" ", "")),))
        if not conn.execute("SELECT 1 FROM email_sync WHERE provider='gmail'").fetchone():
            conn.execute("INSERT INTO email_sync(provider,token_json,status) VALUES ('gmail','','connected')")


def remove_imap():
    with get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('gmail_imap_user','gmail_imap_password')")


def open_mailbox():
    """IMAP when an App Password is saved (it never expires), otherwise the OAuth API. None if neither."""
    if imap_configured():
        return ImapBox(_imap_open())
    svc = get_service()
    return GmailApiBox(svc) if svc else None


def disconnect():
    with get_db() as conn:
        conn.execute("UPDATE email_sync SET token_json='',status='disconnected' WHERE provider='gmail'")


def _decode_body(part):
    """Decode a Gmail message part body to text."""
    data = part.get("body", {}).get("data", "")
    if data:
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
        except Exception:
            pass
    # check parts recursively
    for sub in part.get("parts", []):
        result = _decode_body(sub)
        if result:
            return result
    return ""


def _strip_html(html):
    """Basic HTML stripping."""
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def _get_email_body(service, msg_id):
    """Fetch and decode full email body (plain text preferred)."""
    try:
        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = msg.get("payload", {})
        mime = payload.get("mimeType", "")
        if "text/plain" in mime:
            return _decode_body(payload)
        if "text/html" in mime:
            return _strip_html(_decode_body(payload))
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                text = _decode_body(part)
                if text: return text
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html":
                text = _strip_html(_decode_body(part))
                if text: return text
        return _decode_body(payload)
    except Exception:
        return ""


def _extract_pdf_attachment(service, msg_id, pdf_password=""):
    """Find and extract text from a PDF attachment in the email.

    Bank statements are often password-protected PDFs. We try the stored
    pdf_password to unlock them, then extract text for amount parsing.
    Returns extracted text or empty string.
    """
    try:
        import pypdf
    except ImportError:
        return ""  # pypdf not installed — install with: pip install pypdf

    try:
        msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        payload = msg.get("payload", {})

        def find_pdf_parts(part):
            """Recursively find PDF parts."""
            results = []
            mime = part.get("mimeType", "")
            filename = part.get("filename", "")
            if "pdf" in mime.lower() or filename.lower().endswith(".pdf"):
                results.append(part)
            for sub in part.get("parts", []):
                results.extend(find_pdf_parts(sub))
            return results

        pdf_parts = find_pdf_parts(payload)
        if not pdf_parts:
            return ""

        all_text = []
        for part in pdf_parts[:3]:  # max 3 attachments
            attach_id = part.get("body", {}).get("attachmentId")
            if not attach_id:
                # inline data
                data = part.get("body", {}).get("data", "")
                if not data: continue
                pdf_bytes = base64.urlsafe_b64decode(data + "==")
            else:
                att = service.users().messages().attachments().get(
                    userId="me", messageId=msg_id, id=attach_id).execute()
                pdf_bytes = base64.urlsafe_b64decode(att.get("data", "") + "==")

            if not pdf_bytes:
                continue

            try:
                import io
                reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))

                # try to decrypt if encrypted
                if reader.is_encrypted:
                    # try stored password first, then empty string
                    passwords_to_try = []
                    if pdf_password:
                        passwords_to_try.append(pdf_password)
                    passwords_to_try.append("")
                    decrypted = False
                    for pw in passwords_to_try:
                        try:
                            if reader.decrypt(pw):
                                decrypted = True
                                break
                        except Exception:
                            continue
                    if not decrypted:
                        continue  # can't decrypt — skip this PDF

                # extract text from all pages
                text = ""
                for page in reader.pages:
                    try:
                        text += page.extract_text() or ""
                    except Exception:
                        continue
                if text:
                    all_text.append(text)
            except Exception:
                continue

        return "\n".join(all_text)
    except Exception:
        return ""


def sync_emails(days_back=90, backfill=False):
    """Scan Gmail for bank statement emails. Returns summary dict."""
    try:
        box = open_mailbox()
    except GmailAuthError as e:
        return {"ok": False, "expired": True, "found": 0, "imported": 0,
                "error": str(e) if imap_configured() else
                "Gmail access expired. Reconnect Gmail in Settings, or switch to an App Password (see GMAIL-SETUP.md)."}
    except Exception as e:
        return {"ok": False, "error": f"Could not reach Gmail: {e}", "found": 0, "imported": 0}
    if not box:
        return {"ok": False, "error": "Not connected to Gmail", "found": 0, "imported": 0}

    cards = get_all_cards()
    if not cards:
        return {"ok": False, "error": "No cards configured", "found": 0, "imported": 0}

    # build one big sender query covering every card's configured senders (custom or bank default)
    all_senders = set()
    for c in cards:
        all_senders.update(card_senders(c))
    if not all_senders:
        return {"ok": False, "error": "No cards have an email sender configured", "found": 0, "imported": 0}
    sender_query = " OR ".join(f"from:{s}" for s in all_senders)
    after_date = (date.today() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    query = f"({sender_query}) after:{after_date}"

    found = 0; imported = 0; errors = []

    # cards with zero statements so far are on their "first sync" — for those,
    # only the single most recent statement found should be imported, since
    # older ones are typically already paid off and shouldn't be re-surfaced.
    # Cards that already have statements get a high-water mark: sync should
    # only ever move forward in time for them, never backfill older months —
    # even ones a user manually deleted (e.g. because they were already paid
    # before this tool existed) — since dedup-by-subject can't tell "already
    # imported and removed on purpose" apart from "never imported".
    with get_db() as conn:
        newest_statement_date = {}
        first_sync_cards = set()
        for c in cards:
            row = conn.execute(
                "SELECT MAX(statement_date) d FROM card_statements WHERE card_id=?", (c["id"],)).fetchone()
            if row and row["d"]:
                newest_statement_date[c["id"]] = row["d"]
            else:
                first_sync_cards.add(c["id"])
    candidates = {}  # card_id -> list of pending statement dicts (for first-sync cards)

    try:
        messages = box.search(query, 400 if backfill else 100)
        found = len(messages)

        for msg in messages[:400 if backfill else 100]:
            try:
                # get headers only first
                headers = box.headers(msg)
                sender = headers.get("From","").lower()
                subject = headers.get("Subject","")

                # identify which card this email belongs to, by its configured sender list
                card = None
                for c in cards:
                    if any(s in sender for s in card_senders(c)):
                        card = c; break
                if not card:
                    continue

                # check subject relevance against this card's configured keywords
                subj_lower = subject.lower()
                if not any(kw in subj_lower for kw in card_subject_keywords(card)):
                    continue

                # get full body
                body = box.body(msg)

                # try email body first
                data = extract_statement_from_email(card, subject, body) if body else None

                # PDF attachment: needed as a fallback for totals, and as the source of
                # line-item transactions (bank PDFs carry the transaction list; the body
                # email is typically just a boilerplate notification)
                pdf_text = box.pdf_text(msg, card.get("pdf_password") or "")
                if not data and pdf_text:
                    data = extract_statement_from_email(card, subject, pdf_text)

                if not data:
                    continue

                card_id = card["id"]

                # determine statement date from email date header
                stmt_date = date.today().isoformat()
                email_date_str = headers.get("Date","")
                try:
                    from email.utils import parsedate_to_datetime
                    ed = parsedate_to_datetime(email_date_str)
                    stmt_date = ed.date().isoformat()
                except Exception:
                    pass

                # never backfill anything older than the newest statement already on
                # file for this card — protects manually-deleted (already paid) old
                # statements from being silently re-imported on the next sync
                watermark = newest_statement_date.get(card_id)
                older = bool(watermark and stmt_date <= watermark)
                if older and not backfill:
                    continue

                # already imported? Same subject AND same date: some banks reuse one subject every month,
                # so subject alone would wrongly treat each new statement as a repeat of the first
                with get_db() as conn:
                    dup = conn.execute(
                        "SELECT id FROM card_statements WHERE card_id=? AND email_subject=? AND statement_date=?",
                        (card_id, subject, stmt_date)).fetchone()
                    if dup:
                        continue

                # set due date: fall back to stmt_date + 25 days if not parsed
                due_date = data.get("due_date") or (
                    date.fromisoformat(stmt_date) + timedelta(days=25)).isoformat()

                stmt = {
                    "statement_date": stmt_date,
                    "due_date": due_date,
                    "total_due": data.get("total_due", 0),
                    "minimum_due": data.get("minimum_due", 0),
                    "source": "email",
                    "email_subject": subject,
                    "email_snippet": body[:300],
                }
                if older:   # history import: statements before the newest one are assumed already paid
                    stmt["status"] = "paid"
                    stmt["notes"] = "Imported from email history; assumed already paid."

                if card_id in first_sync_cards:
                    # defer — only the most recent one gets imported, once all messages are scanned
                    candidates.setdefault(card_id, []).append((stmt, pdf_text))
                    continue

                stmt_id = add_statement(card_id, stmt)
                if pdf_text:
                    txns = extract_transactions(card.get("bank", ""), pdf_text, stmt_date)
                    if txns:
                        save_transactions(stmt_id, card_id, txns)
                    RW.save_for_statement(stmt_id, card_id, card.get("bank", ""), pdf_text)
                imported += 1
            except Exception as e:
                errors.append(str(e)[:100])

        # first-sync cards: import only the single latest statement found
        for card_id, stmts in candidates.items():
            latest_stmt, latest_pdf_text = max(stmts, key=lambda s: s[0]["statement_date"])
            bank = next((c.get("bank", "") for c in cards if c["id"] == card_id), "")
            stmt_id = add_statement(card_id, latest_stmt)
            if latest_pdf_text:
                txns = extract_transactions(bank, latest_pdf_text, latest_stmt["statement_date"])
                if txns:
                    save_transactions(stmt_id, card_id, txns)
                RW.save_for_statement(stmt_id, card_id, bank, latest_pdf_text)
            imported += 1

    except Exception as e:
        return {"ok": False, "error": str(e), "found": 0, "imported": 0}

    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute("UPDATE email_sync SET last_sync=?,emails_found=? WHERE provider='gmail'", (now, found))
    if imported:
        import categorizer as CZ
        CZ.recategorize()
        CZ.auto_classify()

    return {"ok": True, "found": found, "imported": imported, "errors": errors[:5]}



def reparse_statements(days_back=420):
    """Re-reads the PDFs of statements already on file and rebuilds their transaction lists with the current parsers.
    Matches each email to its statement by card, subject and date (the same key the importer uses), and only replaces a
    statement's transactions when the new read finds at least as many lines as before."""
    import time
    from email.utils import parsedate_to_datetime
    box = open_mailbox()
    if not box:
        return {"ok": False, "error": "Not connected to Gmail"}
    since = (date.today() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    report = {"ok": True, "statements": 0, "updated": 0, "foreign_added": 0, "lines_added": 0, "skipped": 0, "errors": []}
    for card in get_all_cards():
        q = "(" + " OR ".join(f"from:{s}" for s in card_senders(card)) + ") after:" + since
        ids = []
        for attempt in range(3):
            try:
                ids = box.search(q, 200)
                break
            except Exception as e:
                if attempt == 2:
                    report["errors"].append(f"{card['name']}: {str(e)[:80]}")
                    box = open_mailbox()
                else:
                    time.sleep(2); box = open_mailbox()
        for msg in ids:
            try:
                h = box.headers(msg)
                subject = h.get("Subject", "")
                if not any(kw in subject.lower() for kw in card_subject_keywords(card)):
                    continue
                stmt_date = parsedate_to_datetime(h.get("Date", "")).date().isoformat()
                with get_db() as conn:
                    st = conn.execute("SELECT id FROM card_statements WHERE card_id=? AND email_subject=? AND statement_date=?", (card["id"], subject, stmt_date)).fetchone()
                    if not st:
                        continue
                    before = conn.execute("SELECT COUNT(*) n FROM statement_transactions WHERE statement_id=?", (st["id"],)).fetchone()["n"]
                report["statements"] += 1
                pdf_text = box.pdf_text(msg, card.get("pdf_password") or "")
                if pdf_text and RW.save_for_statement(st["id"], card["id"], card.get("bank", ""), pdf_text):
                    report["rewards_read"] = report.get("rewards_read", 0) + 1
                txns = extract_transactions(card.get("bank", ""), pdf_text, stmt_date) if pdf_text else []
                if not txns or len(txns) < before:
                    report["skipped"] += 1
                    continue
                save_transactions(st["id"], card["id"], txns)
                report["updated"] += 1
                report["lines_added"] += len(txns) - before
                report["foreign_added"] += sum(1 for t in txns if t.get("fx_currency"))
            except Exception as e:
                report["errors"].append(f"{card['name']}: {str(e)[:100]}")
    import categorizer as CZ
    CZ.recategorize()
    return report

def get_status():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM email_sync WHERE provider='gmail'").fetchone()
    if imap_configured():
        return {"status": "connected", "method": "imap", "account": _setting("gmail_imap_user"),
                "last_sync": row["last_sync"] if row else None, "emails_found": row["emails_found"] if row else 0,
                "libs_available": True, "creds_found": True}
    if not row:
        return {"status": "disconnected", "last_sync": None, "emails_found": 0}
    return {"status": row["status"], "last_sync": row["last_sync"], "emails_found": row["emails_found"],
            "libs_available": GMAIL_LIBS, "creds_found": os.path.exists(CRED_FILE)}
