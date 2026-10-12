"""Run: python backend/test_repo_clean.py
Fails if any file git would commit contains something key-shaped, a private key, a personal-looking e-mail address, or if a secret-bearing file is tracked.
Pattern based on purpose: no names or addresses are listed here, so this file cannot leak what it guards."""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATTERNS = {
    "OpenAI/Anthropic key": r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}",
    "GitHub token": r"gh[pousr]_[A-Za-z0-9]{30,}",
    "AWS key": r"AKIA[0-9A-Z]{16}",
    "Google key": r"AIza[0-9A-Za-z_-]{30,}",
    "Slack token": r"xox[abprs]-[A-Za-z0-9-]{10,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "DPAPI/Fernet blob": r"(?:dpapi1|fernet1):[A-Za-z0-9+/=_-]{20,}",
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
OK_EMAIL = re.compile(r"(example\.(com|org)|yourbank\.com|you@gmail\.com|noreply|users\.noreply\.github\.com)", re.I)
BAD_FILES = re.compile(r"(^|/)(\.env|credentials\.json|token\.json|.*\.db|.*\.key|.*\.pem)$")
SKIP = re.compile(r"(apexcharts\.min\.js|\.woff2|\.png|\.svg|\.ico)$")


def files():
    out = subprocess.run(["git", "ls-files", "--others", "--cached", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def main():
    bad = []
    for f in files():
        if BAD_FILES.search(f):
            bad.append(f"{f}: a secret-bearing file would be committed")
            continue
        if SKIP.search(f):
            continue
        try:
            text = open(os.path.join(ROOT, f), encoding="utf-8").read()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for name, pat in PATTERNS.items():
            if re.search(pat, text):
                bad.append(f"{f}: looks like a {name}")
        for m in EMAIL.finditer(text):
            if not OK_EMAIL.search(m.group(0)) and f != "backend/test_repo_clean.py":
                bad.append(f"{f}: e-mail address {m.group(0)}")
    if bad:
        print("\n".join(bad))
        sys.exit(1)
    print("repo clean: no keys, tokens, private keys or personal addresses")


if __name__ == "__main__":
    main()
