# Contributing

Use Python 3.11+ and a project-specific `.venv`; no global package installs.
Run the commands under Development in README before submitting a change.
Runtime uses only Python's standard library and static browser assets. Adding a
runtime dependency needs an actual requirement and a privacy/supply-chain assessment.

The engine owns case/evidence semantics. CLI and server must reuse it. Collectors
share public-address validation and pinned TLS transport. Never add a shortcut
that bypasses those boundaries. A new source must disclose outbound data, define
bounded request/response behavior, preserve provenance and expose source failures.
Tests should prove user-observable behavior and security/data-integrity invariants.
Never put actual investigation data, launch capabilities, credentials or local
machine paths in commits or test fixtures.

Use Conventional Commits. Work on focused branches. See AGENTS.md for agent
instructions. Publication and release are separate from a successful local build.

Optional distributable build: install a current setuptools and build frontend
inside a dedicated project environment, then build a wheel with that environment.
The wheel must include `local_osint/static/` and `local_osint/tlds.txt`. No wheel publication has occurred.
