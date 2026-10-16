# Recovery and runtime reliability fixes

Status: implemented, qualified, independently reviewed and integrated locally
into main on 2026-10-16, following the user's local-only instruction. Baseline:
`609d433` on `codex/local-osint-engine`. Existing cases and evidence are immutable
inputs to this work. No schema migration or dependency installation is needed.

## Scope and acceptance

| ID | Required outcome | Acceptance evidence |
| --- | --- | --- |
| R1 | Explicit Retry performs a fresh authorized attempt instead of replaying a cached failure | Fail a page, approve Retry through the API, restore its transport, observe one fresh collection and retained prior attempt/originals; automatic continuation still avoids completed URLs |
| R2 | Continue recovers transient source failures after cooldown with bounded retries | Temporary 503 then restored provider causes a later real attempt; completed/permanently blocked work remains preserved; ceilings and cooldowns prevent loops |
| R3 | One active owner controls each workspace's search service | A second manager fails before inspecting/stopping the first container; normal close releases ownership; stale receipts recover after OS lock release; failed initialization releases resources |
| R4 | Slow Stop remains responsive through nginx | Stop returns 202 promptly, status exposes stopping/terminal/error, no HTML 504 on the request, cancellation and eventual cleanup remain safe |
| R5 | Unexpected failures are diagnosable locally without exposing private data | Bounded private diagnostic events contain generated ID, allowlisted operation and exception class only; UI/API show an actionable reference; no request body, token, URL/query, private clue or exception message enters diagnostics |
| R6 | API transport errors remain understandable | HTML/non-JSON proxy errors and network failures show useful local-service status and recovery instructions; valid JSON validation errors retain their meaning |
| R7 | Test SQLite connections are explicitly closed | Warning-enabled tests and the full suite finish without unclosed-database ResourceWarnings |

## Design

Recovery stays in the existing Investigation/Frontier/Workspace flow. Distinguish
an explicitly approved new attempt from automatic reuse: carry the exact approved
input through the shared guard, invalidate only that attempt's cached result, and
retain old evidence and attempt records. Requeue only eligible transient failures
after their cooldown, with a small persisted attempt limit; do not automatically
retry policy blocks, permanent HTTP failures or completed work.

SearchService holds an OS-backed workspace lease for its manager lifetime, using
the existing WorkerLease implementation. Acquire before receipt reconciliation,
release only through idempotent close, and reject closed-manager mutations. Stop
does not release manager ownership. Server initialization and close must release
partial resources on failures. An OS lease avoids PID reuse and stale PID files.

Stop uses the same background-operation and pollable-state approach as Start.
Cancellation signals the startup worker, but the stop worker must not join itself
or replace the startup worker before cancellation is complete. Concurrent
start/stop calls receive stable state or a clear busy response. App departure
still waits for owned cleanup rather than leaving a detached stop worker behind.

Diagnostics are local, private, bounded and deliberately metadata-only. Reuse one
recorder for unexpected HTTP and background-service exceptions. Expected source
blocks remain in existing per-action history. The frontend catches transport and
non-JSON failures centrally; it never renders proxy HTML or invents a successful
service state. SQLite test cleanup uses explicit connection closing.

Increasing nginx timeouts would only hide synchronous Stop and leaves unusable
proxy errors. PID-based ownership is weaker than the existing OS-lock primitive.
Neither alternative is selected. No new service, telemetry, cloud AI or raw error
logging is introduced. Upstream 403/429/999 blocks are not bypassed.

## Delivery sequence and ownership

- [x] Inspect baseline, reproduce defects and define acceptance/design.
- [x] Implement R1/R2 in the canonical recovery flow with behavioral regressions.
- [x] Implement R3/R4 in SearchService; integrate server cleanup.
- [x] Implement R5/R6 and R7, update user/operator documentation.
- [x] Finish browser qualification; Python/UI/native nginx/Docker checks passed.
- [x] Commit coherent slices; complete available independent review/fix rounds.
- [x] Resolve integration scope: user selected local only; GitHub gates do not apply.
- [x] Fast-forward local main to the reviewed changes and verify destination.
- [x] Retire the fully merged local task branch after verifying ancestry.

The primary agent owns integration, server/UI/diagnostics and handoff. A bounded
recovery implementer owns investigation/frontier/workspace and its regressions;
a bounded service implementer owns SearchService and its regressions. Independent
review follows implementation on pinned commits, with up to ten rounds.

## Risks, integration and rollback

The critical path is shared recovery invariants → integrated service lifecycle →
whole-flow qualification → independent review → integration. Preserve exact URL
approval boundaries, completed originals, attempt history and container receipts.
Exercise cancellation and constructor failure explicitly. Test with synthetic
cases and owned temporary services; record real-provider proof separately.

The user selected local-only integration. The repository has no remote; GitHub
PRs, configured remote reviewers and CI are outside the final delivery scope.
Independent Codex review supplied the completed review source. Local main was
created from baseline `609d433` and fast-forwarded to `d44f81b`, retaining all
project history. No source was published or exported to a third-party reviewer.

Rollback is a normal revert of the focused fix commits. There is no data schema
conversion. Release of OS ownership permits the prior service manager to start;
existing evidence and attempt records remain readable. No tag, package release
or production deployment is included.

## Pattern and evidence ledger

Codanna 0.16.0 is indexed for this exact checkout; graphify and direct source
inspection locate callers, sibling paths and public boundaries. Each fix must
cover the local defect, regional siblings (manual tools/queue/Continue and
start/stop/close), and global shared callers before being considered isolated.
Completed review and qualification receipts follow below.

| Cluster | Pattern search and repair boundary | Verification |
| --- | --- | --- |
| R1/R2 | Codanna resolved page/run_queue/web_search and their callers/impact; source searches confirmed manual dispatch, queue, Continue, caching and persisted attempts. Approval was lost at the page dispatcher; failed search deduplication never reactivated eligible work. Repair shared authorization and durable task boundaries. Domain-signal caching has a different cause and remains outside search recovery. | Nine recovery regressions and three authenticated API journeys; legacy 429 without a saved deadline also retains five minutes |
| R3/R4 | SearchService constructor/start/stop/close plus all server callers use one lifetime manager lease. Jobs' per-worker lease is an intentional separate boundary. Separate startup/stop workers preserve cancellation identity; nginx timeout remains unchanged. | 26 service tests, native child-death/lock and nginx slow-cleanup tests; real Docker startup/search/Stop |
| R5/R6 | Local handler error branches, background service/investigation catches, all frontend API callers and existing private JSON writes inspected. One shared metadata recorder and atomic writer replace silent unexpected errors; expected validation/source outcomes remain intentional exceptions. Graphify and Codanna LocalServer impact identify launcher, lifecycle, source API and proxy callers, all covered by regression tests. | Five diagnostics tests, authenticated privacy/reference tests, transport/UI regressions; raw error canaries absent |
| R7 | Global sqlite3.connect search found three unclosed test connections. Production Engine.connection already closes; workspace migration test already uses finally/close. | Explicit contextlib.closing; complete warning-enabled suite clean |

Qualification: 377 Python tests passed (27.550s), 28 UI tests passed, syntax and
compilation passed. Real SearXNG via nginx completed a public search and accepted
Stop in 0.003s, reaching stopped; original synthetic evidence unchanged. Full
receipts and image identity are in [validation](validation.md).

Browser qualification passed safe diagnostics, actionable Docker-unavailable
Retry, five-second asynchronous Stop with disabled controls, terminal recovery
and empty warning/error logs. Owned test fixtures stopped after leaving their
pages. Read-only fingerprints of the actual cases/evidence/entities/mentions
match the pre-change snapshot: 3/515/4,678/35,832 records unchanged.

## Review and integration ledger

Local commits:

- `0a20b92 test: close SQLite connections explicitly`
- `77b5524 fix: recover investigations and own service lifecycles safely`
- `d44f81b docs: record reliability qualification and review outcomes`

Configured available source: independent Codex review, separate reviewer context,
read-only pinned range. Review authority: explicit user invocation of
iterating-pr-bot-fixes. GitHub integrations cannot be discovered without a remote;
none is represented as clean, absent or unavailable by inference.

| Round | Source and scope | Request / outcome | Disposition |
| --- | --- | --- | --- |
| 1 | Independent Codex; `609d4339be5e19000e4a6d6bb88924dbb0e86748..77b5524e957ecf1e8e64122778e33d4fdb80c041` | Requested after committing the package; reviewer report dated 2026-10-16T19:59:33Z, saved and read back as `osint-reliability-review-round1.md` in the local temporary evidence directory. Complete: no actionable findings. Independently passed 58 focused Python tests (2.801s) and 28 UI tests, unchanged reviewed head and clean working tree. | Explicit clean; retired. No second round required. |

The active review-source set is empty after the clean outcome. Only qualification
and handoff documentation follows the reviewed implementation commit. The saved
report distinguishes its focused checks from the primary agent's full-suite,
native Docker/nginx and browser evidence.

The user's local-only reply resolved the former GitHub identity blocker. On a
clean checkout, local main was created at `609d433` and fast-forwarded to
`d44f81b82f75648dd575112d44aefe4c2fe9a058`. The merged source and tests are
identical to reviewed head `77b5524`; only qualification/handoff documentation
follows it. All task history remains reachable from main. The fully merged
`codex/local-osint-engine` branch was deleted after verifying ancestry; main is
the only remaining local branch and the primary checkout remains in place.
No remote, upstream, PR, hosted checks, release or deployment was created.

The observed reliability failures were boundary failures: approval lost between
two guards, durable failed tasks never reactivated, manager ownership narrower
than service lifetime, and synchronous cleanup longer than the proxy deadline.
Regressions now exercise those shared boundaries and distinguish local lifecycle
success from upstream search availability and factual target coverage.
