# Validation evidence

## Recovery and runtime reliability package — 2026-10-16

The implementation covers fresh explicitly approved page retries, bounded recovery
of transient search failures, exclusive service-manager ownership, asynchronous
Stop, private diagnostics and understandable transport errors. Acceptance criteria
and integration status are tracked in [the reliability plan](reliability-fixes.md).

Focused service qualification passed **26 tests** with
`.venv/bin/python -W error::ResourceWarning -m unittest discover -s tests -p test_search_service.py`.
These use an isolated Docker runtime fixture plus native child-process ownership
tests. They verify second-manager rejection before Docker access, process-death
lease release, stale receipt recovery, initialization cleanup, closed-manager
rejection, prompt/pollable Stop, cancellation and joined close, failed Stop retry,
and metadata-only background diagnostic references. No existing case data or real
Docker service was modified by those focused checks.

Whole-package qualification passed **377 Python tests** in **27.550 seconds**
with `-W error::ResourceWarning`, and **28 UI controller tests**. JavaScript syntax,
Python compilation and whitespace checks passed. Authenticated HTTP journeys
prove failed-page Retry performs one fresh request with prior attempts/originals
retained, Continue respects persisted cooldowns, and a rejected second app leaves
the first usable and releases its failed listener. Native nginx tests verify
prompt asynchronous Stop while cleanup is deliberately blocked.

A separate native nginx → app → Docker → SearXNG journey accepted setup in
**0.002 seconds**, reached HTTP-ready, completed a real public metasearch action,
and accepted Stop in **0.003 seconds** before reaching `stopped`. Fresh container
logs had zero missing-forwarding-header errors and zero missing-limiter warnings;
unexpected diagnostic events were zero. A synthetic original's SHA-256 remained
unchanged. The official Linux/arm64 image resolved during this run to
`sha256:3619192d55f19eaec4b01892bdf10f80c67e8592fafd14b26ca504e52229210f`.
The test-owned nginx/container stopped afterward; Docker remained running.

Visible browser qualification and independent review are pending. Historical
counts and provider results below describe their dated revisions. Real upstream
availability and target relevance remain independent of lifecycle/recovery proof.

## Native SearXNG gateway and readiness — 2026-10-16

Fixed the internal gateway's missing forwarding headers and supplied a minimal
loopback-only limiter configuration. Added HTTP readiness checks, actionable
unhealthy-container recovery and stage-specific setup errors. The source catalogue
and service panel now share one readiness observation per API response.

- **349 Python tests passed in 42.914 seconds**; focused service/API/connector/nginx
  coverage passed **53 tests**. The existing nonfatal SQLite ResourceWarnings
  remain outside this fix.
- **25 UI controller tests passed**; JavaScript syntax, Python compilation,
  whitespace checks and independent diff review passed.
- A real browser started the official image through the nginx-backed app and ran
  a public SearXNG query. It returned results, with Brave's upstream rate limit
  recorded separately. The initial missing-header log was reproduced.
- After the fix, a native authenticated nginx → app → Docker → SearXNG journey
  accepted setup in **0.005 seconds**, reached HTTP-verified ready, completed a
  real public search and stopped the container through nginx. Fresh logs contained
  **zero missing-header errors and zero missing-limiter-file warnings**. A saved
  synthetic original retained its SHA-256. Only isolated test workspaces were used.
- Direct native search after the header fix returned HTTP 200 and **16 results**;
  Brave reported “too many requests.” This is partial upstream coverage, not a
  local startup failure or proof of relevance for every result.
- Official Linux/arm64 image used:
  `sha256:b4e34a39f76b1c860bbc53a0f35b10492029b2b18ef1d868df464fe43d7e0b93`.
  The image was downloaded during explicit qualification; test-owned containers
  and nginx stopped afterward. Docker itself remained running.

The user-visible error in Brave was inspected through desktop accessibility.
The Brave extension connection failed before tab access, and Docker Desktop's
window inspection timed out; CLI and native HTTP checks supplied Docker/runtime
evidence. No Docker Desktop settings were changed. Upstream tracker-rule mirror
timeouts and search-provider cooldowns remain possible and are documented in
the README.

## Docker-unavailable metasearch startup — 2026-10-16

The service now reports an explicit error when its Docker engine check fails,
with instructions to start Docker and retry. The browser polls background setup
through its terminal result, preserves Stop during setup or retained-container
recovery, and enables retry after a failure. Docker remains user-managed.

- **345 Python tests passed in 41.507 seconds**, including authenticated HTTP
  failure reporting, unchanged case/evidence exports, retained ownership receipts,
  cancellation, and failure-to-ready retry with a simulated Docker runtime.
- **25 UI controller tests passed**, including asynchronous failure polling without
  a selected case, request errors, retry-to-ready and retained cleanup controls.
- A real browser exercised the nginx-backed app in an isolated workspace with a
  controlled Docker CLI fixture returning a stopped-engine failure. Both initial
  startup and retry showed the actionable error and enabled another attempt.
  No image pull, container creation, public-source collection or model launch ran.
- The host Docker engine was running during verification (29.8.2). It was left
  running; successful native SearXNG startup was not part of this qualification.
- JavaScript syntax, Python compilation, whitespace checks and the AST-only
  graph update passed. The full suite emitted nonfatal unclosed SQLite connection
  ResourceWarnings; all tests completed successfully.

## Automatic question-driven investigations and Evidence Atlas — 2026-10-16

Status: implemented and locally qualified. The primary Investigate/Continue
actions select source recipes, recover saved work and use the available cached
local model without a manual source-tool selection. The Evidence Atlas exposes
saved source/candidate/question provenance and preserves unresolved outcomes.

| Evidence layer | Observed result |
| --- | --- |
| Python behavioral, HTTP and native-process suite | **342 passed in 30.278 seconds**, no failures or skips; run outside the sandbox for loopback listeners, launcher and owned-process checks |
| UI controller suite | **22 passed**, including automatic Investigate/Continue, AI opt-out, document import/retry, page references, reversible decisions, atlas provenance, keyboard activation, stale-case protection and saved-source titles |
| Actual existing-workspace upgrade | Schema 2 → 3, integrity `ok`; byte-for-byte fingerprints of cases/evidence/entities/mentions unchanged: **3 / 513 / 4,678 / 35,832** rows. Every original evidence ID, raw SHA-256 and extracted text preserved. A consistent private schema-2 backup was created before migration; no collection ran during upgrade |
| Native document tools | Installed Poppler 26.10.0 and Tesseract 5.5.3 extracted text PDF, scanned PDF and image fixtures locally. Office, page provenance, extraction retry, ZIP/XML/image bounds and original-row preservation passed |
| Browser + nginx + cached REDCELL | A fictional ordinary-person case used one Investigate click, started the owned model, collected three substantive public-source fixtures, extracted a target passage **on PDF page 18, after more than 6,500 characters**, and saved exact page citations. The explicit objective received quoted support; identity and independent corroboration remained unresolved. Atlas source inspection, local extraction retry, match/reversal controls and reload were exercised; browser error log was empty |
| Native source-backed AI planning | Two real model calls: an unsupported page proposal was rejected; the repetition strategy then produced one fresh validated Bing action with an exact public passage and the correct saved evidence ID. Zero private-canary argument echoes and zero outbound collection during this isolated qualification; owned model stopped afterward |
| Live public search | DuckDuckGo and Bing each returned **10 leads** for a public organization query. These are discovery results, not validated identity evidence |
| Live federated lookup | WebFinger succeeded with actor and HTML-profile links. Properly negotiated ActivityPub returned **HTTP 401 / blocked**; response original retained, no bypass. Positive actor parsing and fixed-header content negotiation passed deterministic fixtures |
| Live Common Crawl | Catalogue succeeded; the selected CDX index query timed out. Two attempts, catalogue original retained, explicit error outcome; positive index normalization/bounds passed fixtures. No WARC retrieval |
| Page/process lifecycle | Final browser closure stopped test nginx and local-model listeners; heartbeat expiry remains the fallback when a close event is lost. Original evidence retained and private canary absent from outbound fixture inputs. Multiple-page/reload/grace and parent-death paths also passed the regression suite |
| Static and review checks | Python compilation, JavaScript syntax, whitespace checks, canonical-document relative links and AST-only graph update passed. Independent review fixes are covered by **11 focused regressions** |

Public-source fixtures are synthetic; native model and document processing were
real. Real-provider checks above are reported separately. These checks do not
establish universal people-search coverage, correct attribution of namesakes or
semantic truth of a source. Question interpretation validates literal provenance;
it still requires review for meaning and identity. No success was inferred from
an empty response, a login wall, an archive index record or collection volume.

The first native browser trial exposed an unsupported inference in free-form
model prose. Automatic findings now render verified source passages; optional
advanced prose analysis stays visibly unverified. Unknown origins cannot become
independent corroboration, and explicit same-target decisions reconcile claims
through a reversible projection without rewriting stored claims or originals.

Docker/SearXNG startup was not requalified in this delivery. No cloud AI,
mandatory third-party framework, global package installation, push, PR, merge,
publication or release was performed. The schema upgrade is local; rollback
requires restoring the pre-upgrade backup with a compatible app version.

---

The following evidence describes earlier revisions.


## Source-driven workspace delivery — 2026-10-15

Status: implementation and local qualification complete for the core workspace.
Native optional SearXNG startup remains unqualified because the installed Docker
client cannot connect to its daemon. All receipts below the historical divider
apply only to their dated older snapshots.

The current acceptance scope covers:

- In-place schema-1 to schema-2 migration with a consistent private backup,
  rollback fixture and preservation of existing case fields/evidence originals.
- Durable source actions, attempts, cooldowns and unknown dispatched outcomes;
  retry/skip/priority controls and explicitly requested resume.
- Separate candidates and exact, versioned supporting/contradicting passages;
  snippets and readable pages do not become verified identity claims.
- Local retrieval of relevant later evidence, bounded local AI action selection
  and exact-passage extraction, with eight configurable prompts.
- Public/private input separation, offline rejection, permitted connector scope,
  safe text rendering and explicit single-source configuration.
- Website sources, Crossref, GitHub/GitLab public profiles, Wayback, public places,
  local email-header processing and optional owned SearXNG.
- Page departure, multiple tabs/reload grace, persistent owned-process receipts
  and interruption recovery without discarding cases or evidence.

| Evidence layer | Observed result |
| --- | --- |
| Python behavioral/integration suite | `.venv/bin/python -m unittest discover -s tests -q`: **295 passed in 24.821 seconds**, no failures/skips, outside the sandbox for loopback/process checks |
| Node UI controller tests | `node --test tests/test_workspace_ui.cjs`: **10 passed**, including source settings, named email selection, stale-case protection, queue controls, eight prompts and candidate-filtered relationships |
| Syntax/static/document checks | Python compilation, JavaScript syntax, launcher shell syntax, whitespace and existing local Markdown links passed; AST-only Graphify refresh completed |
| Schema-1 fixture migration/rollback | Suite verifies private consistent backup, transactional rollback, conservative historical import, running-to-success checkpoint reconciliation and immutable evidence |
| Existing user cases/evidence preservation | Actual upgrade to schema 2: **3 cases, 513 originals, 4,678 indicators and 35,832 mentions** unchanged. Complete original-table fingerprints match the pre-upgrade backup, every raw SHA-256 verifies, SQLite integrity is `ok` |
| Browser workflow | Actual authenticated app/nginx with controlled public responses: save public settings → run website discovery → resume follow-up → inspect candidate/exact passage/relationships → retry with two retained attempts → refresh → select saved email by title and analyze locally. No browser warnings/errors observed; all 14 fixture originals verify |
| Core live providers | Five successful public integrations: GitHub API (`octocat`), website discovery (`www.python.org`), Crossref, Wayback and GeoNames. GitLab returned an HTTP-200 access barrier and was correctly blocked. All six returned originals retained and hashes verified |
| Native local model | Installed llama.cpp and cached REDCELL I-Compact: valid offered-action selection, **two exact-passage observations**, retrieval of the relevant 36th source, one successful local evidence read and a draft citing one saved source. No literal synthetic private canary in output; owned listener closed afterward |
| Owned-process lifecycle | Native suite covers parent death, exclusive worker ownership, nginx routing/master-worker cleanup and page lease behavior. Closing the actual fixture tab eventually stopped its app/proxy and closed the listener; teardown receipt retained all 14 originals, six mocked public requests and zero canary disclosures |
| Native SearXNG container | **Not qualified**: `docker info` outside the sandbox confirmed the daemon was unavailable. No image pull or container startup was attempted; mocked ownership/setup/cancellation/fanout contracts passed |
| Independent review | All material findings repaired and re-reviewed: encoded/private URL egress, pack-only false completion, retry isolation, historical checkpoint outcomes, bounded AI JSON, citation enforcement and decompression bounds. No material issue remaining in that bounded scope |

The browser's public transports used synthetic responses; that journey proves app
integration, not provider availability. Separate live checks used only the public
test targets named above, never private case details. A first live website attempt
exposed a server sending gzip despite the identity preference. The shared collector
now decodes gzip/zlib-wrapped deflate with separate 2 MiB wire/decoded bounds; the
same real website then returned 40 same-host leads. Its decoded entity bytes are
the preserved evidence original. Truncation, bombs, checksum failures, trailing
streams and unsupported encodings have behavioral regression coverage.

Native AI qualification initially exposed fenced structured JSON and an uncited
draft. Complete JSON fences now pass the same strict schema/span checks; truncated
responses remain rejected. A draft without a known saved-source citation is marked
failed even after a successful read. The final native run selected an offered
source, saved two literal observations and produced a cited draft. Another actual
browser model run searched, collected and read evidence before producing a draft
with three retained source citations. Model assertions remain unverified; these
receipts do not establish factual correctness or the identity of any real subject.

Before upgrade, a separate private consistent backup was retained at
`.local-osint/backups/before-source-engine-20261015.sqlite3`; the migrator also made
its own schema-1 backup. No old case field, original byte, indicator or mention was
changed. The canonical app was reopened against the preserved workspace and the
new source settings/workspace controls loaded successfully. No investigation of a
real saved subject was started as part of qualification.

Environment: macOS, Python 3.14.8, nginx 1.31.6 and the installed native llama.cpp.
Other Python versions, Windows, other GGUF families and native SearXNG remain
unqualified. The suite emits existing SQLite fixture `ResourceWarning` messages
about unclosed test connections; they did not fail checks and are not evidence of
production connection leaks. Browser close delivery remains best effort: normal
close uses a two-second reload grace; missed departure signals expire under the
90-second heartbeat lease. Stop/checkpoint cannot interrupt every in-flight network
or model request immediately.

Optional SearXNG code configures an official image, pins immutable identity,
uses a container-private loopback API through a fixed Docker-exec client, and
reports selected-engine fanout/failures. Mocked Docker fixtures can establish
command/ownership contracts; they cannot establish native image startup, resource
compatibility or engine reliability. The app does not start or install an
externally managed Docker daemon as part of investigation. Core collection
remains available without this optional service.

SearXNG upstream HTTP is a distinct transport: it does not inherit core DNS/IP
pinning and redirect controls. This limitation must remain explicit even after a
future container smoke test passes. Configured engine count is fanout disclosure,
not an exact upstream request count.

No publication, deployment, remote CI or release is implied by implementation or
local checks. Unmet qualification remains explicit above; local test success does not imply
user acceptance or exhaustive security certification.

## Historical validation receipts — 2026-10-09 to 2026-10-10

The following material is retained as historical evidence for specific earlier
snapshots. Their fixed deadlines, six-prompt counts, schema statements and tool
scope describe those snapshots only. Current behavior is documented in
[README](../README.md), [design](design.md) and [security](../SECURITY.md).

## Continue investigation planner — 2026-10-10

The repaired feature passed **153 tests in 6.763 seconds**, independently repeated
as **153 tests in 8.509 seconds**. The independent source snapshot was
`0be2a4edcb1e5dfa08673171c66c14467e1ac054e5ecfc9123480d5189a78c21`;
subsequent changes only update documentation. Python compilation, JavaScript syntax,
whitespace checks and the AST-only graph update also passed.

New behavioral checks prove immutable source retention, cumulative useful-page
progress, fresh budgets, access/login exclusions, replacement leads in a full
display window, restart-interrupted recovery, cancellation/deadline preservation,
unavailable model recovery, selected-source enforcement, complete retained-news
collection, explicitly approved public context, private-query rejection and
dispatcher refusal of network tools during local-only final drafting. Model tests
cover strict actions/arguments, fenced JSON, invalid/truncated/tool responses,
prompt bounds and sanitized reasons. Continue API retains authentication and owned
model configuration boundaries. Most provider/model behavior in the suite is synthetic.

Separately, the existing Eric case was continued through the actual browser,
installed llama.cpp and cached REDCELL I-Compact model. Automatic startup visibly
progressed from Loading to Ready. The final native run used settings of 100 request
units and a 70-useful-page goal. It retained all **12** parent source references;
all **12** original byte hashes verified. It made **two** fresh public searches,
both with `-site:` exclusions for known access barriers, **two** successful local
evidence reads and a local draft citing **two** case sources. Derived AI output and
outbound queries had **zero observed private-clue echoes** under the same output
guard; this is not a general data-loss-prevention guarantee.

The native investigation remained **partial, 0 useful pages, 2 request units**.
One search was unavailable and the model repeated an already attempted query;
both were visible with actionable errors. No blocked profile was recollected.
Earlier native recovery also performed five fresh public searches and produced a
cited local draft, but found no useful corroborating source. These receipts prove
execution, retention and transparency; they do not confirm Eric's identity or
guarantee provider availability, new evidence, Windows behavior or user acceptance.

Source review required two bounded rounds. The final review found no material
remaining P0/P1/P2 issue. A nonblocking limit remains: search exclusions for hosted
subdomains under .com/.org/.net may cover a broader parent domain; user-edited tool
queries remain available. No external publication, release or worktree was created.

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


## Local case comparison and useful-page correction — 2026-10-10

The accepted delta uses saved case details locally, distinguishes corroborated
leads from name-only candidates, excludes login/empty/unrelated pages from the
useful goal, continues bounded public search, and starts/uses the optional local
LLM. Original cases and source evidence were retained; no schema migration,
dependency install, model download or external publication was needed.

Behavioral verification passed **128 tests in 5.407 seconds**, with loopback tests
outside the sandbox. New contracts cover local contact/context ranking without
copying clue values, exact query-selected profile identity, strict no-tool model
comparison output, private model context without public vocabulary expansion,
public-result-derived refinements with private phrase rejection, saved shell
retention and continued useful collection, model echo redaction, and useful/match
metadata in safe exports. A transient launcher fixture failure was a race between
creating and writing the browser-handoff file; its reader now waits for nonempty
content. Canonical launcher behavior was unchanged. Python compilation, JavaScript
syntax, shell syntax and diff checks passed. SQLite resource warnings appeared in
full-suite runs without failures; their broader lifecycle investigation is deferred.

Independent review established three bounded defects in an interim candidate:
query-selected profiles could compare equal by host/path, rejected model tool
activity could retain private prompt echoes, and an AI-reserved page slot could
stop useful collection early. Repairs preserve query/fragment URL identity, redact
blocked activity and timeout traces, and fill the full useful goal before drafting.
The repaired snapshot
`8477a863adc44df7be7408fcddf2b3fe1223050b18a7feec88e9fc9d9dd4dd5a`
returned no material in-scope P0/P1/P2 finding, with **128 tests independently passed
in 5.265 seconds**. Independent normal/timeout fixtures made zero outbound calls
for rejected private queries; an AI-enabled fixture kept good/shell/next originals
and reached its useful goal before analysis. Hash stayed unchanged through review.
Final documentation receipts were appended afterward; source/tests remained frozen.
This is bounded source/synthetic evidence, not exhaustive security certification.

Actual native validation started the cached REDCELL I-Compact GGUF through the
app's owned authenticated loopback llama.cpp control. The final Eric run
`a4a36ec4905b4c4292f53369c1cd912b` ran from 14:46:41 to 14:47:32 UTC, approximately
51.292 seconds. Local comparison completed for eight leads; all remained uncertain,
with no saved-clue corroboration. Collection continued beyond the prior nine-unit
stop to **17 of 24 units**, retaining **10 raw pages** and reaching **four readable
name-only candidate pages**. Six saved pages were excluded from the useful goal,
including a Facebook login wall, Instagram shell and unrelated institutional/broker
content. These counts measure readable candidate pages, not distinct identities or
proof that the candidates are the target.

The model made successful real `web_search` and `read_evidence` calls, using the
observed public query `"Eric Mulhall" Dublin LinkedIn`, then produced a cited local
draft with `status=draft`, `finish_reason=stop` and two known run evidence IDs.
Model comparison and drafting received saved context locally. No literal saved
private clue appeared in the observed model trace or tool activity; query policy
and synthetic tests provide the separate outbound-boundary evidence. No packet
capture or general anonymization claim is made. Overall status remained partial:
three LinkedIn attempts returned HTTP 999 and Harvard returned HTTP 403. Those
failures remain visible and individually editable/retryable. The target Eric's
identity remains unconfirmed; academic biographies stay candidate evidence and
are not merged into verified subject claims.

An earlier native attempt completed private comparison but rejected a model query
using public employer/location words. That attempt's AI draft remained failed;
it is not a completed-draft receipt. Returned public titles/snippets now supply
nonnumeric query vocabulary, while exact/normalized saved private clues remain
blocked unless the user explicitly supplied them in public query/context.

Browser verification showed local AI ready, enabled AI analysis, completed useful
count, actual model-requested search/read inputs and edit/retry controls. Final
console check had no warnings/errors. The proof screenshot excludes the private
case header. The app and owned model remain running for the user. Provider blocking,
unrendered JavaScript pages, heuristic quality decisions, namesake ambiguity and
model paraphrases/inferences remain bounded limitations. Original saved case fields
and user-requested case exports intentionally retain their private data.

## User-selected limits — 2026-10-10

Removed the fixed 64-request and eight-useful-page caps from UI and backend.
Defaults remain 24 and four; settings accept positive exact browser integers
through 2^53−1. The run's selected ceiling/goal, 300-second soft deadline, Stop,
bounded search refinements and source eligibility remain independently effective.
No schema, case migration, package/model download or provider request was needed
for this correction.

The complete suite passed **129 tests in 6.628 seconds**, outside the sandbox for
loopback fixtures. A synthetic public-provider regression actually collected
70 useful pages in 73 units under a 100-unit ceiling, proving larger settings
affect work rather than only validation. Invalid zero, boolean, fractional and
unsafe values remain rejected; existing one-unit exhaustion and offline behavior
still pass. Compilation, JavaScript syntax and diff checks passed.

Independent bounded review returned no material in-scope P0/P1/P2 finding against
stable snapshot
`76a0256f6c522a977530bb9126ae6d8065ae41aaf02856a25e3a873666fca445`.
It passed 24 investigation tests and separately exercised backend and actual
frontend validation code at larger and numeric-boundary values. Final receipt
documentation was appended afterward; source/tests remained unchanged.

Browser verification in the refreshed canonical app accepted 100 requests and
70 useful pages. Attempting a value above the exact integer range displayed its
validation error before starting collection. The app and optional local model
were restored to ready state. No further Eric provider run was launched: blocked
sites and uncorroborated identity require different public leads/context, and
larger numeric settings alone cannot establish identity or guarantee coverage.
The refreshed UI proof excludes the private case header.

## Adaptive strategy recovery, nginx and page lifetime (2026-10-10)

The repeated-query/two-search-failure stop rules have been removed. Recovery now
tracks provider/query pairs, retires failing providers for the run and dispatches
fresh public strategies or eligible pages when the model repeats, rejects an
instruction or finishes prematurely. Public context can be broadened while the
complete subject/alias and private-clue guards remain enforced. All five focus
kinds have deterministic fallback coverage. Six bounded local prompt stages and
provider choices are editable and persisted in report options.

Root final verification: **211 tests passed in 18.784 seconds**. Independent final
verification: **211 passed in 18.114 seconds**, with no skips/failures, plus clean
whitespace checks. Python compilation, JavaScript syntax and launcher shell checks
passed. The suite includes actual installed nginx 1.31.6 routing/authentication,
Host/Origin/body limits, SIGTERM cleanup, final-page closure, multiple-page leases,
refresh grace, heartbeat expiry, unknown/unauthenticated closure and evidence
retention. Provider parsing/privacy/repetition cases use controlled fixtures.
A separate native Bing collection through the actual pinned HTTPS transport
returned seven organic HTTPS results for a public Ada Lovelace query.

Independent review's first adaptive/runtime pass found no material issue. The
subsequent page-context pass reproduced and repaired two P2 defects: only ten
retained pages were reassessed, and edited context could not revive substantive
pages previously rejected as namesakes. Final reassessment covers all candidates
in batches of ten and keeps original quality separate from the local context
verdict. Cache and current-run activity summaries share that verdict. Eleven-page,
corrected-context, login-shell and immutable-original regressions pass. No
outstanding material review finding remains in this bounded scope. Final reviewed
source/test/launcher/manifest hash is
`6d20cf77938fb97f4659194ed02e1832d2916f7ffc6071b13bd8e35418117a29`,
using sorted relative path + NUL + contents + NUL. Documentation receipts follow
source review; this is not exhaustive security certification or factual identity
acceptance.

Canonical browser startup used dedicated foreground nginx and an ephemeral
loopback Python backend. No Homebrew service was started; no shared nginx config
was loaded. Actual browser navigation away from the last app page exited the
launcher with status zero, stopped the ownership-verified backend/nginx/local-model
PIDs, and closed all three listener ports. Existing evidence stayed on disk.
Actual refresh reconnected without runtime shutdown, and closing a temporary second
app tab preserved the first tab's investigation. A custom planning prompt survived
case reload, browser refresh and the next continuation.

The first live continuation retained all fourteen parent source IDs and verified
all fourteen original hashes. Repeated affiliation planning was blocked and
recovered with a genuine Bing query, then both engines and fresh strategies.
It executed fifteen new searches and seven page attempts in twenty-two collection
units before the 300-second soft deadline. Private-clue query echoes were zero.
Three substantive pages belonged to a likely scientific namesake, exposing a
relevance gap: the comparator lacked the chosen public role/location context.
The corrected comparator receives that context locally and reassesses saved page
text without changing originals. Native corrected-run results follow below.

The corrected native continuation used the installed local llama.cpp model to
exclude all three scientific namesake pages using the chosen public role/location
context. It executed fifteen fresh searches across both engines, consuming fifteen
collection units, and stopped at the 300-second soft deadline with **zero useful
target pages**. It retained all **34** parent source references and verified all
**34** original byte hashes. No saved private clue appeared in the observed search
queries. This run proves actual local comparison and continued public recovery;
it does not establish target identity or a completed AI draft. Provider results
offered no eligible untried target page to collect during this run.

The final nginx correction uses the documented foreground master/worker mode and
stops its owned process group. Native tests verify both processes disappear after
teardown. POSIX/macOS behavior was exercised; the non-POSIX fallback was inspected
but not run. The final app was restored in the browser on an automatically chosen
free port; explicit occupied-port rejection preserved the existing listener.

Graphify AST refresh completed with 744 nodes, 1,930 edges and 28 communities;
no paid semantic graph calls were made. Environment-specific provider availability,
model categorization and target identity remain separate from test success.
