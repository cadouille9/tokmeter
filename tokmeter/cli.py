from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from contextlib import contextmanager

from rich.console import Console

from . import config, db, pricing, report

console = Console()


def _build_servers(settings: config.Settings, writer) -> list:
    import uvicorn

    from .proxy import create_app

    class _Server(uvicorn.Server):
        # Signals are handled centrally in _serve_until_signal. Uvicorn's own
        # capture uses signal.signal per server, so with several servers only
        # the last-registered one would ever see SIGINT/SIGTERM.
        @contextmanager
        def capture_signals(self):
            yield

    servers = []
    for listener in settings.listeners:
        app = create_app(upstream=listener.upstream, writer=writer)
        cfg = uvicorn.Config(
            app, host=settings.listen_host, port=listener.port, log_level="warning"
        )
        servers.append(_Server(cfg))
    return servers


async def _serve_until_signal(servers) -> None:
    loop = asyncio.get_running_loop()

    def request_exit() -> None:
        for server in servers:
            server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_exit)
    try:
        await asyncio.gather(*(server.serve() for server in servers))
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def _cmd_serve(args) -> int:
    from .writer import UsageWriter

    settings = config.load_settings()
    writer = UsageWriter(config.db_path())
    writer.start()
    servers = _build_servers(settings, writer)
    for listener in settings.listeners:
        console.print(
            f"[bold green]tokmeter[/] proxying "
            f"http://{settings.listen_host}:{listener.port} -> {listener.upstream}"
        )
    try:
        asyncio.run(_serve_until_signal(servers))
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
        key, title = "model", "Usage by Model"

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
    elec, warnings = pricing.electricity_config(pricing_data)
    for warning in warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    if elec is not None:
        energy_rows = db.rows_for_energy(conn, since=args.since, until=args.until, model=args.model)
        summary, _ = report.energy_summary(pricing_data, energy_rows)
        for line in report.energy_lines(summary, gross_saved_usd=t["saved_usd"]):
            console.print(line)
    return 0


def _cmd_models(args) -> int:
    conn = db.connect(config.db_path())
    db.init_db(conn)
    pricing_data = pricing.load_pricing(config.pricing_path())
    for model in sorted(db.distinct_models(conn)):
        rate = pricing.resolve_rate(pricing_data, model)
        state = "priced" if rate.mapped else "default"
        # markup=False so the bracketed state isn't parsed as Rich style markup.
        console.print(f"{model}  [{state}]", markup=False)
    return 0


def _cmd_compare(args) -> int:
    conn = db.connect(config.db_path())
    db.init_db(conn)
    pricing_path = config.pricing_path()
    pricing_data = pricing.load_pricing(pricing_path)
    for warning in pricing.reference_warnings(pricing_data):
        console.print(f"[yellow]warning:[/] {warning}")
    references = pricing.reference_rates(pricing_data)
    if not references:
        console.print(
            "No reference models configured. Add a 'references:' section to "
            f"{pricing_path} (see config/pricing.yaml for an example)."
        )
        return 0

    per_model = db.aggregate_by_model(
        conn, since=args.since, until=args.until, model=args.model
    )

    if args.by_model:
        rows = report.build_comparison_matrix(per_model, references)
        names = [name for name, _ in references]
        console.print(report.render_matrix_table(rows, names))
        return 0

    prompt_tokens = sum(r.get("prompt_tokens", 0) for r in per_model)
    completion_tokens = sum(r.get("completion_tokens", 0) for r in per_model)
    console.print(
        f"[bold]Recorded usage:[/] {prompt_tokens:,} prompt + "
        f"{completion_tokens:,} completion tokens"
    )
    rows = report.build_comparison(prompt_tokens, completion_tokens, references)
    console.print(report.render_comparison_table(rows))
    elec, warnings = pricing.electricity_config(pricing_data)
    for warning in warnings:
        console.print(f"[yellow]warning:[/] {warning}")
    if elec is not None:
        energy_rows = db.rows_for_energy(conn, since=args.since, until=args.until, model=args.model)
        summary, _ = report.energy_summary(pricing_data, energy_rows)
        for line in report.energy_lines(summary, gross_saved_usd=None):
            console.print(line)
        if summary["cost_usd"] is not None:
            console.print(
                "[dim]Subtract the electricity ≈$ figure from any reference above for net savings.[/]"
            )
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

    cp = sub.add_parser("compare", help="Compare cost vs reference cloud models")
    cp.add_argument("--since", help="YYYY-MM-DD (inclusive)")
    cp.add_argument("--until", help="YYYY-MM-DD (inclusive)")
    cp.add_argument("--model", help="Filter to one local model")
    cp.add_argument(
        "--by-model", action="store_true", help="Matrix: local models x references"
    )
    cp.set_defaults(func=_cmd_compare)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)
