"""Browser launcher and scriptable evidence workflow."""

import argparse
import errno
import json
import os
from pathlib import Path
import sqlite3
import sys
import tomllib
import webbrowser

from .collector import collect, research, discover
from .engine import Engine, InvalidInput, MAX_BYTES
from .server import LocalServer


def write_private(path, value):
    raw = value.encode("utf-8")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def parser():
    root = argparse.ArgumentParser(prog="osint", description="Private local evidence workspace. No network access unless explicitly enabled.")
    commands = root.add_subparsers(dest="command", required=True)
    for name, help in [("serve", "Open the local browser workspace"), ("case", "Create a case"), ("cases", "List cases"), ("import", "Import UTF-8 evidence"), ("search", "Search a case"), ("entity", "Inspect an indicator and shared sources"), ("export", "Export a case to a new private file"), ("discover", "Discover public-source candidates for a person, organization or topic"), ("research", "Collect public DNS, registration, TLS and website signals for a domain"), ("fetch", "Collect one public HTTPS page"), ("backup", "Create an integrity-checked database backup")]:
        sub = commands.add_parser(name, help=help)
        sub.add_argument("--data-dir", help="Private data directory (default: .local-osint)")
        sub.add_argument("--config", type=Path, help="Optional TOML file with data_dir and port")
        if name == "serve":
            sub.add_argument("--port", type=int, help="Local port (default: automatic; 0 chooses a free port)")
            sub.add_argument("--allow-network", action="store_true", help="Permit explicitly requested public signal collection")
            sub.add_argument("--offline", dest="allow_network", action="store_false", help="Disable every collection operation")
            sub.add_argument("--open", action="store_true", help="Open browser automatically")
        elif name == "case":
            sub.add_argument("name")
            sub.add_argument("--type", choices=["person", "organization", "domain", "topic", "other"], default="topic")
            sub.add_argument("--alias", action="append", default=[])
            sub.add_argument("--identifier", action="append", default=[])
        elif name == "import":
            sub.add_argument("case_id")
            sub.add_argument("file", type=Path)
            sub.add_argument("--title")
            sub.add_argument("--source", help="Evidence source label; defaults to basename")
            sub.add_argument("--format", choices=["text", "html", "json", "csv"], help="Override format inferred from extension")
        elif name in {"search", "entity", "export", "fetch", "research", "discover"}:
            sub.add_argument("case_id")
            if name == "search":
                sub.add_argument("query", nargs="?", default="")
                sub.add_argument("--kind", choices=["email", "domain", "url", "ipv4", "ipv6"], default="")
            elif name == "entity":
                sub.add_argument("entity_id")
            elif name == "export":
                sub.add_argument("file", type=Path)
                sub.add_argument("--format", choices=["json", "markdown"], default="json")
            elif name == "discover":
                sub.add_argument("--query", help="Optional query, such as a known alias; defaults to subject name")
                sub.add_argument("--sources", nargs="+", choices=["wikipedia", "wikidata"], default=["wikipedia", "wikidata"])
                sub.add_argument("--allow-network", action="store_true", required=True)
            elif name == "research":
                sub.add_argument("domain")
                sub.add_argument("--signals", nargs="+", choices=["dns", "registration", "certificate", "website"], default=["dns", "registration", "certificate", "website"])
                sub.add_argument("--allow-network", action="store_true", required=True)
            else:
                sub.add_argument("url")
                sub.add_argument("--allow-network", action="store_true", required=True)
        elif name == "backup":
            sub.add_argument("file", type=Path)
    return root


def settings(args):
    config = {}
    if args.config:
        try:
            with args.config.open("rb") as stream:
                config = tomllib.load(stream)
        except (OSError, ValueError) as exc:
            raise InvalidInput("Could not read configuration. Use valid TOML with data_dir and port.") from exc
        if set(config) - {"data_dir", "port"}:
            raise InvalidInput("Unknown configuration key. Supported keys: data_dir, port.")
        if "data_dir" in config:
            if not isinstance(config["data_dir"], str) or not config["data_dir"]:
                raise InvalidInput("Configuration data_dir must be non-empty text.")
            config["data_dir"] = str(args.config.absolute().parent / Path(config["data_dir"]).expanduser())
    directory = args.data_dir or config.get("data_dir", ".local-osint")
    port = getattr(args, "port", None)
    port = config.get("port", 0) if port is None else port
    if type(port) is not int or not 0 <= port <= 65535:
        raise InvalidInput("Port must be an integer from 0 to 65535.")
    return directory, port


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        directory, port = settings(args)
        engine = Engine(directory)
        if args.command == "serve":
            with LocalServer(engine, port, args.allow_network) as server:
                link = server.origin + "/#token=" + server.token
                print("Local OSINT ready. " + ("Public HTTPS collection enabled." if args.allow_network else "Offline mode; web collection disabled."), flush=True)
                print("Private launch link (keep it on this machine): " + link, flush=True)
                print("Stop with Ctrl+C. Evidence remains in your private data directory.", flush=True)
                if args.open:
                    webbrowser.open(link)
                try:
                    server.serve_forever(poll_interval=0.2)
                except KeyboardInterrupt:
                    pass
            return 0
        if args.command == "case":
            result = engine.create_case(args.name, args.type, args.alias, args.identifier)
        elif args.command == "cases":
            result = engine.cases()
        elif args.command == "import":
            with args.file.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            format = args.format or {".html": "html", ".htm": "html", ".json": "json", ".csv": "csv", ".txt": "text", ".md": "text"}.get(args.file.suffix.lower(), "text")
            result = engine.ingest(args.case_id, raw, args.title or args.file.name, args.source or args.file.name, format)
        elif args.command == "search":
            result = engine.view(args.case_id, args.query, args.kind)
        elif args.command == "entity":
            result = engine.entity(args.case_id, args.entity_id)
        elif args.command == "discover":
            result = discover(engine, args.case_id, args.sources, args.allow_network, args.query)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0 if all(item["ok"] for item in result["results"]) else 1
        elif args.command == "research":
            result = research(engine, args.case_id, args.domain, args.signals, args.allow_network)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0 if all(item["ok"] for item in result["results"]) else 1
        elif args.command == "fetch":
            result = collect(engine, args.case_id, args.url, args.allow_network)
        elif args.command == "backup":
            result = {"backup": str(engine.backup(args.file))}
        else:
            report = engine.markdown(args.case_id) if args.format == "markdown" else json.dumps(engine.export(args.case_id), indent=2, ensure_ascii=True)
            write_private(args.file, report)
            result = {"export": str(args.file), "format": args.format}
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    except (InvalidInput, OSError, sqlite3.Error) as exc:
        # Filesystem errors can include private paths; use a fixed message for those.
        if isinstance(exc, OSError) and exc.errno == errno.EADDRINUSE and args.command == "serve":
            message = f"Local port {port} is already in use. Run with --port 0 to choose a free port."
        else:
            message = str(exc) if isinstance(exc, InvalidInput) else "Local operation failed. Check permissions, disk space, file existence and port availability. Existing files are never overwritten."
        print("Error: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
