"""Evidence storage and deterministic indicator extraction. No network operations."""

import base64
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import stat
import uuid
from bisect import bisect_right
from contextlib import contextmanager
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_BYTES = 2 * 1024 * 1024
MAX_ENTITIES = 10000
FORMATS = {"text", "html", "json", "csv", "pdf", "docx", "odt", "png", "jpeg", "tiff"}
FOCUS_TYPES = {"person", "organization", "domain", "topic", "other"}
KINDS = {"email", "domain", "url", "ipv4", "ipv6"}
DOMAIN = r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}"
TLD_SOURCE = Path(__file__).with_name("tlds.txt")
TLDS = frozenset(line.strip().lower() for line in TLD_SOURCE.read_text().splitlines() if line and not line.startswith("#"))
PATTERNS = [
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@" + DOMAIN + r"\b", re.ASCII)),
    ("url", re.compile(r"https?://[^\s<>\"']+", re.I)),
    ("domain", re.compile(r"(?<![\w.-])" + DOMAIN + r"(?![\w.-])", re.ASCII)),
    ("ipv4", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")),
    ("ipv6", re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.]*(?![\w:])")),
]


class InvalidInput(ValueError):
    """An actionable, safe-to-display validation failure."""


def now():
    return datetime.now(timezone.utc).isoformat()


def field(value, name, limit=200, required=True):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise InvalidInput(f"{name} must be text, up to {limit} characters.")
    value = value.strip()
    if required and not value:
        raise InvalidInput(f"Enter {name.lower()}.")
    return value


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}:
            self.hidden += 1
        if not self.hidden:
            self.parts.append("\n")
            for key, value in attrs:
                if key in {"href", "src"} and value:
                    self.parts.append(value + "\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def searchable_text(raw, format):
    if format not in FORMATS:
        raise InvalidInput("Choose text, HTML, JSON, CSV, PDF, DOCX, ODT, PNG, JPEG or TIFF.")
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES:
        raise InvalidInput("Evidence must contain 1 byte to 2 MiB.")
    from .documents import FORMATS as DOCUMENT_FORMATS, extract as extract_document
    if format in DOCUMENT_FORMATS:
        return extract_document(raw, format)["text"]
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidInput("Use UTF-8 text, HTML, JSON or CSV; binary files are unsupported.") from exc
    if "\x00" in text:
        raise InvalidInput("Binary evidence is unsupported. Convert it to UTF-8 text first.")
    if format == "json":
        try:
            json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise InvalidInput("This file is not valid JSON.") from exc
    if format == "html":
        parser = PlainHTML()
        parser.feed(text)
        text = "".join(parser.parts)
    return text


def indicators(text):
    matches = []
    url_ranges = []

    def add(kind, value, start, end):
        matches.append({"kind": kind, "value": value, "start": start, "end": end})
        if len(matches) > MAX_ENTITIES:
            raise InvalidInput("Evidence has over 10,000 indicator mentions. Split it into smaller files.")

    # URLs establish spans so filenames and query text cannot become domain indicators.
    url_pattern = next(pattern for kind, pattern in PATTERNS if kind == "url")
    for match in url_pattern.finditer(text):
        value = match.group().rstrip(".,;:!?")
        pairs = {")": "(", "]": "[", "}": "{"}
        while value and value[-1] in pairs and value.count(value[-1]) > value.count(pairs[value[-1]]):
            value = value[:-1].rstrip(".,;:!?")
        host_start, host_end = -1, -1
        try:
            parsed = urlsplit(value)
            if parsed.hostname:
                authority_start = match.start() + value.index("://") + 3
                host_start = authority_start + parsed.netloc.lower().rfind(parsed.hostname.lower())
                host_end = host_start + len(parsed.hostname)
            if parsed.hostname and not parsed.username and not parsed.password:
                # Query and fragment remain in original evidence, but not the normalized entity.
                normalized = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
                add("url", normalized, match.start(), match.start() + len(value))
        except ValueError:
            pass
        url_ranges.append((match.start(), match.end(), host_start, host_end))
    url_starts = [item[0] for item in url_ranges]

    for kind, pattern in PATTERNS:
        if kind == "url":
            continue
        for match in pattern.finditer(text):
            value = match.group()
            try:
                if kind in {"ipv4", "ipv6"}:
                    value = str(ipaddress.ip_address(value))
                elif kind == "email":
                    local, domain = value.rsplit("@", 1)
                    if domain.rsplit(".", 1)[-1].lower() not in TLDS:
                        continue
                    value = local + "@" + domain.lower()
                else:
                    value = value.lower()
                    if value.rsplit(".", 1)[-1] not in TLDS:
                        continue
                    index = bisect_right(url_starts, match.start()) - 1
                    if index >= 0:
                        start, end, host_start, host_end = url_ranges[index]
                        if start <= match.start() < end and not (host_start <= match.start() and match.end() <= host_end):
                            continue
            except ValueError:
                continue
            add(kind, value, match.start(), match.end())
    return sorted(matches, key=lambda x: (x["start"], x["kind"]))


def private_directory(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    # Reject symlinks at every existing component, including the final path.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise InvalidInput("Data directory must not contain symlinks.")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise InvalidInput("Data path must be a directory.")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise InvalidInput("Data directory must belong to the current user.")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
        raise InvalidInput("Data directory must be private. Set its permissions to 700.")
    return path


class Engine:
    def __init__(self, directory):
        self.directory = private_directory(directory)
        self.path = self.directory / "evidence.sqlite3"
        if self.path.is_symlink():
            raise InvalidInput("Database must not be a symlink.")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise InvalidInput("Database must be a regular, unlinked private file.")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise InvalidInput("Database must belong to the current user.")
            if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                raise InvalidInput("Database permissions must be 600.")
        finally:
            os.close(fd)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise InvalidInput("Unsupported database version. Restore a compatible backup.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    focus_type TEXT NOT NULL, aliases TEXT NOT NULL, identifiers TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    title TEXT NOT NULL, source TEXT NOT NULL, format TEXT NOT NULL,
                    collected_at TEXT NOT NULL, sha256 TEXT NOT NULL, raw BLOB NOT NULL,
                    text TEXT NOT NULL, UNIQUE(case_id, source, sha256));
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    kind TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(case_id, kind, value));
                CREATE TABLE IF NOT EXISTS mentions (
                    entity_id TEXT NOT NULL REFERENCES entities(id),
                    evidence_id TEXT NOT NULL REFERENCES evidence(id),
                    start INTEGER NOT NULL, end INTEGER NOT NULL,
                    PRIMARY KEY(entity_id, evidence_id, start, end));
                CREATE INDEX IF NOT EXISTS evidence_case ON evidence(case_id);
                CREATE INDEX IF NOT EXISTS entities_case ON entities(case_id);
                CREATE INDEX IF NOT EXISTS mentions_evidence ON mentions(evidence_id);
            """)
        from .workspace import initialize_storage
        initialize_storage(self, version)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def case_record(row):
        result = dict(row)
        result["aliases"] = json.loads(result["aliases"])
        result["identifiers"] = json.loads(result["identifiers"])
        return result

    def create_case(self, name, focus_type="topic", aliases=None, identifiers=None):
        if focus_type not in FOCUS_TYPES:
            raise InvalidInput("Choose person, organization, domain, topic or other.")
        def values(items, label):
            if items is None:
                return []
            if not isinstance(items, list) or len(items) > 20:
                raise InvalidInput(f"Provide up to 20 {label.lower()} as a list.")
            return list(dict.fromkeys(field(item, label, 200) for item in items))
        case = {"id": uuid.uuid4().hex, "name": field(name, "Subject name", 100), "created_at": now(),
                "focus_type": focus_type, "aliases": values(aliases, "Aliases"), "identifiers": values(identifiers, "Known identifiers")}
        with self.connection() as db:
            db.execute("INSERT INTO cases VALUES (?,?,?,?,?,?)", (case["id"], case["name"], case["created_at"], focus_type, json.dumps(case["aliases"]), json.dumps(case["identifiers"])))
        return case

    def cases(self):
        with self.connection() as db:
            return [self.case_record(x) for x in db.execute("SELECT * FROM cases ORDER BY created_at DESC")]

    @staticmethod
    def require_case(db, case_id):
        case = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case:
            raise InvalidInput("Case not found. Choose an existing case.")
        return Engine.case_record(case)

    def ingest(self, case_id, raw, title, source="Imported file", format="text", extract=True, document_options=None):
        title = field(title, "Evidence title")
        source = field(source, "Source", 2000)
        if source.startswith('local-osint:') and extract:
            raise InvalidInput('This source prefix is reserved for generated local reports.')
        with self.connection() as db:
            self.require_case(db, case_id)
        from .documents import FORMATS as DOCUMENT_FORMATS, extract as extract_document
        document = None
        if format in DOCUMENT_FORMATS:
            if document_options is not None and (not isinstance(document_options, dict) or set(document_options) - {"ocr", "language"}):
                raise InvalidInput("Document options accept only OCR on/off and installed language codes.")
            document = extract_document(raw, format, **(document_options or {}))
            text = document["text"]
        else:
            text = searchable_text(raw, format)
        found = indicators(text) if extract else []
        digest = hashlib.sha256(raw).hexdigest()
        with self.connection() as db:
            self.require_case(db, case_id)
            # Serialize deduplication and all subsequent writes as one transaction.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM evidence WHERE case_id=? AND source=? AND sha256=?", (case_id, source, digest)).fetchone()
            if existing:
                return {"id": existing["id"], "duplicate": True}
            evidence_id = uuid.uuid4().hex
            db.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?)", (evidence_id, case_id, title, source, format, now(), digest, raw, text))
            for item in found:
                db.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?,?)", (uuid.uuid4().hex, case_id, item["kind"], item["value"]))
                entity_id = db.execute("SELECT id FROM entities WHERE case_id=? AND kind=? AND value=?", (case_id, item["kind"], item["value"])).fetchone()[0]
                db.execute("INSERT OR IGNORE INTO mentions VALUES (?,?,?,?)", (entity_id, evidence_id, item["start"], item["end"]))
        result = {"id": evidence_id, "duplicate": False, "mentions": len(found)}
        if document is not None:
            from .workspace import Workspace
            record = Workspace(self).record_extraction(case_id, evidence_id, **document)
            result["document"] = {key: value for key, value in document.items() if key != "text"}
            result["extraction"] = record
        return result

    def reextract(self, case_id, evidence_id, document_options=None):
        """Create another versioned extraction; never change the original row."""
        from .documents import FORMATS as DOCUMENT_FORMATS, extract as extract_document
        from .workspace import Workspace
        if document_options is not None and (not isinstance(document_options, dict) or set(document_options) - {"ocr", "language"}):
            raise InvalidInput("Document options accept only OCR on/off and installed language codes.")
        with self.connection() as db:
            self.require_case(db, case_id)
            row = db.execute("SELECT raw,format FROM evidence WHERE case_id=? AND id=?", (case_id, evidence_id)).fetchone()
        if not row or row["format"] not in DOCUMENT_FORMATS:
            raise InvalidInput("Choose a saved PDF, office document or image from this case.")
        document = extract_document(row["raw"], row["format"], **(document_options or {}))
        return {"id": evidence_id, "document": {key: value for key, value in document.items() if key != "text"}, "extraction": Workspace(self).record_extraction(case_id, evidence_id, **document)}

    def view(self, case_id, query="", kind=""):
        query = field(query, "Search", 200, required=False)
        if kind and kind not in KINDS:
            raise InvalidInput("Unknown indicator type.")
        # Literal substring matching, including percent and underscore characters.
        with self.connection() as db:
            case = self.require_case(db, case_id)
            entities = [dict(x) for x in db.execute("""SELECT e.*, COUNT(DISTINCT m.evidence_id) AS sources,
                COUNT(*) AS mentions FROM entities e JOIN mentions m ON e.id=m.entity_id
                WHERE e.case_id=? AND (?='' OR e.kind=?) AND (?='' OR instr(lower(e.value),lower(?))>0)
                GROUP BY e.id ORDER BY sources DESC,e.kind,e.value LIMIT 500""", (case_id, kind, kind, query, query))]
            evidence = [dict(x) for x in db.execute("""SELECT id,title,source,format,collected_at,sha256,length(raw) AS bytes
                FROM evidence e WHERE case_id=? AND (?='' OR instr(lower(text),lower(?))>0
                OR instr(lower(title),lower(?))>0 OR instr(lower(source),lower(?))>0
                OR EXISTS(SELECT 1 FROM workspace_extractions x WHERE x.evidence_id=e.id AND x.case_id=e.case_id AND instr(lower(x.text),lower(?))>0))
                ORDER BY collected_at DESC LIMIT 200""", (case_id, query, query, query, query, query))]
            counts = dict(db.execute("""SELECT (SELECT count(*) FROM evidence WHERE case_id=?) AS evidence,
                (SELECT count(*) FROM entities WHERE case_id=?) AS entities,
                (SELECT count(*) FROM mentions m JOIN evidence e ON e.id=m.evidence_id WHERE e.case_id=?) AS mentions""", (case_id, case_id, case_id)).fetchone())
        return {"case": case, "entities": entities, "evidence": evidence, "counts": counts, "limits": {"entities": 500, "evidence": 200}}

    def evidence(self, case_id, evidence_id):
        with self.connection() as db:
            self.require_case(db, case_id)
            row = db.execute("SELECT id,title,source,format,collected_at,sha256,text FROM evidence WHERE id=? AND case_id=?", (evidence_id, case_id)).fetchone()
            if not row:
                raise InvalidInput("Evidence not found in this case.")
            result = dict(row)
            result["mentions"] = [dict(x) for x in db.execute("""SELECT e.kind,e.value,m.start,m.end FROM mentions m
                JOIN entities e ON e.id=m.entity_id WHERE m.evidence_id=? ORDER BY m.start""", (evidence_id,))]
            extraction = db.execute("""SELECT x.id,x.extractor_version,x.text,x.text_sha256,x.evidence_sha256,p.pages,p.status,p.warnings
                    FROM workspace_extractions x JOIN workspace_document_pages p ON p.extraction_id=x.id
                    WHERE x.case_id=? AND x.evidence_id=? ORDER BY x.created_at DESC,x.id DESC LIMIT 1""", (case_id, evidence_id)).fetchone()
            if extraction:
                result["extraction"] = {**dict(extraction), "pages": json.loads(extraction["pages"]), "warnings": json.loads(extraction["warnings"])}
                if result["format"] in {"pdf", "docx", "odt", "png", "jpeg", "tiff"}:
                    result["document"] = result["extraction"]
            return result

    def entity(self, case_id, entity_id):
        with self.connection() as db:
            self.require_case(db, case_id)
            row = db.execute("SELECT * FROM entities WHERE id=? AND case_id=?", (entity_id, case_id)).fetchone()
            if not row:
                raise InvalidInput("Indicator not found in this case.")
            result = dict(row)
            result["evidence"] = [dict(x) for x in db.execute("""SELECT DISTINCT e.id,e.title,e.source,e.collected_at
                FROM mentions m JOIN evidence e ON e.id=m.evidence_id WHERE m.entity_id=? ORDER BY e.collected_at""", (entity_id,))]
            result["related"] = [dict(x) for x in db.execute("""SELECT e.id,e.kind,e.value,COUNT(DISTINCT other.evidence_id) AS shared_sources
                FROM (SELECT DISTINCT evidence_id FROM mentions WHERE entity_id=?) own
                JOIN mentions other ON own.evidence_id=other.evidence_id
                JOIN entities e ON other.entity_id=e.id WHERE other.entity_id!=?
                GROUP BY e.id ORDER BY shared_sources DESC,e.value LIMIT 100""", (entity_id, entity_id))]
        return result

    def export(self, case_id):
        from .workspace import Workspace
        with self.connection() as db:
            # One read transaction keeps report tables on the same snapshot.
            db.execute("BEGIN")
            case = self.require_case(db, case_id)
            evidence = []
            for row in db.execute("SELECT * FROM evidence WHERE case_id=? ORDER BY collected_at", (case_id,)):
                item = dict(row)
                item["original_base64"] = base64.b64encode(item.pop("raw")).decode("ascii")
                evidence.append(item)
            entities = [dict(x) for x in db.execute("SELECT * FROM entities WHERE case_id=? ORDER BY kind,value", (case_id,))]
            mentions = [dict(x) for x in db.execute("SELECT m.* FROM mentions m JOIN evidence e ON m.evidence_id=e.id WHERE e.case_id=? ORDER BY m.evidence_id,m.start", (case_id,))]
            workspace = Workspace(self).snapshot(case_id, connection=db)
            extractions = []
            for row in db.execute("""SELECT x.*,p.pages,p.status,p.warnings FROM workspace_extractions x
                    LEFT JOIN workspace_document_pages p ON p.extraction_id=x.id
                    WHERE x.case_id=? ORDER BY x.created_at,x.id""", (case_id,)):
                item = dict(row)
                item['pages'] = json.loads(item['pages']) if item['pages'] is not None else []
                item['warnings'] = json.loads(item['warnings']) if item['warnings'] is not None else []
                item['status'] = item['status'] or 'ready'
                extractions.append(item)
        reports = [json.loads(item['text']) for item in evidence if item['source'] == 'local-osint:investigation-report']
        return {"schema_version": 3, "exported_at": now(), "case": case, "evidence": evidence, "entities": entities, "mentions": mentions,
                "workspace": workspace, "extractions": extractions,
                "investigation": reports[-1] if reports else None,
                "interpretation": "Indicators are extracted mentions. Shared sources do not establish ownership or identity."}

    def latest_investigation(self, case_id):
        with self.connection() as db:
            self.require_case(db, case_id)
            row = db.execute("SELECT id,raw FROM evidence WHERE case_id=? AND source='local-osint:investigation-report' ORDER BY collected_at DESC LIMIT 1", (case_id,)).fetchone()
            if not row:
                return None
            try:
                result = json.loads(row["raw"])
                if not isinstance(result, dict) or result.get("report_type") != "investigation" or result.get("case_id") != case_id:
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise InvalidInput("Saved investigation report could not be read.") from exc
            return {**result, "report_id": row["id"]}

    def markdown(self, case_id):
        data = self.export(case_id)
        # Escape user content so a Markdown viewer cannot execute raw HTML or create links/images.
        def escape(value):
            return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")).replace("\n", " ")
        lines = ["# " + escape(data["case"]["name"]), "", "Focus: " + data["case"]["focus_type"], "", "Aliases: " + escape(", ".join(data["case"]["aliases"])), "", "Known identifiers (user supplied): " + escape(", ".join(data["case"]["identifiers"])), "", data["interpretation"], ""]
        report = data['investigation']
        if report:
            lines.extend(['## Investigation dossier', '', 'Status: ' + escape(report['status']), '', escape(report['interpretation']), ''])
            if report.get('stop_reason'):
                lines.extend(['Why it stopped: ' + escape(report['stop_reason']), ''])
            if report.get('budget'):
                budget = report['budget']
                lines.extend(['Request usage: ' + str(budget['used']) + ' of ' + str(budget['limit']) + ' request units. The ceiling is a limit, not a completion target.', ''])
            if 'quality_count' in report:
                lines.extend(['Useful candidate pages: ' + str(report['quality_count']) + ' of goal ' + str(report['options']['page_limit']) + '. Login walls, empty shells and unlikely namesakes do not fill this goal.', ''])
            if report.get('web_results'):
                lines.extend(['### Public web search leads - identity unconfirmed', ''])
                for result in report['web_results']:
                    lines.extend(['- ' + escape(result['title']), '  URL: ' + escape(result['url'])])
                    if result.get('snippet'):
                        lines.append('  Search snippet: ' + escape(result['snippet']))
                    if result.get('reason'):
                        lines.append('  Follow-up decision: ' + escape(result['reason']))
                    if result.get('local_match'):
                        match = result['local_match']
                        lines.append('  Local case comparison: ' + escape(match['summary']))
                        if match.get('model_assessment'):
                            lines.append('  Unverified local AI context hint: ' + escape(match['model_assessment']))
                    if result.get('source_id'):
                        lines.append('  Search evidence ID: ' + escape(result['source_id']))
                    if result.get('page_source_id'):
                        lines.append('  Collected page evidence ID: ' + escape(result['page_source_id']))
                    lines.append('')
            if report.get('pages'):
                lines.extend(['### Collected public pages', ''])
                for page in report['pages']:
                    lines.extend(['- Requested URL: ' + escape(page['url']),
                                  '  Final URL: ' + escape(page.get('final_url', page['url'])),
                                  '  Evidence ID: ' + escape(page['source_id'])])
                    if page.get('excerpt'):
                        lines.append('  Source excerpt: ' + escape(page['excerpt']))
                    if 'useful' in page:
                        lines.append('  Useful candidate content: ' + ('yes' if page['useful'] else 'no'))
                        lines.append('  Quality decision: ' + escape(page.get('quality_reason', '')))
                    for link in page.get('links', []):
                        lines.append('  Discovered follow-up: ' + escape(link.get('title', '')) + '; URL: ' + escape(link['url']))
                    lines.append('')
            for profile in report['profiles']:
                lines.extend(['### Unconfirmed candidate: ' + escape(profile['label']), '', escape(profile['description']), '', 'Candidate ID: ' + escape(profile['candidate_id']), '', 'Evidence ID: ' + escape(profile['source_id']), ''])
                for claim in profile['claims']:
                    context = '; '.join(q['label'] + ': ' + q['value'] for q in claim.get('qualifiers', []))
                    lines.append('- ' + escape(claim['label']) + ': ' + escape(claim['value']) + ' (' + escape(claim.get('rank', 'normal')) + ' statement' + ('; ' + escape(context) if context else '') + ') [evidence ' + escape(claim['source_id']) + ']')
                    if claim.get('qualifiers_truncated'):
                        lines.append('  More qualifiers in original source')
                    if claim.get('label_source_id'):
                        lines.append('  Label evidence: ' + escape(claim['label_source_id']))
                lines.append('')
            if report['articles']:
                lines.extend(['### Recent news mentions', ''])
                for article in report['articles']:
                    lines.extend(['- ' + escape(article['title']), '  URL: ' + escape(article['url']), '  Evidence ID: ' + escape(article.get('page_source_id', article['source_id'])), ''])
            if report.get('ai'):
                lines.extend(['### Local AI draft - unverified', '', escape(report['ai'].get('summary') or report['ai'].get('error', 'No draft available.')), ''])
                if report['ai'].get('status') == 'truncated':
                    lines.extend(['Incomplete: AI draft reached its output limit.', ''])
            lines.extend(['### Collection log', ''])
            for step in report['steps']:
                status = step.get('status') or ('success' if step['ok'] else 'failed')
                lines.append('- ' + escape(step['label']) + '; status: ' + escape(status))
                if step.get('tool'):
                    lines.append('  Tool: ' + escape(step['tool']))
                origin = {'ai': 'Local AI', 'user': 'You', 'engine': 'Automatic investigation'}.get(step.get('origin'), step.get('origin'))
                if origin:
                    lines.append('  Requested by: ' + escape(origin))
                for key, value in step.get('arguments', {}).items():
                    rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                    lines.append('  Input ' + escape(key) + ': ' + escape(rendered))
                if step.get('result_summary'):
                    lines.append('  Result: ' + escape(step['result_summary']))
                if step.get('error'):
                    lines.append('  Error: ' + escape(step['error']))
                if step.get('source_id'):
                    lines.append('  Evidence ID: ' + escape(step['source_id']))
                if step.get('started_at'):
                    lines.append('  Started: ' + escape(step['started_at']))
                if step.get('duration_ms') is not None:
                    lines.append('  Duration: ' + str(step['duration_ms']) + ' ms')
            lines.append('')
        lines.extend(['## Indicators', ''])
        for entity in data["entities"]:
            lines.append(f"- {entity['kind']}: {escape(entity['value'])}")
        lines.extend(["", "## Evidence", ""])
        for item in data["evidence"]:
            lines.extend(["### " + escape(item["title"]), "", "Source: " + escape(item["source"]), "", "Collected: " + item["collected_at"], "", "SHA-256: " + item["sha256"], "", "Evidence ID: " + item["id"], ""])
        return "\n".join(lines)

    def backup(self, destination):
        destination = Path(destination)
        # Exclusive creation prevents overwrite and symlink traversal at the final component.
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.connection() as db:
            target = sqlite3.connect(destination)
            try:
                db.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise InvalidInput("Backup integrity check failed.")
            finally:
                target.close()
        return destination
