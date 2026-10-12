"""Secrets at rest. API keys, broker tokens, mail and PDF passwords are sealed before they are written to the database, so a copy of the database file (a backup, a stolen
disk, a synced folder) holds ciphertext, not credentials.
  Windows      DPAPI: only this Windows account on this machine can open them (no key file exists).
  Mac / Linux  Fernet (AES-128-CBC + HMAC) with a random key in data/secrets.key, created owner-read-only. Keep that file out of any backup that also holds the database.
Reading is transparent: unseal() returns plain text for sealed and for not-yet-sealed values, so old data keeps working until migrate() seals it."""
import base64
import ctypes
import os
import sys

try:
    from ctypes import wintypes
except ImportError:                       # only needed for DPAPI
    wintypes = None

PREFIX = "dpapi1:"
FPREFIX = "fernet1:"
KEY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "secrets.key")
_ENTROPY = b"wealth-tracker/secrets/v1"
SECRET_KEYS = {"ibkr_flex_token", "etoro_api_key", "etoro_user_key", "gmail_imap_password", "payslip_pdf_password", "bank_stmt_pdf_password",
               "anthropic_api_key", "openai_api_key", "typesafe_api_key", "vapid_private"}


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD if wintypes else ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(b):
    buf = ctypes.create_string_buffer(b, len(b))
    return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _run(fn_name, data):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, keep1 = _blob(data)
    ent, keep2 = _blob(_ENTROPY)
    out = _Blob()
    ok = getattr(crypt32, fn_name)(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def available():
    return sys.platform == "win32"


def _fernet():
    from cryptography.fernet import Fernet
    path = os.environ.get("PORTFOLIO_SECRET_KEY_FILE", KEY_FILE)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    with open(path, "rb") as f:
        return Fernet(f.read().strip())


def sealed(value):
    return isinstance(value, str) and value.startswith((PREFIX, FPREFIX))


def seal(value):
    """Encrypt for this machine. Empty values and already-sealed values pass through."""
    if not value or not isinstance(value, str) or sealed(value):
        return value
    if available():
        return PREFIX + base64.b64encode(_run("CryptProtectData", value.encode("utf-8"))).decode()
    return FPREFIX + _fernet().encrypt(value.encode("utf-8")).decode()


def unseal(value):
    try:
        if isinstance(value, str) and value.startswith(PREFIX):
            return _run("CryptUnprotectData", base64.b64decode(value[len(PREFIX):])).decode("utf-8")
        if isinstance(value, str) and value.startswith(FPREFIX):
            return _fernet().decrypt(value[len(FPREFIX):].encode()).decode("utf-8")
    except Exception:
        return ""                     # sealed by another account, machine or key: unusable, so behave as if it were not set
    return value


def migrate():
    """Seal everything that is still plain text. Safe to run on every start."""
    from database import get_db
    n = 0
    with get_db() as conn:
        for k in SECRET_KEYS:
            r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
            if r and r["value"] and not sealed(r["value"]):
                conn.execute("UPDATE settings SET value=? WHERE key=?", (seal(r["value"]), k)); n += 1
        for table, col in (("credit_cards", "pdf_password"), ("account_feeds", "pdf_password"), ("email_sync", "token_json")):
            try:
                for r in conn.execute(f"SELECT rowid, {col} v FROM {table} WHERE {col}!='' AND {col} NOT LIKE ? AND {col} NOT LIKE ?", (PREFIX + "%", FPREFIX + "%")).fetchall():
                    conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (seal(r["v"]), r["rowid"])); n += 1
            except Exception:
                pass                       # a table that does not exist yet has nothing to seal
    return n
