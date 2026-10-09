# oh no, she OSINT.

A private OSINT workspace focused on **people, organizations, domains, topics or
other subjects**. Collect public signals online; store evidence, extract indicators,
search and write reports entirely on your machine.

No accounts, API keys, cloud database, analytics, AI service, external fonts or
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

1. **Create a case.** Choose person, organization, domain, topic/event or other.
   Add a subject name, optional aliases and known identifiers such as a public
   profile URL, domain or email. These identifiers remain user-supplied leads.
2. **Find public-source candidates.** Search Wikipedia and Wikidata for the
   name or an alias. Inspect labels and descriptions. Collect a candidate page
   only when you want it as evidence. A matching name is never proof of identity.
3. **Collect known signals.** For a relevant domain, select DNS addresses,
   registration data, TLS certificate or the public website. Or collect a specific
   public HTTPS page. A source failure leaves successful evidence intact.
4. **Bring your own evidence.** Import UTF-8 text, Markdown, HTML, JSON or CSV,
   or paste notes. Binary files and PDFs need conversion first. Each item is
   limited to 2 MiB. [Fictional sample evidence](examples/sample.txt) is included.
5. **Follow indicators.** Search and filter emails, domains, URLs, IPv4 and IPv6.
   Click an indicator to inspect its sources and other indicators in those sources.
   Evidence displays as text; saved HTML cannot run scripts or load images.
6. **Export.** Download a readable Markdown report or complete JSON evidence
   export, including original bytes, SHA-256 fingerprints, timestamps, subject
   metadata and indicator mentions.

Discovery covers public encyclopedic sources, not the whole web. For subjects
absent from those sources, collect known public URLs or import public records.
There is no social-account enumeration, hidden-profile access, people-search broker,
facial recognition, identity attribution, bulk crawling or automated risk scoring.

## Collection and privacy

| Source | Collected signal | What goes outside your machine |
| --- | --- | --- |
| Wikipedia / Wikidata | Up to 10 candidates per selected source | Your name/search query and source request |
| OS DNS resolver | A/AAAA address results | Requested domain |
| IANA + domain registry | RDAP registration response | Registry sees requested domain; IANA sees bootstrap request |
| Domain TLS endpoint | Verified certificate and DER bytes | Requested hostname and connection |
| Public HTTPS site | One page, up to 2 MiB | Requested hostname/path |

Destination services can see your public IP address. System DNS behavior depends
on your OS/network. Local processing is private; network collection is observable.
There are no credentials or cookies sent to providers, no proxy environment use,
no automatic retries, and no crawling of discovered links. Requests have size and
socket timeout limits. OS DNS resolution has the OS's timeout policy.

Collection permits public HTTPS on port 443. Private/special-use addresses,
credentials in URLs and redirects are blocked. User page URLs must not include
query parameters or fragments; built-in discovery creates its own narrowly scoped
provider query. Some sites require redirects, login, JavaScript or different
encodings and will be unavailable. The app reports that failure rather than
silently changing its security boundary.

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
stderr. Discovery/research exits nonzero if any requested source fails, preserving
successful source evidence. Export and backup never overwrite existing files.
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
