"""Demo data: an invented person ("Morgan Ellis", a school administrator at the made-up Larkspur School District) with a year of cards, payslips, budget, holdings and bank activity,
all in US dollars, so the app can be shown to others and photographed for the docs. Every name, bank, card, merchant and number is invented, and the shape of the finances
(renting, a student loan, a retirement plan, index funds, two cards) is deliberately unlike any real user's. It is written to its own file (data/demo.db) and a demo session
only ever opens that file (see database.get_db)."""
import json
import os
import random
import threading
import time
from datetime import date, datetime, timedelta

import database as DB

VERSION = "7"
PERSONA = {"name": "Morgan", "age": 38, "gender": "", "nationality": "", "residency": "Lives in a made-up city", "employer": "Larkspur School District (employee)",
           "goals": [{"name": "Replace the car", "amount": 18000.0, "currency": "USD", "target_date": (date.today().replace(year=date.today().year + 2, month=9, day=1)).isoformat(), "notes": ""}],
           "investing_rules": "", "other_countries": "", "risk": "moderate", "style": "Plain language, short answers.", "notes": ""}
_LOCK = threading.Lock()

# category, subcategory, merchants, (low, high) USD, uses per month (mean)
SPEND = [
    ("Groceries", "Supermarket", ["PINEVIEW GROCERY", "SUNDAY FARMERS STAND", "BLUE HERON FOODS", "ORCHARD LANE MARKET"], (14, 95), 6),
    ("Groceries", "Convenience store", ["QUICKSTOP FOOD MART", "CORNERSHOP 24"], (4, 16), 4),
    ("Dining", "Cafe & coffee", ["LANTERN COFFEE BAR", "FIRST LIGHT BAKERY CAFE", "THE DAILY GRIND"], (4, 11), 7),
    ("Dining", "Restaurant", ["RIVERBEND PIZZERIA", "MAPLE & SALT DINER", "GOLDEN LOTUS KITCHEN", "ANCHOR BREWPUB"], (18, 85), 4),
    ("Dining", "Delivery", ["MEALHOP DELIVERY", "TAKEOUT TRAIN"], (14, 42), 3),
    ("Transport", "Fuel", ["NORTHSTAR FUEL", "BRIGHTWAY GAS & GO"], (32, 64), 3),
    ("Transport", "Public transit", ["CITY TRANSIT FARE", "REGIONAL RAIL TICKET"], (3, 24), 5),
    ("Transport", "Car services", ["EVERGREEN AUTO CARE", "QUICKLUBE EXPRESS"], (45, 260), 0.3),
    ("Shopping", "Online marketplace", ["PARCELPOINT ONLINE", "HOMEGOODS DIRECT ONLINE"], (18, 150), 3),
    ("Home & Furniture", "Hardware & repairs", ["HARDWARE HAVEN", "GREENTHUMB GARDEN CENTER"], (12, 130), 1),
    ("Shopping", "Books & stationery", ["PAGE TURNER BOOKS", "INKWELL STATIONERS"], (9, 48), 1),
    ("Health & Fitness", "Pharmacy", ["MAPLE DRUGSTORE", "HEALTHFIRST PHARMACY"], (7, 60), 2),
    ("Health & Fitness", "Clinic & hospital", ["LAKESIDE FAMILY CLINIC", "BRIGHT SMILE DENTAL"], (30, 210), 0.5),
    ("Entertainment", "Cinema & shows", ["ORPHEUM THEATRE", "SPOTLIGHT LIVE EVENTS"], (14, 90), 1.5),
    ("Shopping", "General retail", ["PAWS & CLAWS SUPPLY", "HAPPY TAILS VET"], (18, 120), 1),
]
MONTHLY = [   # fixed monthly charges: category, subcategory, description, amount, day offset in cycle
    ("Bills & Utilities", "Utilities", "RIVERVIEW ELECTRIC & GAS", (70, 160), 4),
    ("Bills & Utilities", "Mobile & internet", "FIBERLINK INTERNET", (65, 65), 9),
    ("Bills & Utilities", "Mobile & internet", "TALKWAVE MOBILE", (42, 42), 13),
    ("Subscriptions", "Streaming", "REELSTREAM PLUS", (17, 17), 11),
    ("Subscriptions", "Cloud storage", "NOTEBOX CLOUD STORAGE", (3, 3), 18),
    ("Health & Fitness", "Gym & fitness", "COMMUNITY POOL & FITNESS", (35, 35), 6),
]
TRIPS = [   # month offset (0 = this month), label, ccy, rate to USD, [(sub, description, local amount)]
    (3, "🇯🇵 Japan", "JPY", 0.0067, [("Flights", "PACIFIC WINGS TOKYO", 98000), ("Accommodation", "SAKURA BUSINESS HOTEL KYOTO", 142000), ("Dining", "IZAKAYA KURO OSAKA", 38000),
                                      ("Tours & activities", "KYOTO WALKING TOURS", 24000), ("Taxi & rides", "TOKYO METRO PASS", 12000)]),
    (7, "🇲🇽 Mexico", "MXN", 0.055, [("Flights", "SOLARIS AIR CANCUN", 7900), ("Accommodation", "CASA AZUL OAXACA", 18500), ("Dining", "MERCADO COCINA OAXACA", 4200), ("Tours & activities", "MONTE ALBAN GUIDES", 5800)]),
]
CARDS = [("Cedar Everyday Visa", "Cedar Credit Union", "2264", 6000, 5, "#4a7c8c", .70), ("Pinecone Travel Mastercard", "Pinecone Bank", "9035", 9000, 21, "#7a5c9c", .30)]


def _month_start(d, back):
    y, m = d.year, d.month - back
    while m <= 0:
        m += 12; y -= 1
    return date(y, m, 1)


def _clamp_day(y, m, day):
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, min(day, (nxt - timedelta(days=1)).day))


def _build():
    import analytics  # noqa: F401  (imports register nothing; kept so the build fails early if the app modules are broken)
    import agent as AG, bank_tx as BT, budget as BG, categorizer as CZ, feeds as FD, payslips as PS, rewards as RW, signals as SG, transactions as TX, wealth as WL, aiusage as AU
    DB.init_db()
    for fn in (BT.ensure, PS.ensure_tables, RW.ensure, BG.ensure_columns, CZ.ensure_columns, FD.ensure_table, AG.ensure_tables, WL._ensure, SG._ensure, TX._ensure_custom_table, AU._ensure):
        fn()
    rnd = random.Random(2026)
    today = date.today()
    now = datetime.utcnow().isoformat()
    with DB.get_db() as c:
        # ───────── settings ─────────
        for k, v in [("base_currency", "USD"), ("monthly_income", "4560"), ("pay_day", "28"), ("timezone", "America/Chicago"), ("home_country", "United States"), ("date_order", "mdy"), ("app_title", "Aurum"), ("auto_sync_enabled", "0"), ("demo_version", VERSION),
                     ("agent_profile", json.dumps(PERSONA)), ("ai_categorise", "0"),
                     ("plan_settings", json.dumps({"savings_rate": 4.2, "term_rate": 0.0, "emergency_months": 6.0, "buffer_months": 1.0, "assume": {"cash": [4.2, 0.0], "equity": [8, 16], "gold": [5.0, 15.0]},
                                                   "policy": {"equity": 100}, "tranches": 6.0, "card_info": {}, "alerts": [{"symbol": "VTI", "drop_pct": 10}], "holding_rates": {},
                                                   "auto_review": False, "savings_cap": 0.0, "term_maturity": "", "income_since": "",
                                                   "invest_usd": 300.0, "invest_label": "VTI (Total US Stock Market)"}))]:
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))
        for ccy, r in (("USD", 1.0), ("JPY", 0.0067), ("MXN", 0.055)):
            c.execute("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)", (ccy, r, today.isoformat()))
        for sym, name in (("VTI", "Vanguard Total Stock Market"), ("VXUS", "Vanguard Total International"), ("BND", "Vanguard Total Bond Market")):
            c.execute("INSERT INTO watchlist(symbol,name,added_at) VALUES (?,?,?)", (sym, name, now))

        # ───────── holdings and history ─────────
        H = [  # name, class, platform, geo, ccy, mode, symbol, units, last_price, manual_value, cost_basis, target, kind of history
            ("Checking", "cash", "Cedar Credit Union", "US", "USD", "manual", "", 0, 0, 4120.0, 0, 8, "cash"),
            ("Emergency Savings", "savings", "Cedar Credit Union", "US", "USD", "manual", "", 0, 0, 18500.0, 0, 20, "savings"),
            ("Series I Savings Bonds", "bond", "TreasuryDirect", "US", "USD", "manual", "", 0, 0, 9000.0, 0, 10, "gold"),
            ("VTI", "etf", "Index Brokerage", "US", "USD", "ticker", "VTI", 120, 262.4, 0, 25800.0, 28, "eq"),
            ("VXUS", "etf", "Index Brokerage", "Global", "USD", "ticker", "VXUS", 150, 63.1, 0, 8900.0, 12, "eq"),
            ("BND", "etf", "Index Brokerage", "US", "USD", "ticker", "BND", 60, 72.6, 0, 4500.0, 7, "eq"),
            ("401(k) Target Date 2050", "mutual_fund", "Employer plan", "US", "USD", "manual", "", 0, 0, 41200.0, 31000.0, 12, "eq"),
            ("Bitcoin", "crypto", "Self-custody wallet", "Global", "USD", "manual", "", 0, 0, 1800.0, 1200.0, 3, "eq"),
        ]
        fx = {"USD": 1.0}
        val_now, ids = {}, {}
        for i, (name, cls, plat, geo, ccy, mode, sym, units, lp, mv, cb, tp, hist) in enumerate(H):
            c.execute("""INSERT INTO holdings(name,asset_class,platform,geography,currency,price_mode,symbol,units,last_price,price_ccy,manual_value,cost_basis,target_pct,sort_order,created_at,updated_at,price_updated)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (name, cls, plat, geo, ccy, mode, sym, units, lp, ccy, mv, cb, tp, i, now, now, now))
            hid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            ids[name] = hid
            val_now[hid] = (mv if mode == "manual" else units * lp) * fx[ccy]
            H[i] = H[i][:-1] + (hid, hist)
        for (name, *_rest, hid, hist) in H:
            v = val_now[hid]
            walk, level = [], 1.0
            for m in range(18, -1, -1):
                level *= 1 + rnd.gauss(0.012, 0.035)
                walk.append(level)
            norm = walk[-1]
            for k, m in enumerate(range(18, -1, -1)):
                d = _month_start(today, m) if m else today
                if hist == "eq":
                    val = v * walk[k] / norm
                elif hist == "cash":
                    val = max(1500, v + rnd.uniform(-1500, 1500) * (m > 0))
                elif hist == "savings":
                    val = max(4000, v - m * 900 + rnd.uniform(-250, 250) * (m > 0))
                elif hist == "term":
                    if m > 8:
                        continue
                    val = v
                else:
                    val = v * (0.97 + 0.03 * (18 - m) / 18)
                c.execute("INSERT INTO snapshots(holding_id,snap_date,value_base) VALUES (?,?,?)", (hid, d.isoformat(), round(val, 2)))

        # ───────── cards, statements, transactions ─────────
        for name, bank, last4, limit, sday, color, _share in CARDS:
            c.execute("""INSERT INTO credit_cards(name,bank,last_four,currency,credit_limit,statement_day,due_days,card_color,pdf_password,notes,archived,created_at,updated_at,email_sender,email_subject,amount_label)
                         VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)""", (name, bank, last4, "USD", limit, sday, 25, color, "", "", now, now, "", "", ""))
        card_rows = c.execute("SELECT id, statement_day, name FROM credit_cards ORDER BY id").fetchall()
        shares = [x[6] for x in CARDS]
        trip_by_month = {}
        for off, label, ccy, rate, items in TRIPS:
            trip_by_month[_month_start(today, off)] = (label, ccy, rate, items)
        prev_total = {cr["id"]: 0.0 for cr in card_rows}
        for back in range(12, -1, -1):
            ms = _month_start(today, back)
            for cr, share in zip(card_rows, shares):
                sdate = _clamp_day(ms.year, ms.month, cr["statement_day"])
                if sdate > today:
                    continue
                pm = _month_start(sdate, 1)
                start = _clamp_day(pm.year, pm.month, cr["statement_day"]) + timedelta(days=1)
                span = max((sdate - start).days, 1)
                rows = []
                for cat, sub, merchants, (lo, hi), mean in SPEND:
                    for _ in range(max(0, round(rnd.gauss(mean * share * 0.95, 1.0)))):
                        rows.append((start + timedelta(days=rnd.randrange(span)), rnd.choice(merchants), round(rnd.uniform(lo, hi), 2), cat, sub, "", 0, 0, ""))
                if share > .5:
                    for cat, sub, desc, (lo, hi), off in MONTHLY:
                        rows.append((start + timedelta(days=min(off, span - 1)), desc, round(rnd.uniform(lo, hi), 2), cat, sub, "", 0, 0, ""))
                if rnd.random() < .22 * share * 2:
                    rows.append((start + timedelta(days=rnd.randrange(span)), rnd.choice(["VOLT & WIRE ELECTRONICS", "TECHNOOK OUTLET"]), round(rnd.uniform(60, 520), 2), "Electronics", "Gadgets", "", 0, 0, ""))
                trip = trip_by_month.get(_month_start(sdate, 0)) if share >= .58 else None
                if trip:
                    label, ccy, rate, items = trip
                    tname = f"{sdate.strftime('%b %Y')} · {label}"
                    for sub, desc, local in items:
                        cat = "Travel" if sub in ("Flights", "Accommodation", "Tours & activities") else ("Dining" if sub == "Dining" else "Transport")
                        rows.append((start + timedelta(days=rnd.randrange(span)), desc, round(local * rate * 1.027, 2), cat, sub, tname, local, rate * 1.027, ccy))
                total = round(sum(r[2] for r in rows), 2)
                if not rows:
                    continue
                paid = back > 0 or (today - sdate).days > 25
                due = sdate + timedelta(days=25)
                c.execute("""INSERT INTO card_statements(card_id,statement_date,due_date,total_due,minimum_due,paid_amount,paid_date,status,source,email_subject,email_snippet,notes,created_at)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (cr["id"], sdate.isoformat(), due.isoformat(), total, round(max(100, total * .05), 2), total if paid else 0,
                                                                     (due - timedelta(days=rnd.randrange(3, 9))).isoformat() if paid else "", "paid" if paid else "unpaid", "email",
                                                                     f"Your {cr['name']} statement", "", "", now))
                sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                for d, desc, amt, cat, sub, trip_name, fxa, fxr, fxc in sorted(rows):
                    c.execute("""INSERT INTO statement_transactions(statement_id,card_id,txn_date,description,amount,category,created_at,fx_currency,fx_amount,fx_rate,subcategory,trip,trip_manual)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", (sid, cr["id"], d.isoformat(), desc, amt, cat, now, fxc, fxa, fxr, sub, trip_name, ""))
                if prev_total[cr["id"]]:
                    c.execute("""INSERT INTO statement_transactions(statement_id,card_id,txn_date,description,amount,category,created_at,subcategory,trip,trip_manual)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""", (sid, cr["id"], (start + timedelta(days=2)).isoformat(), "PAYMENT RECEIVED - THANK YOU", -prev_total[cr["id"]], "Payments & Transfers", now, "Card payment", "", ""))
                prev_total[cr["id"]] = total

        # ───────── payslips ─────────
        for back in range(12, 0, -1):
            ms = _month_start(today, back - 1) if today.day >= 26 else _month_start(today, back)
            raise_pct = 1.06 if back <= 6 else 1.0
            earn = [("Base Pay", round(4500 * raise_pct)), ("Stipend", 250)]
            ded = [("Retirement 401(k) contribution", 350), ("Health Insurance", 110)]
            gross, dd = sum(a for _, a in earn), sum(a for _, a in ded)
            period = ms.strftime("%Y-%m")
            c.execute("INSERT OR IGNORE INTO payslips(period,subject,filename,gross,deductions,net,currency,ok,notes,raw_text,created_at) VALUES (?,?,?,?,?,?,?,1,'','',?)",
                      (period, f"Payslip {period}", f"payslip-{period}.pdf", gross, dd, gross - dd, "USD", now))
            pid = c.execute("SELECT id FROM payslips WHERE period=?", (period,)).fetchone()[0]
            for lbl, a in earn:
                c.execute("INSERT INTO payslip_items(payslip_id,kind,label,amount) VALUES (?,?,?,?)", (pid, "earning", lbl, a))
            for lbl, a in ded:
                c.execute("INSERT INTO payslip_items(payslip_id,kind,label,amount) VALUES (?,?,?,?)", (pid, "deduction", lbl, a))

        # ───────── budget ─────────
        B = [("income", "Salary", 4560, "income", "monthly", "payslip_typical", ""), ("expense", "Rent", 1450, "expense", "monthly", "manual", "__offcard"),
             ("expense", "Student loan", 320, "expense", "monthly", "manual", "__offcard"), ("expense", "Groceries & dining", 480, "expense", "monthly", "manual", "Dining,Groceries"),
             ("expense", "Transport & car", 170, "expense", "monthly", "manual", "Transport"), ("expense", "Shopping & home", 200, "expense", "monthly", "manual", "Shopping,Home & Furniture"),
             ("expense", "Travel", 400, "expense", "monthly", "manual", "Travel,__lump"),
             ("expense", "Health, fun & bills", 380, "expense", "monthly", "manual", "Health & Fitness,Entertainment,Bills & Utilities,Subscriptions"),
             ("invest", "Index funds (VTI, VXUS, BND)", 300, "invest", "monthly", "manual", ""), ("invest", "Retirement 401(k)", 350, "invest", "monthly", "manual", "")]
        for i, (cat, lbl, amt, typ, freq, src, cats) in enumerate(B):
            c.execute("INSERT INTO budget_items(category,label,amount,currency,type,sort_order,active,frequency,source,categories) VALUES (?,?,?,?,?,?,1,?,?,?)", (cat, lbl, amt, "USD", typ, i, freq, src, cats))

        # ───────── the bank account ─────────
        bal = 3300.0
        rows = []
        for back in range(6, -1, -1):
            ms = _month_start(today, back)
            for day, desc, deb, cred, kind, who in [(1, "RENT PAYMENT ASHFORD PROPERTIES", 1450, 0, "other_out", "Landlord"), (3, "TRANSFER TO INDEX BROKERAGE", 300, 0, "investing", "Index Brokerage"),
                                                    (8, "STUDENT LOAN PAYMENT NORTHBRIDGE SERVICING", 320, 0, "other_out", "Loan servicer"), (10, "CARD PAYMENT CEDAR EVERYDAY VISA", round(rnd.uniform(1150, 1450), 2), 0, "own", "Own card"),
                                                    (12, "TRANSFER TO EMERGENCY SAVINGS", 400, 0, "own", "Own account"), (15, "PEER PAYMENT FROM J OKAFOR", 0, rnd.choice([25, 45, 60]), "from_person", "J Okafor"),
                                                    (17, "ATM WITHDRAWAL ELM STREET", rnd.choice([60, 80, 100]), 0, "cash", ""), (22, "CARD PAYMENT PINECONE TRAVEL", round(rnd.uniform(350, 600), 2), 0, "own", "Own card"),
                                                    (27, "MONTHLY MAINTENANCE FEE", 3.0, 0, "fee", ""), (28, "PAYROLL LARKSPUR SCHOOL DISTRICT", 0, 4560, "salary", "Larkspur School District")]:
                d = _clamp_day(ms.year, ms.month, day)
                if d <= today:
                    rows.append((d, desc, deb, cred, kind, who))
        for d, desc, deb, cred, kind, who in sorted(rows, key=lambda r: r[0]):
            bal = round(bal - deb + cred, 2)
            c.execute("INSERT OR IGNORE INTO bank_transactions(bank,account,txn_date,description,debit,credit,balance,kind,counterparty,user_kind) VALUES ('Cedar Credit Union','0017',?,?,?,?,?,?,?,?)",
                      (d.isoformat(), desc, deb, cred, bal, kind, who, kind))
        c.execute("UPDATE holdings SET manual_value=? WHERE name='Checking'", (max(bal, 1500),))

        # needs-vs-wants: normally sorted by the AI, which a demo does not use, so sort the invented merchants here
        needs = {"Groceries", "Transport", "Health & Fitness", "Bills & Utilities", "Government & Fines", "Education", "Personal Care"}
        for desc, cat in c.execute("SELECT DISTINCT description, category FROM statement_transactions WHERE amount>0").fetchall():
            c.execute("INSERT OR IGNORE INTO merchant_need VALUES (?,?,0.9,'ai')", (CZ.merchant_key(desc), "need" if cat in needs else "want"))


def build():
    """Write a fresh demo database. Runs in its own thread so that the demo flag does not leak into the caller's request."""
    tmp = DB.DEMO_PATH + ".new"
    for p in (tmp, tmp + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    err = []

    def go():
        old = DB.DEMO_PATH
        DB.DEMO_PATH = tmp
        DB.set_demo(True)
        try:
            _build()
        except Exception as e:  # noqa: BLE001
            err.append(e)
        finally:
            DB.DEMO_PATH = old
    t = threading.Thread(target=go)
    t.start(); t.join()
    if err:
        raise err[0]
    for attempt in range(10):                                   # a request may have the old file open for a moment
        try:
            os.replace(tmp, DB.DEMO_PATH)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(.3)


def ensure_fresh(max_age_h=20):
    with _LOCK:
        p = DB.DEMO_PATH
        stale = not os.path.exists(p) or (datetime.now().timestamp() - os.path.getmtime(p)) > max_age_h * 3600
        if not stale:
            try:
                import sqlite3
                with sqlite3.connect(p) as _c:
                    v = _c.execute("SELECT value FROM settings WHERE key='demo_version'").fetchone()
                _c.close()
                stale = not v or v[0] != VERSION
            except Exception:  # noqa: BLE001
                stale = True
        if stale:
            build()
