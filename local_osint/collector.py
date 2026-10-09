"""One-page HTTPS collection with DNS validation and a pinned destination."""

import base64
import http.client
import json
import re
import ipaddress
import socket
import ssl
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from .engine import InvalidInput, MAX_BYTES


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


def target(url, allow_query=False):
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
        records = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
        if not addresses or any(not public_address(address) for address in addresses):
            raise InvalidInput("Destination resolves to a private or special-use address.")
        authority = f"[{host}]" if ":" in host else host
        canonical = urlunsplit(("https", authority, parsed.path or "/", parsed.query if allow_query else "", ""))
        request_path = (parsed.path or "/") + (("?" + parsed.query) if allow_query and parsed.query else "")
        return host, addresses[0], request_path, canonical
    except (ValueError, OSError) as exc:
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


def collect(engine, case_id, url, enabled=False):
    if not enabled:
        raise InvalidInput("Web collection is off. Restart with --allow-network to enable it.")
    # Validate case before DNS or outbound requests.
    with engine.connection() as db:
        engine.require_case(db, case_id)
    raw, format, canonical = fetch_public(url)
    return engine.ingest(case_id, raw, generated_title(canonical), canonical, format)


def fetch_public(url, allow_query=False):
    host, address, path, canonical = target(url, allow_query=allow_query)
    connection = PinnedHTTPS(host, address)
    try:
        connection.request("GET", path, headers={"User-Agent": "LocalOSINT/0.1 (single-page public evidence collection)", "Accept": "text/html, text/plain, application/json, text/csv", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:
            raise InvalidInput("Destination did not return HTTP 200. Redirects are not followed.")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise InvalidInput("Compressed responses are unsupported.")
        mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        formats = {"text/html": "html", "text/plain": "text", "application/json": "json", "application/rdap+json": "json", "text/csv": "csv"}
        if mime not in formats:
            raise InvalidInput("Only UTF-8 HTML, text, JSON and CSV responses are supported.")
        length = response.getheader("Content-Length")
        if length and (not length.isdecimal() or int(length) > MAX_BYTES):
            raise InvalidInput("Response exceeds the 2 MiB evidence limit.")
        deadline = time.monotonic() + 20
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise InvalidInput("Collection timed out.")
            if connection.sock:
                connection.sock.settimeout(min(10, remaining))
            chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                raise InvalidInput("Response exceeds the 2 MiB evidence limit.")
        return b"".join(chunks), formats[mime], canonical
    except (OSError, http.client.HTTPException) as exc:
        # Do not echo URLs, cookies, TLS internals, or server-controlled error data.
        raise InvalidInput("HTTPS collection failed. Check connectivity and the site's certificate.") from exc
    finally:
        connection.close()


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
                raw, format, canonical = fetch_public(endpoints[0].rstrip("/") + "/domain/" + domain)
                if format != "json":
                    raise InvalidInput("Registration service did not return JSON.")
                saved = engine.ingest(case_id, raw, generated_title("Registration: " + domain), canonical, "json")
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
                        candidates.append({"label": title, "description": re.sub(r"<[^>]*>", "", str(item.get("snippet", ""))), "url": "https://en.wikipedia.org/?curid=" + str(int(item["pageid"]))})
                else:
                    for item in payload["search"][:10]:
                        identifier = item["id"]
                        if not isinstance(identifier, str) or not re.fullmatch(r"Q[0-9]+", identifier):
                            continue
                        candidates.append({"label": str(item.get("label", identifier)), "description": str(item.get("description", "")), "url": "https://www.wikidata.org/wiki/" + identifier})
            except (ValueError, KeyError, TypeError) as exc:
                raise InvalidInput("Discovery response could not be interpreted.") from exc
            saved = engine.ingest(case_id, raw, generated_title("Discovery: " + query + " (" + source + ")"), canonical, "json")
            results.append({"source": source, "ok": True, "candidates": candidates, **saved})
        except (InvalidInput, OSError, http.client.HTTPException) as exc:
            results.append({"source": source, "ok": False, "error": str(exc) if isinstance(exc, InvalidInput) else "Discovery failed. Check connectivity or TLS certificate validity."})
    return {"query": query, "focus_type": case["focus_type"], "interpretation": "Search results are candidates, not confirmed identities or relationships.", "results": results}
