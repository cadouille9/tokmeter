from __future__ import annotations

import argparse
import sys

from rich.console import Console

from . import config, db, pricing, report

console = Console()


def _cmd_serve(args) -> int:
    import uvicorn

    from .proxy import create_app
    from .writer import UsageWriter

    settings = config.load_settings()
    writer = UsageWriter(config.db_path())
    writer.start()
    app = create_app(upstream=settings.upstream, writer=writer)
    console.print(
        f"[bold green]tokmeter[/] proxying "
        f"http://{settings.listen_host}:{settings.listen_port} -> {settings.upstream}"
    )
    try:
        uvicorn.run(app, host=settings.listen_host, port=settings.listen_port, log_level="warning")
    finally:
        writer.stop()
    return 0


def _cmd_report(args) -> int:
    conn = db.connect(config.db_path())
    db.init_db(conn)
    pricing_data = pricing.load_pricing(config.pricing_path())

    if args.by == "day":
        agg = db.aggregate_by_day(conn, since=args.since, until=args.until, model=args.model)
        key, title = "day", "Usage by Day"
    else:
        agg = db.aggregate_by_model(conn, since=args.since, until=args.until, model=args.model)
        key, title = "model", "Usage by Model (* = default pricing)"

    rows = report.build_rows(agg, pricing_data, key=key)

    if args.csv:
        report.write_csv(rows, args.csv)
        console.print(f"Wrote {len(rows)} rows to {args.csv}")
        return 0

    console.print(report.render_table(rows, key=key, title=title))
    t = report.totals(rows)
    console.print(
        f"[bold]Totals:[/] {t['requests']} requests, "
        f"{t['total_tokens']:,} tokens, ~${t['saved_usd']:.2f} saved"
    )
    return 0


def _cmd_models(args) -> int:
    conn = db.connect(config.db_path())
    db.init_db(conn)
    pricing_data = pricing.load_pricing(config.pricing_path())
    for model in sorted(db.distinct_models(conn)):
        rate = pricing.resolve_rate(pricing_data, model)
        state = "priced" if rate.mapped else "default"
        console.print(f"{model}  [{state}]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokmeter", description="Local LLM usage tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="Run the logging proxy").set_defaults(func=_cmd_serve)

    rp = sub.add_parser("report", help="Print usage report")
    rp.add_argument("--by", choices=["model", "day"], default="model")
    rp.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    rp.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    rp.add_argument("--model", help="Filter to one model")
    rp.add_argument("--csv", help="Write rows to this CSV path instead of printing")
    rp.set_defaults(func=_cmd_report)

    sub.add_parser("models", help="List models seen and pricing state").set_defaults(func=_cmd_models)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)
