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
2. Press **Start investigation**. The app searches Wikipedia/Wikidata, collects
   candidate profiles and relationship claims, resolves names, collects a Wikipedia
   page, searches recent GDELT news and follows up to two articles. It also collects
   DNS, registration, TLS and website evidence for one supplied or candidate domain.
3. Read the dossier while progress updates. Candidates remain unconfirmed: a shared
   name is not proof of identity. Each profile, claim and news item keeps evidence
   references. **Collection log** explains unavailable sources. **Stop** retains
   sources already saved and stops before the next request.
   Wikidata claims preserve statement rank, qualifiers, date precision and calendar;
   deprecated claims are excluded and ended website claims are not followed.
4. Optionally open **Local AI**, click **Start local AI**, wait for Ready and enable
   **Add AI analysis to next run**. Then run the investigation again. AI reads the
   sources, uses bounded tools and drafts a summary locally.
5. Inspect indicators and original sources below the dossier. Advanced sections let
   you search an alias, collect another known public page or import your own evidence.
6. Export a readable Markdown dossier or complete JSON export with originals,
   fingerprints, timestamps, subject metadata, run history and source references.

An investigation uses at most 24 collection request units and a 180-second soft
collection deadline. One run at a time. Requests in flight finish before cancellation;
OS DNS follows the OS timeout policy. Restart marks unfinished runs **interrupted**
and keeps collected evidence; it never automatically resumes outbound collection.
A partial result is useful evidence, not complete coverage. News rate limits,
redirects and blocked sites appear as source failures.

Coverage begins with public encyclopedic sources and recent news. Subjects absent
from those sources may need an alias or known public identifier. No social-account
enumeration, login-required scraping, people-search brokers, facial recognition,
identity attribution, vulnerability scanning or unrestricted crawling.

## Optional local AI

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
| `search_subject` | Read this run's public candidate search results |
| `news_search` | Read this run's recent news search results |
| `collect_page` | Collect a URL already found for this investigation |
| `domain_signals` | Collect an approved domain's public records |
| `read_evidence` | Read a collected source from this run |

Search tools reuse collected results. The model cannot transmit new free-form
queries, fetch invented URLs, access other cases or execute commands. Up to four
model turns and six tool calls; 512 generated tokens per turn. AI drafts can be
wrong and remain labelled unverified. An evidence read is required before drafting
when sources exist. Source references let you check the result. Token-limited
drafts are explicitly marked incomplete and retained for inspection.
**Stop local AI** or stopping the app shuts down only its owned model process.
Initial loading can take a minute and requires enough free memory. External model
servers and unrelated llama.cpp sessions are left alone.

## Collection and privacy

| Source | Collected signal | What goes outside your machine |
| --- | --- | --- |
| Wikipedia / Wikidata | Candidates, profiles, relationship labels and a page | Subject name or first alias; selected public IDs and page requests |
| GDELT / linked news sites | Up to 8 recent mentions; up to 2 article pages | Quoted subject name; selected article requests |
| Local llama.cpp (optional) | Tool calls and draft summary | Nothing: authenticated loopback; cached model only |
| OS DNS resolver | A/AAAA address results | Requested domain |
| IANA + domain registry | RDAP registration response | Registry sees requested domain; IANA sees bootstrap request |
| Domain TLS endpoint | Verified certificate and DER bytes | Requested hostname and connection |
| Public HTTPS site | One page, up to 2 MiB | Requested hostname/path |

Destination services can see your public IP address. System DNS behavior depends
on your OS/network. Local processing is private; network collection is observable.
There are no credentials or cookies sent to providers, no proxy environment use,
no automatic retries, and only bounded follow-up of discovered profiles and article links. Requests have size and
socket timeout limits. OS DNS resolution has the OS's timeout policy.

Collection permits public HTTPS on port 443. Private/special-use addresses,
credentials in URLs and redirects are blocked. User page URLs must not include
query parameters or fragments; built-in provider searches create narrowly scoped query parameters. Some sites require redirects, login, JavaScript or different
encodings and will be unavailable. The app reports that failure. Registration strips a leading `www.` from the website host; other subdomains can lack registration records. Candidate HTTP website claims may be attempted over HTTPS, with normal certificate verification.

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

Primary protocol references: [IANA RDAP bootstrap registry](https://www.iana.org/assignments/rdap-dns/),
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
