# Local OSINT engine: scope and design

Status: implementation baseline, 2026-10-09. Initial repository had no commits,
application, dependency manifest, tests, graph output, or configured remote.

## Outcome and scope

User requirement: a fully local, open source, private, secure engine that is easy
to use and configure. Initial scope is a complete evidence investigation workflow:
create a case focused on a person, organization, domain, topic or other subject,
record aliases/known identifiers, discover public-source candidates, collect signals,
import evidence, extract indicators, search, inspect shared sources, and export a
portable report. User clarified that collection goes online while processing stays
local. Launcher enables collection capability; each request is user-directed.
An explicit offline mode requires no external service. User-observed acceptance
remains outstanding.

Excluded: account login, paid APIs, scraping search engines, bulk crawling, identity
attribution, facial recognition, vulnerability scanning, remote hosting, AI services,
and cloud sync. No such systems are necessary for this workflow.

## Acceptance and traceability

| ID | Required outcome | Evidence |
| --- | --- | --- |
| REQ-1 | Start with one command; no runtime package downloads | launcher + CLI/browser smoke |
| REQ-2 | Typed subject cases preserve aliases and known identifiers, isolate evidence and entities; restart preserves state | engine integration tests |
| REQ-3 | UTF-8 text, HTML, CSV and JSON imports retain bytes and provenance | ingestion/hash tests |
| REQ-4 | Extract email, domain, URL, IPv4 and IPv6 indicators; trace mentions | extraction/source tests |
| REQ-5 | Search evidence and indicators, filter type, inspect co-occurrence | engine/browser tests |
| REQ-6 | Export case JSON with original base64 bytes and readable Markdown | export roundtrip tests |
| REQ-7 | No outbound calls in offline mode, including DNS | network denial tests |
| REQ-8 | Explicit discovery, domain signals and HTTPS collection prevents private-address access and redirects | collector adversarial tests |
| REQ-9 | Browser access requires local session; evidence cannot execute | HTTP authentication/origin/header tests |
| REQ-10 | Private local storage, backup/restore instructions, honest limits, MIT license | permissions/backup tests + docs |

## Architecture and decisions

Python 3.11+ standard library owns the engine, SQLite, HTML parsing, TLS and CLI.
No runtime dependencies, model downloads, telemetry, analytics, external fonts or
CDNs. The static browser UI and CLI call one engine; no alternate data path.
SQLite has explicit transactions, foreign keys and per-operation connections.
Stored originals are immutable. Same case/source/hash imports are idempotent.
Cases retain typed subjects, aliases and known identifiers without identity inference.
Each occurrence stores offsets into derived searchable text. Co-occurrence means
only that indicators appear in the same evidence, not ownership or identity.

The HTTP listener binds only 127.0.0.1. A per-process random session token is entered
through a URL fragment and exchanged for an HttpOnly SameSite=Strict cookie.
Fragments do not reach HTTP logs; the UI removes the fragment. API requests require
that cookie plus exact Origin and Host. No cross-origin access or filesystem API.
Only fixed static assets are served. Imported markup is extracted, never rendered.
The server is a local single-user tool, not an internet deployment target.

HTTPS collection is a process flag, never a browser setting. It accepts only public
HTTPS URLs on port 443, no userinfo, query or fragments; rejects local/special IPs,
validates all DNS results and connects to the selected numeric IP with original TLS
hostname verification. Redirects, cookies, proxy environment variables, compressed
responses and automatic retries are unsupported. Each page action fetches one page. Domain research collects selected DNS address,
RDAP registration, TLS certificate and website signals separately. RDAP uses the
IANA bootstrap service directory. Subject discovery searches fixed Wikipedia and
Wikidata APIs with an explicit name/alias query and up to ten candidates per source.
Only those built-in APIs may carry application-generated query parameters.
Candidates are not automatically attributed to the subject.
DNS and the destination can observe requested host/path and the machine's IP.

Tradeoffs: stdlib avoids dependency installation and supply-chain exposure but
requires bounded bespoke HTTP security controls. SQLite and source-size caps suit
personal cases, not unlimited corpora. Regular expressions provide deterministic
indicators, not probabilistic identity inference. Domain/email suffixes use a bundled
IANA TLD snapshot; URL authority spans prevent filenames in paths becoming domains.
Balanced path delimiters are preserved. Generated collection titles are bounded
without truncating source metadata. Related-indicator queries join distinct sources
to keep repeated mentions from multiplying work. Disk encryption belongs to the OS;
file permissions alone do not protect against same-user malware or backups.

No legacy data exists; no migration needed. Schema version mismatch fails closed.
Rollback: stop server and restore a verified local backup. No destructive UI operations.

## Delivery route and ordered work

Project Delivery 1.5.0 `medium-feature-change`, medium scale, high risk because
untrusted evidence and private data cross filesystem/browser/network boundaries.
Authority: change; task-scoped commits/PR/review/merge supplied by invoked skills.
Required owners applied: context → requirements → design → planning → implementation
→ quality → documentation → independent review → release disposition.
Security/operations activated before implementation and at review. Coordination
activated for remote/PR identity discovery; external workflow blocked by absent remote.
Retrospective planned-future; no observed user/operational outcome yet.

1. WI-1: subject/evidence store, extraction, search, export, bounded collectors.
2. WI-2: authenticated local server, browser workspace, CLI and launcher.
3. WI-3: behavioral/adversarial tests, actual browser smoke, independent review fixes.
4. WI-4: usage/security/maintenance documentation, focused local commit and handoff.

Single writer builds code. Independent reviewer reads a frozen snapshot; fixes follow
root-cause searches. No worktree is created. Original AGENTS.md and pre-existing local
index/tool files are preserved. External publication waits on verified remote identity.

## Completed verification

WI-1 through WI-4 implementation, documentation and local verification are complete.
42 tests pass and an independent second review is clean after four bounded repairs.
Six live provider checks and the actual person/organization browser workflow passed.
See [validation evidence](validation.md) for exact scope and untested surfaces.
External PR/review/CI/release delivery remains blocked by the missing remote.

Canonical startup repair: the CLI now selects a free loopback port by default.
Explicit configured/requested ports are honored and report occupied-port recovery
instructions. Real fresh-environment launcher tests cover browser-link handoff and
session authentication. See validation evidence for the observed default-port
collision and repaired canonical startup; the original 42-test snapshot above is
historical, and current suite has 46 tests.
