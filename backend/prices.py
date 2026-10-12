"""Live data: ticker search, quotes, FX, and fund NAVs (NAV lookups come from a plugin: plugins.py, NAV_PROVIDER = {"search": fn(query), "nav": fn(code)})."""
import json, urllib.request, urllib.parse
import plugins

UA = {"User-Agent": "Mozilla/5.0 (wealth-tracker)"}
TIMEOUT = 9
YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{s}"
YF_SEARCH = "https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=10&newsCount=0"

def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())

def search_symbols(query):
    out = []
    try:
        d = _get(YF_SEARCH.format(q=urllib.parse.quote(query)))
        for q in d.get("quotes", []):
            if not q.get("symbol"): continue
            out.append({"symbol":q.get("symbol"),"name":q.get("shortname") or q.get("longname") or q.get("symbol"),
                        "exchange":q.get("exchDisp") or q.get("exchange") or "","type":(q.get("quoteType") or "").lower(),"currency":q.get("currency","")})
    except Exception: pass
    return out


def search_mutual_funds(query):
    fn = (plugins.get("NAV_PROVIDER") or {}).get("search")
    return fn(query) if fn else []


def nav(scheme_code):
    fn = (plugins.get("NAV_PROVIDER") or {}).get("nav")
    return fn(scheme_code) if fn else None


def quote(symbol):
    try:
        d = _get(YF_CHART.format(s=urllib.parse.quote(symbol)))
        m = d["chart"]["result"][0]["meta"]
        return {"price":m.get("regularMarketPrice"),"currency":m.get("currency","USD"),"name":m.get("longName") or m.get("shortName") or symbol,
                "exchange":m.get("fullExchangeName") or m.get("exchangeName") or "","prev_close":m.get("chartPreviousClose") or m.get("previousClose"),
                "high52":m.get("fiftyTwoWeekHigh"),"low52":m.get("fiftyTwoWeekLow")}
    except Exception: return None


def history(symbol, rng="3mo"):
    """Daily close-price series for charting. rng: 1mo/3mo/6mo/1y/5y."""
    if rng not in ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"):
        return None                      # the range goes into a URL: only known values
    interval = "1d" if rng not in ("1d", "5d") else "5m"
    try:
        url = YF_CHART.format(s=urllib.parse.quote(symbol)) + f"?range={rng}&interval={interval}"
        d = _get(url)
        res = d["chart"]["result"][0]
        ts = res.get("timestamp", [])
        closes = res["indicators"]["quote"][0].get("close", [])
        meta = res.get("meta", {})
        import datetime
        points = [{"date": datetime.datetime.utcfromtimestamp(t).date().isoformat(), "close": c}
                  for t, c in zip(ts, closes) if c is not None]
        return {"points": points, "currency": meta.get("currency", "USD"),
                "name": meta.get("longName") or meta.get("shortName") or symbol}
    except Exception:
        return None


def fx_to_base(currency):
    """How many units of the app currency (Settings > Currency) one unit of `currency` is worth."""
    from database import base_ccy
    base = base_ccy()
    if currency == base: return 1.0
    try:
        d = _get(YF_CHART.format(s=f"{currency}{base}=X"))
        return d["chart"]["result"][0]["meta"].get("regularMarketPrice")
    except Exception: return None




def news(query, count=6):
    """Recent headlines for a symbol or topic (Yahoo Finance search)."""
    import datetime
    try:
        d = _get(f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}&quotesCount=0&newsCount={count}")
        out = []
        for n in d.get("news", [])[:count]:
            t = n.get("providerPublishTime")
            out.append({"title": n.get("title"), "publisher": n.get("publisher"), "link": n.get("link"),
                        "date": datetime.datetime.utcfromtimestamp(t).date().isoformat() if t else ""})
        return out
    except Exception:
        return []
