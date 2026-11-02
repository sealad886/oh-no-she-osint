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

Planned next: strict repository/issue inputs, fixed public REST endpoints, bounded
README/comment collection, retained response originals, versioned text provenance,
and guarded integration with the existing source frontier. No live collection has
been performed for this milestone yet.
