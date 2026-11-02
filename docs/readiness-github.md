# Readiness and public GitHub collection

Implementation and verification record, 2026-11-02.

## Scope

Extend the existing trusted connector registry, dispatcher and evidence store with
local readiness diagnostics and public GitHub repository/issue collection. Keep
collection opt-in, processing local, original responses immutable, and private
comparison clues outside outbound requests. No Agent-Reach runtime, credentials,
browser sessions, hosted processing or additional package dependency is introduced.

## Milestone 1: local readiness diagnostics

Completed:

- One local report powers `osint doctor`, authenticated `/api/readiness` and the
  browser readiness panel/source cards.
- Implementation, prerequisites, process network permission, saved case health,
  observation age and cooldowns remain separate. A previous successful operation
  does not establish current reachability or the presence of useful evidence.
- Inspection reads local files and recorded metadata. It does not perform DNS,
  open sockets, execute binaries, construct/start runtimes, install tools, or
  perform live source checks.
- Source cards and open forms respond to refreshed availability. Case changes
  reject late readiness responses from a previously selected case.

Verification:

- Full Python suite: **385 tests passed**, using
  `.venv/bin/python -m unittest discover -s tests -v`. The suite ran outside the
  sandbox because authenticated local HTTP journeys require loopback listeners.
- Browser behavior harness: **32 tests passed**, using
  `node --test tests/test_workspace_ui.cjs`.
- `node --check local_osint/static/app.js` and `git diff --check` passed.
- Independent Code Reviewer inspected the updated implementation and reran the
  readiness, CLI, authenticated source API and UI tests. No actionable P1/P2
  findings remained. A stale-availability transition found during review was
  corrected and covered by a behavior test before commit.
- A real browser in an offline temporary workspace displayed readiness without a
  case, refreshed the local inspection time, and distinguished installed tools,
  stopped/unprobed AI and missing metasearch setup. The screenshot was inspected.
  The existing missing favicon returned HTTP 404; no feature error was observed.
- `graphify update .` refreshed the ignored local AST graph without semantic
  extraction or paid API calls.

Saved source health currently collapses successful and valid-empty operations into
`ready`; diagnostics label this conservatively as the last successful operation.
The new report does not claim provider or model qualification from installed files.

## Milestone 2: public GitHub collection

Completed:

- Two trusted sources accept exact public repository and numbered issue URLs.
  Fixed unauthenticated GitHub REST endpoints use API version `2026-03-10` through
  the existing pinned HTTPS transport. Ambient tokens, cookies, proxy settings,
  returned download links and pagination links are not used.
- Repository collection reads metadata and the preferred README. Issue collection
  reads its body and at most the first 30 comments. Each action reserves two
  collection units before dispatch; second requests respect cancellation and the
  collection deadline. Part failures retain earlier successful originals.
- Every returned response is retained before interpretation. Decoded README,
  issue and comment text has a versioned extraction tied to the original hash.
  Repeated comment passages retain distinct exact spans and author/comment labels.
  The evidence viewer and case search expose derived text while keeping the
  original response inspectable. Identity and corroboration remain unconfirmed.
- Automatic routing requires the enabled pack and an exact public seed or
  recorded public-source reference. Imported text cannot grant collection scope.
  Discovery from another source does not automatically refetch the same GitHub
  resource. Explicit Retry remains subject to its normal cooldown and permissions.
- GitHub primary/secondary rate limits retain bounded Retry-After/reset deadlines,
  including after the third automatic attempt. Automatic retry ceilings remain.

Verification:

- Final full Python suite: **401 tests passed** in 27.797 seconds, using
  `.venv/bin/python -m unittest discover -s tests -v` outside the sandbox for
  loopback HTTP tests. This includes **16 new GitHub behavior tests** and existing
  transport, frontier, migration, document, provenance, retry and API regressions.
- Browser behavior harness: **33 tests passed**. JavaScript syntax and whitespace
  checks passed. The provenance regression verifies original database bytes,
  hashes, text and collection time remain immutable across extraction versions.
- Independent Code Reviewer found an automatic self-follow-up loop during the
  first pass. It was corrected before commit, with repository/issue regression
  tests proving one bounded action. Re-review found no remaining actionable P1/P2
  issues, and independently checked the updated original-evidence invariant test.
- Real-browser offline checks showed both new source cards disabled; no source
  request was sent. The generic extraction viewer has behavioral coverage for
  multiline text, inert markup, original-response separation and local retrieval.
- Native live qualification used temporary fixture cases and explicit public
  reads of `octocat/Hello-World` and its non-PR issue `11679`. Both actions completed
  in one attempt using two reserved units. Repository metadata/README produced
  two originals; the zero-comment issue produced one. All original SHA-256 values,
  derived-text hashes, original links and source-search results were checked.
- After those reads, local readiness showed the saved successful observations and
  timestamps while leaving current source reachability unverified. Blocking DNS
  and subprocess mocks confirmed this inspection performed neither operation.
- The local AST graph was refreshed with `graphify update .`; graph outputs stay
  ignored and no semantic extraction/API charge was requested.

The live fixture does not qualify nonempty comment collection, rate limits or
provider failures; deterministic tests cover those behaviors. Collection remains
unauthenticated, subject to upstream availability and rate limits, and bounded to
the selected resource/first comments page. Pull request content, repository-wide
crawling, accounts, browser sessions and cloud processing remain outside scope.
