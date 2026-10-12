"""The sign-in surface: the gate every request passes, password and passkey sign-in, locking, and the Security settings (devices, sessions, change password).
Registered on the app by app.py. Kept apart from the finance code so it can be read, and tested, on its own."""
import io
import os

from flask import Blueprint, jsonify, make_response, redirect, request, send_from_directory

import auth
import database
import passkeys
import security as SEC

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
bp = Blueprint("auth", __name__)

PUBLIC_PAGES = {"/login", "/setup", "/enroll"}
# in a demo these never run: anything that reaches a real account, a mailbox, a broker, a phone, or the sign-in material
DEMO_BLOCKED = ("/api/security", "/api/notify", "/api/backup", "/api/email", "/api/etoro", "/api/ibkr", "/api/feeds", "/api/payslips/scan", "/api/payslips/config",
                "/api/bank-statement", "/api/bank/sync", "/api/import", "/api/cards/reparse", "/api/agent/settings", "/api/agent/test")
PUBLIC_API = {"/api/demo/start", "/api/auth/state", "/api/login", "/api/setup", "/api/passkey/login/options", "/api/passkey/login/verify", "/api/passkey/register/options",
              "/api/passkey/register/verify", "/api/email/callback"}


def _err(msg, code):
    return jsonify({"ok": False, "error": msg}), code


def _cookie(resp, token, remote):
    resp.set_cookie(auth.COOKIE, token, httponly=True, secure=remote, samesite="Strict", path="/", max_age=auth.REMOTE_MAX if remote else auth.LOCAL_MAX)
    return resp


def _token():
    return request.cookies.get(auth.COOKIE, "")


def gate():
    """Runs before every request. Order matters: address, then identity of the other device, then forgery, then the session."""
    p = request.path
    database.set_demo(False)                      # until a demo session is proven below, every request uses the real database
    if not SEC.host_ok():
        return _err("not found", 400)
    if SEC.is_remote() and not SEC.owner_ok(allow_first_use=(p in PUBLIC_PAGES or p == "/api/auth/state" or p.startswith("/api/passkey/register/") or p.startswith("/static/"))):
        auth.event("remote_identity_refused", False, SEC.tailscale_login() or "no identity")
        return _err("forbidden", 403)
    if not SEC.csrf_ok():
        auth.event("csrf_refused", False, p)
        return _err("forbidden", 403)
    if p.startswith("/static/") or p in PUBLIC_PAGES or p in PUBLIC_API:
        return None
    s = auth.lookup(_token())
    ok = bool(s) and not s.get("locked") and (not SEC.is_remote() or s.get("method") in ("passkey", "demo")) and (s["remote"] == SEC.is_remote())
    if ok:
        if s.get("method") == "demo":
            database.set_demo(True)
            if p.startswith(DEMO_BLOCKED) and (p.startswith(("/api/security", "/api/notify", "/api/backup")) or request.method != "GET"):
                return _err("Not available in the demo.", 403)
        return None
    if p.startswith("/api/"):
        return jsonify({"error": "locked" if (s and s.get("locked")) else "auth required"}), 401
    return redirect("/login")


@bp.teardown_app_request
def _reset_demo(_exc=None):
    database.set_demo(False)


@bp.post("/api/demo/start")
def demo_start():
    """Open the invented-data version. Needs no sign-in because it holds nothing real; on a phone the Tailscale owner check in gate() still applies."""
    import demo
    demo.ensure_fresh()
    return _cookie(make_response(jsonify({"ok": True})), auth.issue("demo", SEC.is_remote(), request.headers.get("User-Agent", "")), SEC.is_remote())


@bp.get("/login")
def login_page():
    return send_from_directory(os.path.join(BASE, "templates"), "login.html")


@bp.get("/setup")
def setup_page():
    if SEC.is_remote() or auth.password_set():
        return redirect("/login")
    return send_from_directory(os.path.join(BASE, "templates"), "setup.html")


@bp.get("/enroll")
def enroll_page():
    return send_from_directory(os.path.join(BASE, "templates"), "enroll.html")


@bp.get("/api/auth/state")
def state():
    remote = SEC.is_remote()
    s = auth.lookup(_token())
    return jsonify({"remote": remote, "setup_needed": (not remote) and not auth.password_set(), "passkeys": passkeys.count(), "signed_in": bool(s) and not s.get("locked"), "locked": bool(s and s.get("locked")), "demo": bool(s and s.get("method") == "demo")})


@bp.post("/api/setup")
def do_setup():
    if SEC.is_remote() or auth.password_set():
        return _err("not available", 403)
    try:
        body = request.json or {}
        auth.set_password(body.get("password", ""))
    except ValueError as e:
        return _err(str(e), 400)
    ccy = str(body.get("currency", "")).strip().upper()
    if len(ccy) == 3 and ccy.isalpha():                       # first-run choice; only possible before a password exists, so never an unauthenticated change
        auth._set("base_currency", ccy)
        with database.real_db() as conn:
            conn.execute("DELETE FROM fx_rates"); conn.execute("INSERT INTO fx_rates VALUES (?,?,date('now'))", (ccy, 1.0))
    auth.event("password_set", True)
    return _cookie(make_response(jsonify({"ok": True})), auth.issue("password", False, request.headers.get("User-Agent", "")), False)


@bp.post("/api/login")
def do_login():
    """Password sign-in exists only on this PC. From a phone, Face ID is the only way in."""
    if SEC.is_remote():
        return _err("On this device use Face ID.", 403)
    wait = auth.lockout_seconds()
    if wait:
        r = _err(f"Too many wrong passwords. Try again in {wait} seconds.", 429)
        r[0].headers["Retry-After"] = str(wait)
        return r
    if not auth.password_set():
        return _err("setup", 409)
    if not auth.check_password((request.json or {}).get("password", "")):
        return _err("Wrong password.", 401)
    return _cookie(make_response(jsonify({"ok": True})), auth.issue("password", False, request.headers.get("User-Agent", "")), False)


@bp.post("/api/logout")
def do_logout():
    auth.revoke(_token())
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@bp.post("/api/session/lock")
def do_lock():
    auth.lock(_token())
    return jsonify({"ok": True})


# ───────── passkeys ─────────
def _rp():
    return SEC.public_host(), f"https://{SEC.public_host()}"


@bp.post("/api/passkey/register/options")
def reg_options():
    if not SEC.is_remote():
        return _err("Open the link on your phone.", 400)
    try:
        return jsonify({"ok": True, **passkeys.register_options(_rp()[0], (request.json or {}).get("token", ""))})
    except ValueError as e:
        auth.event("passkey_register_options", False, str(e))
        return _err(str(e), 400)


@bp.post("/api/passkey/register/verify")
def reg_verify():
    if not SEC.is_remote():
        return _err("Open the link on your phone.", 400)
    b = request.json or {}
    try:
        passkeys.register_verify(_rp()[0], _rp()[1], b.get("cid"), b.get("credential"), b.get("name"))
    except Exception as e:
        auth.event("passkey_register", False, str(e)[:120])
        return _err("Could not set up Face ID: " + str(e)[:120], 400)
    if not auth._get("owner_login") and SEC.tailscale_login():
        auth._set("owner_login", SEC.tailscale_login())          # the first device to enrol with a one-time link fixes who the owner is
    auth.event("passkey_register", True, b.get("name", ""))
    return _cookie(make_response(jsonify({"ok": True})), auth.issue("passkey", True, request.headers.get("User-Agent", "")), True)


@bp.post("/api/passkey/login/options")
def pk_options():
    if not SEC.is_remote():
        return _err("Face ID is for your phone.", 400)
    try:
        return jsonify({"ok": True, **passkeys.login_options(_rp()[0])})
    except ValueError as e:
        return _err(str(e), 400)


@bp.post("/api/passkey/login/verify")
def pk_verify():
    if not SEC.is_remote():
        return _err("Face ID is for your phone.", 400)
    b = request.json or {}
    try:
        passkeys.login_verify(_rp()[0], _rp()[1], b.get("cid"), b.get("credential"))
    except Exception as e:
        auth.event("passkey_login", False, str(e)[:120])
        return _err("Face ID did not verify.", 401)
    old = _token()
    if old:
        auth.revoke(old)
    auth.event("passkey_login", True)
    return _cookie(make_response(jsonify({"ok": True})), auth.issue("passkey", True, request.headers.get("User-Agent", "")), True)


# ───────── security settings (this PC only) ─────────
def _local_only():
    return None if not SEC.is_remote() else _err("Do this on your PC.", 403)


@bp.get("/api/security/status")
def sec_status():
    return jsonify({"password_set": auth.password_set(), "remote": SEC.is_remote(), "public_host": SEC.public_host(), "owner_set": bool(auth._get("owner_login")), "passkeys": passkeys.listing(),
                    "sessions": auth.sessions(_token()), "events": auth.events(15)})


@bp.post("/api/security/change-password")
def sec_change_password():
    if (r := _local_only()):
        return r
    b = request.json or {}
    if auth.lockout_seconds():
        return _err("Too many attempts. Wait a little.", 429)
    if not auth.check_password(b.get("current", "")):
        return _err("Current password is wrong.", 401)
    try:
        auth.set_password(b.get("new", ""))
    except ValueError as e:
        return _err(str(e), 400)
    auth.event("password_changed", True)
    return _cookie(make_response(jsonify({"ok": True})), auth.issue("password", False, request.headers.get("User-Agent", "")), False)


@bp.post("/api/security/enroll-link")
def sec_enroll_link():
    if (r := _local_only()):
        return r
    ph = SEC.public_host()
    if not ph:
        return _err("Set up Tailscale HTTPS first: no private address is configured.", 400)
    url = f"https://{ph}/enroll#{auth.new_enroll_token()}"
    import qrcode
    import qrcode.image.svg
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=8).save(buf)
    auth.event("enroll_link_made", True)
    return jsonify({"ok": True, "url": url, "svg": buf.getvalue().decode(), "expires_in": 600})


@bp.delete("/api/security/passkey/<pid>")
def sec_remove_passkey(pid):
    if (r := _local_only()):
        return r
    passkeys.remove(pid[:12])
    auth.event("passkey_removed", True)
    return jsonify({"ok": True})


@bp.post("/api/security/sign-out-others")
def sec_sign_out_others():
    auth.revoke_all(keep_token=_token())
    auth.event("signed_out_others", True)
    return jsonify({"ok": True})
