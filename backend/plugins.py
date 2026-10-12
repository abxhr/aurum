"""Optional local parser plugins.

The repository ships only generic readers (heuristic statement lines, CSV import, labelled fields). Anything specific to one bank, card, employer or country belongs in a plain
Python file you put in `data/parsers/` (git-ignored, so it never leaves your machine). Every `*.py` there is imported once at start-up and may define any of these names;
dicts are merged, lists are appended, functions are registered:

  CARD_TXN_PARSERS      {"Bank name": fn(pdf_text, statement_date_iso) -> [{"txn_date", "description", "amount"[, "fx_currency", "fx_amount", "fx_rate"]}]}   amount > 0 = spend
  CARD_SUMMARY_PARSERS  {"Bank name": fn(text_lowercase) -> {"total_due", "minimum_due", "due_date"} | None}
  CARD_SUMMARY_PATTERNS {"Bank name": {"total": [regex], "minimum": [regex], "due_date": [regex]}}      first capture group is the value
  BANK_EMAIL_SENDERS    {"Bank name": ["sender@example.com", ...]}
  BANK_COLORS           {"Bank name": "#rrggbb"}
  ACCOUNT_PARSERS       {"name": {"sender": str, "subject": str, "parse": fn(pdf_text) -> [{"txn_date","description","debit","credit","balance","account"}]}}
  ACCOUNT_BALANCE_PARSER fn(pdf_text) -> {"current": float|None, "savings": float|None}
  TRANSFER_RULES        fn(description_lowercase, debit, credit, family_names, my_names) -> kind | None     run before the built-in classifier
  REWARD_PARSERS        {"Bank name": fn(text) -> {...}}
  CATEGORY_RULES        [(category, sub_category, regex), ...]     tried before the built-in rules
  CATEGORY_SEED         {"lowercase merchant fragment": (category, sub_category)}
  PAYSLIP_EXTRA         module-level hooks used by payslips.py (see there)
  MARKET_FUNDS          [(symbol, name, role, note), ...]   funds the Market page compares
"""
import glob
import importlib.util
import logging
import os

DIR = os.environ.get("PORTFOLIO_PARSERS_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "parsers")
_cache = None
_log = logging.getLogger("plugins")


def _load():
    global _cache
    if _cache is not None:
        return _cache
    reg = {}
    for path in sorted(glob.glob(os.path.join(DIR, "*.py"))):
        try:
            spec = importlib.util.spec_from_file_location("local_parser_" + os.path.basename(path)[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:                                  # a broken plugin must never stop the app
            _log.exception("could not load parser plugin %s", path)
            continue
        for name in dir(mod):
            if not name.isupper() or name.startswith("_"):
                continue
            val = getattr(mod, name)
            if isinstance(val, dict):
                reg.setdefault(name, {}).update(val)
            elif isinstance(val, list):
                reg.setdefault(name, []).extend(val)
            else:
                reg[name] = val
    _cache = reg
    return reg


def get(name, default=None):
    v = _load().get(name)
    return default if v is None else v


def reload():
    global _cache
    _cache = None
    return _load()
