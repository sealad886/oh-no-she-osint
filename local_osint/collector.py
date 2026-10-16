"""One-page HTTPS collection with DNS validation and a pinned destination."""

import base64
import http.client
import json
import re
import ipaddress
import socket
import ssl
import time
import zlib
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit

from .engine import InvalidInput, MAX_BYTES


SEARCH_PROVIDERS = {"duckduckgo": "DuckDuckGo", "bing": "Bing"}
ACCEPT_PROFILES = {
    "default": "text/html, text/plain, application/json, application/jrd+json, application/activity+json, application/pdf, application/xml, text/csv",
    "activitypub": 'application/activity+json, application/ld+json; profile="https://www.w3.org/ns/activitystreams", application/json;q=0.9',
}


def public_address(value):
    address = ipaddress.ip_address(value)
    # Reject IPv4-mapped and transition addresses; avoid alternate routing to private networks.
    if not address.is_global or address.is_multicast or address.is_unspecified:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network("64:ff9b:1::/48"):
            return False
    return True


def public_url(url, allow_query=False):
    """Validate URL syntax without contacting the destination."""
    if not isinstance(url, str) or len(url) > 2000 or any(ord(c) < 33 or ord(c) > 126 for c in url) or "\\" in url:
        raise InvalidInput("Enter a plain ASCII public HTTPS URL, up to 2,000 characters.")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.port not in {None, 443}:
            raise InvalidInput("Only public HTTPS URLs on port 443 are allowed.")
        if parsed.username is not None or parsed.password is not None or (parsed.query and not allow_query) or parsed.fragment:
            raise InvalidInput("Remove credentials, query parameters and fragments from the URL.")
        host = parsed.hostname.lower()
        if not all(c.isalnum() or c in ".-:" for c in host) or host.endswith("."):
            raise InvalidInput("Invalid destination hostname.")
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home", ".test", ".invalid", ".onion")):
            raise InvalidInput("Local and special-use destinations are blocked.")
        try:
            numeric_address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host:
                raise InvalidInput("Use a public, fully qualified hostname.")
        else:
            if not public_address(str(numeric_address)):
                raise InvalidInput("Private and special-use addresses are blocked.")
        authority = f"[{host}]" if ":" in host else host
        canonical = urlunsplit(("https", authority, parsed.path or "/", parsed.query if allow_query else "", ""))
        request_path = (parsed.path or "/") + (("?" + parsed.query) if allow_query and parsed.query else "")
        return host, request_path, canonical
    except (ValueError, OSError) as exc:
        raise InvalidInput("Could not validate a public HTTPS destination.") from exc


def target(url, allow_query=False):
    host, request_path, canonical = public_url(url, allow_query)
    try:
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not public_address(address) for address in addresses):
            raise InvalidInput("Destination resolves to a private or special-use address.")
        return host, addresses[0], request_path, canonical
    except OSError as exc:
        raise InvalidInput("Could not validate a public HTTPS destination.") from exc


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        context = ssl.create_default_context()
        # Never write TLS secrets through a user's ambient SSLKEYLOGFILE setting.
        context.keylog_filename = None
        super().__init__(host, port=443, timeout=10, context=context)
        self.address = address

    def connect(self):
        # Numeric address is validated once; no second DNS lookup and no environment proxies.
        family = socket.AF_INET6 if ":" in self.address else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.address, 443))
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def generated_title(value):
    """Bound display titles; complete identifiers remain in source and original evidence."""
    return value if len(value) <= 200 else value[:197] + "..."


def collect(engine, case_id, url, enabled=False, allow_query=False):
    if not enabled:
        raise InvalidInput("Web collection is off. Restart with --allow-network to enable it.")
    # Validate case before DNS or outbound requests.
    with engine.connection() as db:
        engine.require_case(db, case_id)
    raw, format, canonical = fetch_public(url, allow_query=allow_query)
    return engine.ingest(case_id, raw, generated_title(canonical), canonical, format)


def fetch_public(url, allow_query=False):
    return _fetch_public(url, allow_query)


def fetch_public_response(url, allow_query=False, allowed_hosts=None, accept="default"):
    """Collect bounded response bytes and HTTP outcome for trusted connectors.

    Unlike ordinary page collection, failure bodies are evidence too. DNS,
    destination pinning, redirect checks and body limits remain identical.
    """
    return _fetch_public(url, allow_query, structured=True, allowed_hosts=allowed_hosts, accept=accept)


def _fetch_public(url, allow_query=False, structured=False, allowed_hosts=None, accept="default"):
    """Fetch a bounded public response, revalidating and pinning every redirect."""
    if accept not in ACCEPT_PROFILES:
        raise InvalidInput("Choose a built-in response content profile.")
    deadline = time.monotonic() + 20
    visited = set()
    current = url
    for hop in range(4):
        if time.monotonic() >= deadline:
            raise InvalidInput("Collection timed out.")
        if allowed_hosts is not None and public_url(current, allow_query=allow_query)[0] not in allowed_hosts:
            raise InvalidInput("Source redirected outside its declared destination hosts.")
        host, address, path, canonical = target(current, allow_query=allow_query)
        if canonical in visited:
            raise InvalidInput("Destination returned a redirect loop.")
        visited.add(canonical)
        connection = PinnedHTTPS(host, address)
        connection.timeout = min(10, max(0.001, deadline - time.monotonic()))
        try:
            connection.request("GET", path, headers={"User-Agent": "LocalOSINT/0.1 (public evidence collection)", "Accept": ACCEPT_PROFILES[accept], "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise InvalidInput("Destination returned a redirect without a location.")
                if hop == 3:
                    raise InvalidInput("Destination exceeded the three-redirect limit.")
                if len(location) > 2000 or any(ord(c) < 33 or ord(c) > 126 for c in location) or "\\" in location:
                    raise InvalidInput("Destination returned an invalid redirect URL.")
                current = urljoin(canonical, location)
                continue
            if structured:
                raw, format, canonical = read_public_response(response, connection, canonical, deadline, permit_status=True, permit_xml=True)
                retry_after = response.getheader('Retry-After', '')
                retry_after = min(int(retry_after), 86400) if str(retry_after).isdecimal() else None
                return {"raw": raw, "format": format, "canonical": canonical,
                        "status": response.status, "retry_after": retry_after, "requests": hop + 1}
            return read_public_response(response, connection, canonical, deadline)
        except (OSError, http.client.HTTPException) as exc:
            # Do not echo URLs, cookies, TLS internals, or server-controlled error data.
            raise InvalidInput("HTTPS collection failed. Check connectivity and the site's certificate.") from exc
        finally:
            connection.close()


def read_public_response(response, connection, canonical, deadline, permit_status=False, permit_xml=False):
    try:
        if response.status == 429 and not permit_status:
            raise InvalidInput('Source rate limited this request (HTTP 429). Try a later investigation.')
        if response.status != 200 and not permit_status:
            raise InvalidInput(f"Destination returned HTTP {response.status}.")
        encoding = response.getheader("Content-Encoding", "identity").strip().lower()
        if encoding not in {"identity", "gzip", "deflate"}:
            raise InvalidInput("Only identity, gzip and deflate response encodings are supported.")
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding != "identity" else None
        mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        formats = {"text/html": "html", "text/plain": "text", "application/json": "json", "application/rdap+json": "json", "text/csv": "csv"}
        formats.update({"application/jrd+json": "json", "application/activity+json": "json", "application/ld+json": "json",
                        "application/pdf": "pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
                        "application/vnd.oasis.opendocument.text": "odt", "image/png": "png", "image/jpeg": "jpeg", "image/tiff": "tiff"})
        if permit_xml:
            formats.update({"application/xml": "text", "text/xml": "text", "application/rss+xml": "text", "application/atom+xml": "text", "application/x-ndjson": "text", "application/jsonl": "text"})
        if permit_status and not mime:
            formats[""] = "text"
        if mime not in formats:
            raise InvalidInput("Source returned an unsupported evidence format. Supported types include text, HTML, JSON, CSV, PDF, DOCX, ODT and OCR images.")
        length = response.getheader("Content-Length")
        if length and (not length.isdecimal() or int(length) > MAX_BYTES):
            raise InvalidInput("Response exceeds the 2 MiB evidence limit.")
        chunks, size, wire_size = [], 0, 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InvalidInput("Collection timed out.")
            if connection.sock:
                connection.sock.settimeout(min(10, remaining))
            chunk = response.read1(min(65536, MAX_BYTES + 1 - wire_size))
            if not chunk:
                break
            wire_size += len(chunk)
            if wire_size > MAX_BYTES:
                raise InvalidInput("Response exceeds the 2 MiB wire limit.")
            if decoder is not None:
                # max_length bounds allocation even for a highly compressed body.
                # Do not call flush(): its length is only an initial buffer size,
                # not an upper bound on decoded output.
                chunk = decoder.decompress(chunk, MAX_BYTES + 1 - size)
                if decoder.unused_data:
                    raise InvalidInput("Compressed response has trailing data or multiple streams.")
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise InvalidInput("Response exceeds the 2 MiB evidence limit.")
        if decoder is not None and (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail):
            raise InvalidInput("Compressed response is incomplete or contains trailing data.")
        return b"".join(chunks), formats[mime], canonical
    except zlib.error as exc:
        raise InvalidInput("Compressed response is malformed or failed its integrity check.") from exc
    except (OSError, http.client.HTTPException) as exc:
        # Do not echo URLs, cookies, TLS internals, or server-controlled error data.
        raise InvalidInput("HTTPS collection failed. Check connectivity and the site's certificate.") from exc


class SearchHTML(HTMLParser):
    """Read organic links and snippets from DuckDuckGo's non-JavaScript pages."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.capture = None
        self.depth = 0
        self.parts = []
        self.size = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if self.capture:
            if tag not in {"br", "hr", "img", "input", "meta", "link", "wbr"}:
                self.depth += 1
            return
        if tag == "a" and classes & {"result__a", "result-link"} and len(self.results) < 30:
            self.results.append({"title": "", "url": attrs.get("href", ""), "snippet": ""})
            self.capture = "title"
        elif classes & {"result__snippet", "result-snippet"} and self.results:
            self.capture = "snippet"
        if self.capture:
            self.depth, self.parts, self.size = 1, [], 0

    def handle_endtag(self, tag):
        if not self.capture:
            return
        self.depth -= 1
        if self.depth <= 0:
            self.results[-1][self.capture] = " ".join("".join(self.parts).split())[:1000 if self.capture == "snippet" else 300]
            self.capture = None

    def handle_data(self, data):
        if self.capture and self.size < 2000:
            part = data[:2000 - self.size]
            self.parts.append(part)
            self.size += len(part)


class BingSearchHTML(HTMLParser):
    """Read headings and snippets only inside Bing's organic result items."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.stack = []
        self.result_depth = None
        self.heading_depth = None
        self.capture = None
        self.capture_depth = None
        self.parts = []
        self.size = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        self.stack.append(tag)
        depth = len(self.stack)
        if tag == "li" and "b_algo" in attrs.get("class", "").split() and len(self.results) < 30:
            self.results.append({"title": "", "url": "", "snippet": ""})
            self.result_depth = depth
        if self.result_depth is None:
            return
        if tag == "h2":
            self.heading_depth = depth
        if self.capture is None and tag == "a" and self.heading_depth and not self.results[-1]["url"]:
            self.results[-1]["url"] = attrs.get("href", "")
            self.capture = "title"
        elif self.capture is None and tag == "p" and not self.results[-1]["snippet"]:
            self.capture = "snippet"
        else:
            return
        self.capture_depth, self.parts, self.size = depth, [], 0

    def handle_endtag(self, tag):
        if tag not in self.stack:
            return
        depth = len(self.stack) - self.stack[::-1].index(tag)
        if self.capture is not None and depth <= self.capture_depth:
            limit = 300 if self.capture == "title" else 1000
            self.results[-1][self.capture] = " ".join("".join(self.parts).split())[:limit]
            self.capture = self.capture_depth = None
        if self.heading_depth and depth <= self.heading_depth:
            self.heading_depth = None
        if self.result_depth and depth <= self.result_depth:
            self.result_depth = None
        del self.stack[depth - 1:]

    def handle_data(self, data):
        if self.capture is not None and self.size < 2000 and not {"script", "style"}.intersection(self.stack):
            part = data[:2000 - self.size]
            self.parts.append(part)
            self.size += len(part)


def search_result_url(value, provider="duckduckgo"):
    """Decode a search redirect locally; never request the search wrapper."""
    if not isinstance(value, str) or len(value) > 8000:
        return None
    try:
        base = "https://www.bing.com/" if provider == "bing" else "https://lite.duckduckgo.com/"
        wrapper = urlsplit(urljoin(base, value))
        if wrapper.hostname in {"duckduckgo.com", "html.duckduckgo.com", "lite.duckduckgo.com"}:
            if wrapper.path != "/l/":
                return None
            values = parse_qs(wrapper.query, max_num_fields=12).get("uddg", [])
            if len(values) != 1:
                return None
            value = values[0]
        elif wrapper.hostname in {"bing.com", "www.bing.com"}:
            if provider != "bing" or wrapper.path != "/ck/a":
                return None
            values = parse_qs(wrapper.query, max_num_fields=20).get("u", [])
            if len(values) != 1 or not re.fullmatch(r"a1[A-Za-z0-9_-]+={0,2}", values[0]):
                return None
            encoded = values[0][2:]
            value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
        elif value.startswith("//"):
            value = wrapper.geturl()
        # Fragments select in-page content and never reach the server.
        value = value.split("#", 1)[0]
        value = quote(value, safe=":/?=&;%+@!$'()*,-._~[]")
        host, _, canonical = public_url(value, allow_query=True)
        if host in {"duckduckgo.com", "bing.com"} or host.endswith((".duckduckgo.com", ".bing.com")):
            return None
        return canonical
    except (InvalidInput, ValueError):
        return None


def parse_search(raw, provider="duckduckgo"):
    if not isinstance(provider, str) or provider not in SEARCH_PROVIDERS:
        raise InvalidInput("Choose a supported public search provider.")
    if len(raw) > MAX_BYTES:
        raise InvalidInput("Search response exceeds the 2 MiB evidence limit.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidInput("Search source did not return UTF-8 HTML.") from exc
    lower = text.lower()
    challenges = ("anomaly.js", "anomaly-modal", "challenge-form") if provider == "duckduckgo" else ("b_captcha", "b_captch", "captchaform", 'id="challenge-form"')
    if any(marker in lower for marker in challenges):
        raise InvalidInput(f"Search provider requires a human check. Open {SEARCH_PROVIDERS[provider]} in your browser or retry later; no challenge bypass is attempted.")
    parser = BingSearchHTML() if provider == "bing" else SearchHTML()
    parser.feed(text)
    parser.close()
    results, seen = [], set()
    for result in parser.results:
        url = search_result_url(result["url"], provider)
        if not url or url in seen:
            continue
        seen.add(url)
        results.append({**result, "url": url})
        if len(results) == 10:
            break
    no_results = ("no-results", "no results found", "no more results") if provider == "duckduckgo" else ('class="b_no"', 'class="b_no ', "no results found for", "there are no results for")
    if not results and not any(marker in lower for marker in no_results):
        raise InvalidInput("Search response could not be interpreted. The provider may have changed its page or blocked this request.")
    return results


def web_search(engine, case_id, query, enabled=False, provider="duckduckgo"):
    """No-key public web search; preserve the original search page as evidence."""
    if not enabled:
        raise InvalidInput("Collection is off. Restart with --allow-network to enable it.")
    if not isinstance(provider, str) or provider not in SEARCH_PROVIDERS:
        raise InvalidInput("Choose a supported public search provider.")
    with engine.connection() as db:
        engine.require_case(db, case_id)
    from .engine import field
    query = field(query, "Web search query", 200)
    base = "https://www.bing.com/search?" if provider == "bing" else "https://lite.duckduckgo.com/lite/?"
    url = base + urlencode({"q": query})
    raw, format, canonical = fetch_public(url, allow_query=True)
    saved = engine.ingest(case_id, raw, generated_title(SEARCH_PROVIDERS[provider] + " search: " + query), canonical, format)
    try:
        if format != "html":
            raise InvalidInput("Search source did not return HTML.")
        results = parse_search(raw, provider)
    except InvalidInput as exc:
        exc.evidence_id = saved["id"]
        raise
    return {"query": query, "provider": provider, "results": results, **saved}


SIGNALS = {"dns", "registration", "certificate", "website"}


def domain_name(value):
    if not isinstance(value, str) or len(value) > 253:
        raise InvalidInput("Enter a domain such as example.org, without a URL or path.")
    value = value.lower().strip()
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", value):
        raise InvalidInput("Enter a domain such as example.org, without a URL or path.")
    return value


def research(engine, case_id, domain, signals, enabled=False):
    if not enabled:
        raise InvalidInput("Collection is off. Restart with --allow-network to enable it.")
    if not isinstance(signals, list) or not signals or len(signals) > 4 or any(not isinstance(item, str) or item not in SIGNALS for item in signals) or len(set(signals)) != len(signals):
        raise InvalidInput("Select one or more distinct supported signals.")
    domain = domain_name(domain)
    with engine.connection() as db:
        engine.require_case(db, case_id)
    results = []
    for signal in signals:
        try:
            if signal == "website":
                saved = collect(engine, case_id, "https://" + domain + "/", enabled=True)
            elif signal == "registration":
                bootstrap, _, _ = fetch_public("https://data.iana.org/rdap/dns.json")
                try:
                    services = json.loads(bootstrap)["services"]
                    suffix = domain.rsplit(".", 1)[-1]
                    endpoints = [endpoint for suffixes, urls in services if suffix in suffixes for endpoint in urls if isinstance(endpoint, str) and endpoint.startswith("https://")]
                except (ValueError, KeyError, TypeError) as exc:
                    raise InvalidInput("Registration service directory could not be read.") from exc
                if not endpoints:
                    raise InvalidInput("No HTTPS registration service is listed for this domain suffix.")
                registration_domain = domain[4:] if domain.startswith('www.') else domain
                raw, format, canonical = fetch_public(endpoints[0].rstrip("/") + "/domain/" + registration_domain)
                if format != "json":
                    raise InvalidInput("Registration service did not return JSON.")
                saved = engine.ingest(case_id, raw, generated_title("Registration: " + registration_domain), canonical, "json")
            else:
                host, address, _, _ = target("https://" + domain + "/")
                if signal == "dns":
                    # Address records only: no claims about MX, NS, TXT or authoritative DNS.
                    records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                    addresses = sorted({record[4][0] for record in records})
                    if len(addresses) > 100 or any(not public_address(value) for value in addresses):
                        raise InvalidInput("DNS returned private, special-use or too many addresses.")
                    payload = {"domain": host, "addresses": addresses, "resolver": "operating system resolver", "scope": "A/AAAA address lookup; not authoritative DNS"}
                    source = "System DNS address lookup: " + domain
                else:
                    connection = PinnedHTTPS(host, address)
                    try:
                        connection.connect()
                        payload = {"domain": host, "connected_address": address, "certificate": connection.sock.getpeercert(), "certificate_der_base64": base64.b64encode(connection.sock.getpeercert(binary_form=True)).decode("ascii")}
                    finally:
                        connection.close()
                    source = "TLS certificate: https://" + domain + "/"
                raw = json.dumps(payload, indent=2, ensure_ascii=True).encode()
                saved = engine.ingest(case_id, raw, generated_title(signal.capitalize() + ": " + domain), source, "json")
            results.append({"signal": signal, "ok": True, **saved})
        except (InvalidInput, OSError, http.client.HTTPException) as exc:
            error = str(exc) if isinstance(exc, InvalidInput) else "Signal collection failed. Check connectivity or TLS certificate validity."
            results.append({"signal": signal, "ok": False, "error": error})
    return {"domain": domain, "results": results}


DISCOVERY_SOURCES = {"wikipedia", "wikidata"}


def discover(engine, case_id, sources, enabled=False, query=None):
    if not enabled:
        raise InvalidInput("Collection is off. Restart with --allow-network to enable it.")
    if not isinstance(sources, list) or not sources or len(sources) > 2 or any(not isinstance(item, str) or item not in DISCOVERY_SOURCES for item in sources) or len(set(sources)) != len(sources):
        raise InvalidInput("Select Wikipedia, Wikidata or both.")
    with engine.connection() as db:
        case = engine.require_case(db, case_id)
    from .engine import field
    query = case["name"] if query is None else field(query, "Discovery query", 200)
    results = []
    for source in sources:
        try:
            if source == "wikipedia":
                params = {"action":"query", "list":"search", "srsearch":query, "srlimit":"10", "format":"json", "utf8":"1"}
                url = "https://en.wikipedia.org/w/api.php?" + urlencode(params)
            else:
                params = {"action":"wbsearchentities", "search":query, "language":"en", "limit":"10", "format":"json"}
                url = "https://www.wikidata.org/w/api.php?" + urlencode(params)
            raw, format, canonical = fetch_public(url, allow_query=True)
            if format != "json":
                raise InvalidInput("Discovery source did not return JSON.")
            try:
                payload = json.loads(raw)
                if "error" in payload:
                    raise InvalidInput("Discovery source rejected the search. Try again later.")
                candidates = []
                if source == "wikipedia":
                    for item in payload["query"]["search"][:10]:
                        title = str(item["title"])
                        candidates.append({"label": title[:300], "description": re.sub(r"<[^>]*>", "", str(item.get("snippet", "")))[:1000], "url": "https://en.wikipedia.org/?curid=" + str(int(item["pageid"])), "collection_url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="")})
                else:
                    for item in payload["search"][:10]:
                        identifier = item["id"]
                        if not isinstance(identifier, str) or not re.fullmatch(r"Q[0-9]+", identifier):
                            continue
                        candidates.append({"label": str(item.get("label", identifier))[:300], "description": str(item.get("description", ""))[:1000], "url": "https://www.wikidata.org/wiki/" + identifier, "collection_url": "https://www.wikidata.org/wiki/Special:EntityData/" + identifier + ".json"})
            except (ValueError, KeyError, TypeError) as exc:
                raise InvalidInput("Discovery response could not be interpreted.") from exc
            saved = engine.ingest(case_id, raw, generated_title("Discovery: " + query + " (" + source + ")"), canonical, "json")
            results.append({"source": source, "ok": True, "candidates": candidates, **saved})
        except (InvalidInput, OSError, http.client.HTTPException) as exc:
            results.append({"source": source, "ok": False, "error": str(exc) if isinstance(exc, InvalidInput) else "Discovery failed. Check connectivity or TLS certificate validity."})
    return {"query": query, "focus_type": case["focus_type"], "interpretation": "Search results are candidates, not confirmed identities or relationships.", "results": results}


def news(engine, case_id, query, enabled=False):
    """Search recent public news metadata through the fixed GDELT DOC API."""
    if not enabled:
        raise InvalidInput("Collection is off. Restart with --allow-network to enable it.")
    with engine.connection() as db:
        engine.require_case(db, case_id)
    from .engine import field
    query = field(query, "News query", 200)
    phrase = " ".join(query.replace('"', ' ').split())
    params = {"query": '"' + phrase + '"', "mode": "artlist", "format": "json", "maxrecords": "8", "timespan": "3months", "sort": "datedesc"}
    raw, format, canonical = fetch_public("https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode(params), allow_query=True)
    if format != "json":
        raise InvalidInput("News source did not return JSON.")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
            raise ValueError()
        articles = []
        for item in payload["articles"][:8]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            articles.append({key: str(item.get(key, ""))[:2000 if key == "url" else 1000] for key in ("url", "title", "seendate", "language", "sourcecountry")})
    except (ValueError, TypeError) as exc:
        raise InvalidInput("News response could not be interpreted.") from exc
    saved = engine.ingest(case_id, raw, generated_title("News search: " + query), canonical, "json")
    return {"articles": articles, **saved}
