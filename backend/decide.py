"""Cheap multiple-choice classification for anything in the app that is a decision rather than writing.

Backends, cheapest first:
  jev      TypeSafe AI's Jev, POST https://api.typesafe.ai/v1/systemone      (needs a TypeSafe key in AI settings)
  openai   OpenAI Decisions API, POST /v1/decisions on gpt-6-luna            (uses the OpenAI key; input-only billing)
Both return a choice with a probability per option and a confidence, so callers can refuse low-confidence answers.
A backend that errors with 401/403/404 is parked for 30 minutes so a missing beta or bad key never causes a retry storm.
"""
import json
import os
import time
import urllib.error
import urllib.request

import vault
from database import get_db

_DEAD = {}


def _setting(k):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return (vault.unseal(r["value"]) if r else "") or ""


def keys():
    import config
    return {"jev": _setting("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY", ""),
            "openai": _setting("openai_api_key") or config.OPENAI_API_KEY or os.environ.get("OPENAI_API_KEY", "")}


def available():
    k = keys()
    return [b for b in ("jev", "openai") if k[b] and time.time() - _DEAD.get(b, 0) > 1800]


def _post(url, key, body, timeout=25):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _jev(key, text, qs):
    body = {"model": "jev-latest", "state": text,
            "questions": {q["name"]: {"type": "choice", "instructions": q["instructions"], "criteria": {v: d for v, d in q["choices"]}} for q in qs}}
    return _norm(_post("https://api.typesafe.ai/v1/systemone", key, body)["answers"])


def _openai(key, text, qs):
    body = {"model": "gpt-6-luna", "input": text,
            "questions": [{"type": "choice", "name": q["name"], "instructions": q["instructions"],
                           "choices": [{"value": v, "description": d} for v, d in q["choices"]]} for q in qs]}
    return _norm(_post("https://api.openai.com/v1/decisions", key, body)["answers"])


def _norm(answers):
    """-> {name: {"choice", "confidence"}} whether the service returns a list or a map of answers."""
    items = answers.items() if isinstance(answers, dict) else ((a.get("name"), a) for a in answers)
    return {n: {"choice": a.get("choice"), "confidence": float(a.get("confidence") or 0)} for n, a in items if isinstance(a, dict)}


def decide(text, questions, feature="decision"):
    """questions: [{name, instructions, choices: [(value, description), ...]}]. Returns ({name: {choice, confidence}}, backend) or (None, None)."""
    k = keys()
    for b in available():
        try:
            res = (_jev if b == "jev" else _openai)(k[b], text, questions)
            import aiusage
            aiusage.log(feature, b, kind="decision")
            return res, b
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 422):
                _DEAD[b] = time.time()
        except Exception:
            pass
    return None, None
