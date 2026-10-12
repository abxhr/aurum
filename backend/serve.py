"""Production entry point: the app behind a real web server (waitress), bound to this PC only.

Nothing listens on the network. Your phone reaches it through `tailscale serve`, which terminates HTTPS and forwards to 127.0.0.1, so the only way in from outside is
your own Tailscale account plus Face ID. Start it with pythonw (see scripts/install-background.ps1) so it runs hidden, at logon, and comes back if it ever stops."""
import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PORT = int(os.environ.get("PORT", "5000"))
LOG_DIR = os.path.join(os.path.dirname(HERE), "data", "logs")


def tailscale_host():
    """This machine's private HTTPS name, e.g. pc-name.tailnet.ts.net, if Tailscale is installed and signed in."""
    exe = next((p for p in (r"C:\Program Files\Tailscale\tailscale.exe", "tailscale") if p == "tailscale" or os.path.exists(p)), None)
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, timeout=10, creationflags=0x08000000)
        return json.loads(out.stdout)["Self"]["DNSName"].rstrip(".").lower()
    except Exception:
        return ""


def port_in_use():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    if port_in_use():
        return                                      # already running: the watchdog task starts this every few minutes and must not start a second copy
    os.makedirs(LOG_DIR, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, "wealth.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])
    if sys.stdout is None:                          # pythonw has no console: keep stray prints out of the way
        sys.stdout = sys.stderr = open(os.devnull, "w")
    import app as A
    import auth
    import vault
    from database import init_db, seed_if_empty
    init_db(); seed_if_empty()
    if auth.purge_default():
        logging.warning("the shipped default password was removed; set a new one at http://localhost:%s", PORT)
    logging.info("sealed %s plain-text secrets", vault.migrate())
    if not auth._get("public_host"):
        ph = tailscale_host()
        if ph:
            auth._set("public_host", ph)
    from database import get_db
    if A.setting("auto_sync_v2", "") != "1":          # automation is the default; turn it on once for older installs
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('auto_sync_enabled','1')")
            conn.execute("INSERT OR REPLACE INTO settings VALUES ('auto_sync_v2','1')")
    threading.Thread(target=A._auto_sync_loop, daemon=True).start()
    import waitress
    logging.info("serving on 127.0.0.1:%s", PORT)
    waitress.serve(A.app, host="127.0.0.1", port=PORT, threads=8, ident="", send_bytes=1, max_request_body_size=8 * 1024 * 1024, connection_limit=100, channel_timeout=120,
                   clear_untrusted_proxy_headers=True)


if __name__ == "__main__":
    main()
