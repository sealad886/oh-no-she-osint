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
  mapped and common IPv6 transition address ranges are refused. At most three
  redirects are followed; every hop repeats URL validation, public DNS validation,
  IP pinning and TLS hostname verification. Loops, private destinations and HTTPS
  downgrades are refused. Proxies, authentication, outgoing cookies and compressed
  content are unsupported. The final URL remains the evidence source.
- Collection requires a process-level flag and an explicit user action. Offline
  mode rejects all collection paths before DNS or connection. Built-in discovery
  and news search construct query parameters for fixed public providers. Real web
  search sends a user-approved query to DuckDuckGo Lite without an API key.
  Investigation/page tools permit query parameters on selected public URLs;
  standalone CLI `fetch` rejects them. Queries are outbound data, including those
  received from links or redirects, and can contain tracking values. URL credentials
  and fragments remain prohibited.
- No HTTP access logs, analytics, cloud storage, cloud AI calls, CDN dependencies,
  or environment TLS key logging. CLI errors suppress private filesystem details.

## Automatic collection and model boundary

Runs reserve one worker, a default 24-request-unit ceiling and a default
four-useful-page goal,
both chosen by the user as positive whole numbers. Fixed product caps are removed;
values must remain within JavaScript's exact integer range (1 through 2^53−1).
Runs retain a 300-second soft deadline. A ceiling bounds work; it is not a
completion target. Failed collection
attempts consume units but do not fill the useful-page goal. A saved login screen,
empty JavaScript shell or unrelated page also does not fill the goal. Raw originals
remain evidence even when they fail the usefulness heuristic. Useful counts refer
to the current run; retained prior pages remain separately inspectable. Registration
reserves two units for directory and registry; redirect contacts occur within an
operation rather than consuming separate units. Returned URLs use the same
public-address pinning and redirect revalidation as manual collection.
Checkpoints and final dossiers are local derived evidence with no extracted mentions;
all originals and reports remain exportable. Restart marks running checkpoints
interrupted without resuming requests. Activity records tool arguments, origin,
start/finish time, results or errors and source IDs. Individual retries create a new
run while retaining earlier report evidence and tool history. Only the selected tool
runs again; a human can explicitly edit its query, URL, domain or selected signals.

Real web search saves the original public search response before interpreting it.
Malformed or challenge pages report failure, not an empty footprint; no challenge
bypass is attempted. Automatic follow-up requires a full subject-name or alias match
in a title/snippet. Wikipedia/Wikidata results use the same full-name relevance
filter, so unrelated name fragments do not drive profile collection. This heuristic
cannot confirm identity or guarantee relevance. User-entered public context narrows
queries; stored contact identifiers are not silently appended. A deterministic local
comparator checks returned public material against known case identifiers and labels
identifier corroboration, public-context matches, name-only matches and unrelated
material. These are bounded heuristic signals, not identity confirmation.
Email-provider domains
are not researched automatically. Namesake profiles remain separate, unconfirmed
candidates. Candidate-domain research uses only the first collected profile's
website claims, with its candidate and evidence IDs attached.
Structured claims retain rank and qualifiers; temporal values display only fields
supported by their declared precision, with calendar and uncertainty metadata.
Deprecated claims and ended website leads cannot drive automatic domain research.

Optional llama.cpp runs from a local cached GGUF in an owned subprocess. It binds
127.0.0.1, requires a random API key passed through process environment, disables
its web UI, downloads, built-in tools and prompt logging, and clears ambient llama
options, proxy settings and TLS key logging. Browser requests cannot choose a model
path or endpoint. A trusted startup `--model` flag can choose a different local GGUF.
The app stops only its owned process. Model responses cap at 1 MiB and prompts at
128 KiB. The assistant loop caps at four turns and six tool calls; optional local
relevance comparisons make separate bounded model requests. GPU memory is
model-dependent, not resource-isolated.

Continue investigation owns the same single worker and starts only this installed,
cached local model. Planning responses are strict bounded JSON selecting a public
search, an eligible untried page, or exhaustion. Saved source IDs, raw bytes and prior
tool history carry forward; useful pages count cumulatively and collection uses a
fresh user-selected request ceiling and 300-second soft deadline. Each executed
collection action consumes that ceiling. Cancellation, model errors, repeated invalid
choices and repeated unavailable searches stop further planning while retaining
evidence. An in-flight model or provider request may finish before a stop takes effect.

Continuation restores only case-approved source URLs, respects selected source
categories, skips attempted URLs/queries and excludes access/login-barrier hosts.
Visible public `-site:` terms ask the search provider for alternatives; these terms
are bounded and revalidated against the same public vocabulary and private-clue
guard. Planner reasons and rejected arguments use the private-output guard.
Continuation's final draft disables outbound tools in the dispatcher itself, even
when a model requests a tool that was absent from its offered schemas. Source reading
remains case-scoped and local. Restart records an interrupted job; collection resumes
only when the user clicks Continue, never as unsolicited background traffic.

Private case identifiers and context are passed only to the owned local model and
deterministic local comparator. The ephemeral model prompt context is excluded from
persisted investigation reports and prompt logs. It is not appended to outbound web
queries and does not enlarge the model's permitted public-query vocabulary. Explicit
public URL/domain leads remain independently eligible for collection; private
contact details are not converted into outbound leads. The local model is instructed
not to repeat raw private identifiers in a draft. A deterministic output guard
also removes literal clue/contact echoes and formatted phone echoes from model
drafts, rejected tool activity and timeout traces. This does not detect every
paraphrase or inferred personal detail; users must inspect drafts before exporting
or sharing them. The original case
identifiers remain stored in the private case database and its case exports.

Optional local model comparison adds an unverified context-consistent, unlikely or
uncertain hint to public leads without including private values in that assessment.
An unlikely namesake does not count toward the useful-page goal unless a direct
identifier match corroborates it; collected raw evidence is retained. Model hints
can be wrong and never establish identity.

A central dispatcher permits only case-run evidence IDs and approved URL/domain
leads. The model's real `web_search` queries must contain the full subject name or
alias and use only approved public context, approved source domains, nonnumeric
words observed in returned public titles/snippets, and a bounded keyword vocabulary.
Private clue echoes and normalized private-clue phrases remain blocked before
dispatch, even when those words appear publicly, unless the user explicitly placed
the clue in their public query/context. The model cannot add unrelated tokens, new private contact
identifiers or arbitrary query text. The query is still observable by the provider.
`search_subject` and `news_search` return already-collected responses; `collect_page`
can follow relevant search results or approved links discovered on collected pages.
New URLs invented by the model, other cases, filesystem tools and shell commands
are rejected. Selected source controls and process-level network permission constrain
outbound model tools; offline AI can read saved evidence without web collection.
Sources are explicitly untrusted
in prompts. Enforced tool restrictions contain prompt injection even if the model
ignores those prompts. They do not make model conclusions trustworthy. AI drafts
remain unverified and citations are filtered against known run evidence IDs.
The model is directed to search and follow sources; successful evidence reading
is required before accepting a draft when sources exist. Required tool failures
mark the draft incomplete. Tool calls, including blocked attempts, are shown in
activity alongside engine and human calls. Truncated responses
are visibly incomplete and make the investigation partial; text is retained.

## Residual limits

A process running as your OS user can read your evidence, inspect browser/terminal
state, obtain the launch capability, or tamper with files. File modes do not encrypt
storage and POSIX modes are not a Windows ACL guarantee. Protect your OS account,
use disk encryption, avoid shared/cloud-synced folders, and protect browser downloads
and backups. A copied launch link grants access while that app process is running.
Local HTTP is not encrypted; do not forward its port, reverse-proxy it, or share it.

DNS resolution uses OS resolver behavior and timeout policy. Website operators,
DuckDuckGo, Wikipedia/Wikidata, news services and registries can observe collection
requests and public IP. Page queries and redirect destinations expand the observable
request scope. Search results and follow-up links are not a guarantee that a source
is benign; public pages can redirect to other public sites within the allowed bounds.
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
