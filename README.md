# oh no, she OSINT.

A private investigation workspace for **people, organizations, domains, topics
and other subjects**. Search public sources, follow relevant leads, inspect what
each tool did, and keep original evidence on your machine. A Wikipedia entry is
not required.

Collection goes online. Storage, searching, comparison and optional llama.cpp
analysis stay local. The core uses Python's standard library and SQLite: no cloud
AI, cloud database, telemetry, provider accounts or API keys required. The app is
MIT licensed; optional tools and source definitions have their own
[licence notices](docs/SOURCE_LICENSES.md).

## Start

Install Python 3.11+ and nginx. On macOS, `brew install nginx` installs the binary;
do not start a separate Homebrew nginx service. From this project directory:

```sh
./start.sh
```

The launcher creates a project-only virtual environment if needed, starts its own
foreground nginx on a free `127.0.0.1` port and opens a private launch link. nginx
forwards to an ephemeral loopback Python API. Generated configuration and runtime
files stay in private app directories. Keep the launch link private.

Closing or leaving the last app page checkpoints work and stops app-owned backend,
nginx, local AI and optional metasearch. Reloads have a two-second grace period;
other open app pages keep the runtime alive. If a page crashes without its close
event, the heartbeat expires after 90 seconds. **Ctrl+C** also stops the app.
Saved cases remain on disk. Reopen with `./start.sh`, select the case, and use
**Resume pending actions** or **Continue investigation**.

```sh
./start.sh --port 9000                    # request a specific free local port
./start.sh --data-dir ./private-cases     # choose private local storage
./start.sh --offline                     # prohibit external collection, including DNS
./start.sh --direct                      # explicit Python listener without nginx
./start.sh --model /absolute/path/model.gguf
```

Without the launcher:

```sh
python3 -m venv .venv
.venv/bin/python -m local_osint serve --nginx --open --allow-network
```

Windows uses `.venv\Scripts\python.exe`. Windows support is intended; native
validation recorded so far is on macOS. Python 3.11+ is the declared floor; see
[validation evidence](docs/validation.md) for versions actually run.

## Investigate with one action

1. Select **New case**, choose person, organization, domain, topic or other, and
   enter its name. Optionally enter the public question you want answered.
2. Add public country, source language, organization or context if it helps
   distinguish the subject. These fields and the objective may guide external
   searches. Keep private contact details, URLs and context in **Private comparison
   clues**; they remain local unless explicitly supplied again as public inputs.
3. Press **Investigate**. The app chooses a source recipe for the subject, starts
   the available cached local AI and follows relevant public evidence. You do not
   need to choose source tools, start a model manually or find pages first.
4. Read **Findings**, **Possible matches**, **Questions** and **Activity**. Findings
   cite exact passages; possible matches remain separate; questions expose source
   support and remaining gaps; activity shows actual calls, inputs and failures.
5. For each candidate, choose **Same target**, **Different target** or **Unsure**.
   Decisions are local, reversible and recorded. Marking a candidate as the target
   does not verify every statement on its pages. Different-target observations stay
   retained and are explicitly labelled rather than silently attributed.
6. Use **Continue investigation** to extend the same saved case. The automatic
   recipe resumes the durable frontier and can change source strategy/provider.
   **Resume pending actions** runs the saved queue directly. Existing originals,
   attempts and prior reports stay intact.

Local AI is enabled by default and starts in the investigation worker when its
cached model/runtime is available. If it cannot start, deterministic source
recipes continue and the warning stays visible. **Local AI settings** offers an
explicit opt-out and manual controls. No model or package is downloaded by an
investigation. Reopening a case does not itself start outbound work.

The public objective can be supported, disputed or unresolved separately from
identity and independent-corroboration questions. Local AI can interpret an
objective using literal references to offered evidence; quoted text is verified
against the saved source, but semantic correctness still needs review. Discovering
a readable page cannot automatically answer an arbitrary question or identify a
person. Partial status and its reason remain visible even with many collected pages.

Advanced query, source packs, prompts, explicit individual tools and collection
limits remain available without being prerequisites. **Stop** retains evidence;
an in-flight request/model call may finish before cancellation takes effect.

Export Markdown or JSON when needed. JSON includes original bytes, hashes, case
details, run history, workspace state and every saved extraction version with its
text and page provenance. Review private fields before sharing.
The candidate-filtered **Evidence relationships and observation timeline** links
observations to supporting/contradicting originals. Its dates are local recording
times, not inferred real-world event dates. Large views show up to 80 entries;
exports retain all references.

## Optional advanced source tools

Open **Advanced source tools · configure and run** when you want an individual action. Each card explains inputs, destinations,
licence and availability. **Configure & run** submits only the shown inputs and
saves the action. A single source can run without a full investigation; local
tools remain available in offline mode.

| Source tool | Input | Result |
| --- | --- | --- |
| Website source discovery | Public HTTPS URL | Same-host links, feeds, sitemap references and structured source metadata; eligible leads enter the queue |
| Crossref publications | Public query | Publication metadata and DOI links; the automatic recipe adds it for research/academic context |
| GitHub public profile | Public username | Public API account metadata; ownership by the target remains unconfirmed |
| Public username profile | Username and GitHub/GitLab site | Requires matching account metadata; generic HTTP 200 responses, login screens and challenges are insufficient |
| Wayback public URL lookup | Public URL | Existing archive availability with original URL and capture date |
| Public place references | Public locality query | GeoNames references; no private-address or automatic Nominatim lookup |
| Local email header analysis | Saved source selected by title | Local analysis of imported UTF-8 `.eml` or header evidence; no correspondence upload |
| Common Crawl URL discovery | Approved public URL prefix | Up to two operations for index catalogue and index query; bounded capture/URL metadata, not WARC bodies or substantive page evidence |
| Public federated account lookup | Exact public `user@domain` | WebFinger at that account's domain; no guessing of usernames across instances |
| Public ActivityPub profile | Approved or source-discovered profile URL | Public actor metadata; linked contexts, inboxes, outboxes and follower collections are not fetched |
| Owned SearXNG metasearch | Public query | Optional local service with selected external engines, fanout and failure reporting |

Automatic account checks use only **Approved public usernames**. Newly discovered
handles do not authorize guessed checks on other sites. Automatic WebFinger
follow-up requires an exact public account literally present in a saved source;
it is not cross-instance username enumeration. A manually configured source action
authorizes its exact input, not wider collection. Public starting URLs remain
separate from private case clues. Automatic sources and links are revalidated at
dispatch against network permission, public URL scope and private-clue guards.

**Run a tool** also retains individual web/news/encyclopedia searches, public-page
collection, domain signals and saved-evidence reads. No extra shell tool server
or Python package installation is required.

## Progress, recovery and limits

The workspace reports **discovery leads**, **readable documents** and
**corroborated claims** separately. Snippets create leads. Login walls, empty shells
and unrelated pages stay saved but do not fill the useful-page goal. Readability
is separate from attribution and corroboration. Archived copies and syndicated
material are not independent corroboration of the same original source. AI
observations default to unconfirmed.

The persistent queue records connector, arguments, purpose, originating evidence
and candidate where known, priority and attempt history. Outcomes distinguish
success, valid empty results, blocked access, rate limits, uninterpretable responses
and errors. Failure is not proof of no public footprint.

Local AI can rank offered actions and propose typed follow-ups using an exact
quote from an offered public source. New search vocabulary, URLs and federated
accounts must be tied to that source and pass the same private-clue/scope guards.
Repeated, invalid or ungrounded proposals fall back to queue order and fresh
source/provider strategies. Adaptive web search can change provider after
repetition, poor results or a provider failure. Saved case clues guide local namesake comparison.
These assessments remain unverified; originals never change.

**Continue investigation** retains prior sources and useful-page progress, uses
the same automatic source recipe and local AI preference, and plans further
permitted work. **Resume pending actions** processes the saved queue under current settings. An unfinished
remote read recovers as **interrupted after dispatch**: it may have reached the
source before the crash. Resuming can repeat that read, retaining the previous
attempt. Exactly-once delivery is not promised.

Cooldowns and connector health survive restarts. Inspect the eligible-after time
before retrying; retry cannot bypass a current rate-limit cooldown. A partial run
can retain cooling-down work for later resumption.

Defaults: **24 collection units**, **4 useful pages**, **300 seconds**. Request
ceiling and page goal accept positive exact browser integers through 2^53−1.
Choose another duration or enable **Keep working while this page is open**
(`runtime_seconds = 0`, `keep_working = true`). This removes the soft elapsed-time
deadline while retaining request ceilings, page-collection goals, source exhaustion,
per-request limits, cooldowns and Stop. It cannot guarantee more evidence.

Units count operations, not packets or exact upstream requests. RDAP reserves
directory and registry work; redirects can add contacts within an operation. One
SearXNG action can contact all selected engines. Its separately reported fanout
is configured engine count, not exact upstream traffic. Local reads and model
processing use no outbound collection units.

A useful-page goal limits page collection; reaching it is not a completion test.
Question support, candidate identity and independent corroboration remain separate.
Raise the goal under advanced limits if more pages are needed. Source exhaustion,
provider failures, cooldowns, unanswered questions and budget/time limits can leave
an investigation partial. The app reports that state rather than calling volume
or a successful HTTP response a completed investigation.

Source origin is tracked as unknown, original, copy, syndicated or archive with
lineage where known. Unknown origin is not independent corroboration. Copies and
archive captures cannot multiply the independent support of one original merely
by appearing at several URLs.

## Import documents and scans locally

Under **Import a file or paste notes**, choose PDF, DOCX, ODT, PNG, JPEG or TIFF;
the format is selected automatically. UTF-8 text, email `.eml`, saved HTML, JSON
and CSV remain supported. No source-tool choice is required. Original bytes and
their hash stay in the case; extracted/OCR text is a separate versioned artifact.

DOCX/ODT text uses Python's standard library without opening an office application.
PDF text uses installed Poppler; scanned PDF pages and images use installed
Tesseract. These optional native tools are not installed automatically. If needed
on macOS, install them yourself with `brew install poppler tesseract`; ensure the
required language data is installed. Default OCR is English (`eng`). The public
source-language field affects research, not the OCR language setting.

Evidence details show extraction status, numbered pages and warnings. Missing tools
or image-only pages leave originals available with an actionable warning. After
fixing the local prerequisite, press **Retry local extraction**. It creates/reuses
a versioned extraction without changing original bytes or invalidating earlier
citations. Claims cite the extraction version, page and exact character passage.
OCR can misread text: a matching extracted quote is not proof of what the image
actually says. Office pagination follows embedded page breaks and can differ from
rendered page layout.

Documents remain bounded to 2 MiB and at most 100 processed PDF pages; extraction
can be limited or unavailable. Images are capped at 16 megapixels and 10,000 pixels
per dimension. Multi-page TIFF is currently unsupported; convert it to PDF first.
Malformed/encrypted/unsafe office containers can be rejected. See
[security controls](SECURITY.md#local-document-processing) and the current
[native qualification receipts](docs/validation.md); format support does not imply
that every file, OCR language or native-tool version has been exercised.

## Optional local AI

Install [llama.cpp](https://github.com/ggml-org/llama.cpp) and cache a compatible
GGUF yourself. Example one-time download through an independently installed
Hugging Face CLI:

```sh
hf download terrorswift/REDCELL-26B-A4B-OSINT-Cyber-APEX-GGUF --include '*I-Compact.gguf'
```

The app detects the exact cached I-Compact GGUF, about 14.7 GB. Investigating and
starting AI never download models or install packages. A trusted launcher
`--model` flag selects another local GGUF. Tool calling and sufficient memory are
required; other model families need separate qualification.

Investigate and Continue start the available cached model automatically by
default. **Local AI settings** also provides manual Start/Stop and an opt-out.
**Analyze collected evidence** retries drafting independently once the model is
ready. Only app-owned model
processes are stopped; unrelated llama.cpp sessions remain untouched.

Eight editable prompts cover planning, repetition, provider failure, no useful
pages, identity ambiguity, draft writing, action selection and exact-passage
extraction. Under **Advanced recovery settings and AI prompts**, each accepts 1–4,000
characters, with a combined 12,000-character limit. Prompts persist in local run
options. **Restore recovery defaults** changes the form, not saved reports or
sources. Prompts cannot expand permissions, outbound scope or resource limits.

Completed and partial investigations include an interactive **Evidence Atlas**.
Select a source, possible match or question to trace recorded citations and open
saved evidence. The map labels uncertain matches, unknown origins and unanswered
questions. Its central subject is a research anchor; positions are visual layout,
and connecting lines represent saved provenance. A keyboard-accessible picker
provides the same interactions. Large cases show a bounded selection; the tables
and export retain all records.

Automatic findings display verified source passages with source IDs. Local AI
plans follow-ups, extracts literal observations and interprets questions, while
the automatic findings renderer adds no free-form model assertions. The advanced
**Analyze collected evidence** action can still produce an unverified prose draft.

Case-scoped SQLite retrieval brings relevant evidence into draft context rather
than relying only on the earliest pages. FTS5 is used when available, with a local
literal-search fallback. Extracted observations must match source text exactly
before being saved. Draft citations remain unverified: a valid evidence ID alone
does not prove every sentence is supported. Inspect the cited passage.

Private clues go to local comparison, not public searches. Output guards reduce
literal/private-contact echoes but cannot detect all inference or paraphrase.
Inspect drafts before sharing; case exports intentionally retain private fields.

## Optional SearXNG setup

The core works without Docker or SearXNG. Install and start Docker yourself. Open
**Advanced source packs and collection time → Optional local metasearch**, review options,
then press **Set up & start metasearch**. This explicit action may download the
selected official image. Ordinary investigations do not silently install,
download or start a missing search service.

Only `docker.io/searxng/searxng` tags or SHA-256 digests are accepted. Setup records
an immutable local image ID; container creation uses that ID with pulling disabled.
Choose Bing, DuckDuckGo and/or Brave. Stop the owned service before changing setup.
The app does not install or stop Docker itself.

Setup checks the Docker engine before downloading an image or creating a container.
If Docker is stopped or inaccessible, the service panel shows an error with recovery
steps. Start Docker Desktop (or your Docker engine), wait until it is ready, then
press **Set up & start metasearch** again; you do not need to reopen the app.
If Docker is already running, check its connection and permissions. Progress and
errors update automatically, and **Stop metasearch** can cancel an active setup.
Your saved cases, evidence and pinned image settings survive a failed start.

No host port is published. A fixed Docker-exec client contacts container-private
loopback; the browser cannot choose another metasearch endpoint. The container
has no host bind mounts or Docker socket, drops capabilities and applies bounded
memory, CPU, PID and request settings. Labelled ownership records support cleanup
and reconciliation after interruptions.

SearXNG makes its own external requests. They **do not inherit the core
collector's DNS validation or IP pinning**. Engine selection, timeouts and disabled
retries constrain behavior but do not make the transports equivalent. Upstream
engines see approved queries and the network's public IP. Read
[security](SECURITY.md) and [qualification status](docs/validation.md).

## Collection and privacy

| Recipient | Outbound data |
| --- | --- |
| DuckDuckGo Lite / Bing | Approved public query, context and refinements |
| Crossref / GeoNames | Chosen public research/locality query |
| GitHub / GitLab | Approved account handle/profile request |
| Internet Archive / Common Crawl | Approved public URL/prefix whose archive or index metadata is requested |
| Federated account host / public actor host | Exact approved/source-derived public account and selected profile request |
| Wikipedia / Wikidata / GDELT | Public subject query, selected IDs and page requests |
| Public websites and redirects | Host, path and permitted query parameters |
| DNS / IANA / registries / TLS endpoint | Relevant domain/hostname |
| SearXNG upstream engines | Approved query sent to selected engines |
| Docker registry during explicit setup | Official image request/network metadata |
| Local email analysis / SQLite / llama.cpp | No outbound request for local processing |

Core HTTPS collection requires public HTTPS port 443, rejects URL credentials,
fragments/private destinations, validates DNS and pins a public address with TLS
hostname verification. Up to three redirects repeat those checks. Investigation
page tools permit query parameters; CLI `fetch` retains its no-query restriction.
Do not supply signed or private links.

Sources can be blocked, rate limited, stale, JavaScript-only, incomplete or
malicious. The app does not bypass login, CAPTCHA or access controls. Public
profile checks do not use password-reset/account-recovery queries. No breach
aggregation, people-search brokers, facial recognition, vulnerability scanning or
cloud sync is included. Local processing does not anonymize collection.

## Storage, upgrades and recovery

Default storage: `.local-osint/evidence.sqlite3`. It holds cases, immutable
originals/hashes, extracted text, indicators, reports, settings, actions, attempts,
candidates and exact-passage references. Same case + source + hash deduplicates;
changed content becomes separate evidence. Hashes detect changed bytes relative
to a reference, not authorship or truth.

First opening a schema-1 or schema-2 database makes a consistent,
integrity-checked owner-only backup at
`.local-osint/backups/schema<old-version>-<unique-id>.sqlite3`, then upgrades the
same database transactionally to schema 3. Existing cases, original evidence,
hashes, reports and identifiers remain. Schema 3 adds questions, identity decisions,
source-origin lineage and document-page provenance alongside the existing queue.
Historical claims are not promoted to verified identity or independent support.
Migration is offline and starts no collection. Stop other app/CLI writers before
upgrading and retain the pre-upgrade backup.

```sh
.venv/bin/python -m local_osint backup evidence-backup.sqlite3
```

To restore or roll back: stop every process using the destination; retain the
current private directory; create a separate owner-only directory; copy the
chosen backup there as `evidence.sqlite3` with mode `600`; launch with `--data-dir`
pointing there. This version upgrades restored older-schema backups again. Use a
previous compatible app version if you need to retain its earlier schema. Never overwrite an open
database. JSON export is not a database restore format.

POSIX data directories require `700`; database/backup files require `600`.
Symlinked data paths are refused. Avoid shared or cloud-synced folders. Disk
encryption, total disk space, backup retention and download permissions remain
OS/user responsibilities. No case/evidence deletion or automatic backup pruning
is performed.

Optional launcher `osint.toml`:

```toml
data_dir = "private-cases"
port = 0
```

```sh
./start.sh --config osint.toml
```

Relative data paths resolve beside the config. Command options take precedence.
Network permission remains a launch flag; source settings cannot enable networking
in an offline process. Explicit occupied ports fail; `--port 0` chooses a free one.
Unknown settings fail visibly.

## CLI and development

The CLI uses the same engine:

```sh
.venv/bin/python -m local_osint case "Example Foundation" --type organization --alias Example
.venv/bin/python -m local_osint cases
.venv/bin/python -m local_osint import CASE_ID examples/sample.txt
.venv/bin/python -m local_osint investigate CASE_ID --allow-network
.venv/bin/python -m local_osint investigate CASE_ID --allow-network --ai --question "Which public sources describe its work?"
.venv/bin/python -m local_osint discover CASE_ID --sources wikidata --allow-network
.venv/bin/python -m local_osint research CASE_ID example.org --signals dns registration certificate website --allow-network
.venv/bin/python -m local_osint fetch CASE_ID https://example.org/ --allow-network
.venv/bin/python -m local_osint search CASE_ID example --kind domain
.venv/bin/python -m local_osint entity CASE_ID ENTITY_ID
.venv/bin/python -m local_osint export CASE_ID case-evidence.json
.venv/bin/python -m local_osint export CASE_ID case-report.md --format markdown
```

CLI investigation uses the same automatic question planner and resumes saved
case work. `--ai` starts the cached local model and stops that owned model when
the command exits; it never downloads a model.

Commands accept `--data-dir` and `--config`. Investigation can return partial
results with exit status zero; inspect tool outcomes. Export/backup refuse to
overwrite files. Source-pack configuration and queue controls are in the app.

```sh
.venv/bin/python -m unittest discover -s tests -v
node --test tests/test_workspace_ui.cjs
.venv/bin/python -m compileall -q local_osint tests
node --check local_osint/static/app.js
```

Python checks use private temporary databases, loopback listeners and controlled
providers/models. Node controller tests require Node without packages; Node is not
needed to run the app. Tests do not establish live-provider availability or model
factual accuracy. See [validation](docs/validation.md), [design](docs/design.md),
[security](SECURITY.md) and [contributing](CONTRIBUTING.md).

Protocol references: [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/),
[GitHub public user API](https://docs.github.com/en/rest/users/users#get-a-user),
[Wayback APIs](https://archive.org/help/wayback_api.php),
[Common Crawl index](https://index.commoncrawl.org/),
[WebFinger RFC 7033](https://www.rfc-editor.org/rfc/rfc7033.html),
[ActivityPub](https://www.w3.org/TR/activitypub/),
[Poppler](https://poppler.freedesktop.org/),
[Tesseract usage](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html),
[SearXNG API](https://docs.searxng.org/dev/search_api.html),
[SearXNG outgoing settings](https://docs.searxng.org/admin/settings/settings_outgoing.html),
[nginx proxy directives](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[IANA RDAP bootstrap](https://www.iana.org/assignments/rdap-dns/), and
[Wikidata access](https://www.wikidata.org/wiki/Wikidata:Data_access).

Domain/email extraction uses a bundled
[IANA TLD snapshot](https://data.iana.org/TLD/tlds-alpha-by-domain.txt), dated
2026-10-09. It reduces filename/markup noise without proving domain existence or
ownership. No suffix update is downloaded at runtime.
