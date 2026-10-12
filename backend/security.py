"""Request-level protection that applies to every route: which addresses may reach the app, who is asking, cross-site request forgery, and browser security headers."""
import hmac
import os

from flask import request

import auth

PORT = int(os.environ.get("PORT", "5000"))
LOCAL_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}", f"[::1]:{PORT}"}
CSP = ("default-src 'self'; script-src 'self'; script-src-attr 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
       "media-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'; manifest-src 'self'; worker-src 'self'")


def public_host():
    return (auth._get("public_host") or "").lower()


def host_ok():
    """Only addresses the app knows as its own. A page on another site that points its DNS at this PC (DNS rebinding) sends a different Host and is refused."""
    h = (request.host or "").lower()
    ph = public_host()
    return h in LOCAL_HOSTS or (bool(ph) and h in (ph, ph + ":443"))


def is_remote():
    """True when the request came in through the Tailscale address (another device) rather than from this PC."""
    ph = public_host()
    return bool(ph) and (request.host or "").lower() in (ph, ph + ":443")


def tailscale_login():
    return (request.headers.get("Tailscale-User-Login") or "").strip().lower()


def owner_ok(allow_first_use=False):
    """Remote requests must carry the Tailscale identity of the owner (set by Tailscale's own proxy, not by the client)."""
    if not is_remote():
        return True
    login = tailscale_login()
    if not login:
        return False
    owner = (auth._get("owner_login") or "").lower()
    if not owner:
        return allow_first_use
    return hmac.compare_digest(owner, login)


def origins():
    o = {f"http://{h}" for h in LOCAL_HOSTS}
    ph = public_host()
    if ph:
        o.add(f"https://{ph}")
    return o


def csrf_ok():
    """State-changing requests must carry our own header (a foreign page cannot add it without a CORS preflight, which is never granted), be same-site, and come from our origin."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    if request.headers.get("X-Wealth") != "1":
        return False
    if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        return False
    org = request.headers.get("Origin")
    return org is None or org in origins()


def add_headers(resp):
    h = resp.headers
    h["Content-Security-Policy"] = CSP
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "camera=(), geolocation=(), payment=(), usb=(), microphone=(self)"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.path == "/static/sw.js":
        h["Service-Worker-Allowed"] = "/"          # the worker lives in /static but must cover the whole app to receive alerts
    if is_remote():
        h["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/api/") or request.path in ("/", "/login", "/setup", "/enroll"):
        h["Cache-Control"] = "no-store"
    h.pop("Server", None)
    return resp
