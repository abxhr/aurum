"""Run: python backend/test_basics.py. On a scratch seeded DB (no payslips, no card data): the shared monthly numbers add up,
and the savings-plan form round-trips into the budget lines every page reads."""
import os, tempfile
os.environ["PORTFOLIO_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
import database
database.init_db(); database.seed_if_empty()
import app  # noqa: F401  (loads the agent globals the planner reads)
import planner as PL, budget as BG

with database.get_db() as c:                      # a fresh install is empty, so give the budget something to report
    for lab, typ, amt in (("Salary", "income", 20000), ("Index fund", "invest", 2100), ("Second fund", "invest", 2000), ("Home fund", "savings", 2800), ("Buffer", "savings", 1000)):
        c.execute("INSERT INTO budget_items(category,label,amount,currency,type,sort_order) VALUES (?,?,?,?,?,0)", (typ if typ != "income" else "income", lab, amt, "USD", typ))
B = PL.basics()
assert B["flow_typical"] == B["net_typical"] - B["spend"] - B["saved"], B
assert B["spend"] == B["planned_spend"], "with no card data, spending falls back to the plan"

r = PL.save_requirements({"invest_usd": 1000, "invest_label": "VTI", "save_base": 500, "emergency_months": 6, "buffer_months": 4, "savings_rate": 6.5,
                          "goals": [{"name": "Marriage", "amount": 100000, "date": "2028-04-01"}], "term_maturity": "2027-06-01"})
assert r["invest_base"] == 1000 and r["save_base"] == 500 and r["goals"][0]["amount"] == 100000 and r["term_maturity"] == "2027-06-01", r
inv = [i for i in BG.list_items() if i["type"] == "invest"]
assert abs(inv[0]["monthly"] - 1000) < 0.01, inv       # the same line the Budget and Overview read
assert r["other_base"] == 3000, r                          # the seed's other invest and savings lines are reported, not silently ignored
try:
    PL.save_requirements({"goals": [{"name": "x", "amount": 5, "date": "nope"}]}); raise SystemExit("bad date accepted")
except ValueError:
    pass
print("basics ok")
