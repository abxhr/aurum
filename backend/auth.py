"""Authentication for a single-owner, local-first app.

Two ways in, and they never mix:
  on this PC        the password (scrypt), over localhost only
  on your iPhone    a passkey, which means Face ID: the phone's secure chip signs a challenge and refuses to without a live face
Sessions live on the server (only a hash of the cookie value is stored), expire, can be revoked, and a remote session locks itself after two idle minutes
so the next tap needs Face ID again. Every sign-in attempt is logged. Wrong passwords are slowed down with a growing delay."""
import hashlib
import hmac
import json
import re
import secrets
import time

from database import real_db as get_db

COOKIE = "wealth_session"
LOCAL_IDLE, LOCAL_MAX = 7 * 86400, 30 * 86400
REMOTE_MAX, REMOTE_LOCK = 12 * 3600, 120
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
COMMON = {"changeme", "password", "password123", "123456789012", "qwertyuiop12", "letmein12345", "iloveyou1234", "welcome12345", "admin1234567"}


def now():
    return int(time.time())


def ensure():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, created INTEGER, last_seen INTEGER, expires INTEGER, method TEXT, remote INTEGER DEFAULT 0, locked INTEGER DEFAULT 0, ua TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS auth_events (id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER, kind TEXT, ok INTEGER, detail TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS passkeys (cred_id TEXT PRIMARY KEY, public_key BLOB, sign_count INTEGER DEFAULT 0, name TEXT DEFAULT '', created INTEGER, last_used INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS enroll_tokens (hash TEXT PRIMARY KEY, expires INTEGER, used INTEGER DEFAULT 0);
        """)


def _get(k, default=""):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r["value"] if r else default


def _set(k, v):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))


def _h(token):
    return hashlib.sha256(token.encode()).hexdigest()


def event(kind, ok, detail=""):
    ensure()
    with get_db() as conn:
        conn.execute("INSERT INTO auth_events(at,kind,ok,detail) VALUES (?,?,?,?)", (now(), kind, int(bool(ok)), str(detail)[:200]))
        conn.execute("DELETE FROM auth_events WHERE id NOT IN (SELECT id FROM auth_events ORDER BY id DESC LIMIT 300)")


def events(n=20):
    ensure()
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT at,kind,ok,detail FROM auth_events ORDER BY id DESC LIMIT ?", (n,)).fetchall()]


# ───────────────────────── password ─────────────────────────
def _scrypt(pw, salt):
    return hashlib.scrypt(pw.encode(), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **SCRYPT)


def password_set():
    return _get("pw_hash").startswith("scrypt$")


def purge_default():
    """A password hash from an older version is dropped if it is the shipped default, so the app starts in setup mode instead of accepting 'changeme'."""
    salt, h = _get("pw_salt"), _get("pw_hash")
    if h and not h.startswith("scrypt$") and salt:
        legacy = hashlib.pbkdf2_hmac("sha256", b"changeme", salt.encode(), 120_000).hex()
        if hmac.compare_digest(legacy, h):
            with get_db() as conn:
                conn.execute("DELETE FROM settings WHERE key IN ('pw_hash','pw_salt')")
                conn.execute("DELETE FROM settings WHERE key='session_tokens'")
            ensure()
            with get_db() as conn:
                conn.execute("DELETE FROM sessions")
            event("default_password_removed", True)
            return True
    return False


def password_problem(pw):
    if len(pw or "") < 12:
        return "Use at least 12 characters. A few random words is easiest."
    if pw.lower() in COMMON or "changeme" in pw.lower() or len(set(pw)) < 5:
        return "That password is too easy to guess."
    return None


def set_password(pw):
    bad = password_problem(pw)
    if bad:
        raise ValueError(bad)
    salt = secrets.token_bytes(16)
    _set("pw_hash", f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${_scrypt(pw, salt).hex()}")
    with get_db() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('pw_salt','session_tokens')")
    revoke_all()


def _verify(pw):
    parts = _get("pw_hash").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        _scrypt("x", b"0" * 16)                       # same work either way, so timing does not reveal whether a password is set
        return False
    got = hashlib.scrypt((pw or "").encode(), salt=bytes.fromhex(parts[4]), dklen=32, maxmem=128 * 1024 * 1024, n=int(parts[1]), r=int(parts[2]), p=int(parts[3]))
    return hmac.compare_digest(got.hex(), parts[5])


def lockout_seconds():
    """After 3 wrong passwords in the last hour the wait doubles each time (30 s, 60 s ... capped at 1 hour)."""
    ensure()
    with get_db() as conn:
        rows = conn.execute("SELECT at, ok FROM auth_events WHERE kind='password' AND at>? ORDER BY id DESC", (now() - 3600,)).fetchall()
    fails = 0
    for r in rows:
        if r["ok"]:
            break
        fails += 1
    if fails < 3:
        return 0
    wait = min(3600, 30 * 2 ** (fails - 3))
    last = rows[0]["at"] if rows else 0
    return max(0, last + wait - now())


def check_password(pw):
    ok = _verify(pw)
    event("password", ok)
    return ok


# ───────────────────────── sessions ─────────────────────────
def issue(method, remote, ua=""):
    ensure()
    token = secrets.token_urlsafe(32)
    t = now()
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires<?", (t,))
        conn.execute("INSERT INTO sessions(id,created,last_seen,expires,method,remote,locked,ua) VALUES (?,?,?,?,?,?,0,?)",
                     (_h(token), t, t, t + (REMOTE_MAX if remote else LOCAL_MAX), method, int(remote), (ua or "")[:120]))
    return token


def lookup(token):
    """-> None (no valid session) | {"locked": True} | {"method", "remote"}. Touches last_seen on success."""
    if not token:
        return None
    ensure()
    t = now()
    with get_db() as conn:
        r = conn.execute("SELECT * FROM sessions WHERE id=?", (_h(token),)).fetchone()
        if not r or r["expires"] < t:
            return None
        if r["method"] == "demo":
            pass                                    # a demo shows invented data only, so it never locks
        elif r["remote"]:
            if r["locked"] or t - r["last_seen"] > REMOTE_LOCK:
                conn.execute("UPDATE sessions SET locked=1 WHERE id=?", (r["id"],))
                return {"locked": True, "method": r["method"], "remote": True}
        elif t - r["last_seen"] > LOCAL_IDLE:
            conn.execute("DELETE FROM sessions WHERE id=?", (r["id"],))
            return None
        conn.execute("UPDATE sessions SET last_seen=? WHERE id=?", (t, r["id"]))
    return {"method": r["method"], "remote": bool(r["remote"])}


def lock(token):
    with get_db() as conn:
        conn.execute("UPDATE sessions SET locked=1 WHERE id=?", (_h(token),))


def revoke(token):
    with get_db() as conn:
        conn.execute("DELETE FROM sessions WHERE id=?", (_h(token),))


def revoke_all(keep_token=None):
    ensure()
    with get_db() as conn:
        if keep_token:
            conn.execute("DELETE FROM sessions WHERE id!=?", (_h(keep_token),))
        else:
            conn.execute("DELETE FROM sessions")


def sessions(current_token=None):
    ensure()
    me = _h(current_token) if current_token else ""
    with get_db() as conn:
        return [{"id": r["id"][:10], "method": r["method"], "remote": bool(r["remote"]), "created": r["created"], "last_seen": r["last_seen"], "current": r["id"] == me, "device": r["ua"]}
                for r in conn.execute("SELECT * FROM sessions WHERE expires>? ORDER BY last_seen DESC", (now(),)).fetchall()]


# ───────────────────────── one-time links for adding a phone ─────────────────────────
def new_enroll_token():
    ensure()
    tok = secrets.token_urlsafe(24)
    with get_db() as conn:
        conn.execute("DELETE FROM enroll_tokens WHERE expires<? OR used=1", (now(),))
        conn.execute("INSERT INTO enroll_tokens(hash,expires,used) VALUES (?,?,0)", (_h(tok), now() + 600))
    return tok


def enroll_token_valid(tok):
    ensure()
    with get_db() as conn:
        r = conn.execute("SELECT * FROM enroll_tokens WHERE hash=?", (_h(tok or ""),)).fetchone()
    return bool(r and not r["used"] and r["expires"] >= now())


def use_enroll_token(tok):
    with get_db() as conn:
        return conn.execute("UPDATE enroll_tokens SET used=1 WHERE hash=? AND used=0 AND expires>=?", (_h(tok or ""), now())).rowcount == 1
