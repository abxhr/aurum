"""Market intelligence for one investor who is paid on a fixed day and buys a fund each month.

Three questions, each answered from price history with the sample size shown, so a result that is just noise says so:
  buy_timing   which trading day and weekday after payday has historically been cheapest for the monthly buy
  ideas        how a set of broad funds compare (return, swings, worst fall, overlap with each other, versus your cash rate)
  briefing     today's conditions, what they mean for you, and the ordered steps with reasons

Nothing here predicts. Past prices describe what happened; the reasons say what is and is not safe to conclude.
Settings > Payday and Settings > Time zone feed it; the funds compared can be changed with a plugin (plugins.py: MARKET_FUNDS)."""
from database import base_ccy as CCY, get_setting
import plugins
import bisect
import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from statistics import mean, median, stdev
from defusedxml import ElementTree        # news feeds are untrusted XML: no entity tricks

import planner as PL
import prices as P

DEFAULT_FUNDS = [
    ("VTI", "Vanguard Total Stock Market", "Core: US total market", "Broad US exposure."),
    ("VXUS", "Vanguard Total International", "International developed and emerging markets", "Diversifies away from the US."),
    ("BND", "Vanguard Total Bond Market", "Bonds: fixed income", "Falls when rates rise; pays interest."),
    ("GLD", "SPDR Gold Shares", "Gold: diversifier", "No yield; has moved differently from shares."),
]


def funds():
    return plugins.get("MARKET_FUNDS") or DEFAULT_FUNDS


def pay_day():
    """Day of the month your pay arrives (Settings > Payday)."""
    try:
        return max(1, min(28, int(get_setting("pay_day", "1"))))
    except ValueError:
        return 1


def core():
    return funds()[0][0]


_C = {}


def _cached(key, ttl, fn):
    if key in _C and time.time() - _C[key][0] < ttl:
        return _C[key][1]
    v = fn()
    _C[key] = (time.time(), v)
    return v


def _series(sym, rng="10y"):
    h = PL._hist(sym, rng, ttl=6 * 3600)
    return [(p["date"], p["close"]) for p in h["points"]] if h else []


# ───────────────────────── buy timing ─────────────────────────
def _paired(vals):
    """mean, standard error, share of months below zero."""
    n = len(vals)
    if n < 3:
        return None
    m = mean(vals)
    se = stdev(vals) / math.sqrt(n)
    return {"mean_pct": round(m * 100, 3), "median_pct": round(median(vals) * 100, 3), "se_pct": round(se * 100, 3), "n": n,
            "cheaper_pct": round(sum(v < 0 for v in vals) / n * 100), "reliable": abs(m) > 2 * se}


def buy_timing(sym=None, lag=1, horizon=9, usd=1000):
    """Each month: the first trading day on or after payday is day 0. 'lag' trading days is how long your transfer needs to reach the broker.
    For each later day, how much dearer or cheaper was the close than on day `lag`? And which weekday is cheapest inside that window?"""
    sym = sym or core()
    ser = _series(sym)
    if len(ser) < 400:
        return {"error": f"not enough price history for {sym}"}
    dates = [d for d, _ in ser]
    px = [c for _, c in ser]
    wins, y, m = [], int(dates[0][:4]), int(dates[0][5:7])
    last = date.fromisoformat(dates[-1])
    while date(y, m, pay_day()) <= last:
        i0 = bisect.bisect_left(dates, date(y, m, pay_day()).isoformat())
        if i0 + horizon < len(px) and dates[i0][:7] == f"{y}-{m:02d}":
            wins.append(i0)
        y, m = (y + (m == 12), m % 12 + 1)
    lag = max(0, min(int(lag), horizon - 2))
    offs, best = [], None
    for k in range(lag, horizon + 1):
        rel = [px[i + k] / px[i + lag] - 1 for i in wins]
        st = _paired(rel) if k > lag else {"mean_pct": 0.0, "median_pct": 0.0, "se_pct": 0.0, "n": len(rel), "cheaper_pct": 0, "reliable": False}
        row = {"offset": k, **st}
        offs.append(row)
        if k > lag and (best is None or st["mean_pct"] < best["mean_pct"]):
            best = row
    dev = {w: [] for w in range(5)}
    for i in wins:
        win = px[i + lag: i + horizon + 1]
        mu = mean(win)
        for k, p in enumerate(win):
            wd = date.fromisoformat(dates[i + lag + k]).weekday()
            if wd < 5:
                dev[wd].append(p / mu - 1)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    wd = [{"day": names[w], **(_paired(dev[w]) or {"mean_pct": 0, "n": 0, "reliable": False})} for w in range(5)]
    cheapest = min(wd, key=lambda r: r["mean_pct"])
    split = []
    for i in wins:                                   # half on day `lag`, half `horizon` days later, versus all on day `lag`
        units = 0.5 / px[i + lag] + 0.5 / px[i + horizon]
        split.append(1 / units / px[i + lag] - 1)
    sp = _paired(split)
    reliable_best = best and best["reliable"] and best["mean_pct"] < 0
    waits = [o for o in offs if o["offset"] > lag]
    drift = mean(o["mean_pct"] for o in waits[-3:]) if waits else 0
    rec_offset = best["offset"] if reliable_best else lag
    usd_base = PL.usd_rate()
    if reliable_best:
        verdict = (f"Buying {best['offset'] - lag} trading day(s) after your money lands has been {abs(best['mean_pct']):.2f}% cheaper on average "
                   f"(cheaper in {best['cheaper_pct']}% of {best['n']} months). That is about {CCY()} {abs(best['mean_pct']) / 100 * usd * usd_base:,.0f} on USD {usd:,.0f}.")
    else:
        verdict = (f"Across {len(wins)} months no later day has been reliably cheaper than buying as soon as the money lands. "
                   + (f"Prices drifted up by {drift:.2f}% over the following days on average, so waiting cost you money rather than saving it." if drift > 0 else
                      f"The best later day was {abs(best['mean_pct']):.2f}% cheaper, which is inside the noise.") + " Buy when the transfer lands.")
    return {"symbol": sym, "name": next((f[1] for f in funds() if f[0] == sym), sym), "months": len(wins), "from": dates[wins[0]] if wins else None, "pay_day": pay_day(), "lag": lag,
            "horizon": horizon, "offsets": offs, "weekdays": wd, "cheapest_weekday": cheapest["day"] if cheapest["reliable"] else None,
            "weekday_note": (f"{cheapest['day']} has been cheapest by {abs(cheapest['mean_pct']):.2f}% against the window average ({cheapest['n']} observations)."
                             if cheapest["reliable"] else "No weekday stands out: the differences are smaller than the noise."),
            "split": sp, "recommended_offset": rec_offset, "verdict": verdict, "usd": usd,
            "caution": "This tests closing prices over past months. The payday-to-9-trading-days window mixes in ordinary market drift, so a result that is not marked reliable means the data cannot tell the days apart. Consistency matters far more than the day."}


def next_dates(lag=1, n=3):
    """Your next paydays and the first day the money can be invested (weekends skipped; exchange holidays are not known)."""
    out, d = [], date.today()
    y, m = d.year, d.month
    while len(out) < n:
        pay = date(y, m, pay_day())
        while pay.weekday() >= 5:                    # pay falling on a weekend arrives the working day before
            pay -= timedelta(days=1)
        if pay >= d:
            buy, left = pay, lag
            while left > 0 or buy.weekday() >= 5:
                buy += timedelta(days=1)
                if buy.weekday() < 5:
                    left -= 1
            out.append({"pay": pay.isoformat(), "pay_dow": pay.strftime("%a"), "buy": buy.isoformat(), "buy_dow": buy.strftime("%a")})
        y, m = (y + (m == 12), m % 12 + 1)
    return out


def trading_hours():
    """US market hours (09:30 to 16:00 New York) in your own time zone (Settings > Time zone, an IANA name such as Europe/Berlin)."""
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(get_setting("timezone", "") or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    ny = ZoneInfo("America/New_York")
    today = date.today()
    at = lambda hh, mm: datetime(today.year, today.month, today.day, hh, mm, tzinfo=ny).astimezone(tz).strftime("%H:%M")
    o, c = at(9, 30), at(16, 0)
    return {"open": o, "close": c, "us_open": o,
            "tips": [
                f"US markets trade {o} to {c} your time ({tz.key}). A fund holding mostly US shares is priced most reliably once the US is open.",
                "Avoid the first 15 minutes and the last 10: spreads are widest and the price is least tied to what the shares are worth.",
                "Use a limit order at or just above the ask instead of a market order, and buy in one order if your broker charges a minimum per order.",
                "If the fund is priced in another currency, convert once rather than per order."]}


# ───────────────────────── comparing the options ─────────────────────────
def _perf(ser):
    if len(ser) < 250:
        return None
    dates = [d for d, _ in ser]
    c = [x for _, x in ser]
    last = c[-1]

    def back(days):
        i = bisect.bisect_left(dates, (date.fromisoformat(dates[-1]) - timedelta(days=days)).isoformat())
        return c[i] if i < len(c) - 5 and dates[i] >= (date.fromisoformat(dates[-1]) - timedelta(days=days + 10)).isoformat() else None
    r = lambda d, y: ((last / d) ** (1 / y) - 1) * 100 if d else None
    rets = [math.log(c[i] / c[i - 1]) for i in range(1, len(c)) if c[i - 1] > 0 and c[i] > 0]
    tail = c[-1260:]
    peak, mdd = tail[0], 0.0
    for x in tail:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    y1, y3, y5 = back(365), back(1095), back(1826)
    return {"ret_1y": round((last / y1 - 1) * 100, 1) if y1 else None, "cagr_3y": round(r(y3, 3), 1) if y3 else None, "cagr_5y": round(r(y5, 5), 1) if y5 else None,
            "vol": round(stdev(rets[-756:]) * math.sqrt(252) * 100, 1), "max_drawdown": round(mdd * 100, 1), "from_high": round((last / max(c[-252:]) - 1) * 100, 1),
            "since": dates[0]}


def _monthly(ser, months=36):
    last = {}
    for d, c in ser:
        last[d[:7]] = c
    ks = sorted(last)[-(months + 1):]
    return {ks[i]: last[ks[i]] / last[ks[i - 1]] - 1 for i in range(1, len(ks))}


def _corr(a, b):
    ks = sorted(set(a) & set(b))
    if len(ks) < 12:
        return None
    x, y = [a[k] for k in ks], [b[k] for k in ks]
    mx, my = mean(x), mean(y)
    sx, sy = math.sqrt(sum((v - mx) ** 2 for v in x)), math.sqrt(sum((v - my) ** 2 for v in y))
    return round(sum((p - mx) * (q - my) for p, q in zip(x, y)) / (sx * sy), 2) if sx and sy else None


def ideas():
    S = PL.settings()
    cash = S["savings_rate"]
    fs = funds()
    with ThreadPoolExecutor(8) as ex:
        sers = list(ex.map(lambda f: _series(f[0]), fs))
    base = _monthly(sers[0])
    rows = []
    for f, ser in zip(fs, sers):
        pf = _perf(ser)
        if not pf:
            continue
        pf["corr_core"] = _corr(base, _monthly(ser)) if f[0] != fs[0][0] else 1.0
        rows.append({"symbol": f[0], "name": f[1], "role": f[2], "note": f[3], **pf, "vs_cash": round(pf["cagr_3y"] - cash, 1) if pf["cagr_3y"] is not None else None})
    return {"cash_rate": cash, "rows": rows, "core": fs[0][0],
            "how": "3-year growth is compound yearly price growth (distributions excluded for dividend-paying funds, so income funds look lower than their true return). 'Worst fall' is the deepest drop from a peak in the last 5 years. "
                   f"'Overlap with {fs[0][0]}' is the correlation of monthly returns over 3 years: near 1 means it moves with {fs[0][0]} and adds little; low or negative means it diversifies.",
            "caution": "Past returns are not forecasts. Check each fund's index, fees and rules yourself before buying."}


# ───────────────────────── today's briefing ─────────────────────────
def headlines(n=8):
    def go():
        out = []
        for q in ("stock market S&P 500 when:2d", "gold price when:3d", "index fund ETF when:14d"):
            url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
            try:
                root = ElementTree.fromstring(urllib.request.urlopen(urllib.request.Request(url, headers=P.UA), timeout=10).read())
            except Exception:
                continue
            for it in root.iter("item"):
                t = re.sub(r"\s+", " ", it.findtext("title") or "").strip()
                if t:
                    out.append({"topic": q.split(" when")[0], "title": t[:150], "link": it.findtext("link") or "", "date": (it.findtext("pubDate") or "")[5:16]})
                if sum(1 for x in out if x["topic"] == q.split(" when")[0]) >= 4:
                    break
        return out
    def tagged():
        import decide as DC
        items = go()
        q = [{"name": "tone", "instructions": "For a long-term index investor, is this headline good, bad or neutral news for the markets or asset it is about?",
              "choices": [("good", "positive for prices"), ("neutral", "mixed, routine or informational"), ("bad", "negative for prices")]}]
        def one(h):
            ans, _ = DC.decide(h["title"], q, "headline tone")
            return (ans or {}).get("tone", {}).get("choice")
        if DC.available():
            with ThreadPoolExecutor(6) as ex:
                for h, t in zip(items, ex.map(one, items)):
                    h["tone"] = t
        return items
    return _cached("news", 3600, tagged)[:n * 2]


HOLD_MAP = {}       # held symbols whose quote symbol differs from the one you typed (add pairs here)


def _rss(q, n):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        root = ElementTree.fromstring(urllib.request.urlopen(urllib.request.Request(url, headers=P.UA), timeout=10).read())
    except Exception:
        return []
    out = []
    for it in root.iter("item"):
        t = re.sub(r"\s+", " ", it.findtext("title") or "").strip()
        if t:
            out.append({"title": t[:150], "link": it.findtext("link") or "", "date": (it.findtext("pubDate") or "")[5:16]})
        if len(out) >= n:
            break
    return out


def holdings_watch():
    """What you own that trades on an exchange: where it stands, and its latest headlines."""
    from database import get_db
    with get_db() as conn:
        hs = conn.execute("SELECT name, symbol FROM holdings WHERE archived=0 AND units>0 AND symbol IS NOT NULL AND symbol<>'' AND is_liability=0 AND asset_class IN ('stock','etf')").fetchall()
    out = []
    for h in hs:
        sym = HOLD_MAP.get(h["symbol"], h["symbol"])
        st = PL._stats(sym, h["name"])
        h_ = PL._hist(sym)
        st["spark"] = [round(p["close"], 2) for p in h_["points"][-90:]] if h_ else []
        st["held_as"] = h["symbol"]
        q = h["name"] + " " + sym
        st["news"] = _cached("hn:" + sym, 3600, lambda q=q: _rss(q + " when:7d", 3))
        out.append(st)
    return out


def briefing():
    S = PL.settings()
    w = PL.market_weather()
    by = {r["symbol"]: r for r in w["rows"]}
    plan = PL.money_plan()
    lag = 1
    label = S["invest_label"].split(" (")[0]
    tm = buy_timing(core(), lag)
    nd = next_dates(lag, 3)
    reasons = []
    vix, spx, tnx, irx = by["^VIX"].get("last"), by["^GSPC"], by["^TNX"].get("last"), by["^IRX"].get("last")
    if vix is not None:
        reasons.append({"tone": "bad" if vix > 25 else "good" if vix < 16 else "warn",
                        "text": f"Fear gauge (VIX) is {vix:.1f}. " + ("Above 25: prices are swinging, so stage buys." if vix > 25 else "Under 16: calm markets. Nothing argues for rushing or waiting.")})
    if spx.get("from_1y_high_pct") is not None:
        d = spx["from_1y_high_pct"]
        reasons.append({"tone": "warn" if d > -2 else "good", "text": f"The S&P 500 is {abs(d):.1f}% {'below' if d < 0 else 'at'} its 1-year high and {spx.get('vs_200d_pct', 0):+.1f}% versus its 200-day average. "
                        + ("Near highs means little cushion if it turns, but a monthly buy does not need to guess: buying every month is how you average through it." if d > -3 else "A pullback is when a regular buyer gets more units for the same money.")})
    if tnx and irx:
        reasons.append({"tone": "warn", "text": f"US 10-year yield {tnx:.2f}% against the 3-month {irx:.2f}%. Your savings account pays {S['savings_rate']:g}%, so equities have to earn their extra risk over that, which is why only money you will not need for 5+ years belongs in them."})
    gold = by["GC=F"]
    if gold.get("from_1y_high_pct") is not None:
        reasons.append({"tone": "warn" if gold["from_1y_high_pct"] > -5 else "good", "text": f"Gold is {abs(gold['from_1y_high_pct']):.1f}% below its 1-year high ({gold.get('vs_200d_pct', 0):+.1f}% vs its 200-day). Your plan sets a {S['policy'].get('gold', 0)}% gold sleeve."})
    steps = []
    nxt = nd[0]
    invest = plan["deploy"]["monthly_contribution"]
    steps.append({"title": f"Buy your monthly {label} on {nxt['buy_dow']} {nxt['buy']}", "amount": invest,
                  "why": f"Pay lands about {nxt['pay_dow']} {nxt['pay']}; with a day for the transfer to reach your broker, {nxt['buy_dow']} is the first day you can buy. {tm.get('verdict', '')}"})
    free = plan["deploy"]["free_to_deploy"]
    if free >= 5000:
        n = plan["deploy"]["tranches"]
        bt = PL.dca_backtest(core(), 6, "10y")
        steps.append({"title": f"Stage {CCY()} {free:,.0f} of free cash: {CCY()} {free / n:,.0f} after each of the next {n:g} paydays", "amount": round(free / n),
                      "why": ("Free cash is what is left after your emergency fund and dated goals. " + (f"Over {bt['windows_tested']} past 6-month windows investing it all at once beat spreading it {bt['lump_sum_won_pct']}% of the time (median edge {bt['median_lump_edge_pct']}%), "
                              "because markets rise more often than they fall. Staging costs a little in expected return and buys protection against a bad month, so choose by how much a 15% drop right after buying would hurt." if "error" not in bt else "")).strip()})
    gold_have = plan["balances"]["growth"].get("gold", 0)
    pol_gold = S["policy"].get("gold", 0)
    if pol_gold and not gold_have:
        steps.append({"title": f"Start the {pol_gold}% gold sleeve with part of the staged cash", "amount": None,
                      "why": "Gold has moved differently from shares (see the Ideas tab: low overlap with the core fund), which is the only reason to hold it. Buy it in the same tranches rather than at once."})
    for fl in plan["flags"]:
        if "CFD" in fl["holding"]:
            steps.append({"title": "Review your leveraged position", "amount": None, "why": "Leverage with overnight financing can lose more than a plain fund in a bad month. Replace the exposure with an unleveraged fund if it does not fit your plan."})
            break
    mat = S.get("term_maturity")
    if mat:
        steps.append({"title": f"Decide what happens to your {CCY()} {plan['balances']['term_assets']:,.0f} of fixed-term holdings before {mat}", "amount": plan["balances"]["term_assets"],
                      "why": "At maturity you can roll it over at whatever rate is offered, or move part to equities. Check new rates a month before so you are not forced to decide on the day."})
    rows = []
    for r in w["rows"]:
        h = PL._hist(r["symbol"])
        rows.append({**r, "spark": [round(p["close"], 2) for p in h["points"][-90:]] if h else []})
    mood = ("Fearful", "bad", "VIX above 25: prices are swinging hard.") if vix and vix > 25 else ("Nervous", "warn", "VIX above 20: more swings than usual.") if vix and vix > 20 else ("Calm", "good", "VIX under 16: quiet markets.") if vix and vix < 16 else ("Steady", "good", "Volatility is ordinary.")
    nxt_pay = date.fromisoformat(nd[0]["pay"])
    return {"as_of": date.today().isoformat(), "weather": rows, "read": w["read"], "mood": {"label": mood[0], "tone": mood[1], "detail": mood[2]},
            "invest_usd": S["invest_usd"] or round(invest / PL.usd_rate()), "invest_label": S["invest_label"], "days_to_pay": (nxt_pay - date.today()).days, "reasons": reasons, "steps": steps, "next": nd,
            "headlines": headlines(), "holdings": holdings_watch(),
            "caveat": "This describes conditions and applies your own plan. It does not predict where prices go."}


def _override(name):
    return (plugins.get("INTEL_OVERRIDES") or {}).get(name)


_ideas, _briefing, _hours = ideas, briefing, trading_hours
ideas = lambda: (_override("ideas") or _ideas)()
briefing = lambda: (_override("briefing") or _briefing)()
trading_hours = lambda: (_override("trading_hours") or _hours)()
