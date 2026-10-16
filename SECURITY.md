# Security and privacy

## Intended boundary

This is a single-user local investigation tool. Public collection goes online;
evidence storage, extraction, searching and optional model processing stay local.
It is not an anonymity network, a remotely deployable multi-user service or an
identity-verification system. Access to a source does not establish its truth.

## Local application and storage

- The Python listener binds only `127.0.0.1`. Canonical launch owns foreground
  nginx master/worker processes and an ephemeral loopback API listener. No shared
  nginx service/configuration, access logs, caches or third-party routes are used.
- A random per-launch 256-bit capability travels in the launch URL fragment,
  exchanges for an HttpOnly SameSite=Strict cookie, and is removed from the URL.
  Restart invalidates that session. Keep the link and cookie private.
- Exact Host/Origin checks plus the custom API header constrain browser CSRF,
  cross-origin reads and DNS rebinding. Cookie possession alone is insufficient.
  There is no CORS access, arbitrary-file route or configurable remote endpoint.
- Fixed static assets, DOM text nodes and a restrictive Content Security Policy
  keep evidence/source HTML inert. Imported HTML is text extraction input, never
  executable application markup. SQL uses bound parameters and case-scoped reads.
- POSIX directories require mode `700`, database/backup files `600`. Paths must
  not be symlinked; linked/non-owned databases are refused. File modes do not
  encrypt content or provide a Windows ACL guarantee.
- Original evidence bytes and hashes remain immutable. Changed content creates
  new evidence. Case/evidence references use foreign keys and transactional writes.
  Exports use a consistent snapshot and include private case data intentionally.
- Evidence bodies are capped at 2 MiB; request bodies at 3 MiB. Indicator extraction
  caps mentions per item. Searchable UI lists are bounded; exports retain originals.
  These are per-item/request bounds, not a total workspace disk quota.

Schema-1/2 upgrades first create a consistent, integrity-checked private database
backup, then add schema-3 tables/columns transactionally and offline. Historical reports stay intact;
imported old tasks are marked historical and cannot silently resume. New data
includes task/attempt history, source health, candidates, questions, reversible
identity decisions, source-origin lineage, versioned document text/page spans and
claim references. Rollback requires stopping writers and restoring a retained
backup into a separate private directory; see [README](README.md#storage-upgrades-and-recovery).
Migration is not consent to perform collection.

## External collection boundaries

Core HTTP collection uses one public HTTPS transport. It validates URL syntax,
port 443 and every resolved address, rejects private/special/mapped/transition
ranges, pins the chosen numeric public IP, and verifies TLS against the original
hostname. Up to three redirects repeat those checks. URL credentials, fragments,
HTTPS downgrades, cookies, authentication, proxy environment variables and ambient
TLS key logging are unsupported. Requests prefer identity encoding; gzip and
zlib-wrapped deflate responses are decoded with separate 2 MiB wire and decoded
limits. Malformed, incomplete, trailing/multiple streams and other encodings are
rejected. Evidence hashes cover the decoded response entity, not compressed
wire bytes or TLS traffic. Existing evidence originals are never rewritten.

Built-in API connectors construct requests for fixed providers; website/page
connectors accept approved public URLs. Selected page tools allow query parameters
because public source URLs often need them. CLI `fetch` retains its no-query rule.
Queries and redirect destinations are outbound disclosure: avoid signed/private
links and tracking values you do not intend to send. DNS resolution uses OS
resolver behavior and timeout policy.

Public collection requires process-level network permission and user-directed
work. Offline mode rejects provider/page/domain/connector operations before DNS.
Local email/document/OCR analysis, SQLite retrieval and cached-model processing
remain local.
The optional search-service setup action is separately explicit; it is not part
of an ordinary offline or missing-service investigation.

The trusted source registry declares input kinds, destination families, request
bounds, provenance/licence and availability. Configuration selects built-in
implementations; it cannot contain executable Python, shell snippets, arbitrary
plugins or model-generated commands. Password-reset enumeration, login-required
scraping, CAPTCHA bypass, brokers and breach aggregation are absent.

Automatic public-profile checks require usernames approved in case settings.
Newly discovered handles never authorize guessed checks on other sites. A separate
WebFinger path can resolve an exact account literally present in a cited public
source, after source/private-clue validation; it never guesses accounts across
instances. An explicit source action
approves only its exact inputs; this permission is persisted for resumption.
Private identifiers remain comparison clues, not automatically generated search
terms. The dispatcher revalidates private-clue rejection, connector permission,
public URL scope and current network permission immediately before execution.
A matching account handle or metadata field never establishes target ownership.

Outcomes distinguish successful observations, valid empty results, blocked access,
rate limits, uninterpretable responses and errors. Response originals are retained
where available. Login walls, shells and unrelated pages do not fill useful goals.
A challenge or failed parser is not represented as evidence of absence.

Common Crawl performs a bounded catalogue/index lookup for an approved public URL
prefix. It reads capture/URL metadata, not WARC bodies or an unlimited corpus.
Index captures are leads, never substantive page evidence. WebFinger requires an
exact public account and checks the returned subject. Public ActivityPub documents
must identify a plausible actor on the expected origin; contexts, inbox/outbox and
follower links stay inert. No posting, account login or cross-instance enumeration
is performed.

## Local document processing

PDF, DOCX, ODT, PNG, JPEG and TIFF originals stay local. Office text uses bounded
in-memory ZIP/XML parsing; archive traversal, symlinks, encrypted members, duplicate
members and XML entity/DOCTYPE declarations are refused. No office application or
macro runs. Optional installed Poppler/Tesseract tools process PDF text and scans
through fixed argument lists, not document-supplied commands. The app never uploads
a document or installs tools/language data automatically.

Inputs and extracted text are bounded to 2 MiB; office expansion to 8 MiB/300
members; PDF processing to 100 pages. Images are limited to 16 megapixels and
10,000 pixels per dimension, and multi-page TIFF is refused. Native calls use
private temporary files, parent-bound supervision, output limits, per-call time
bounds and a total extraction deadline; POSIX CPU/file-size limits also apply.
This is not a full native-parser sandbox. The installed binaries and their image/
document decoders are additional local trust boundaries running as the OS user.

Supported originals can remain available with unavailable/needs-OCR/limited
status when extraction cannot complete. Malformed/unsafe inputs may be rejected.
Retry extraction creates or reuses a versioned artifact without overwriting raw
bytes or previous cited spans. Page metadata includes extraction version and exact
text ranges; OCR can be wrong, and office page breaks need not match rendered
pagination. Matching a passage proves extracted-text correspondence, not visual
truth. Default OCR language is English and is separate from public search language.

## Persistent actions, retries and attribution

Actions persist their arguments, reason, candidate/source scope, priority and
attempt receipts. Deduplication preserves quoted phrases, operators and scope;
it is not broad token-set equivalence. Restart recovers dispatched unfinished
work as `interrupted_after_dispatch`: a remote read may have completed before the
local process died. Resume can repeat supported read-only actions. Exactly-once
external execution is not claimed. Existing evidence and prior attempts remain.

Rate-limit cooldowns/health survive restart. Retry cannot bypass an active cooldown.
Explicit Retry authorizes only the selected action's exact inputs and can perform
a fresh page request despite a cached failure; previous evidence and attempt
records remain. Automatic recovery does not refetch attempted pages or restart
completed, permanently blocked or imported historical actions. Eligible search
failures have at most three automatic attempts, a 60-second transient cooldown
or 300-second rate-limit cooldown, and respect any later connector deadline.
In-flight work can complete before Stop takes effect. Request ceiling, page
collection goal, per-request bounds and cancellation remain effective when the
user removes the soft elapsed-time deadline with page-scoped keep-working mode.

The single Investigate action selects a bounded source recipe and starts the
available cached model, with deterministic fallback if unavailable. Public objective,
country/language/organization/context are explicitly outbound-capable inputs;
private comparison clues remain local. Reaching a useful-page goal or obtaining
HTTP 200 cannot mark all investigation questions answered.

Candidates are source-backed, unconfirmed hypotheses; matching names do not merge
them. Reversible same/different/uncertain identity decisions are recorded separately
from claim and question status. Marking a candidate same does not verify every
claim. Unknown source origin is not independent support; original/copy/syndicated/
archive lineage must be established before independence is counted. Identical or
linked copies do not gain independent support merely by appearing at more URLs.

Observations cite exact start/end spans in immutable versioned text copies,
with text and original-evidence hashes. Invalid spans and cross-case references
are rejected. Supporting/contradicting references are separate; contradictory
references can mark a claim disputed. Generated local reports cannot support
source observations. Snippets, archive copies and syndicated text do not count
as independent corroboration merely because they appear multiple times.

A valid span proves the quote matches the saved text; it does not prove the
predicate, source truth, attribution or semantic entailment of a model's prose.
Automatic observations remain unconfirmed. Custom questions can receive local AI
interpretation with literal offered-source quotes; disputed answers require
supporting and opposing references. These checks validate citations, not arbitrary
semantic entailment. Question support stays separate from unresolved identity and
corroboration. Review the original source and its context.

## Local model boundary

The optional llama.cpp process uses a cached local GGUF. It binds loopback with a
random API key, disables its web UI, downloads, built-in tools and prompt logging,
and clears ambient llama/proxy/TLS-keylog settings. Browser requests cannot select
an arbitrary model path or endpoint; a trusted launcher `--model` selects a file.
No model/package download occurs during investigation or model startup. GPU memory
use depends on the chosen model and is not isolated from other local applications.

Eight bounded prompts guide planning, recovery, action ranking, extraction and
drafting. Prompt text and public source material cannot expand dispatcher scope.
The bounded action chooser selects offered IDs. Automatic planning can also
propose typed queries, literal source URLs or exact public federated accounts,
each tied to an offered saved source and verified quote. It cannot invent private
inputs, claim a synthetic report as authority or guess a cross-site handle.
Invalid, repeated or ungrounded proposals fall back to deterministic recipes,
provider changes and queue order. Extraction requires exact source passages;
case-scoped SQLite retrieval supplies relevant local evidence for drafting.
Assistant calls remain bounded; model output may be truncated or incomplete.

Private case context is supplied only to local comparison. It is not persisted as
raw model prompt logs, appended to queries, or used to enlarge approved public
vocabulary. Literal/private-contact output guards reduce echoes in draft and
blocked-tool activity. They cannot detect all paraphrases or inferred details.
Case metadata/exports intentionally retain private identifiers. Prompts saved in
run options can also appear in exports; keep private identifying clues in case
fields rather than custom instructions.

Model-invented URLs, unrelated queries, other-case evidence, filesystem access,
shell commands and arbitrary providers are rejected. Local-only final drafting
disables outbound tools in the dispatcher. Citations are restricted to available
case evidence, but draft correctness and identity attribution remain unverified.

## Optional SearXNG and Docker boundary

Setup is an explicit app action that can download an official
`docker.io/searxng/searxng` tag or digest. The resulting immutable image ID is
recorded and used with pulling disabled during container creation. Ordinary runs
do not install Docker or silently prepare a missing search service. Docker itself
is externally managed and is not stopped by app cleanup.

The owned container publishes no host port and mounts no host path or Docker
socket. Its JSON API binds container-private loopback. The backend uses a fixed
Docker-exec client with fixed API paths; neither browser nor model can supply
arbitrary commands, a container identity or a service endpoint. Ownership checks
compare persisted workspace/launch labels and reject unexpected host exposure
before control. The immutable image identity is used during container creation.
Failed reconciliation retains the ownership receipt rather than acting
on an unverified container.

Internal HTTP requests supply fixed `127.0.0.1` forwarding headers, and the local
limiter configuration trusts only that loopback address. Browser-supplied client
addresses and headers are not forwarded. Readiness verifies the private HTTP
health endpoint after ownership inspection; a running container alone is not
treated as a usable search service. Status probes have short deadlines to avoid
blocking the app's nginx proxy.

An OS-backed exclusive manager lease is acquired before receipt reconciliation.
A second app cannot control the live owner's service. Stop retains that lease;
close joins startup/shutdown workers before releasing it, and OS process exit
releases it automatically. Failed Docker cleanup preserves the exact receipt for
later recovery. Stop runs in a pollable background worker; a browser request
timeout or disconnect is not proof that cleanup did not run.

The container drops capabilities, disallows privilege escalation and bounds
memory, CPU and PIDs. Engine configuration selects only Bing, DuckDuckGo and/or
Brave; outgoing timeouts are bounded and HTTP retries disabled. JSON search
results retain configured upstream fanout and failures. Fanout is engine count,
not an exact accounting of remote requests or redirects.

**SearXNG's upstream HTTP transport does not inherit the core collector's DNS/IP
pinning or redirect checks.** The container still has network access for its
selected search engines. Docker access is a privileged host capability, and the
third-party image is an additional trust/dependency boundary. Resource settings
and a private API do not imply complete network isolation. Direct collection of
returned pages still uses the core pinned transport.

The upstream image can also download public ClearURLs tracker rules during
startup, trying its CDN and mirror hosts with bounded requests. Those static
downloads contain no case query, but expose ordinary connection metadata and
can time out independently of local service health.

See [validation status](docs/validation.md) for actual native-container qualification;
mocked Docker tests do not prove an image starts or real engines return results.
SearXNG retains its separate [AGPL licence](docs/SOURCE_LICENSES.md#searxng).

## Runtime ownership and departure

Authenticated per-page leases govern owned services. Last-page departure starts a
two-second reload grace; missing heartbeats expire after 90 seconds. Unknown page
IDs cannot close registered pages. Cleanup cancels jobs, checkpoints partial work
and stops only ownership-verified app children/container, including the owned
model, nginx and metasearch. Multiple pages keep the runtime alive.

A hard parent crash can leave resources temporarily. Persisted ownership receipts
allow reconciliation on a later startup, subject to OS/Docker reachability and
identity checks. Browser close delivery is best effort; this is not an OS-level
process or container watchdog guarantee.

## Private local diagnostics

Unexpected failures retain at most 100 local events: a generated reference ID,
timestamp, allowlisted operation and allowlisted exception class. The private
workspace's `diagnostics.json` uses atomic owner-only writes; the authenticated
Local diagnostics view exposes these fields. If persistence fails, in-session
events remain available. Request bodies, case contents, clues, URLs, search
queries, credentials, exception messages and tracebacks are not recorded.
Expected source blocks remain in normal per-action history. Proxy HTML is never
rendered as an error description; the UI reports a safe HTTP status and advises
checking operation status before retrying.

## Residual limits and reporting

A process running as your OS user can read evidence, steal the launch capability
or tamper with files. Protect your OS account, use disk encryption and private
backups/download folders. Local HTTP is unencrypted: do not port-forward or expose
this service through another reverse proxy. Public operators, resolvers, registries
and metasearch engines can observe requests and your public IP.

Sparse results, rate limits, source changes, parser/model errors and namesakes can
leave a partial investigation. No unlimited-corpus, denial-of-service, legal-
compliance, identity-verification or exhaustive security-certification claim is
made. Hashes and TLS establish limited technical properties, not content truth.

No public security-report destination is configured. Send sensitive findings
privately to the repository owner. Never place evidence, private identifiers,
credentials or launch links in public issues.
