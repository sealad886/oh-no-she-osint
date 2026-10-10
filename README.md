# oh no, she OSINT.

A private OSINT workspace focused on **people, organizations, domains, topics or
other subjects**. Collect public signals online; store evidence, extract indicators,
search and write dossiers entirely on your machine. Enter a subject and press **Start investigation**; the app collects evidence for you.

No accounts, API keys, cloud database, analytics, cloud AI service, external fonts or
runtime dependencies. Open source under the [MIT license](LICENSE).

## Start

Install Python 3.11 or newer. From this project directory:

```sh
./start.sh
```

The launcher creates a project-only virtual environment if needed, starts the app
on `127.0.0.1` using a free port, and opens a private browser launch link. Nothing
goes online until you request a collection. Keep that link private. Stop with **Ctrl+C**;
your cases remain on disk.

For Windows, or without the launcher:

```sh
python3 -m venv .venv
.venv/bin/python -m local_osint serve --open --allow-network
```

On Windows use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.
Windows support is intended; current validation was performed on macOS.

To disable external collection, including DNS:

```sh
./start.sh --offline
```

## First investigation

1. Click **Start your first investigation** or **New case**. Choose a person,
   organization, domain or topic, and enter its name. Aliases and public identifiers
   are optional; adding a known website can improve relevance.
2. Press **Start investigation**. The app searches the real public web through
   DuckDuckGo Lite, saves the original search response, and follows relevant public
   pages. A Wikipedia entry is not required. Selected Wikipedia/Wikidata, recent
   GDELT news and domain tools add further evidence when available.
3. For a common name, edit **Web search query** and add **Public context**, such as
   a public location, employer or topic. The default query quotes the subject's
   full name. Existing contact details are not appended to searches automatically.
   They still help: the engine compares known identifiers with collected public
   material locally, and optional local AI receives private case context for comparison.
   Select the tools you want, set limits, then click **Run investigation**.
4. Follow **Tool activity** while the run works. Each call shows its inputs, status,
   result or error, timing, source evidence and whether the engine, AI or you
   requested it. **Edit & retry** changes and reruns one tool without rerunning the
   whole investigation. **Run a tool** starts a separate search, page collection,
   domain signal or evidence read. Earlier activity and sources stay saved.
5. Read the dossier and its source references. Leads distinguish local identifier
   corroboration, public-context matches, name-only matches and unrelated material.
   Local matching compares known clues with returned public material without sending
   those clues to search providers. Optional AI context assessment is an unverified
   local hint. A name match alone does not establish identity, and partial name
   fragments do not qualify for automatic follow-up.
   Candidates and AI drafts remain unconfirmed. **Stop** retains sources already
   saved and stops before the next request.
   Wikidata claims preserve statement rank, qualifiers, date precision and calendar;
   deprecated claims are excluded and ended website claims are not followed.
6. Optionally open **Optional local AI**, click **Start local AI**, wait for Ready and enable
   **Add AI analysis to next run**. Then run the investigation again. AI reads the
   sources, searches the public web, follows approved leads and drafts locally.
   **Analyze collected evidence** can retry AI independently, including after a
   failed or incomplete draft.
   **Continue investigation** uses the saved dossier and tool outcomes to plan
   further collection. It starts the owned local model automatically if needed;
   the AI checkbox does not need to be enabled.
7. Inspect indicators and original sources below the dossier, or import your own
   evidence. Export a readable Markdown dossier or complete JSON export with originals,
   fingerprints, timestamps, subject metadata, run history and source references.

The default **request ceiling is 24**; choose any positive whole number within the
browser's exact integer range (up to 9,007,199,254,740,991). There is no fixed
product cap of 64. It is a limit,
not a target: a run can finish at 9 requests when its selected work and relevant
follow-ups are done. The status explains whether work finished, a limit was reached,
you stopped it, or tools were unavailable. Units count collection operations;
registration reserves two, and a permitted redirect can add HTTP requests within
one operation. Reading saved evidence and local model requests use no outbound units.

The page goal defaults to **4 useful relevant pages**; it uses the same positive
whole-number range, with no fixed product cap of 8.
A successful HTTP response can still be a login screen, empty JavaScript shell or
unrelated page. Those originals are saved and labelled, but do not fill the goal.
Failed or non-useful pages still consume request units; the engine tries later
relevant results. A fresh investigation counts its own useful pages. Continuing
retains the existing useful-page count and evidence, then works toward your chosen
goal with a fresh collection ceiling.
With AI enabled, automatic collection tries to fill the useful goal before drafting.
Larger settings permit more work but do not guarantee more available sources;
eligible public leads and bounded search refinements can still be exhausted.
There is a 300-second soft run deadline and one run
at a time. Requests in flight finish before cancellation;
OS DNS follows the OS timeout policy. Restart marks unfinished runs **interrupted**
and keeps collected evidence; it never automatically resumes outbound collection.
A partial result is useful evidence, not complete coverage. Search challenges,
news rate limits and blocked sites appear as tool failures. Inspect the saved search
response when available, edit the query or retry later. No challenge bypass occurs.

Coverage includes indexed public websites, profiles, organization pages, news and
encyclopedic sources. Ordinary people can have useful web results without appearing
on Wikipedia. Sparse or ambiguous results may need an alias, public context or known
public URL; absence of results does not prove absence of a public footprint. No social-account
enumeration, login-required scraping, people-search brokers, facial recognition,
identity attribution, vulnerability scanning or unrestricted crawling.

## Optional local AI

To extend a saved case, click **Continue investigation**. The local planner reviews
collected evidence, unavailable tools and remaining public leads, then chooses
search refinements or page follow-ups. Its decisions, inputs, reasons and outcomes
appear in **Local AI continuation plan** and **Tool activity**. Excluded hosts and
the final stop reason explain which paths were exhausted or uncertain.

Continuation requires an installed `llama-server` and a cached local model. It can
start its owned model automatically, but never downloads a model or installs a
package. If the model is unavailable, the saved case remains intact and the error
explains what is needed. You can still edit and retry individual tools yourself.

The useful-page count carries forward; collection units start at zero for each
continuation. Set the useful-page goal higher before continuing a completed case
to request more evidence. Each continuation respects your selected tools, query,
public context, collection ceiling and a fresh 300-second soft deadline. It stops
when the goal is met, eligible leads are exhausted, uncertainty prevents a useful
next action, a limit is reached, or you press **Stop**. Original sources and prior
reports remain saved. Continuing does not confirm identity or guarantee new results.

Previously attempted page URLs and public queries are skipped automatically. Login
and access-barrier hosts are excluded from follow-up, with visible `-site:` search
exclusions added when they fit the public query limit. The planner can follow saved
leads from selected web, Wikipedia, Wikidata and news sources; fresh discovery uses
public web search when selected. Individual provider/domain retries remain available
in tool activity. A malformed plan, repeated ineligible choice or unavailable search
can still leave a partial investigation, with its reason shown. Final continuation
drafting reads saved evidence locally and cannot launch more network tools.

Install [llama.cpp](https://github.com/ggml-org/llama.cpp) and download the model once:

```sh
hf download terrorswift/REDCELL-26B-A4B-OSINT-Cyber-APEX-GGUF --include '*I-Compact.gguf'
```

The app detects the cached exact I-Compact GGUF (about 14.7 GB). No automatic model
or package downloads occur when investigating or starting AI. For another local
GGUF, launch with `./start.sh --model /absolute/path/to/model.gguf`. The model must
support tool calling; REDCELL I-Compact was tested with the installed llama.cpp.

**Start local AI** owns a separate authenticated loopback llama-server process.
No shell commands, arbitrary files or remote AI endpoints are exposed to the model.
The app provides these tools; you do not need to install an extra tool server:

| Tool | Capability |
| --- | --- |
| `web_search` | Send a subject-scoped query to DuckDuckGo Lite and discover public sources |
| `search_subject` | Read this run's public candidate search results |
| `news_search` | Read this run's recent news search results |
| `collect_page` | Collect a relevant discovered URL or approved link from a collected page |
| `domain_signals` | Collect an approved domain's public records |
| `read_evidence` | Read a collected source from this run |

`search_subject` and `news_search` reuse collected results; `web_search` performs
real searches. Model queries must include the subject's name or alias and use only
approved public context, source domains, words observed in returned public titles/snippets,
and bounded profile/news keywords. Saved private clues remain blocked even when a
clue's words also appear in a public result. The model
cannot send unrelated queries, fetch invented URLs, access other cases or execute
commands. Tool activity exposes its actual calls and failures. The assistant loop
allows up to four model turns and six tool calls; local relevance comparisons are
separate model requests. Generation is bounded to 512 tokens per request. AI drafts can be
wrong and remain labelled unverified. The model is directed to search and follow a
relevant page; successful evidence reading is required before a draft is accepted
when sources exist. A required search/tool failure leaves the draft incomplete.
The first AI search is offered your configured public query, adding the quoted subject
only if no complete subject or alias is present. Required page/evidence calls choose
from available leads and saved source IDs. Model search queries cap at 200 characters;
shorten the query if adding the subject would exceed this limit.
Source references let you check the result. Token-limited drafts are explicitly
marked incomplete and retained for inspection. Later timeouts preserve completed
tool calls, so you can inspect them and retry analysis.
Cached tool reuse is also visible and uses no collection unit. AI follow-up prefers
untried leads; use an individual retry to attempt a previously failed page again.

Private contact identifiers and case context are supplied only to the owned local
model and the deterministic local comparator for judging public leads. This private
prompt context is not copied into investigation reports or prompt logs, is not
appended to web queries, and does not expand the model's allowed public vocabulary.
The model is asked to avoid repeating raw private identifiers. An output guard also
redacts literal clue echoes and formatted phone echoes from drafts and rejected tool
activity, including timeout traces. This is not a general anonymizer: inspect drafts
before sharing them. Its context assessment remains an unverified hint.
An unlikely namesake does not fill the useful-page goal unless a direct identifier
match corroborates it. Explicit public URL/domain leads retain their separate
collection behavior; private contact hints are not treated as outbound leads.
**Stop local AI** or stopping the app shuts down only its owned model process.
Initial loading can take a minute and requires enough free memory. External model
servers and unrelated llama.cpp sessions are left alone.

## Collection and privacy

| Source | Collected signal | What goes outside your machine |
| --- | --- | --- |
| DuckDuckGo Lite | Up to 10 public web results per search | Full query, including chosen public context; AI's approved query refinements |
| Wikipedia / Wikidata | Candidates, profiles, relationship labels and a page | Subject name or first alias; selected public IDs and page requests |
| GDELT / linked news sites | Up to 8 recent mentions; up to 2 article pages | Quoted subject name; selected article requests |
| Local comparator and llama.cpp (optional) | Compare private known clues with public results; draft summary | Nothing: local comparison and authenticated loopback; cached model only |
| OS DNS resolver | A/AAAA address results | Requested domain |
| IANA + domain registry | RDAP registration response | Registry sees requested domain; IANA sees bootstrap request |
| Domain TLS endpoint | Verified certificate and DER bytes | Requested hostname and connection |
| Public HTTPS sites and redirect destinations | Selected pages, up to 2 MiB each | Requested hostname, path and allowed query parameters at every hop |

Destination services can see your public IP address. System DNS behavior depends
on your OS/network. Local processing is private; network collection is observable.
There are no credentials or cookies sent to providers, no proxy environment use,
no automatic retry of failed requests, and only bounded follow-up of discovered
public pages and links. Requests have size and
socket timeout limits. OS DNS resolution has the OS's timeout policy.

Collection permits public HTTPS on port 443. Private/special-use addresses,
credentials in URLs and HTTPS-to-HTTP downgrades are blocked. Investigation and
individual page tools permit query parameters because public profile/search URLs
often need them. Treat queries as outbound data: avoid private identifiers, access
tokens or signed links. At most three redirects are followed, with public-address
validation, IP pinning and TLS verification repeated for every destination. Each
redirect destination can observe the request it receives. Final page URLs stay in
source evidence. Fragments are not accepted by page tools; result fragments are
removed before collection. The standalone CLI `fetch` command retains its simpler
no-query restriction.

Login-required, JavaScript-only, non-UTF-8 or unsupported content may be unavailable.
The app reports failure instead of silently claiming no results. Registration strips
a leading `www.` from the website host; other subdomains can lack registration
records. Candidate HTTP website claims may be attempted over HTTPS, with normal
certificate verification.

See [security and privacy](SECURITY.md) for controls, assumptions and limits.

## Configure

The UI needs no configuration file or API keys. Useful launcher options:

```sh
./start.sh --port 9000                      # request a specific free port
./start.sh --data-dir ./private-cases       # choose private local storage
./start.sh --offline                       # prohibit collection
```

The data directory is created with owner-only permissions. An existing directory
must already be private. On macOS/Linux use `chmod 700` for the data directory and
`chmod 600` for its database if necessary. Symlinked data paths are rejected;
choose their real paths. Do not put cases inside a shared or cloud-synced directory.

Optional `osint.toml`:

```toml
data_dir = "private-cases"
port = 0 # automatically choose a free local port
```

```sh
./start.sh --config osint.toml
```

Relative `data_dir` paths in TOML resolve beside that file. Explicit command options
win. With no port configured, the OS selects a free port. An explicitly requested
busy port fails with an actionable error; use `--port 0` to select a free port.
Unknown keys or invalid ports fail visibly. Network permission is a launch
flag, not a persisted preference. `--offline` disables it for that launch.

## CLI

Use the same evidence engine without a browser:

```sh
.venv/bin/python -m local_osint case "Example Foundation" --type organization \
  --alias "Example" --identifier example.org
.venv/bin/python -m local_osint cases
```

Copy the returned case ID into the following commands:

```sh
.venv/bin/python -m local_osint import CASE_ID examples/sample.txt
.venv/bin/python -m local_osint investigate CASE_ID --allow-network
.venv/bin/python -m local_osint discover CASE_ID --allow-network
.venv/bin/python -m local_osint discover CASE_ID --query "Known alias" \
  --sources wikidata --allow-network
.venv/bin/python -m local_osint research CASE_ID example.org \
  --signals dns registration certificate website --allow-network
.venv/bin/python -m local_osint fetch CASE_ID https://example.org/ --allow-network
.venv/bin/python -m local_osint search CASE_ID example --kind domain
.venv/bin/python -m local_osint entity CASE_ID ENTITY_ID
.venv/bin/python -m local_osint export CASE_ID case-evidence.json
.venv/bin/python -m local_osint export CASE_ID case-report.md --format markdown
.venv/bin/python -m local_osint backup evidence-backup.sqlite3
```

Each command accepts `--data-dir` and `--config`. CLI output is JSON; errors go to
stderr. Investigation exits zero for complete or partial results; inspect its
status and collection log. Discovery/research exits nonzero if any requested source
fails, preserving successful evidence. Export and backup never overwrite existing files.
CLI output files have owner-only permissions; browser download permissions are
controlled by your browser and OS.

## Storage, backup and recovery

By default all case data lives in `.local-osint/evidence.sqlite3` inside the project.
The database stores original evidence, derived searchable text, extracted indicators
and source offsets. Imported file paths are not stored automatically—only basenames
or your explicit source labels. Same bytes + same source + same case are deduplicated.
A later changed page is a separate item with its own fingerprint and timestamp.

Use the backup command above; it uses SQLite's consistent backup operation and
checks integrity. To restore, stop every app/CLI process using the destination,
keep its old private directory as a local backup, create a new directory with
permissions `700`, and copy the backup into it as `evidence.sqlite3` with permissions
`600`. Start with `--data-dir` pointing there. Do not overwrite a running database.
The JSON export is an interchange/report artifact; it is not a database restore file.

Nothing is deleted automatically. To remove an entire workspace, stop the app,
verify the exact private data directory and backups, then remove them using your
OS. The initial UI deliberately has no destructive actions. Disk encryption and
backup retention remain your responsibility. Hashes detect changed bytes relative
to an exported fingerprint; they do not prove authorship or truth.

## Development and validation

```sh
python3 -m venv .venv
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q local_osint tests
```

Tests use synthetic evidence and mock external providers. HTTP tests bind temporary
loopback ports. No provider credentials, live targets or internet access required.
See [design and acceptance](docs/design.md), [validation evidence](docs/validation.md),
and [contributing](CONTRIBUTING.md).

Primary protocol references: [DuckDuckGo non-JavaScript search](https://duckduckgo.com/duckduckgo-help-pages/features/non-javascript),
[IANA RDAP bootstrap registry](https://www.iana.org/assignments/rdap-dns/),
[RDAP query format](https://www.rfc-editor.org/rfc/rfc9082.html),
[MediaWiki search API](https://www.mediawiki.org/wiki/API:Search), and
[Wikidata data access](https://www.wikidata.org/wiki/Wikidata:Data_access).

Domain/email extraction validates ASCII domain suffixes against the bundled
[IANA TLD snapshot](https://data.iana.org/TLD/tlds-alpha-by-domain.txt), dated
2026-10-09. URL paths and query strings are excluded from standalone domain matches.
This reduces filename/markup noise; it does not prove a domain exists, identify its
owner, or recognize every internationalized spelling. No suffix update is fetched
at runtime. Maintainers can replace `local_osint/tlds.txt` with the complete current
IANA file, review the diff, rerun extraction tests and record its date/hash.
