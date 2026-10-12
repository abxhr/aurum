# Contributing

Issues and pull requests are welcome, especially **improvements to the generic statement readers** (`backend/statement_parse.py`) and anything that makes more layouts work without a plugin (a plugin file with `CARD_TXN_PARSERS` / `ACCOUNT_PARSERS`, see `backend/plugins.py`; `backend/test_parsers.py` shows the pattern).

## Ground rules
* **Never include real data**: no real statements, payslips, balances, names, account numbers or keys, in code, tests, fixtures or screenshots. Invent them. The built-in demo (`backend/demo.py`) is the model.
* Run the checks before opening a PR:
  ```bash
  python backend/test_security.py && python backend/test_basics.py && python backend/test_categorizer.py && python backend/test_parsers.py && python backend/test_repo_clean.py
  ```
* Keep it dependency-light: Flask, SQLite, vanilla JS. Pinned versions go in `requirements.txt`.
* Security-sensitive changes (`auth.py`, `security.py`, `authroutes.py`, `vault.py`, `webtools.py`) need a matching check in `test_security.py`.
* Amounts are stored in the app currency (the `*_base` fields mean "in the app currency"). Use `database.base_ccy()` in Python and `CUR` in JS, never a literal currency in user-facing text.

## Dev setup
`run.sh` / `run.bat` create `.venv` and start the app on port 5000. To work without touching your real data, set `PORTFOLIO_DB=/path/to/scratch.db` and `PORT=5099`, then use the demo login.
