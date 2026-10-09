# Validation evidence

Date: 2026-10-09. Environment: macOS, Python 3.14.8. Python 3.11+ is the declared
runtime floor; other interpreter versions and Windows have not been exercised.
No user acceptance or exhaustive security certification is claimed.

## Behavioral checks

The initial engine and launcher checks comprised 46 standard-library unittest
cases. The automatic-investigation correction below brings the suite to 73.
Initial checks include:

- Canonical launcher bootstrap in a fresh project with spaces, browser-link
  handoff and authenticated loopback session, automatic default port, explicit
  occupied-port errors and offline override.
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

The app is a local single-user evidence engine. The original automatic collector
covered Wikipedia/Wikidata, recent GDELT news and public domain signals. The general
web-search correction below adds indexed public websites for ordinary subjects.
Sparse results may need public context, aliases or known public URLs. Co-occurrence and candidate matches
never establish identity, ownership or truth. Security assumptions and residual
risks are documented in [SECURITY.md](../SECURITY.md).

Source execution is the validated installation path. A distributable wheel,
multiple Python versions, Windows ACL behavior and unattended service deployment
are not validated. There is no configured Git remote. External PR/CI/release checks
cannot run until an existing repository identity is supplied. Local review and
verification do not count as remote CI, publication or user acceptance.

## Automatic investigation and local AI, 2026-10-09

The user correctly identified that the manual-first baseline failed click-to-run
acceptance. The primary path now starts collection from a subject name, displays
progress and produces a sourced dossier without manual import. Optional local AI
is supplied five bounded tools by the app; no separate tool server is needed.

Final full suite: **73 tests passed in 7.445 seconds**. Added regressions prove
name-only collection and provenance, offline/unknown-case rejection before DNS,
partial failure and cancellation retention, request/time limits, single-job
reservation, interrupted-run recovery, known-identifier priority, case/URL/domain
tool restrictions, statement rank and qualifiers, temporal precision in JSON,
Markdown and model input, required evidence reads and explicit AI truncation.
Python compilation, JavaScript syntax, launcher shell syntax and diff whitespace
checks passed. Two SQLite ResourceWarnings originate in test fixtures; no product
failure was established by those warnings.

Actual canonical browser runs used only public demonstration subjects:

| Subject | Collected external source IDs | Successful / attempted collection steps | Request units |
| --- | --- | --- | --- |
| Ada Lovelace | 6 | 6 / 7 | 7 |
| Mozilla | 10 | 10 / 11 | 12 |

Namesake profiles remained separate unconfirmed candidates. Automatic domain
research used only the first selected profile unless a known identifier supplied
the domain. Mozilla DNS, RDAP, certificate and website collection succeeded.
Ada dates showed declared day precision; Firefox historical claims displayed end
qualifiers with month/day precision. GDELT was unavailable during these runs:
direct checks observed HTTP 429, and some requests timed out. The UI retained
successful evidence and reported partial coverage. Successful live news-article
collection is not claimed; synthetic tests cover metadata retention and article
failure. These observations prove integrations at that time, not future availability.

Native model validation used installed llama.cpp **0.6.0, build 11429,
commit d81235049** and the already-cached REDCELL I-Compact GGUF
(**14,651,485,440 bytes**). No model or package download was performed. Owned
fixture runs confirmed actual tool calling, a required evidence read and a normal
completed draft. Those fixture runs are separate from real-provider verification.

In the actual browser, the app started its own local model process. The final
Mozilla investigation made one successful `read_evidence` model tool call and
displayed a sourced, unverified draft. Browser warnings/errors were empty at the
final check. Earlier actual JSON and Markdown downloads of the same workflow
passed: JSON retained original bytes, ten collected source IDs, a normal model
finish reason and citation IDs belonging to the run; Markdown included the dossier,
AI draft and historical claim qualifiers. An additional final JSON download check
stalled and was interrupted; it is not counted as a successful check. The completed
final browser dossier and screenshot remain separate evidence of the runtime flow.

Independent automatic-feature review used three rounds, with root-cause searches
across central owners and sibling consumers before each repair:

| Round | Finding | Resolution |
| --- | --- | --- |
| 1 | Rank/qualifiers were lost; historical website leads could be followed | Preserve bounded rank/qualifiers, exclude deprecated statements and ended website leads |
| 1 | Model truncation looked like a completed draft | Preserve finish reason, mark truncated/limited drafts and partial runs |
| 2 | Year-only timestamps displayed unsupported month/day fields | Shared precision-aware temporal projection for main claims and qualifiers |
| 3 | No actionable P1/P2 finding within bounded scope | Independently reran all 73 tests in 5.800 seconds; whitespace check passed |

Round 3 independently verified frozen snapshot
`c44add628282605733961b6179156e666657045bbf58a32a596c367046eec4a3`.
Its algorithm hashes sorted tracked and nonignored untracked files, relative path
+ NUL + contents + NUL. Source/tests remained unchanged after review; final
documentation receipts were appended afterward. Graphify AST refresh completed
with 344 nodes, 814 edges and 17 communities. Review is bounded source/synthetic
verification; it is not exhaustive security certification or user acceptance.

Delivery remains medium-feature-change, medium scale and high risk at the
untrusted-URL/model-tool boundaries. Existing design, acceptance, implementation,
quality, security, documentation and independent-review owners were reused. No
schema migration was needed. Retrospective outcome: launch/import success did not
prove the user's intended product. Future automatic-investigation acceptance must
exercise name-to-dossier collection and, when enabled, actual local model tools.
No remote, PR, remote CI, publication or release was created. Windows, additional
Python versions and other GGUF model families remain unvalidated.

## Canonical launcher repair, 2026-10-09

User reported `./start.sh` exiting with the generic local-operation error. The exact
command reproduced exit 1 because another local application occupied port 8765.
Earlier preview validation selected random ports and the launcher check only used
`--help`, so it missed the documented default startup path.

CLI settings now request port 0 by default, letting the OS select a free loopback
port. Explicit TOML/command ports retain their intended meaning. An explicit busy
port reports the port number and recommends `--port 0`; unrelated listeners are
never stopped. Storage, permission checks, authentication and loopback binding are
unchanged. No data/config migration is needed; configured fixed ports are explicit
user choices. Rollback is the bounded source change, not a database operation.

Two new CLI regressions failed before the repair and pass afterward. Two launcher
integration tests execute the real script, create fresh project virtual environments
without package downloads, exercise paths with spaces and a different working
directory, capture browser handoff through a local test helper, unlock the session,
and verify explicit occupied-port failure plus offline override. Each test owns and
cleans up its temporary process and data. These tests do not open an actual GUI.

The repaired canonical `./start.sh` also ran in the user's actual checkout and
started on port 51256 while the existing port-8765 service remained running. The
actual in-app browser displayed the unlocked empty workspace and no console
warnings/errors. Existing user environment/evidence was preserved. All 46 tests,
Python compilation, shell syntax and diff whitespace checks pass. Graphify refreshed
to 239 nodes, 518 edges, 15 communities.

Delivery route: small-feature-change, small scale, low risk, change authority.
Required context/acceptance/implementation/quality owners reused the existing design
and evidence. Design, documentation, security/operations and independent review
activate for the changed startup contract and its local privacy boundary. Planning,
release and external coordination are not applicable to this bounded local repair.
Retrospective activates from the observed escaped launcher failure: future startup
claims must execute the canonical command and authenticate its resulting session,
not substitute a random-port preview or `--help` check.

Independent launcher-fix review returned clean against base `35de2f3` and snapshot
`46710cd40d80fa726ee5e0d013fd8bd29b3589727f825e44ee9cc9bde5fba919`.
The reviewer independently reran all 46 tests in 6.103 seconds. This replaces the
original launcher acceptance gap; the earlier full-engine review remains historical
evidence for its separately frozen snapshot. No publication or remote CI occurred.

## General web search and tool control, 2026-10-10

The actual Eric Mulhall case exposed the original collector's relevance and
operator-control failures. Its old run stopped at 9 units because the selected
encyclopedia/news/domain plan was exhausted, not because it reached its 24-unit
ceiling or deadline. Wikipedia returned unrelated name fragments; an unrelated
article was collected, a mailbox provider was treated as a subject domain, news
returned HTTP 429, and local inference failed. A ceiling alone gave the user no
explanation of completion, tool inputs or individual recovery.

The canonical collector now uses DuckDuckGo Lite for no-key real web search,
retains original responses, and follows full-name/alias matches with bounded,
revalidated HTTPS redirects. Failed pages consume collection units but do not
fill the successful-page goal. Unrelated encyclopedia hits and email-provider
domains are skipped automatically. Queries use the public name and user-selected
public context; the case's stored private contact identifiers were not transmitted.
Namesakes remain unconfirmed rather than being merged into one identity.

In the actual browser, the Eric case collected four public pages in 11 collection
units, with seven successful tool calls. The engine continued after LinkedIn HTTP
999 and a Harvard HTTP 403; GDELT HTTP 429 remained visible. Public profile shells
and pages mentioning other namesakes are discovery evidence, not identity proof
or a guarantee of useful content. The browser also ran an individually edited
`"Eric Mulhall" profile` search: one tool, one unit, prior history and sources
preserved. Query/context, source selection, page goal and request ceiling are
editable. Activity displays engine, AI and human calls, their inputs, timing,
results/errors, source references and individual edit/retry controls.

Behavioral tests cover real-search parsing and challenge detection, redirect
pinning and failure, full-name relevance, successful-page budgeting, offline
rejection before DNS, model query/URL/evidence permissions, case isolation,
single-job reservation, editable single-tool retries, retained partial history,
required successful AI evidence reads, actionable inference failures and safe
Markdown export. Cached page/domain reuse also retains its requester, failure
details and successful sources without new network requests or collection units;
required AI follow-up prefers untried leads over pages already failed in the run.
Existing authenticated local HTTP and launcher tests remain part of the suite.
Independent final validation passed **111 tests in 6.662 seconds**.
Python compilation, JavaScript syntax, launcher shell syntax and diff whitespace
checks passed. Graphify AST refresh completed: 454 nodes, 1,127 edges, 16 communities.

Independent review used nine bounded snapshots. Corrections addressed premature
AI drafts without successful reads, invisible policy-rejected inputs, initial
query scoping and aliases, model selection of available URLs/evidence IDs, retained
tool history after timeouts, context pressure/actionable error reporting, and visible
cached page/domain outcomes. Round 8 identified a false-success domain-cache
sibling; round 9 independently verified all-failed and mixed-result correction.
Round 9 returned no actionable P1/P2 finding within scope against frozen snapshot
`5000ca70b89bb9f084a6cb0ab6a87a5552d32f752cd1820494fd091245966b1d`.
The identity hashes sorted tracked/nonignored-untracked path + NUL + contents +
NUL. Source and tests remained unchanged after review; these final documentation
receipts were appended afterward. Review is bounded source and synthetic evidence,
not exhaustive security certification or native-model acceptance.

Native validation used the existing cached REDCELL I-Compact GGUF with installed
llama.cpp 0.6.0, build 11429 (`d81235049`), launched and owned by this app. No model
download or package installation occurred. Earlier attempts proved actual model
`web_search`, `collect_page` and `read_evidence` calls, but later inference timed
out or failed; those attempts are not counted as completed drafts. Tool activity
preserves successful calls and exposes incomplete analysis for retry. Model inputs
now use smaller public-evidence projections and bounded available choices while
original sources and exports remain intact. Character limits reduce context
pressure without guaranteeing capacity for every model/tokenizer.

The final web-only native Eric run completed in 99.061 seconds: six successful
tool calls, six of 24 collection units, two collected pages, and successful model
`web_search`, `collect_page` and `read_evidence` calls. AI followed the untried
public Patapoutian Lab people page and discovered two follow-up links. The final
analysis had `status=draft`, `finish_reason=stop`, two citation IDs belonging to
the run, and explicit namesake uncertainty. The overall run correctly remained
partial because LinkedIn blocked two automatic page attempts with HTTP 999.
This verifies native tool use and completed drafting, not factual correctness or
identity confirmation. Domain failure normalization was verified separately with
all-failed/mixed behavioral cases; domain tools were disabled in this native run.

Browser validation also verified editable individual search retry, preserved
history, visible current/completed AI calls and explicit stop reasons. The final
activity screenshot omits the private case identifiers. No browser console
warnings/errors were observed in the final check. Markdown/JSON behavior is
covered by current export tests and the earlier actual export workflow; another
browser download was not required for this correction.

Search challenges, provider blocking/rate limits, sparse results and model latency
remain visible limitations. Login/CAPTCHA bypass, people-search brokers and private
contact searching were not added. Other GGUF families, Windows and additional
Python versions remain unvalidated. No schema/data migration or dependency change
was needed. Existing case data, raw evidence and unrelated model processes were
preserved. No remote, PR, remote CI, publication or release was created.

Retrospective outcome: public-web collection, native tool use and operator recovery
are separate acceptance claims. Future changes must exercise an ordinary subject,
inspect actual tool inputs/failures, and prove a single edited retry, rather than
treating an encyclopedia hit, server launch or progress counter as product acceptance.
