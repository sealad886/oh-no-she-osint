# Validation evidence

Date: 2026-10-09. Environment: macOS, Python 3.14.8. Python 3.11+ is the declared
runtime floor; other interpreter versions and Windows have not been exercised.
No user acceptance or exhaustive security certification is claimed.

## Behavioral checks

42 standard-library unittest cases pass, including:

- Typed subjects, aliases, known identifiers, isolation and restart persistence.
- All four import formats, original-byte hashes, deduplication under concurrent
  imports, source offsets, literal searches and complete export.
- Co-occurrence counts, repeated mentions, balanced URL delimiters, URL filename
  exclusions and maximum supported collection query/identifier lengths.
- Backup roundtrip, exclusive output creation, symlink/mode rejection and schema
  mismatch rejection.
- Offline rejection before network/DNS, private/mixed DNS rejection, pinned IP/TLS,
  response bounds, redirect refusal and per-source failure preservation.
- Loopback authentication, browser-origin and Host checks, request limits, inert
  static assets and the complete browser API workflow.

Run `.venv/bin/python -m unittest discover -s tests -v`. The suite uses temporary
private directories, synthetic evidence and mocked providers. Loopback tests need
permission to bind a local socket; restricted sandboxes may require running outside
the sandbox. Compilation, JavaScript syntax and launcher shell syntax also pass.

## Live public-source checks

A separate temporary workspace exercised public historical subject **Ada Lovelace**
and reserved example domain **example.org**. Wikipedia and Wikidata each returned
10 candidates and persisted their original responses. DNS A/AAAA, registry RDAP,
verified TLS certificate and HTTPS website each succeeded and persisted evidence:
**six requested source checks, six successes**. No private target, credentials or
user case was used. This proves those integrations worked at that time, not future
provider availability or comprehensive coverage of people/organizations.

Actual in-app browser verification created both a person and an organization case,
preserved aliases/identifiers, searched candidate results, collected a candidate
page, imported fictional notes, inspected indicator provenance and original-text
fingerprints, and collected all four domain signals. The downloaded JSON contained
the organization case, five sources and original bytes for each. Browser console
reported no warnings/errors at the final check. Markdown export is covered by CLI,
engine and HTTP tests; a browser Markdown download was not separately exercised.

The temporary preview stores only public/synthetic fixtures outside the project.
It is not the user's persistent workspace. Screenshots capture the organization
workspace and source inspection. Live fixture evidence is separate from synthetic
network security tests.

## Independent review and repair ledger

Round 1 identified four P2 defects. Root-cause searches covered each central owner,
regional consumers and repository-wide sibling patterns before repairs. Graphify
and Codanna caller/callee/impact evidence informed those searches; source confirmed
dynamic HTTP dispatch that the index does not represent fully.

| Finding | Bounded repair | Verification |
| --- | --- | --- |
| Balanced URL parentheses were stripped | Central extractor distinguishes unmatched prose closers | Extraction/offset regression |
| Filenames and arbitrary suffixes became domains | Bundled IANA TLD check and URL authority spans | Host/path/noise regression; originals unchanged |
| Generated titles exceeded the 200-character contract | Shared bounded display title; full source/query retained | Long URL, 253-character domain, 200-character query |
| Repeated mentions multiplied related-query work | Join distinct source IDs before co-occurrence aggregation | Distinct-source regression + operational benchmark |

Operational observation: the 9,999-mention, approximately 120 KB fixture took
5.097 seconds to inspect before repair and 0.003529 seconds after repair on this
machine. Permanent tests assert source counts, not a brittle timing threshold.

Round 2 explicitly returned **clean: no actionable findings remain** on snapshot
`0199e561d3e8a96cf2f8dbb33ff53a3e1dde7025147dfe762c05f886c8a0bfa9`.
The reviewer independently reran all 42 tests and each defect reproduction. Source,
static assets, tests, launcher, manifest and bundled TLD snapshot were frozen during
review. Documentation received final validation evidence afterward. This is an
independent bounded review, not exhaustive security certification.

Snapshot algorithm: sorted relative paths of `local_osint/**/*.py`,
`local_osint/static/*`, `tests/*.py`, `local_osint/tlds.txt`, `start.sh`,
`pyproject.toml`; SHA-256 over each path + NUL + contents + NUL.
Bundled IANA snapshot SHA-256:
`d8596c734273ec6280d42c5feb4db8618e71dc485f64d483774817a781093d85`.
Graphify AST refresh completed: 222 nodes, 470 edges, 13 communities.

## Limits and release disposition

The app is a local single-user evidence engine. Discovery covers Wikipedia/Wikidata;
other subjects require known URLs/imports. Co-occurrence and candidate matches
never establish identity, ownership or truth. Security assumptions and residual
risks are documented in [SECURITY.md](../SECURITY.md).

Source execution is the validated installation path. A distributable wheel,
multiple Python versions, Windows ACL behavior and unattended service deployment
are not validated. There is no configured Git remote. External PR/CI/release checks
cannot run until an existing repository identity is supplied. Local review and
verification do not count as remote CI, publication or user acceptance.
