"""Run: python backend/test_parsers.py. The generic readers (statement lines, CSV, labelled fields, payslips, rewards), CSV import end to end, and the plugin hooks.
Every sample is invented. A scratch database and a scratch plugin folder are used, so this checks exactly what a fresh checkout does."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PLUG = tempfile.mkdtemp()
os.environ["PORTFOLIO_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["PORTFOLIO_PARSERS_DIR"] = PLUG
open(os.path.join(PLUG, "mine.py"), "w").write('''
import re
CARD_TXN_PARSERS = {"Odd Bank": lambda text, d: [{"txn_date": "2026-01-02", "description": "FROM PLUGIN", "amount": 5.0}]}
CATEGORY_RULES = [("Charity", "Donations", r"zzcause")]
TRANSFER_RULES = lambda d, debit, credit, family, me: "education" if "zzschool" in d else None
FAMILY_LABEL_WORDS = ["zzkin"]
''')
import database
database.init_db()
database.seed_if_empty()
import statement_parse as SP
import transactions as TX
import cards as C
import bank_tx as BT
import payslips as PS
import rewards as RW
import categorizer as CZ
import budget as BG

D = "2026-07-31"
# ── card statement lines, in several layouts
txt = """07/07/2026 08/07/2026 GREENLEAF MARKET 56.00
08/07/2026 08/07/2026 PAYMENT RECEIVED - THANK YOU 1,220.00CR
12 Jul 14 Jul ACME COFFEE ROASTERS 4.50
Jul 15 HARBOR HARDWARE 19.99
03/08 04/08 SKYWAY AIR TICKETS USD 40.63 150.10
Page 2 of 3
Total new balance 999.00"""
r = SP.parse_lines(txt, D)
assert [x["description"] for x in r] == ["GREENLEAF MARKET", "PAYMENT RECEIVED - THANK YOU", "ACME COFFEE ROASTERS", "HARBOR HARDWARE", "SKYWAY AIR TICKETS"], r
assert r[0]["amount"] == 56 and r[1]["amount"] == -1220 and r[2]["txn_date"] == "2026-07-12" and r[4]["fx_currency"] == "USD" and r[4]["fx_amount"] == 40.63 and r[4]["amount"] == 150.1
neg = SP.parse_lines("2026-07-01 SHOP ONE -20.00\n2026-07-02 SHOP TWO -35.50\n2026-07-05 REFUND 5.00\n")     # a file that prints purchases as negatives
assert [x["amount"] for x in neg] == [20.0, 35.5, -5.0]
assert TX.extract_transactions("Unknown Bank", "05/08/2026 FOO SHOP 12.30\n", D)[0]["amount"] == 12.3
# ── CSV
c1 = SP.parse_csv("Transaction Date,Post Date,Description,Category,Type,Amount\n13/07/2026,14/07/2026,SHOP ONE,Food,Sale,-20.00\n14/07/2026,14/07/2026,SHOP TWO,Food,Sale,-5.50\n15/07/2026,15/07/2026,PAYMENT,,Payment,100.00\n")
assert c1["spend_is_negative"] and [x["amount"] for x in c1["rows"]] == [20.0, 5.5, -100.0] and c1["rows"][0]["txn_date"] == "2026-07-13", c1
c2 = SP.parse_csv("Date;Details;Debit;Credit;Balance\n2026-07-01;RENT;1200.00;;800.00\n2026-07-02;SALARY;;3000,00;3800.00\n", "account")
assert c2["rows"][0]["debit"] == 1200 and c2["rows"][1]["credit"] == 3000 and c2["rows"][1]["balance"] == 3800
try:
    SP.parse_csv("a,b\n1,2\n")
    raise SystemExit("bad csv accepted")
except ValueError as e:
    assert "date and amount" in str(e)
# ── account statement lines: debit or credit from the balance movement
acc = SP.parse_account_lines("01/07/2026 RENT PAYMENT 1,200.00 2,800.00\n02/07/2026 PAYROLL ACME 3,000.00 5,800.00\n03/07/2026 GROCERIES 80.10 5,719.90\n", D)
assert [(x["debit"], x["credit"]) for x in acc] == [(1200.0, 0.0), (0.0, 3000.0), (80.1, 0.0)]
# ── statement summary: labelled fields, any bank
sm = C.extract_statement_from_email({"bank": "X"}, "", "total amount due: usd 1,234.56 minimum payment due: $35.00 payment due date: 31 oct 2026")
assert sm == {"total_due": 1234.56, "minimum_due": 35.0, "due_date": "2026-10-31"}, sm
assert C.extract_statement_from_email({"bank": "X"}, "", "New Balance 2,000.10  Minimum Payment Due 40.00 Due Date: Nov 12, 2026")["due_date"] == "2026-11-12"
# ── payslip: one-column layout, printed net
earn, ded = PS._flat_items("Earnings\nBase pay 4,000.00\nBonus 500.00\nTotal 4,500.00\nDeductions\nTax 800.00\nHealth plan 120.00\nNet pay 3,580.00")
assert earn == [("Base pay", 4000.0), ("Bonus", 500.0)] and ded == [("Tax", 800.0), ("Health plan", 120.0)], (earn, ded)
# ── rewards: generic labels
rw = RW.parse("Any Bank", "Cashback opening balance: 10.00\nCashback earned: 25.50\nCashback closing balance: 35.50")
assert rw["earned"] == 25.5 and rw["closing"] == 35.5 and rw["opening"] == 10
# ── bank classification, generic
k = lambda d, deb, cre, fam=(), me=(): BT.kind_of(d, deb, cre, fam, me)
assert k("PAYROLL ACME INC", 0, 3000) == "salary" and k("ATM WITHDRAWAL MAIN ST", 100, 0) == "cash" and k("MONTHLY SERVICE FEE", 5, 0) == "fee"
assert k("TRANSFER TO BROKERAGE ACCOUNT", 300, 0) == "investing" and k("CREDIT CARD PAYMENT", 900, 0) == "own" and k("ZELLE FROM PAT", 0, 40) == "from_person"
assert k("TRANSFER TO JAMIE LEE", 100, 0, fam=("jamie lee",)) == "family" and k("TRANSFER TO RIVER STONE", 100, 0, me=("river stone",)) == "own"
assert k("DIVIDEND PAYMENT", 0, 12) == "interest"
# ── plugins: a bank's own parser wins for that bank, rules and transfer rules extend the built-ins, others still use the generic reader
assert TX.extract_transactions("Odd Bank", "anything", D)[0]["description"] == "FROM PLUGIN"
assert TX.extract_transactions("Other Bank", "05/08/2026 FOO SHOP 12.30\n", D)[0]["description"] == "FOO SHOP"
assert CZ.classify("ZZCAUSE FUND")[:2] == ("Charity", "Donations") and CZ.classify("STARBUCKS 1")[0] == "Dining"
assert BT.kind_of("zzschool fee", 50, 0) == "education" and BT.kind_of("PAYROLL X", 0, 9) == "salary"
assert BG._is_family("support for zzkin") and BG._is_family("family support") and not BG._is_family("groceries")
# ── CSV import end to end: a card and a bank account
import json
with database.get_db() as conn:
    conn.execute("INSERT OR REPLACE INTO settings VALUES ('date_order','dmy')")
cid = C.add_card({"name": "Test Visa", "bank": "Test Bank", "last_four": "1234", "statement_day": 5})
res = C.import_csv(cid, "Date,Description,Amount\n03/06/2026,GROCERY MART,-40.00\n20/06/2026,CINEMA NIGHT,-25.00\n02/07/2026,PAYMENT,60.00\n")
assert res["transactions"] == 3 and res["statements"] == 2, res
assert sum(1 for t in TX.get_spend_transactions(days_back=4000)) >= 2
b = BT.import_csv("Date,Description,Debit,Credit,Balance\n2026-06-01,PAYROLL ACME,,3000.00,4000.00\n2026-06-02,CITY MARKET,50.00,,3950.00\n", "Test account")
assert b["read"] == 2 and b["added"] == 2 and BT.import_csv("Date,Description,Debit,Credit,Balance\n2026-06-01,PAYROLL ACME,,3000.00,4000.00\n", "Test account")["added"] == 0
assert BT.summary()["totals"].get("salary") == -3000
BT.save_sources([{"name": "Mine", "sender": "statements@example.com", "subject": "Statement"}, {"name": "", "sender": ""}])
assert [s["name"] for s in BT.list_sources()] == ["Mine"]
print("parsers ok")
