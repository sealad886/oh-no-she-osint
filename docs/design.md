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
attribution, facial recognition, vulnerability scanning, remote hosting, cloud AI services,
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

## Automatic investigation and local AI correction

The manual-first baseline did not meet the user's click-to-run requirement. The
accepted correction is: enter a person, organization, domain or topic, press Start
investigation, and receive collected public evidence and a source-linked dossier.
The existing medium-feature-change route remains active, with security review for
returned public URLs and model tool dispatch. Local-only processing includes an
optional owned llama.cpp process; the engine works without a model or API keys.

The bounded run searches Wikipedia/Wikidata, collects up to two candidate profiles,
resolves up to twenty linked entity labels, collects a Wikipedia page, searches
recent GDELT news, follows up to two articles, and investigates one public domain
from known identifiers or candidate website claims. Namesake candidates remain
separate and unconfirmed. Every structured claim carries candidate and evidence
IDs. Failed sources leave successful evidence intact. The UI shows steps, stop,
partial results and source links. All original responses and the derived dossier
are exported. Derived reports do not contribute extracted indicator mentions.

One active run, at most 24 collection request units, 180-second soft collection
deadline, existing 2 MiB response caps, and cancellation between requests limit
resources. Registration reserves two request units. Running checkpoints persist
as local report evidence; restart marks unfinished work interrupted rather than
silently resuming outbound requests. No schema migration is needed.

Optional local AI uses OpenAI-compatible tool calls over authenticated loopback.
Tools can search this subject, search its news, collect already-discovered URLs,
investigate approved domains, and read evidence in this run. No shell, arbitrary
filesystem, cloud endpoint or unrelated-case access. Search arguments cannot
transmit model-generated text or private evidence. At most six tool calls and four
model turns. Sources are untrusted data; model output is an explicitly unverified
draft with source IDs. Model startup uses a cached local GGUF, never an automatic
download, and stops only the process this app owns.

Acceptance additions: automatic person and organization runs collect evidence
without manual import; offline start performs no DNS; cancellation/restart retain
partial evidence; model tool calls enforce case, URL/domain and call budgets;
real local-model verification is reported separately from simulated tool tests.

These additions are implemented and verified with 73 behavioral tests, independent
review, actual person/organization browser runs and native REDCELL tool calling.
Claims retain rank, qualifiers and temporal precision; incomplete AI output stays
explicitly partial. See the automatic-investigation section of the validation record
for provider failures, review snapshot identity and remaining acceptance limits.

Canonical startup repair: the CLI now selects a free loopback port by default.
Explicit configured/requested ports are honored and report occupied-port recovery
instructions. Real fresh-environment launcher tests cover browser-link handoff and
session authentication. See validation evidence for the observed default-port
collision and repaired canonical startup; the original 42-test snapshot above is
historical, and the automatic-investigation correction brings the current suite to
73 tests.

## General web search and tool control

Current user-observed failure: Eric Mulhall's run spent 9 of its 24-unit ceiling,
then exhausted an encyclopedia-first plan. It collected an unrelated article,
treated an email provider as a subject domain, encountered news rate limiting and
failed local inference. The ceiling was displayed without explaining completion;
tools lacked visible inputs and individual retries.

Accepted change: ordinary people must be discoverable through real web search.
Local AI can search and follow relevant public leads. Users can inspect current
and completed tools, edit queries/context and source selection, and retry one tool
without repeating the full investigation. Original evidence and prior reports stay
immutable. A run explains why it ended below its budget.

Design extends the canonical collector, Investigation/Jobs, local AI dispatcher
and browser UI. DuckDuckGo Lite supplies no-key public search through pinned
HTTPS; a live subject-name probe succeeded. HTML returned HTTP 202, so Lite is the
default. SearXNG JSON was considered, but requiring another service or relying on
unverified public instances does not meet current ease-of-use acceptance. Search
results retain raw evidence. Challenges and rate limits remain visible failures.
Relevant name/context matches rank before follow-up; unrelated encyclopedia hits
and email-provider domains are not followed automatically. Bounded HTTPS redirects
revalidate and pin every destination; discovered query URLs are explicitly allowed.

Options expose public query/context, selected sources, page limit and request
ceiling. Activity records tool, inputs, owner, status, timing, output/source IDs and
error before and after execution. Retry is one reserved asynchronous job and keeps
previous evidence. Model searches require subject terms and bounded public context;
the model cannot search arbitrary private notes or other cases. Follow-up URLs must
come from this run's search results or collected page links. Users can explicitly
run a new public URL/query through the same guarded collector.

Acceptance: non-encyclopedic search returns relevant public leads and collects
pages; AI makes real search/follow calls; unrelated matches and mailbox domains are
skipped; budget/deadline/stop reasons are explicit; each source/tool is editable and
retryable; offline, case isolation, redirect pinning, concurrency and original-byte
provenance remain enforced. Prove these through behavioral tests, the actual Eric
case (public name only), browser operation and native-model validation separately.

Route remains medium-feature-change, medium scale, high risk, change authority.
Reuse required context, acceptance, design, planning, implementation, quality,
documentation, review and local release-disposition owners. Security is activated
for model queries, returned URLs and per-tool execution; coordination is not
applicable because no external tracker or publication is requested. Retrospective
is activated by the observed relevance/visibility failure. No schema migration is
needed: additive run records are generated evidence, not mutable case data.
Rollback is the focused source change with preserved local evidence.

Ordered work: (1) public search/guarded transport and UI contracts; (2) shared
tool execution, relevance, bounded AI and retry integration; (3) behavioral/live
verification and independent review; (4) final docs and focused local commit.
One active step at a time; bounded collector/UI writers work independently while
the primary integrates the canonical run owner. No worktree or publication.

Implementation, behavioral/live verification and independent review are complete
for this correction. Final review passed 111 tests against the recorded frozen
snapshot. Cached page/domain calls preserve visible outcomes and original errors;
AI follow-up selects untried leads. Original reports/evidence remain immutable.
The final handoff includes user/operator documentation and a focused local
Conventional Commit; external publication and release remain unauthorized.

Retrospective: an encyclopedia response and a progress counter did not prove
ordinary-person OSINT. Acceptance now requires actual public-web discovery,
relevant collection, visible inputs/results, individual edited retry, explicit
stop reasons and, when enabled, native local-model search/follow/read/drafting.
Provider blocking and namesake uncertainty remain evidence limits, not hidden
successes. See the current validation record for separate synthetic, provider,
browser and model receipts.

## Local case matching and useful evidence

User correction: compare saved case details locally to distinguish namesakes,
keep looking past login/empty shells, and start/use the optional local model.
The prior public-name filter did not use private clues, and saved HTML bytes
counted toward the page goal regardless of substantive content.

Accepted behavior: private identifiers enter only deterministic local comparison
and the owned authenticated loopback model prompt. They never expand public query
vocabulary or become automatic search refinements. Direct contact/known-public-URL
matches rank above context matches and name-only leads. Local model comparison
adds an unverified context hint, never proof of identity. Model comparisons return
bounded candidate indices/categories; raw private clue values are not persisted
in derived reports or tool activity. Explicit public URL/domain leads retain their
existing collection meaning.

Visible-text quality rejects login barriers, challenge pages and empty shells from
the useful-page goal, while retaining originals and quality reasons. A namesake
flag with no direct identifier corroboration also does not fill that goal. Continue
through ranked results, approved links and at most two public-only query refinements
within existing request/time ceilings; report exhaustion honestly. Model drafting
receives private case context locally and must explain uncertainty without echoing
raw contact details. Historical immutable reports remain historical; no case/schema
migration is needed. Rollback preserves all source evidence.

Route remains medium-feature-change, medium scale, high privacy risk, change
authority. Reuse context, acceptance, design, planning, implementation, quality,
documentation, independent review and local release-disposition owners. Security
activates at private-model/public-query boundaries; coordination remains not
applicable; retrospective activates from the observed shell/name-only failure.
Ready/design gates derive from this explicit user correction and canonical owners.
The engine fills the full useful-page goal before model drafting, avoiding a reserved
AI page slot that could falsely report exhaustion while eligible leads remained.
Public search results can supply employer/location/topic words for model refinements;
literal and normalized saved private clues remain blocked before dispatch. Known
URL corroboration includes query/fragment identity, preventing different profiles
on the same endpoint from becoming strong matches. Rejected model arguments and
timeout traces use the same private-output guard as drafts.

Checklist complete: inspect/accept delta; implement local ranking/page quality;
behavioral and actual Eric/native validation; independent review and documentation.
Focused local commit is the remaining handoff action. No publication or new worktree.
Independent repaired snapshot passed 128 tests without a material in-scope finding.
Native validation reached four readable name-only candidate pages, rejected six
nonuseful saved pages, and completed local comparison/search/read/drafting. No saved
clue corroborated those candidates; target identity remains unconfirmed.

Retrospective: readable content and candidate identity are separate facts. Private
context can guide local comparison without yielding corroboration. The engine must
show that uncertainty, preserve barriers and continue eligible public work; neither
an academic namesake biography nor a model draft proves the requested person's identity.

## User-selected investigation limits — 2026-10-10

Accepted bounded change: remove the fixed 64-request and eight-useful-page product
caps from both UI and backend. Defaults remain 24 and four; each run retains its
user-selected finite ceiling/goal, existing 300-second soft deadline, cancellation
and bounded eligible search work. Values must be positive whole numbers no greater
than 2^53−1 so browser JSON preserves them exactly. This is a numeric representation
boundary. Larger goals do not guarantee source availability or solve provider
blocking or identity uncertainty.

Route: small-feature-change, small scale, low risk, change authority. Canonical
option validation, numeric inputs and run budget are established owners; no schema,
data conversion, dependency or architecture change is needed. Context/acceptance
and design gates follow the explicit request and inspected implementation.
Documentation activates for configuration guidance; bounded independent review
activates under the existing review workflow. Separate planning, security,
coordination and release execution branches are not needed for this slice;
existing resource/privacy controls remain in force. Local commit authority persists
from the invoked commit skill. Implementation, behavioral/browser verification,
independent review and documentation receipt are complete. Local commit is the
remaining handoff action.
External publication unauthorized.

## Continue investigation planner — 2026-10-10

Accepted feature: one click resumes the latest saved investigation, including
partial, limited, cancelled and restart-interrupted reports. Preserve immutable
originals, source references and tool history; count useful pages cumulatively
and give the continuation a fresh user-selected request budget. Automatically
start the owned, installed local model from its cached file without downloads.
The model selects one validated public search or untried page at a time using
private case details only locally. Previously attempted URLs and queries are
not automatically repeated. Access/login barriers exclude that host; alternative
public sources remain eligible. Individual user retries remain available.

The existing 300-second soft deadline/cancellation and network guards remain.
Each executed collection action consumes the selected request budget; model errors,
two repeated invalid decisions, two consecutive unavailable searches or an
explicit exhaustion decision stop visibly while retaining progress. Drafting
reads saved sources locally and cannot reopen network tools. A readable page
and a model hint never establish identity. No new schema or migration is needed.

Route: medium-feature-change, medium scale, high privacy risk, change authority.
Existing context, acceptance, solution design, planning, implementation, security,
quality, documentation and independent-review owners apply. Coordination is not
applicable; publication/release execution remain unauthorized. Retrospective
addresses the observed partial-run dead end. Ready/design gates are satisfied by
the explicit request and inspected canonical Jobs/Investigation/model boundary.
Checklist complete: design, implementation, behavioral/native verification,
independent review and documentation. Focused local commit remains the handoff
action. No publication or worktree creation.

Independent review repaired dispatcher-level draft network authority, selected
source filtering, explicitly approved public-context preservation, visible blocked
planner errors and retained news URL authority. The repaired frozen source passed
153 tests locally and independently. New searches preserve fresh leads even when
the 40-lead display window is full; all original search evidence remains saved.
Actual native continuation retained and verified 12 parent originals, dispatched
two public alternative queries with blocked-host exclusions, made two local reads
and generated a two-citation draft. It remained partial with zero newly useful
pages: provider availability and a repeated model proposal prevented progress.
No private clue echoes were observed in its queries or derived AI output.

Retrospective: continuation recovers a workflow and preserves evidence; it cannot
manufacture public corroboration or make an unavailable provider work. Visible
planner errors and precise stop reasons are required recovery outcomes. A partial
native investigation must remain partial even when the software checks pass.

## Adaptive recovery correction (2026-10-10)

Accepted change: AI repetition and provider failure must trigger different public
work, rather than terminate a continuation. Keep all prior originals, exclude
failed/login hosts, distinguish namesakes locally, and support every defined
subject type. Users can choose DuckDuckGo/Bing and edit six local prompt stages.
Mandatory privacy, URL scope, cancellation and resource guards remain enforced
outside configurable prompts.

Design: track attempted (provider, normalized query) pairs; retire unavailable
providers for the current run. Offer a finite, target-aware queue that broadens
optional public context and changes source keywords. Validate every model and
engine query through the same private-clue guard. Invalid, repeated or premature
finish decisions fall back to eligible untried pages or fresh queued searches.
Successful searches feed ranked collection before more searching. Stop only at
the useful-page goal, chosen request ceiling, deadline, cancellation or actual
exhaustion of eligible providers/queries/pages. Expose stage, provider, blocked
proposal and engine fallback in persisted tool/planner activity.

Checklist complete: discovery/design, implementation, behavioral and native
verification, independent review and documentation. Focused local commit remains
the handoff action. The native investigation still has no corroborated target
page; evidence retention and recovery execution do not establish identity.
No schema conversion needed: settings live in additive report JSON; existing
originals are preserved. No external publication or new worktree authorized.

## Local nginx and page lifetime (2026-10-10)

Additional explicit user requirements: own nginx front end, and tear down all
app-owned background work when the browser page closes or leaves the app.
Canonical launcher runs a dedicated foreground nginx master and one worker,
private generated configuration and temporary files, on 127.0.0.1. Python serves the authenticated
API on a separate ephemeral loopback port. Host/Origin checks use the actual
browser-facing origin; no global nginx configuration or background service is
used. Explicit direct mode remains useful for installations without nginx.

Authenticated per-tab leases track live pages. Last close triggers shutdown after
a short refresh grace; opening a replacement page cancels that shutdown. Heartbeat
expiry covers crashes, while a generous lease avoids ordinary background timer
throttling. Shutdown cancels investigations, stops only the owned model child and
nginx process group, and closes the API listener. Durable original evidence stays
intact.
Browser cleanup delivery is best effort; lease expiry is the fallback. Native
verification must distinguish source tests, real nginx routing, model ownership,
refresh, multiple tabs and actual page navigation teardown.
