"""AI assistant: chat (OpenAI) + free rule-based financial insights + LLM-generated
"AI Analysis" insights (see generate_ai_insights below)."""
from datetime import datetime
import json
import config
from database import get_db, base_ccy as CCY
import cards as C
import transactions as T

try:
    from openai import OpenAI
    _OPENAI_LIB = True
except ImportError:
    _OPENAI_LIB = False

MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """You are the embedded AI assistant inside the user's private, single-user personal wealth \
tracking app ("Wealth"). You can see their live financial data below — use it to give specific, grounded answers, \
not generic advice. You help with everything: net worth, investments, credit card management, spending habits, \
budgeting, savings goals, and general financial questions.

Current financial snapshot:
{context}

Guidelines:
- Be concise and specific — reference actual numbers from the snapshot above.
- Suggest concrete next actions when relevant (e.g. "pay off X before Y", "you have Z surplus you could invest").
- You are not a licensed financial advisor. Only add a brief one-line disclaimer when giving specific investment \
recommendations (buy/sell a particular asset) — not for every message.
- If asked about something not in the snapshot (e.g. current stock prices), say you don't have live data for that \
unless it's provided.
- Keep replies under ~180 words unless the user asks for more detail."""


def _client():
    if not _OPENAI_LIB or not config.OPENAI_API_KEY:
        return None
    return OpenAI(api_key=config.OPENAI_API_KEY)


def available():
    return _OPENAI_LIB and bool(config.OPENAI_API_KEY)


def build_context(net_worth=None, budget=None):
    """Compact text snapshot of the user's finances for the system prompt."""
    lines = []
    if net_worth:
        lines.append(f"Net worth: {CCY()} {net_worth.get('total_base', 0):,.0f} "
                      f"(safe: {net_worth.get('safe_pct', 0)}%, growth: {net_worth.get('growth_pct', 0)}%)")
        for c in net_worth.get("by_class", [])[:8]:
            lines.append(f"  - {c['label']}: {CCY()} {c['value']:,.0f} ({c['pct']}%)")

    if budget:
        lines.append(f"Monthly budget: income {CCY()} {budget.get('income', 0):,.0f}, "
                      f"expenses {CCY()} {budget.get('expenses', 0):,.0f}, "
                      f"savings {CCY()} {budget.get('savings', 0):,.0f}, "
                      f"investments {CCY()} {budget.get('invest', 0):,.0f}, "
                      f"surplus {CCY()} {budget.get('surplus', 0):,.0f}")

    try:
        import analytics as AN
        inc = AN.income_basis()
        if inc["source"] == "payslip":
            lines.append(f"Payslips ({inc['period']}, non-bonus month): gross {CCY()} {inc['gross']:,.0f}, deductions {CCY()} {inc['deductions']:,.0f}, "
                         f"net {CCY()} {inc['net']:,.0f}; typical net {CCY()} {inc['typical_net']:,.0f}; average incl. bonus {CCY()} {inc['avg_net']:,.0f} "
                         f"(includes annual bonus). Savings+investments of {CCY()} {(budget or {}).get('savings', 0) + (budget or {}).get('invest', 0):,.0f}/month "
                         f"= {((budget or {}).get('savings', 0) + (budget or {}).get('invest', 0)) / inc['gross'] * 100:.0f}% of gross pay.")
    except Exception:
        pass

    stats = C.get_stats()
    lines.append(f"Credit cards: {stats['card_count']} cards, {CCY()} {stats['total_outstanding']:,.0f} outstanding, "
                 f"{stats['unpaid_count']} unpaid statement(s). (Credit limits are intentionally not tracked.)")

    dues = C.get_upcoming_dues(30)
    if dues:
        lines.append("Upcoming/overdue card payments:")
        for d in dues[:6]:
            lines.append(f"  - {d['card_name']}: {CCY()} {d['total_due']:,.2f} due {d['due_date']} ({d['urgency']})")

    import analytics as AN
    done = [m for m in AN.spend_cycles()["months"] if m["complete"]][-3:]
    if done:
        lines.append("Card spending by statement cycle (each card closes on a different day; only complete cycles):")
        for m in done:
            lines.append(f"  - {m['month']}: {CCY()} {m['total']:,.0f}")
        for cat, v in sorted(done[-1]["cats"].items(), key=lambda kv: -kv[1])[:8]:
            lines.append(f"  - {done[-1]['month']} {cat}: {CCY()} {v:,.0f}")


    return "\n".join(lines)


def get_history(limit=30):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM assistant_messages ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return list(reversed([dict(r) for r in rows]))


def save_message(role, content):
    with get_db() as conn:
        conn.execute("INSERT INTO assistant_messages (role, content, created_at) VALUES (?,?,?)",
                      (role, content, datetime.utcnow().isoformat()))


def clear_history():
    with get_db() as conn:
        conn.execute("DELETE FROM assistant_messages")


def chat(user_message, net_worth=None, budget=None):
    if not available():
        return {"ok": False, "error": "AI assistant not configured — missing OPENAI_API_KEY."}

    context = build_context(net_worth, budget)
    history = get_history(24)
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]
    messages.append({"role": "user", "content": user_message})

    save_message("user", user_message)
    try:
        resp = _client().chat.completions.create(
            model=MODEL, messages=messages, max_tokens=600, temperature=0.4)
        reply = resp.choices[0].message.content
        save_message("assistant", reply)
        return {"ok": True, "reply": reply}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── LLM-generated "AI Analysis" insights (costs an API call; caller caches) ──
SCOPE_LABELS = {
    "dashboard": "overall financial picture (net worth, readiness, budget, spending)",
    "portfolio": "investment portfolio and asset allocation",
    "cards": "credit card spending patterns and payment health (do not mention credit limits or utilization)",
    "budget": "monthly budget and cash flow",
    "market": "watchlist and market positions",
}

INSIGHTS_SYSTEM_PROMPT = """You are a financial analysis engine embedded in a private, single-user wealth tracker. \
Given the data below about the user's {scope_label}, identify the 2-4 most noteworthy things they should know — \
real patterns, risks, or opportunities grounded in the actual numbers, not generic advice. Skip anything unremarkable \
rather than padding to reach 4 — quality over quantity.

Data:
{context}

Respond with ONLY a JSON object shaped exactly like this (no prose, no markdown fences):
{{"insights": [{{"icon": "<one emoji>", "title": "<short title, under 8 words>", "body": "<1-2 sentences, must cite actual numbers from the data above>", "severity": "high"|"medium"|"low"}}]}}
If there's genuinely nothing noteworthy, return {{"insights": []}}. Never invent a number that isn't in the data above."""


def generate_ai_insights(scope, context):
    """Calls the LLM once for the given scope's context and returns a small list of
    structured insights (same {icon,title,body,severity} shape as the free rule-based
    ones, so the frontend can render both with the same component). The caller is
    responsible for caching the result — this function always makes a fresh API call."""
    if not available():
        return {"ok": False, "error": "AI analysis requires an OpenAI API key."}
    if not context.strip():
        return {"ok": True, "insights": []}
    prompt = INSIGHTS_SYSTEM_PROMPT.format(scope_label=SCOPE_LABELS.get(scope, scope), context=context)
    try:
        resp = _client().chat.completions.create(
            model=MODEL, messages=[{"role": "system", "content": prompt}],
            max_tokens=500, temperature=0.3, response_format={"type": "json_object"})
        import aiusage
        u = getattr(resp, "usage", None)
        aiusage.log("insights:" + scope, MODEL, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
        data = json.loads(resp.choices[0].message.content)
        raw = data.get("insights", []) if isinstance(data, dict) else []
        clean = []
        for i in raw[:4]:
            if isinstance(i, dict) and i.get("title") and i.get("body"):
                sev = i.get("severity")
                clean.append({
                    "icon": str(i.get("icon", "✨"))[:4],
                    "title": str(i["title"])[:80],
                    "body": str(i["body"])[:400],
                    "severity": sev if sev in ("high", "medium", "low") else "low",
                })
        return {"ok": True, "insights": clean}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── free, rule-based insights (no API cost) ───────────────────────────────
def _dismissed_keys():
    with get_db() as conn:
        return {r["insight_key"] for r in conn.execute("SELECT insight_key FROM dismissed_insights").fetchall()}


def dismiss_insight(key):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO dismissed_insights (insight_key, dismissed_at) VALUES (?,?)",
                      (key, datetime.utcnow().isoformat()))


def generate_insights(budget=None):
    insights = []
    dismissed = _dismissed_keys()

    stats = C.get_stats()
    dues = C.get_upcoming_dues(14)
    for d in dues:
        if d["urgency"] in ("overdue", "urgent"):
            key = f"due-{d['id']}"
            insights.append({
                "key": key, "icon": "⚠️" if d["urgency"] == "overdue" else "🔔",
                "title": f"{d['card_name']} payment {'is overdue' if d['urgency'] == 'overdue' else 'due soon'}",
                "body": f"{CCY()} {d['total_due']:,.2f} due {d['due_date']}.",
                "severity": "high" if d["urgency"] == "overdue" else "medium",
            })

    import analytics as AN
    monthly = AN._completed_monthly_totals()   # only months the card data fully covers
    if len(monthly) >= 2 and monthly[-2]["total"] > 0:
        prev, cur = monthly[-2], monthly[-1]
        change = (cur["total"] - prev["total"]) / prev["total"] * 100
        if change > 25:
            insights.append({
                "key": f"spike-{cur['month']}", "icon": "📈", "title": f"Spending jumped in {cur['month']}",
                "body": f"Card spend in {cur['month']} ({CCY()} {cur['total']:,.0f}) was {change:.0f}% higher than "
                        f"{prev['month']} ({CCY()} {prev['total']:,.0f}).",
                "severity": "medium",
            })
        elif change < -25:
            insights.append({
                "key": f"drop-{cur['month']}", "icon": "📉", "title": f"Spending fell in {cur['month']}",
                "body": f"Card spend in {cur['month']} ({CCY()} {cur['total']:,.0f}) was {abs(change):.0f}% lower than "
                        f"{prev['month']} ({CCY()} {prev['total']:,.0f}).",
                "severity": "low",
            })

    # cards that have gone quiet make every spending total look lower than it really is
    with get_db() as conn:
        quiet = [dict(r) for r in conn.execute(
            """SELECT c.name, MAX(s.statement_date) last FROM credit_cards c
               LEFT JOIN card_statements s ON s.card_id=c.id GROUP BY c.id""").fetchall()]
    today = datetime.utcnow().date()
    stale = [q["name"] for q in quiet if not q["last"] or (today - datetime.fromisoformat(q["last"]).date()).days > 35]
    if stale:
        insights.append({
            "key": "stale-cards-" + str(len(stale)), "icon": "📭", "title": f"{len(stale)} card{'s' if len(stale) != 1 else ''} with no recent statement",
            "body": f"{', '.join(stale)}: no statement in over 35 days, so card spend totals may be understated. "
                    f"Check the Gmail sync, or whether the card is still in use.",
            "severity": "medium",
        })

    import planner as PL
    left = PL.basics()["flow_typical"]     # measured: take-home minus real spending and investing; the typed-in plan is not evidence
    if left > 500:
        insights.append({
            "key": "surplus", "icon": "💰", "title": "Unallocated monthly surplus",
            "body": f"{CCY()} {left:,.0f} a month is left after your real spending and investing — "
                    f"consider directing it toward a goal.",
            "severity": "low",
        })

    with get_db() as conn:
        last_snap = conn.execute("SELECT MAX(snap_date) d FROM snapshots").fetchone()["d"]
    if last_snap:
        days_since = (datetime.utcnow().date() - datetime.fromisoformat(last_snap).date()).days
        if days_since > 30:
            insights.append({
                "key": "stale-snapshot", "icon": "🕓", "title": "Net worth history is stale",
                "body": f"Your last snapshot was {days_since} days ago — take a new one to keep your net worth "
                        f"trend accurate.",
                "severity": "low",
            })

    return [i for i in insights if i["key"] not in dismissed]
