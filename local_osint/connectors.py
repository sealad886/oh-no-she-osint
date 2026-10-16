"""Trusted, bounded source adapters. Configuration never supplies executable code.

External observations are leads, not identity matches. Every response returned by
the public collector is saved before parsing, including HTTP failure bodies.
"""

# GitHub/GitLab URL definitions below are derived from Sherlock v0.15.0.
# MIT License
# Copyright (c) 2019 Sherlock Project
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
import re
from email import policy
from email.parser import Parser
from html.parser import HTMLParser
from urllib.parse import quote, urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET

from .collector import fetch_public_response, generated_title, public_url
from .engine import InvalidInput


TARGETS = ["person", "organization", "domain", "topic", "other"]
PROFILE_SITES = {
    "github": {"url": "https://github.com/{username}", "domain": "github.com"},
    "gitlab": {"url": "https://gitlab.com/{username}", "domain": "gitlab.com"},
}
PROFILE_PROVENANCE = {
    "project": "Sherlock", "version": "v0.15.0", "license": "MIT",
    "source": "https://github.com/sherlock-project/sherlock/blob/v0.15.0/sherlock_project/resources/data.json",
    "notice": "Sherlock Project, Copyright (c) 2019. See docs/SOURCE_LICENSES.md. URL definitions only; conservative local response checks.",
}


def _meta(id, label, inputs, domains, description, **extra):
    return {"id": id, "label": label, "input_kinds": inputs, "outbound_fields": inputs,
            "target_types": TARGETS, "domains": domains, "description": description,
            "version": "1", "license": "MIT (adapter); source terms apply",
            "max_requests": 1, "max_redirect_requests": 3, "timeout_seconds": 20,
            "max_bytes": 2097152, "credentials": False, **extra}


REGISTRY = {
    "webfinger": _meta("webfinger", "Public federated account lookup", ["account"], ["approved account domain"], "Resolve one approved public user@domain address. No cross-instance username scanning."),
    "public_actor": _meta("public_actor", "Public ActivityPub profile", ["url"], ["approved profile host"], "Read one public actor/profile document. Linked contexts and inboxes are never fetched."),
    "commoncrawl": _meta("commoncrawl", "Common Crawl URL discovery", ["url"], ["index.commoncrawl.org"], "Find historical URLs for an approved public URL prefix. Index records are discovery leads, not retrieved page evidence.", max_requests=2, max_redirect_requests=6),
    "crossref": _meta("crossref", "Crossref publications", ["query"], ["api.crossref.org"], "Public publication metadata; enable when research is relevant."),
    "github_public": _meta("github_public", "GitHub public profile", ["username"], ["api.github.com"], "One approved public username. Account existence does not establish target identity."),
    "public_profile": _meta("public_profile", "Public username profile", ["username", "site"], ["github.com", "gitlab.com"], "One selected public site per action; no account recovery or automatic ownership inference.", provenance=PROFILE_PROVENANCE, sites=list(PROFILE_SITES)),
    "website_sources": _meta("website_sources", "Website source discovery", ["url"], ["approved URL host"], "Discover same-host links, feeds, sitemaps and structured data from one public response."),
    "wayback": _meta("wayback", "Wayback public URL lookup", ["url"], ["archive.org"], "Check archived availability for an already approved public URL. Archived copies are not independent corroboration."),
    "email_headers": _meta("email_headers", "Local email header analysis", ["evidence_id"], [], "Analyze saved UTF-8 email headers locally. No message upload or outbound lookup.", outbound_fields=[], max_requests=0, max_redirect_requests=0),
    "public_place": _meta("public_place", "Public place references", ["query"], ["www.geonames.org"], "Search public locality references. No private address lookup or automatic Nominatim geocoding."),
    "searxng": _meta("searxng", "Owned SearXNG metasearch", ["query"], ["selected search engines"], "Optional owned service. Upstream fanout and failures are reported separately.", license="AGPL-3.0-or-later (SearXNG); MIT (adapter)"),
}


def catalogue(settings=None):
    settings = settings or {}
    service = settings.get("search_service")
    result = []
    for item in REGISTRY.values():
        entry = json.loads(json.dumps(item))
        entry.update({"available": True, "availability": "ready"})
        if entry["id"] == "searxng":
            state = service.status() if service else {"state": "unavailable", "message": "Set up the optional local search service in Sources."}
            entry.update({"available": state.get("state") == "ready", "availability": state.get("state", "unavailable"), "availability_message": state.get("message", ""), "service": state})
        result.append(entry)
    return result


def validate_arguments(connector_id, arguments):
    if connector_id not in REGISTRY or not isinstance(arguments, dict):
        raise InvalidInput("Choose a built-in source and provide its input fields.")
    allowed = set(REGISTRY[connector_id]["input_kinds"])
    if set(arguments) != allowed:
        raise InvalidInput("Provide exactly the input fields declared by this source.")
    out = {}
    for key, value in arguments.items():
        if not isinstance(value, str) or not value.strip() or len(value) > (2000 if key == "url" else 300) or any(ord(c) < 32 for c in value):
            raise InvalidInput("Source inputs must be nonempty bounded text without control characters.")
        out[key] = value.strip()
    if "url" in out:
        out["url"] = public_url(out["url"], allow_query=True)[2]
    if "account" in out:
        account = out["account"].lstrip("@")
        if account.count("@") != 1:
            raise InvalidInput("Use an approved public federated address in user@domain form.")
        username, host = account.split("@")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
            raise InvalidInput("Public federated username contains unsupported characters.")
        host = public_url("https://" + host + "/")[0]
        out["account"] = username + "@" + host
    if "username" in out and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,38}", out["username"]):
        raise InvalidInput("Use an approved public username with letters, numbers, underscores or hyphens, up to 39 characters.")
    if "site" in out and out["site"] not in PROFILE_SITES:
        raise InvalidInput("Choose GitHub or GitLab from the reviewed site pack.")
    if "evidence_id" in out and not re.fullmatch(r"[a-f0-9]{32}", out["evidence_id"]):
        raise InvalidInput("Choose a saved evidence item from this case.")
    return out


def _lead(url, title="", snippet="", **extra):
    try:
        canonical = public_url(url, allow_query=True)[2]
    except (InvalidInput, TypeError):
        return None
    return {"url": canonical, "title": str(title)[:300], "snippet": str(snippet)[:1000], **extra}


def _save_response(engine, case_id, connector_id, response):
    raw, format, canonical = response["raw"], response["format"], response["canonical"]
    if format == "json":
        try:
            json.loads(raw)
        except (ValueError, UnicodeError):
            format = "text"
    return engine.ingest(case_id, raw, generated_title(REGISTRY[connector_id]["label"] + ": " + canonical), canonical, format)


class SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.scripts, self.current = [], [], None
        self.anchor = None

    def _finish_anchor(self):
        if self.anchor is None:
            return
        index = self.anchor["index"]
        href, title, kind = self.links[index]
        if not title.strip():
            title = " ".join("".join(self.anchor["parts"]).split())[:300]
        self.links[index] = (href, title, kind)
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self._finish_anchor()
        if tag in {"a", "link"} and attrs.get("href") and len(self.links) < 100:
            self.links.append((attrs["href"], attrs.get("title", "")[:300], " ".join((attrs.get("rel", ""), attrs.get("type", "")))))
            if tag == "a":
                self.anchor = {"index": len(self.links) - 1, "parts": [], "size": 0, "tags": []}
        if self.anchor is not None:
            hidden = tag in {"script", "style", "template"} or "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true"
            hidden = hidden or bool(self.anchor["tags"] and self.anchor["tags"][-1][1])
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.anchor["tags"].append((tag, hidden))
            elif tag == "br" and not hidden:
                self._anchor_text(" ")
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.current = []

    def _anchor_text(self, data):
        if self.anchor is not None and self.anchor["size"] < 600:
            part = data[:600 - self.anchor["size"]]
            self.anchor["parts"].append(part)
            self.anchor["size"] += len(part)

    def handle_data(self, data):
        if self.current is not None and sum(map(len, self.current)) < 100000:
            self.current.append(data[:100000])
        if self.anchor is not None and not (self.anchor["tags"] and self.anchor["tags"][-1][1]):
            self._anchor_text(data)

    def handle_endtag(self, tag):
        if self.anchor is not None:
            tags = self.anchor["tags"]
            for index in range(len(tags) - 1, -1, -1):
                if tags[index][0] == tag:
                    del tags[index:]
                    break
            if tag == "a":
                self._finish_anchor()
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None

    def close(self):
        super().close()
        self._finish_anchor()


def _website(raw, format, canonical, context=""):
    text = raw.decode("utf-8")
    host = urlsplit(canonical).hostname
    links, observations = [], []
    if format == "html":
        parser = SourceLinks()
        parser.feed(text)
        parser.close()
        links = [(urljoin(canonical, href), title, kind) for href, title, kind in parser.links]
        for script in parser.scripts[:10]:
            try:
                data = json.loads(script)
            except (ValueError, TypeError):
                continue
            for node in (data if isinstance(data, list) else [data]):
                if isinstance(node, dict) and isinstance(node.get("name"), str):
                    observations.append({"kind": "structured_name", "value": node["name"][:300], "quote": node["name"][:300], "status": "unverified"})
    elif text.lstrip().startswith("<"):
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise InvalidInput("XML declarations containing entities are not parsed.")
        root = ET.fromstring(text)
        for element in root.iter():
            name = element.tag.rsplit("}", 1)[-1].lower()
            if name in {"loc", "link"}:
                value = element.attrib.get("href") or (element.text or "").strip()
                if value:
                    links.append((urljoin(canonical, value), "Public feed or sitemap entry", name))
            if len(links) >= 100:
                break
    seen, leads = set(), []
    for url, title, kind in links:
        item = _lead(url, title or "Same-host public source", kind)
        if item and urlsplit(item["url"]).hostname == host and item["url"] not in seen and item["url"] != canonical:
            seen.add(item["url"])
            leads.append(item)
    # Rank locally: target-bearing pages, documents and feeds should surface
    # before generic navigation. No extra request or context disclosure occurs.
    terms = {term.lower() for term in re.findall(r"\w{3,}", context)[:30]}
    def rank(item):
        value = (item["url"] + " " + item["title"] + " " + item["snippet"]).lower()
        tokens = set(re.findall(r"\w{3,}", value))
        score = len(terms & tokens) * 8
        score += 4 if re.search(r"\.(?:pdf|docx|odt)$", urlsplit(item["url"]).path, re.I) else 0
        score += 3 if any(term in value for term in ("rss", "atom", "sitemap", "feed.xml")) else 0
        score += len(tokens & {"staff", "team", "people", "publications", "reports", "bulletin", "directory", "members", "news"}) * 2
        score -= len(tokens & {"login", "signin", "privacy", "cookies", "terms"}) * 4
        return score
    leads.sort(key=rank, reverse=True)
    return leads[:40], observations


def execute(engine, case_id, connector_id, arguments, enabled=False, settings=None):
    """Run one trusted action. Caller additionally enforces case disclosure scope."""
    args = validate_arguments(connector_id, arguments)
    with engine.connection() as db:
        case = engine.require_case(db, case_id)
    result = {"status": "empty", "evidence_ids": [], "leads": [], "observations": [], "summary": "No usable observations returned.", "requests": 0, "bytes": 0, "connector": connector_id}
    settings = settings or {}
    if connector_id == "email_headers":
        evidence = engine.evidence(case_id, args["evidence_id"])
        headers = Parser(policy=policy.default).parsestr(evidence["text"], headersonly=True)
        observations = []
        for name in ("From", "To", "Date", "Subject", "Message-ID", "Received", "Authentication-Results", "DKIM-Signature", "Return-Path"):
            for value in headers.get_all(name, [])[:20]:
                observations.append({"kind": "email_header", "field": name, "value": str(value)[:2000], "quote": str(value)[:2000], "evidence_id": evidence["id"], "status": "unverified"})
        result.update(status="success" if observations else "empty", evidence_ids=[evidence["id"]], observations=observations, summary="Saved email headers analyzed locally; header assertions are not independently authenticated.")
        return result
    if not enabled:
        raise InvalidInput("Web collection is off. Enable public network collection to run this source.")
    if connector_id == "searxng":
        manager = settings.get("search_service")
        if manager is None:
            raise InvalidInput("Set up and start the optional search service in Sources.")
        response = manager.search(args["query"])
        raw = response["raw"]
        source = "local-osint:searxng-response:" + quote(args["query"], safe="")
        try:
            data = json.loads(raw)
            format = "json"
        except (ValueError, UnicodeError):
            data, format = {}, "text"
        saved = engine.ingest(case_id, raw, "SearXNG public search response", source, format, extract=False)
        result.update(evidence_ids=[saved["id"]], requests=1, bytes=len(raw), upstream_fanout=response.get("upstream_fanout"), upstream_failures=response.get("upstream_failures", []))
        status = response.get("status", 200)
        if status != 200:
            result.update(status="rate_limited" if status == 429 else "blocked" if status in {401, 403} else "error", http_status=status, summary=f"Owned search service returned HTTP {status}; response retained.")
            return result
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            result.update(status="uninterpretable", summary="Owned search response retained, but expected result records were absent.")
            return result
        result["leads"] = [lead for row in data.get("results", [])[:40] if isinstance(row, dict) and (lead := _lead(row.get("url"), row.get("title", ""), row.get("content", "")))]
        result.update(status="success" if result["leads"] else ("blocked" if result["upstream_failures"] else "empty"), summary="Metasearch leads are unverified; fanout is configured engine count, not an exact upstream request count.")
        return result
    if connector_id == "crossref":
        url = "https://api.crossref.org/works?" + urlencode({"query": args["query"], "rows": 10})
    elif connector_id == "webfinger":
        url = "https://" + args["account"].split("@")[1] + "/.well-known/webfinger?" + urlencode({"resource": "acct:" + args["account"]})
    elif connector_id == "commoncrawl":
        index = settings.get("commoncrawl_index")
        if index is None:
            catalogue_url = "https://index.commoncrawl.org/collinfo.json"
            try:
                response = fetch_public_response(catalogue_url, allow_query=True, allowed_hosts={"index.commoncrawl.org"})
                saved = _save_response(engine, case_id, connector_id, response)
                result.update(evidence_ids=[saved["id"]], requests=response.get("requests", 1), bytes=len(response["raw"]))
                if response["status"] != 200:
                    result.update(status="rate_limited" if response["status"] == 429 else "blocked", summary="Common Crawl index catalogue unavailable; response retained.", retry_after=response.get("retry_after"))
                    return result
                entries = json.loads(response["raw"])
                if not isinstance(entries, list):
                    raise ValueError()
                index = next((entry["id"] for entry in entries[:100] if isinstance(entry, dict) and re.fullmatch(r"CC-MAIN-\d{4}-\d{2}", str(entry.get("id", "")))), None)
            except (InvalidInput, ValueError, TypeError, KeyError):
                result.update(status="uninterpretable", summary="Common Crawl index catalogue could not be interpreted; no index query was sent.")
                return result
        if not isinstance(index, str) or not re.fullmatch(r"CC-MAIN-\d{4}-\d{2}", index):
            raise InvalidInput("Choose a Common Crawl index ID such as CC-MAIN-2026-39.")
        result["index"] = index
        url = "https://index.commoncrawl.org/" + index + "-index?" + urlencode({"url": args["url"], "matchType": "prefix", "output": "json", "filter": "status:200", "collapse": "urlkey", "limit": 40})
    elif connector_id == "github_public":
        url = "https://api.github.com/users/" + quote(args["username"], safe="")
    elif connector_id == "public_profile":
        url = PROFILE_SITES[args["site"]]["url"].format(username=quote(args["username"], safe=""))
    elif connector_id == "wayback":
        url = "https://archive.org/wayback/available?" + urlencode({"url": args["url"]})
    elif connector_id == "public_place":
        url = "https://www.geonames.org/search.html?" + urlencode({"q": args["query"], "country": ""})
    else:
        url = args["url"]
    result["requests"] += 1
    try:
        options = {"allow_query": True, "allowed_hosts": {urlsplit(url).hostname}}
        if connector_id == "public_actor":
            options["accept"] = "activitypub"
        response = fetch_public_response(url, **options)
    except InvalidInput as exc:
        result.update(status="error", summary=str(exc))
        return result
    raw, format, canonical = response["raw"], response["format"], response["canonical"]
    saved = _save_response(engine, case_id, connector_id, response)
    evidence_id = saved["id"]
    result.update(evidence_ids=result["evidence_ids"] + [evidence_id], bytes=result["bytes"] + len(raw), requests=result["requests"] + response.get("requests", 1) - 1, http_status=response["status"])
    status = response["status"]
    if status != 200:
        outcome = "rate_limited" if status == 429 else "empty" if status in {404, 410} else "blocked" if status in {401, 403, 999} else "error"
        result.update(status=outcome, summary=f"Source returned HTTP {status}; response retained.", retry_after=response.get("retry_after"))
        return result
    try:
        if connector_id == "webfinger":
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("subject", "").lower() != ("acct:" + args["account"]).lower() or not isinstance(data.get("links"), list):
                raise ValueError("Account subject mismatch")
            for link in data["links"][:30]:
                if not isinstance(link, dict) or link.get("rel") not in {"self", "http://webfinger.net/rel/profile-page"}:
                    continue
                actor = link.get("rel") == "self"
                if actor and str(link.get("type", "")).split(";", 1)[0].strip().lower() not in {"application/activity+json", "application/ld+json"}:
                    continue
                lead = _lead(link.get("href"), args["account"], "Public account discovery; person or organization ownership remains unconfirmed.", connector="public_actor" if actor else "collect_page", account=args["account"], relation=link["rel"], content_type=link.get("type", ""))
                if lead:
                    result["leads"].append(lead)
            result["leads"].sort(key=lambda item: item["connector"] != "public_actor")
            result["observations"] = [{"kind": "public_federated_account", "value": data["subject"], "quote": data["subject"], "ownership": "unconfirmed"}]
        elif connector_id == "public_actor":
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("type") not in {"Person", "Organization", "Group", "Service", "Application"} or not isinstance(data.get("preferredUsername"), str):
                raise ValueError("Not a public actor")
            actor = _lead(data.get("id"), data.get("name") or data["preferredUsername"])
            if not actor or urlsplit(actor["url"]).hostname != urlsplit(canonical).hostname:
                raise ValueError("Actor origin mismatch")
            result["leads"] = [actor]
            for key in ("preferredUsername", "name", "summary", "published"):
                if isinstance(data.get(key), str) and data[key].strip():
                    result["observations"].append({"kind": "actor_" + key, "value": data[key][:1000], "quote": data[key][:1000], "ownership": "unconfirmed"})
            # @context, inbox, sharedInbox, followers and outbox are intentionally
            # inert data: reading a profile does not authorize those actions.
        elif connector_id == "commoncrawl":
            for line in raw.decode("utf-8").splitlines()[:40]:
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue
                lead = _lead(item.get("url"), "Historical public URL", "Common Crawl index capture: " + str(item.get("timestamp", "")), archive_index=True, capture_date=str(item.get("timestamp", "")), original_url=item.get("url"), index=result["index"])
                if lead and urlsplit(lead["url"]).hostname == urlsplit(args["url"]).hostname and lead["url"].startswith(args["url"]):
                    result["leads"].append(lead)
        elif connector_id == "crossref":
            data = json.loads(raw)
            items = data.get("message", {}).get("items")
            if not isinstance(items, list):
                raise ValueError("Missing publication records")
            for item in items[:10]:
                if not isinstance(item, dict):
                    continue
                title = (item.get("title") or [""])[0]
                lead = _lead(item.get("URL") or ("https://doi.org/" + item["DOI"] if isinstance(item.get("DOI"), str) else ""), title, item.get("publisher", ""))
                if lead:
                    result["leads"].append(lead)
                if isinstance(title, str) and title:
                    result["observations"].append({"kind": "publication_title", "value": title[:1000], "quote": title[:1000]})
                for author in (item.get("author") or [])[:30]:
                    if not isinstance(author, dict):
                        continue
                    # Individual JSON fields remain literal evidence passages.
                    for key in ("given", "family", "name"):
                        if isinstance(author.get(key), str) and author[key]:
                            result["observations"].append({"kind": "publication_author_" + key, "value": author[key][:300], "quote": author[key][:300]})
                    for affiliation in (author.get("affiliation") or [])[:10]:
                        if isinstance(affiliation, dict) and isinstance(affiliation.get("name"), str) and affiliation["name"]:
                            result["observations"].append({"kind": "publication_affiliation", "value": affiliation["name"][:1000], "quote": affiliation["name"][:1000]})
        elif connector_id == "github_public":
            data = json.loads(raw)
            if not isinstance(data, dict) or str(data.get("login", "")).lower() != args["username"].lower() or not isinstance(data.get("id"), int):
                raise ValueError("Profile did not identify requested account")
            lead = _lead(data.get("html_url", ""), data.get("name") or data["login"], data.get("bio") or "")
            if lead:
                result["leads"].append(lead)
            result["observations"].append({"kind": "public_account", "value": data["login"], "quote": data["login"], "ownership": "unconfirmed"})
            for key in ("name", "company", "location", "bio", "blog"):
                if isinstance(data.get(key), str) and data[key].strip():
                    result["observations"].append({"kind": "profile_" + key, "value": data[key][:1000], "quote": data[key][:1000], "ownership": "unconfirmed"})
        elif connector_id == "public_profile":
            text = raw.decode("utf-8")
            if any(word in text.lower() for word in ("captcha", "verify you are human", "access denied", "sign in to continue")):
                result.update(status="blocked", summary="Public profile returned an access barrier; ownership remains unconfirmed.")
                return result
            # Positive account metadata is required. A generic HTTP 200 is never existence proof.
            names = re.findall(r'<meta\s+[^>]*(?:name|property)=[\"\'](?:og:title|profile:username|twitter:title)[\"\'][^>]*content=[\"\']([^\"\']+)', text, re.I)
            if not any(re.search(r"(?<![\w-])" + re.escape(args["username"]) + r"(?![\w-])", value, re.I) for value in names):
                result.update(status="uninterpretable", summary="HTTP 200 without matching public account metadata; not counted as a profile.")
                return result
            result["leads"] = [_lead(canonical, args["username"], "Public account metadata matched; target ownership unconfirmed.")]
            result["observations"] = [{"kind": "public_account", "value": args["username"], "quote": next(x for x in names if args["username"].lower() in x.lower()), "ownership": "unconfirmed"}]
        elif connector_id == "wayback":
            data = json.loads(raw)
            snapshot = data.get("archived_snapshots", {}).get("closest", {})
            if snapshot.get("available") is True and str(snapshot.get("status")) == "200":
                candidate = snapshot.get("url", "")
                if candidate.startswith("http://web.archive.org/"):
                    candidate = "https://" + candidate[7:]
                lead = _lead(candidate, "Archived public source", "Capture: " + str(snapshot.get("timestamp", "")), archived=True, original_url=args["url"], capture_date=str(snapshot.get("timestamp", "")))
                if lead and urlsplit(lead["url"]).hostname == "web.archive.org":
                    result["leads"].append(lead)
        else:
            leads, observations = _website(raw, format, canonical, " ".join([case["name"], *case["aliases"]]))
            if connector_id == "public_place":
                leads = [lead for lead in leads if re.search(r"/\d{2,}/", urlsplit(lead["url"]).path)]
            result.update(leads=leads, observations=observations)
        for item in result["observations"]:
            item.update(evidence_id=evidence_id, status="unverified")
        result.update(status="success" if result["leads"] or result["observations"] else "empty", summary="Public observations retained as unverified leads; candidate identity requires separate evidence.")
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError, ET.ParseError):
        result.update(status="uninterpretable", summary="Response retained, but expected public records could not be interpreted.")
    return result
