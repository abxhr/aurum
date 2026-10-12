"""Run: python backend/test_security.py
Exercises the sign-in and protection layer on a scratch database: no default password, setup mode, lockout, forged and cross-site requests, DNS-rebinding hosts, remote
(Tailscale) rules, the Face ID flow end to end with a software authenticator, idle locking, sealed secrets, and a backup that carries no sign-in material."""
import base64
import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import time

os.environ["PORTFOLIO_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["PORT"] = "5000"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import database
database.init_db()
database.seed_if_empty()
import app as A
import auth
import vault
os.environ["PORTFOLIO_SECRET_KEY_FILE"] = os.path.join(tempfile.mkdtemp(), "secrets.key")

PH = "pc.tail123.ts.net"
H = {"X-Wealth": "1"}
ok_n = 0


def check(cond, msg):
    global ok_n
    assert cond, "FAILED: " + msg
    ok_n += 1


def client(host="localhost:5000", extra=None):
    c = A.app.test_client()
    c._h = host
    c._x = extra or {}
    return c


def call(c, method, path, body=None, headers=None, host=None):
    h = {**H, **c._x, **(headers or {})}
    return getattr(c, method)(path, json=body, headers=h, base_url="http://" + (host or c._h), environ_overrides={"wsgi.url_scheme": "http"})


# ───── no default password ─────
auth.ensure()
salt = "deadbeef"
with database.get_db() as conn:     # an old install with the shipped default
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('pw_salt',?)", (salt,))
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('pw_hash',?)", (hashlib.pbkdf2_hmac("sha256", b"changeme", salt.encode(), 120_000).hex(),))
check(auth.purge_default(), "default password is removed")
check(not auth.password_set(), "setup mode afterwards")

c = client()
check(call(c, "get", "/api/summary").status_code == 401, "api needs a session")
r = call(c, "get", "/")
check(r.status_code == 302 and "/login" in r.headers["Location"], "page redirects to login")
check(call(c, "get", "/static/style.css").status_code == 200, "static is public")
check(call(c, "get", "/api/summary", host="evil.example:5000").status_code == 400, "foreign Host refused (DNS rebinding)")
check(call(c, "post", "/api/login", {"password": "changeme"}).status_code == 409, "login impossible before setup")
check(call(c, "post", "/api/setup", {"password": "short"}).status_code == 400, "weak password refused")
check(call(c, "post", "/api/setup", {"password": "changeme-changeme"}).status_code == 400, "default-looking password refused")
r = call(c, "post", "/api/setup", {"password": "correct horse battery staple"})
check(r.status_code == 200, "strong password accepted")
ck = r.headers["Set-Cookie"]
check("HttpOnly" in ck and "SameSite=Strict" in ck, "session cookie is HttpOnly and SameSite=Strict")
check(call(c, "post", "/api/setup", {"password": "another long password here"}).status_code == 403, "setup is one-time")
check(call(c, "get", "/api/summary").status_code == 200, "signed in")

# ───── headers ─────
r = call(c, "get", "/api/summary")
for hname in ("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Cross-Origin-Opener-Policy"):
    check(hname in r.headers, hname + " present")
check("script-src 'self'" in r.headers["Content-Security-Policy"] and "frame-ancestors 'none'" in r.headers["Content-Security-Policy"], "CSP forbids foreign scripts and framing")
check(r.headers.get("Cache-Control") == "no-store", "API responses are not cached")

# ───── forged and cross-site requests ─────
check(A.app.test_client().post("/api/budget", json={}, base_url="http://localhost:5000").status_code == 403, "POST without our header is refused")
check(call(c, "post", "/api/snapshot", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403, "cross-site POST refused")
check(call(c, "post", "/api/snapshot", headers={"Origin": "https://evil.example"}).status_code == 403, "foreign Origin refused")
check(call(c, "post", "/api/snapshot").status_code == 200, "same-origin POST allowed")

# ───── settings cannot be used to read or plant sign-in material ─────
s = call(c, "get", "/api/settings").get_json()
check("pw_hash" not in s and "session_tokens" not in s and "openai_api_key" not in s, "settings API hides secrets")
check(call(c, "put", "/api/settings", {"pw_hash": "x"}).status_code == 400, "settings API refuses to write sign-in keys")
check(call(c, "put", "/api/settings", {"theme": "dark"}).status_code == 200, "harmless settings still write")

# ───── password lockout ─────
c2 = client()
for i in range(3):
    check(call(c2, "post", "/api/login", {"password": "wrong-%d" % i}).status_code == 401, "wrong password refused")
r = call(c2, "post", "/api/login", {"password": "correct horse battery staple"})
check(r.status_code == 429 and "Retry-After" in r.headers, "growing delay after repeated failures, even for the right password")
with database.get_db() as conn:
    conn.execute("DELETE FROM auth_events WHERE kind='password'")
check(call(c2, "post", "/api/login", {"password": "correct horse battery staple"}).status_code == 200, "right password works once the delay has passed")

# ───── remote (Tailscale) rules ─────
with database.get_db() as conn:
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('public_host',?)", (PH,))
rc = client(PH)
check(call(rc, "get", "/login").status_code == 403, "remote request with no Tailscale identity is refused")
rc = client(PH, {"Tailscale-User-Login": "owner@example.com"})
check(call(rc, "get", "/login").status_code == 200, "remote login page loads for a Tailscale identity (first use)")
check(call(rc, "post", "/api/login", {"password": "correct horse battery staple"}).status_code == 403, "password sign-in is refused from a phone")
check(call(rc, "get", "/api/summary").status_code in (401, 403), "remote API refused without a session")
check(call(rc, "post", "/api/passkey/login/options").status_code in (400, 403), "no Face ID device yet, nothing to sign in with")
check(call(c, "post", "/api/security/enroll-link").status_code == 200, "enrol link can be made on the PC")
tok = auth.new_enroll_token()

# ───── software authenticator (what the iPhone's secure chip does) ─────
ORIGIN = "https://" + PH
key = ec.generate_private_key(ec.SECP256R1())
cred_id = os.urandom(32)
b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
rp_hash = hashlib.sha256(PH.encode()).digest()
pub = key.public_key().public_numbers()
cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: pub.x.to_bytes(32, "big"), -3: pub.y.to_bytes(32, "big")})


def client_data(kind, challenge_b64, origin=ORIGIN):
    return json.dumps({"type": kind, "challenge": challenge_b64, "origin": origin, "crossOrigin": False}).encode()


# registration
o = call(rc, "post", "/api/passkey/register/options", {"token": "not-a-real-token"})
check(o.status_code == 400, "enrol needs a valid one-time token")
o = call(rc, "post", "/api/passkey/register/options", {"token": tok}).get_json()
check(o["options"]["authenticatorSelection"]["userVerification"] == "required", "Face ID (user verification) is required, not optional")
check(o["options"]["authenticatorSelection"]["authenticatorAttachment"] == "platform", "only the phone's own authenticator is accepted")
ch = o["options"]["challenge"]
auth_data = rp_hash + bytes([0x45]) + struct.pack(">I", 0) + b"\x00" * 16 + struct.pack(">H", len(cred_id)) + cred_id + cose      # UP + UV + AT
att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
cred = {"id": b64u(cred_id), "rawId": b64u(cred_id), "type": "public-key", "response": {"clientDataJSON": b64u(client_data("webauthn.create", ch)), "attestationObject": b64u(att)}}
bad = dict(cred, response=dict(cred["response"], clientDataJSON=b64u(client_data("webauthn.create", ch, "https://evil.example"))))
check(call(rc, "post", "/api/passkey/register/verify", {"cid": o["cid"], "credential": bad, "name": "x"}).status_code == 400, "registration for another origin is refused")
o = call(rc, "post", "/api/passkey/register/options", {"token": tok}).get_json()      # the failed attempt used up the challenge, not the token
ch = o["options"]["challenge"]
cred["response"]["clientDataJSON"] = b64u(client_data("webauthn.create", ch))
r = call(rc, "post", "/api/passkey/register/verify", {"cid": o["cid"], "credential": cred, "name": "Test phone"})
check(r.status_code == 200 and "Secure" in r.headers["Set-Cookie"], "Face ID device enrolled, remote cookie is Secure")
check(auth._get("owner_login") == "owner@example.com", "the first enrolled device fixes the owner")
check(call(rc, "post", "/api/passkey/register/options", {"token": tok}).status_code == 400, "the one-time link cannot be reused")
check(call(client(PH, {"Tailscale-User-Login": "intruder@example.com"}), "get", "/login").status_code == 403, "another Tailscale user is refused")

# login with the passkey
sign_count = [1]


def assertion(challenge_b64, uv=True, origin=ORIGIN):
    flags = 0x05 if uv else 0x01
    ad = rp_hash + bytes([flags]) + struct.pack(">I", sign_count[0])
    sign_count[0] += 1
    cd = client_data("webauthn.get", challenge_b64, origin)
    sig = key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
    return {"id": b64u(cred_id), "rawId": b64u(cred_id), "type": "public-key", "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(ad), "signature": b64u(sig), "userHandle": None}}


rc2 = client(PH, {"Tailscale-User-Login": "owner@example.com"})
o = call(rc2, "post", "/api/passkey/login/options").get_json()
check(o["options"]["userVerification"] == "required", "login demands Face ID")
check(call(rc2, "post", "/api/passkey/login/verify", {"cid": o["cid"], "credential": assertion(o["options"]["challenge"], uv=False)}).status_code == 401, "a signature without Face ID (no user verification) is refused")
o = call(rc2, "post", "/api/passkey/login/options").get_json()
check(call(rc2, "post", "/api/passkey/login/verify", {"cid": o["cid"], "credential": assertion(o["options"]["challenge"], origin="https://evil.example")}).status_code == 401, "a signature for another site is refused")
o = call(rc2, "post", "/api/passkey/login/options").get_json()
good = assertion(o["options"]["challenge"])
r = call(rc2, "post", "/api/passkey/login/verify", {"cid": o["cid"], "credential": good})
check(r.status_code == 200, "Face ID sign-in works")
check(call(rc2, "post", "/api/passkey/login/verify", {"cid": o["cid"], "credential": good}).status_code == 401, "a challenge cannot be replayed")
check(call(rc2, "get", "/api/summary").status_code == 200, "phone session works")

# idle lock: two quiet minutes and the phone needs Face ID again
with database.get_db() as conn:
    conn.execute("UPDATE sessions SET last_seen=? WHERE remote=1", (auth.now() - 200,))
r = call(rc2, "get", "/api/summary")
check(r.status_code == 401 and r.get_json()["error"] == "locked", "remote session locks itself after idle")
o = call(rc2, "post", "/api/passkey/login/options").get_json()
check(call(rc2, "post", "/api/passkey/login/verify", {"cid": o["cid"], "credential": assertion(o["options"]["challenge"])}).status_code == 200, "Face ID unlocks")
check(call(rc2, "get", "/api/summary").status_code == 200, "unlocked")
check(call(rc2, "post", "/api/security/enroll-link").status_code == 403, "security changes are not possible from the phone")
check(call(rc2, "get", "/api/backup").status_code == 403, "backup download is not possible from the phone")

# ───── secrets at rest and backups ─────
sealed = vault.seal("sk-test-secret")
check(sealed.startswith(("dpapi1:", "fernet1:")) and "sk-test-secret" not in sealed and vault.unseal(sealed) == "sk-test-secret", "secrets are sealed with DPAPI")
with database.get_db() as conn:
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('openai_api_key','sk-plain')")
_f = vault.FPREFIX + vault._fernet().encrypt(b"x").decode()
check(vault.unseal(_f) == "x" and vault.sealed(_f) and vault.unseal("fernet1:garbage") == "", "Fernet fallback round-trips and fails closed")
check(vault.migrate() >= 1, "migration seals existing plain secrets")
with database.get_db() as conn:
    check(conn.execute("SELECT value FROM settings WHERE key='openai_api_key'").fetchone()["value"].startswith(("dpapi1:", "fernet1:")), "stored value is ciphertext")
r = call(c, "get", "/api/backup")
check(r.status_code == 200, "backup downloads on the PC")
p = os.path.join(tempfile.mkdtemp(), "b.db")
open(p, "wb").write(r.data)
db = sqlite3.connect(p)
names = {x[0] for x in db.execute("select name from sqlite_master where type='table'")}
check(not ({"sessions", "passkeys", "auth_events", "enroll_tokens"} & names), "backup has no sessions or Face ID keys")
check(db.execute("select count(*) from settings where key in ('pw_hash','session_tokens','owner_login')").fetchone()[0] == 0, "backup has no password hash or tokens")
check(call(c, "post", "/api/restore").status_code in (404, 405), "there is no restore endpoint to abuse")

# ───── demo mode: invented data only, never the real file ─────
dc = client()
check(call(dc, "post", "/api/demo/start").status_code == 200, "demo opens without a password")
demo_sum = call(dc, "get", "/api/summary")
check(demo_sum.status_code == 200 and json.dumps(demo_sum.json) != json.dumps(call(c, "get", "/api/summary").json), "a demo session sees different (invented) numbers")
check(call(dc, "get", "/api/auth/state").json["demo"] is True, "state reports demo")
for path in ("/api/security/status", "/api/backup", "/api/notify/key"):
    check(call(dc, "get", path).status_code == 403, f"demo cannot reach {path}")
for path in ("/api/email/sync", "/api/ibkr/sync", "/api/etoro/sync", "/api/import/statement"):
    check(call(dc, "post", path, {}).status_code == 403, f"demo cannot run {path}")
check(call(c, "get", "/api/summary").status_code == 200 and call(c, "get", "/api/auth/state").json["demo"] is False, "the real session is untouched by a demo and still sees real data")
call(dc, "post", "/api/logout")
check(call(dc, "get", "/api/summary").status_code == 401, "leaving the demo returns to the sign-in screen")

# ───── sign-out ─────
call(c, "post", "/api/logout")
check(call(c, "get", "/api/summary").status_code == 401, "logged out")
check(call(c2, "get", "/api/summary").status_code == 200, "other sessions unaffected by one logout")
print(f"security checks passed: {ok_n}")
