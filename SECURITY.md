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

Schema-1 upgrades first create a consistent, integrity-checked private database
backup, then add schema-2 tables transactionally. Historical reports stay intact;
imported old tasks are marked historical and cannot silently resume. New data
includes task/attempt history, source health, candidates, versioned extractions and
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
Local email analysis, SQLite retrieval and cached-model processing remain local.
The optional search-service setup action is separately explicit; it is not part
of an ordinary offline or missing-service investigation.

The trusted source registry declares input kinds, destination families, request
bounds, provenance/licence and availability. Configuration selects built-in
implementations; it cannot contain executable Python, shell snippets, arbitrary
plugins or model-generated commands. Password-reset enumeration, login-required
scraping, CAPTCHA bypass, brokers and breach aggregation are absent.

Automatic public-profile checks require usernames approved in case settings.
Newly discovered handles never expand that permission. An explicit source action
approves only its exact inputs; this permission is persisted for resumption.
Private identifiers remain comparison clues, not automatically generated search
terms. The dispatcher revalidates private-clue rejection, connector permission,
public URL scope and current network permission immediately before execution.
A matching account handle or metadata field never establishes target ownership.

Outcomes distinguish successful observations, valid empty results, blocked access,
rate limits, uninterpretable responses and errors. Response originals are retained
where available. Login walls, shells and unrelated pages do not fill useful goals.
A challenge or failed parser is not represented as evidence of absence.

## Persistent actions, retries and attribution

Actions persist their arguments, reason, candidate/source scope, priority and
attempt receipts. Deduplication preserves quoted phrases, operators and scope;
it is not broad token-set equivalence. Restart recovers dispatched unfinished
work as `interrupted_after_dispatch`: a remote read may have completed before the
local process died. Resume can repeat supported read-only actions. Exactly-once
external execution is not claimed. Existing evidence and prior attempts remain.

Rate-limit cooldowns/health survive restart. Retry cannot bypass an active cooldown.
In-flight work can complete before Stop takes effect. Request ceiling, page goal,
per-request bounds and cancellation remain effective when the user removes the
soft elapsed-time deadline with page-scoped keep-working mode.

Candidates are source-backed, unconfirmed hypotheses; matching names do not merge
them. Observations cite exact start/end spans in immutable versioned text copies,
with text and original-evidence hashes. Invalid spans and cross-case references
are rejected. Supporting/contradicting references are separate; contradictory
references can mark a claim disputed. Generated local reports cannot support
source observations. Snippets, archive copies and syndicated text do not count
as independent corroboration merely because they appear multiple times.

A valid span proves the quote matches the saved text; it does not prove the
predicate, source truth, attribution or semantic entailment of a model's prose.
Automatic model observations remain unconfirmed. Review the source and its context.

## Local model boundary

The optional llama.cpp process uses a cached local GGUF. It binds loopback with a
random API key, disables its web UI, downloads, built-in tools and prompt logging,
and clears ambient llama/proxy/TLS-keylog settings. Browser requests cannot select
an arbitrary model path or endpoint; a trusted launcher `--model` selects a file.
No model/package download occurs during investigation or model startup. GPU memory
use depends on the chosen model and is not isolated from other local applications.

Eight bounded prompts guide planning, recovery, action ranking, extraction and
drafting. Prompt text and public source material cannot expand dispatcher scope.
Action planning chooses offered existing IDs. Invalid or repeated choices fall
back to deterministic queue order. Extraction requires exact source passages;
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
compare persisted workspace/launch labels and immutable image identity before
control. Failed reconciliation retains the ownership receipt rather than acting
on an unverified container.

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
