# Source definitions, optional software and attribution

Updated 2026-11-02. The application's own code is covered by the repository
[MIT licence](../LICENSE). Public websites, provider APIs, downloaded models,
optional runtimes and upstream data keep their own terms. This notice does not
relicense collected evidence or promise permission for every intended use.

## Sherlock-derived profile definitions

The small built-in GitHub/GitLab public-profile URL pack uses definitions derived
from **Sherlock v0.15.0**. It does not bundle or invoke Sherlock's Python program,
its dependencies or the full site catalogue. Response interpretation is implemented
locally and conservatively; account existence never proves target ownership.

- [Pinned upstream site definitions](https://github.com/sherlock-project/sherlock/blob/v0.15.0/sherlock_project/resources/data.json)
- [Pinned upstream MIT licence](https://github.com/sherlock-project/sherlock/blob/v0.15.0/LICENSE)
- [Project repository](https://github.com/sherlock-project/sherlock)

The complete upstream permission notice follows:

```text
MIT License

Copyright (c) 2019 Sherlock Project

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Keep this notice when redistributing derived source definitions. Adding more
sites requires review of maintenance, destination scope, matching rules, source
terms and the exact upstream revision; it must not silently import an entire
remote tool directory.

## SearXNG

[SearXNG](https://github.com/searxng/searxng) is separate optional software under
[AGPL-3.0-or-later](https://github.com/searxng/searxng/blob/master/LICENSE).
Its container image is fetched only after explicit setup from the official
`docker.io/searxng/searxng` repository. The local adapter is application code;
SearXNG source is not copied into this repository or relicensed as MIT.

Setup records the selected image and immutable image ID. Maintain the upstream
licence/notices and source-availability obligations applicable to any distribution
or modification of that software. The application's configuration/transport adapter
does not remove those obligations. Docker and the installed model/runtime have
independent licences and are not installed by this application.

Primary interfaces: [Search API](https://docs.searxng.org/dev/search_api.html),
[engine settings](https://docs.searxng.org/admin/settings/settings_engines.html),
[outgoing request settings](https://docs.searxng.org/admin/settings/settings_outgoing.html).

## Provider interfaces and collected material

The following are network integrations, not vendored provider code or general
licence grants over returned content:

| Provider | Interface or provenance |
| --- | --- |
| Crossref | [REST metadata API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) |
| GitHub | [Public user REST API](https://docs.github.com/en/rest/users/users#get-a-user), [repository metadata](https://docs.github.com/en/rest/repos/repos#get-a-repository), [preferred README](https://docs.github.com/en/rest/repos/contents#get-a-repository-readme), [issue body](https://docs.github.com/en/rest/issues/issues#get-an-issue) and [issue comments](https://docs.github.com/en/rest/issues/comments#list-issue-comments); unauthenticated public resources only |
| GitHub / GitLab public pages | Selected Sherlock-derived URLs, subject to each site's access rules |
| Internet Archive | [Wayback availability API](https://archive.org/help/wayback_api.php) |
| Common Crawl | [CDX URL index](https://index.commoncrawl.org/); bounded index catalogue/capture metadata only, no bundled WARC data |
| Public federated account hosts | [WebFinger RFC 7033](https://www.rfc-editor.org/rfc/rfc7033.html), [ActivityPub](https://www.w3.org/TR/activitypub/); independent adapters, not imported server implementations |
| GeoNames | [Public place search](https://www.geonames.org/) |
| DuckDuckGo | [Non-JavaScript public search](https://duckduckgo.com/duckduckgo-help-pages/features/non-javascript) |
| Bing | [Public search operators](https://support.microsoft.com/en-us/bing/advanced-search-keywords) |
| Wikipedia / Wikidata | [MediaWiki API](https://www.mediawiki.org/wiki/API:Search), [Wikidata access](https://www.wikidata.org/wiki/Wikidata:Data_access) |
| IANA / domain registries | [RDAP bootstrap registry](https://www.iana.org/assignments/rdap-dns/) |
| IANA extraction data | [TLD snapshot](https://data.iana.org/TLD/tlds-alpha-by-domain.txt), bundled snapshot dated 2026-10-09 |

Response originals retain their source URL, collection time and original-byte hash.
Archiving a response preserves provenance; it does not transfer copyright or
establish permission to publish it. An archived copy is not independent factual
corroboration of the same original.

## Optional local document tools

PDF extraction uses an independently installed [Poppler](https://poppler.freedesktop.org/)
installation (`pdftotext` and `pdftoppm`). Image/scanned-page OCR uses independently
installed [Tesseract](https://github.com/tesseract-ocr/tesseract) and language data.
They retain their upstream licences and notices; neither executable, its source,
nor language data is bundled or relicensed under the application licence.
Tesseract's upstream [licence](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE)
and [command documentation](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)
remain the canonical references. Distribution of native tools/language packs would
need its own licence inventory rather than assuming the adapter's MIT terms apply.

DOCX/ODT extraction uses application code and Python standard-library ZIP/XML
support. It does not bundle Microsoft Office, LibreOffice, an office renderer or
third-party Python parser. PDF/office/image originals retain their source rights;
extracted text and OCR provenance do not grant publication rights or prove that
recognition is accurate. Retrying extraction does not replace original evidence.

## Research inspirations and deliberately unbundled projects

- [OSINT4ALL](https://start.me/p/L1rEYQ/osint4all) informed resource discovery and
  curation. Its directory is not bundled, mirrored or treated as executable input.
- [WhatsMyName](https://github.com/WebBreacher/WhatsMyName) is not bundled. Its
  dataset uses CC BY-SA 4.0; a future pack needs a separately pinned provenance,
  attribution and share-alike review. The Sherlock pack does not claim that licence.
- [LinkScope Client](https://github.com/AccentuSoft/LinkScope_Client) informed the
  general concept of sourced entity relationships. No LinkScope code/modules are
  imported. Its AGPL licence is not relabelled as the application's MIT licence.
- Maigret and other account-search programs are not runtime dependencies. Adding
  them would need a separate capability, supply-chain and licence review.

This release does not bundle a downloaded model or prescribe its redistribution
terms. Consult the selected GGUF's model card and upstream licence before sharing
model files or outputs.
