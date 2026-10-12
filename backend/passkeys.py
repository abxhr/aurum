"""Passkeys (WebAuthn): sign in with Face ID. The phone creates a key pair inside its secure chip; only the public half is stored here. Signing needs a live face (user
verification is required, not just 'preferred'), and the signature covers this site's exact address, so a look-alike page cannot ask for it."""
import json
import secrets
import time

from webauthn import generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response, verify_registration_response
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (AuthenticatorAttachment, AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, ResidentKeyRequirement, UserVerificationRequirement)

import auth
from database import real_db as get_db

_CH = {}                     # challenge id -> (challenge, expires, purpose, token): single use, five minutes


def _stash(challenge, purpose, token=""):
    now = time.time()
    for k in [k for k, v in _CH.items() if v[1] < now]:
        _CH.pop(k, None)
    cid = secrets.token_urlsafe(16)
    _CH[cid] = (challenge, now + 300, purpose, token)
    return cid


def _take(cid, purpose):
    v = _CH.pop(cid or "", None)
    if not v or v[1] < time.time() or v[2] != purpose:
        raise ValueError("That request expired. Start again.")
    return v


def _user_handle():
    h = auth._get("passkey_user")
    if not h:
        h = secrets.token_hex(16)
        auth._set("passkey_user", h)
    return bytes.fromhex(h)


def count():
    auth.ensure()
    with get_db() as conn:
        return conn.execute("SELECT COUNT(*) c FROM passkeys").fetchone()["c"]


def listing():
    auth.ensure()
    with get_db() as conn:
        return [{"id": r["cred_id"][:12], "name": r["name"], "created": r["created"], "last_used": r["last_used"]} for r in conn.execute("SELECT * FROM passkeys ORDER BY created").fetchall()]


def remove(prefix):
    with get_db() as conn:
        conn.execute("DELETE FROM passkeys WHERE substr(cred_id,1,12)=?", (prefix,))


def _creds():
    auth.ensure()
    with get_db() as conn:
        return conn.execute("SELECT * FROM passkeys").fetchall()


def register_options(rp_id, token):
    if not auth.enroll_token_valid(token):
        raise ValueError("This link has expired or was already used. Make a new one on your PC.")
    opts = generate_registration_options(
        rp_id=rp_id, rp_name="Wealth", user_name="owner", user_display_name="Owner", user_id=_user_handle(),
        authenticator_selection=AuthenticatorSelectionCriteria(authenticator_attachment=AuthenticatorAttachment.PLATFORM, resident_key=ResidentKeyRequirement.REQUIRED,
                                                               user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["cred_id"])) for r in _creds()])
    return {"cid": _stash(opts.challenge, "reg", token), "options": json.loads(options_to_json(opts))}


def register_verify(rp_id, origin, cid, credential, name):
    challenge, _, _, token = _take(cid, "reg")
    v = verify_registration_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin, require_user_verification=True)
    if not auth.use_enroll_token(token):
        raise ValueError("This link was already used.")
    auth.ensure()
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO passkeys(cred_id,public_key,sign_count,name,created,last_used) VALUES (?,?,?,?,?,?)",
                     (bytes_to_base64url(v.credential_id), v.credential_public_key, v.sign_count, (name or "iPhone")[:40], auth.now(), auth.now()))
    return True


def login_options(rp_id):
    rows = _creds()
    if not rows:
        raise ValueError("No Face ID device is set up yet.")
    opts = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED,
                                           allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(r["cred_id"])) for r in rows])
    return {"cid": _stash(opts.challenge, "login"), "options": json.loads(options_to_json(opts))}


def login_verify(rp_id, origin, cid, credential):
    challenge, _, _, _ = _take(cid, "login")
    row = next((r for r in _creds() if r["cred_id"] == (credential or {}).get("id")), None)
    if not row:
        raise ValueError("Unknown device.")
    v = verify_authentication_response(credential=credential, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
                                       credential_public_key=bytes(row["public_key"]), credential_current_sign_count=row["sign_count"], require_user_verification=True)
    with get_db() as conn:
        conn.execute("UPDATE passkeys SET sign_count=?, last_used=? WHERE cred_id=?", (v.new_sign_count, auth.now(), row["cred_id"]))
    return True
