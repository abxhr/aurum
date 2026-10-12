# Security policy

## Reporting a vulnerability
Please **do not open a public issue**. Use [GitHub private vulnerability reporting](https://github.com/abxhr/aurum/security/advisories/new). Include what you found, how to reproduce it and the impact. Expect a first reply within a week.

## Scope
Aurum is a single-user app that runs on your own computer. In scope: bypassing sign-in, CSRF or DNS-rebinding protection, reading secrets or another session's data, SSRF through the web-research tools, script injection, and anything that can make the assistant change data without approval. Out of scope: attacks that need local admin on the machine, a compromised OS, or a user who exposes the port to the internet against the docs.

## Design and tests
The protections, threat model and your own responsibilities are described at <https://abxhr.github.io/aurum/security.html>. `python backend/test_security.py` exercises them; `python backend/test_repo_clean.py` guards the repository against committed secrets.

## If you fork this
Never commit `.env`, `data/`, `*.db`, `credentials.json` or key files (the `.gitignore` excludes them). If a key is ever committed, revoke it at the provider first; removing it from git history is not enough.
