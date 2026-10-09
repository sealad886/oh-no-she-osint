# Security and privacy

## Intended boundary

This is a **single-user local tool**. It is not a multi-user service, anonymity
network, hardened remote server, or guarantee of security. The application stores
potentially sensitive research. Only collect public or authorized information and
assess evidence before attributing it to a person or organization.

## Controls

- Listener binds only `127.0.0.1`; no option permits LAN or internet binding.
- Each process creates a random 256-bit session capability. Launch link carries it
  in a fragment, exchanged for an HttpOnly, SameSite=Strict session cookie.
  The UI immediately removes the fragment. Restart invalidates previous sessions.
- Exact Host/Origin checks and a custom API header block DNS rebinding, cross-origin
  reads and browser CSRF. Cookies alone are insufficient. No CORS access is granted.
- Fixed static routes; browser API cannot open arbitrary filesystem paths.
- Evidence and remote text use DOM text nodes. Content Security Policy blocks
  inline scripts/styles, external resources, embedded frames and dynamic evaluation.
  Imported HTML is converted to searchable text and never executed.
- Parameter-bound SQL, case-scoped reads, foreign keys, transactional imports,
  concurrent deduplication and consistent export snapshots protect data integrity.
- POSIX data directories require `700`, database files require `600`. New files
  are owner-only. Data directory symlinks and symlink/hardlinked databases are refused.
- Evidence caps: 2 MiB, 10,000 indicator mentions per item. Request bodies cap at
  3 MiB. API lists cap at 500 indicators / 200 evidence items; exports contain all.
  Eight request workers maximum, finite client/socket timeouts, no automatic retries.
- Collection validates all DNS addresses and pins the chosen public IP for the TLS
  connection with original hostname verification. Private, reserved, multicast,
  mapped and common IPv6 transition address ranges are refused. Redirects, proxies,
  authentication, outgoing cookies and compressed content are unsupported.
- Collection requires a process-level flag and an explicit user action. Offline
  mode rejects all collection paths before DNS or connection. Built-in discovery
  and news search construct query parameters for fixed public providers.
- No request/evidence logging, analytics, cloud storage, cloud AI calls, CDN dependencies,
  or environment TLS key logging. CLI errors suppress private filesystem details.

## Automatic collection and model boundary

Runs reserve one worker, a 24-request-unit budget and a 180-second soft deadline.
Returned public URLs use the same public-address pinning and redirect rejection as
manual collection. Registration reserves two units for directory and registry.
Checkpoints and final dossiers are local derived evidence with no extracted mentions;
all originals and reports remain exportable. Restart marks running checkpoints
interrupted without resuming requests. Namesake profiles remain separate, unconfirmed
candidates. Default candidate-domain research uses only the first candidate profile's
website claims, with its candidate and evidence IDs attached.
Structured claims retain rank and qualifiers; temporal values display only fields
supported by their declared precision, with calendar and uncertainty metadata.
Deprecated claims and ended website leads cannot drive automatic domain research.

Optional llama.cpp runs from a local cached GGUF in an owned subprocess. It binds
127.0.0.1, requires a random API key passed through process environment, disables
its web UI, downloads, built-in tools and prompt logging, and clears ambient llama
options, proxy settings and TLS key logging. Browser requests cannot choose a model
path or endpoint. A trusted startup `--model` flag can choose a different local GGUF.
The app stops only its owned process. Model responses cap at 1 MiB, prompts at 128 KiB,
four turns and six tool calls. GPU memory is model-dependent, not resource-isolated.

A central dispatcher permits only this run's evidence IDs and discovered URL/domain
leads. Search tools take no arguments and return already-collected public responses;
model-generated text cannot become an outbound query. Sources are explicitly untrusted
in prompts. Enforced tool restrictions contain prompt injection even if the model
ignores those prompts. They do not make model conclusions trustworthy. AI drafts
remain unverified and citations are filtered against known run evidence IDs.
The first model turn must read evidence when sources exist. Truncated responses
are visibly incomplete and make the investigation partial; text is retained.

## Residual limits

A process running as your OS user can read your evidence, inspect browser/terminal
state, obtain the launch capability, or tamper with files. File modes do not encrypt
storage and POSIX modes are not a Windows ACL guarantee. Protect your OS account,
use disk encryption, avoid shared/cloud-synced folders, and protect browser downloads
and backups. A copied launch link grants access while that app process is running.
Local HTTP is not encrypted; do not forward its port, reverse-proxy it, or share it.

DNS resolution uses OS resolver behavior and timeout policy. Website operators,
Wikipedia/Wikidata, news services and registries can observe collection requests and public IP.
Public data can be stale, incomplete, malicious or misleading. A successful TLS
handshake authenticates the endpoint certificate, not the truth of its content.

There is no unlimited-corpus or denial-of-service guarantee. A malicious same-user
process can exhaust worker slots, disk space or total case size. Extraction is a
bounded heuristic; false positives and missed indicators are possible. No model output confirms identity. Aliases and identifiers are user-supplied context; shared sources
are co-occurrence, not confirmed relationships. No claim of legal compliance or
independent exhaustive security audit is made.

## Reporting

No remote security-report destination is configured yet. Keep sensitive reports
private and contact the repository owner directly. Do not include evidence,
credentials, private identifiers or launch links in public issues. Maintainers
should add a verified private reporting address before publication.
