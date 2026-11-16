"""JSON-in/JSON-out CLI for local agents; no model or messenger calls. Catalog sync fetches only approved Git data."""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from .catalog import Catalog, canonical, validate_record
from .composition import compose
from .nutrients import load_registry
from .assessment import assess
from .catalog_git import check_directory, sync_catalog
from .journal import Journal
from .reporting import build, markdown
from .statistics import build as build_statistics, load_profile, render_html, render_public_svg, render_public_daily_svg


def payload():
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", help="Explicit local private SQLite path; never in code Git")
    parser.add_argument("--catalog", help="Explicit public nutrition directory; never in code Git")
    parser.add_argument("--nutrient-definitions", help="Explicit nutrient metadata JSON, never executable plugins")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("nutrients")
    assessment = sub.add_parser("assess")
    assessment.add_argument("--event-id", required=True)
    sub.add_parser("init")
    sub.add_parser("record")
    show = sub.add_parser("show")
    show.add_argument("--event-id", required=True)
    show.add_argument("--history", action="store_true")
    listing = sub.add_parser("list")
    listing.add_argument("--start", required=True)
    listing.add_argument("--end", required=True)
    revise = sub.add_parser("revise")
    revise.add_argument("--event-id", required=True)
    revise.add_argument("--expected-revision", required=True, type=int)
    revise.add_argument("--revision-key", required=True)
    sub.add_parser("catalog-put")
    sub.add_parser("catalog-compose", help="Compose references and amounts without catalog writes")
    sub.add_parser("catalog-validate", help="Validate stdin record and compute ref without any catalog write")
    check = sub.add_parser("catalog-check")
    check.add_argument("--directory", required=True)
    sync = sub.add_parser("catalog-sync")
    sync.add_argument("--source-binding", required=True)
    get = sub.add_parser("catalog-get")
    get.add_argument("--ref", required=True)
    find = sub.add_parser("catalog-find")
    find.add_argument("--query", required=True)
    report = sub.add_parser("report")
    report.add_argument("--start", required=True)
    report.add_argument("--end", required=True)
    report.add_argument("--format", choices=["json", "markdown"], default="json")
    report.add_argument("--nutrients", help="Comma-separated extra nutrient IDs; core four are always included")
    stats = sub.add_parser("stats")
    stats.add_argument("--start", required=True)
    stats.add_argument("--end", required=True)
    stats.add_argument("--mode", choices=["private", "public"], default="private")
    stats.add_argument("--svg-view", choices=["summary", "daily"], default="summary")
    stats.add_argument("--format", choices=["json", "html", "svg"], default="html")
    stats.add_argument("--energy-profile", help="Explicit local profile file, or - for stdin")
    stats.add_argument("--nutrients", help="Comma-separated extra nutrient IDs; private output only")
    claim = sub.add_parser("delivery-claim")
    claim.add_argument("--report-hash", required=True)
    claim.add_argument("--recipient-key", required=True)
    finish = sub.add_parser("delivery-finish")
    finish.add_argument("--delivery-id", required=True)
    finish.add_argument("--status", choices=["sent", "failed", "unknown"], required=True)
    finish.add_argument("--message-id")
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.nutrient_definitions)
        if args.command == "nutrients":
            result = registry.document()
        elif args.command == "catalog-compose":
            record = compose(payload(), registry)
            result = {"valid": True, "nutrition_ref": hashlib.sha256(canonical(record).encode()).hexdigest(),
                      "record": record, "stored": False}
        elif args.command == "catalog-validate":
            record = validate_record(payload(), registry)
            result = {"valid": True, "nutrition_ref": hashlib.sha256(canonical(record).encode()).hexdigest(),
                      "record": record, "stored": False}
        elif args.command == "catalog-check":
            result = check_directory(args.directory, registry)
        elif args.command.startswith("catalog-"):
            if not args.catalog:
                raise ValueError("catalog_path_required")
            catalog = Catalog(args.catalog, registry)
            if args.command == "catalog-put":
                result = catalog.put(payload())
            elif args.command == "catalog-sync":
                result = sync_catalog(catalog, args.source_binding)
            elif args.command == "catalog-get":
                result = catalog.get(args.ref)
            else:
                result = catalog.find(args.query)
        else:
            if not args.journal:
                raise ValueError("journal_path_required")
            journal = Journal(args.journal)
            if args.command == "init":
                result = journal.init()
            elif args.command == "record":
                result = journal.record(payload())
            elif args.command == "assess":
                if not args.catalog:
                    raise ValueError("catalog_path_required")
                result = assess(journal.show(args.event_id), Catalog(args.catalog, registry))
            elif args.command == "show":
                result = journal.show(args.event_id, args.history)
            elif args.command == "list":
                result = journal.list_period(args.start, args.end)
            elif args.command == "revise":
                result = journal.revise(args.event_id, args.expected_revision, args.revision_key, payload())
            elif args.command in {"report", "stats"}:
                if not args.catalog:
                    raise ValueError("catalog_path_required")
                result = build(journal, Catalog(args.catalog, registry), args.start, args.end, include_statistics=args.command == "stats",
                               nutrients=args.nutrients.split(",") if args.nutrients else None)
                if args.command == "stats":
                    if args.format == "svg" and args.mode != "public":
                        raise ValueError("public_mode_required_for_svg")
                    result = build_statistics(result, load_profile(args.energy_profile), args.mode)
                    if args.format == "html":
                        print(render_html(result), end="")
                        return 0
                    if args.format == "svg":
                        render_svg = render_public_daily_svg if args.svg_view == "daily" else render_public_svg
                        print(render_svg(result), end="")
                        return 0
                elif args.format == "markdown":
                    print(markdown(result), end="")
                    return 0
            elif args.command == "delivery-claim":
                result = journal.claim_delivery(args.report_hash, args.recipient_key)
            else:
                result = journal.finish_delivery(args.delivery_id, args.status, args.message_id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).replace("_", "").isalnum() else "operation_failed"
        print(json.dumps({"error": code, "private_payload_logged": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
