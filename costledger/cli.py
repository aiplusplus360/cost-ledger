"""``cost-ledger`` command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from typing import List, Optional

from .core import load_prices, load_usage_csv, reconcile, summarise, total_cost

__version__ = "0.1.0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cost-ledger",
        description="Attribute model spend to tenants and reconcile it against an invoice.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="per-tenant and per-model spend")
    report.add_argument("usage", help="CSV with tenant,model,input_tokens,output_tokens,cached_tokens")
    report.add_argument("--prices", required=True, help="JSON price table")
    report.add_argument("--json", action="store_true")
    report.add_argument("--strict", action="store_true",
                        help="exit 2 when any model has no price")

    recon = sub.add_parser("reconcile", help="compare computed spend with an invoice")
    recon.add_argument("usage")
    recon.add_argument("--prices", required=True)
    recon.add_argument("--invoice", required=True, help="invoice total in dollars")
    recon.add_argument("--tolerance", default="0.01", help="absolute tolerance in dollars")
    recon.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = load_usage_csv(args.usage)
        prices = load_prices(args.prices)
    except OSError as exc:
        print("cannot read input: %s" % exc, file=sys.stderr)
        return 2
    except ValueError as exc:
        print("invalid input: %s" % exc, file=sys.stderr)
        return 2

    report = summarise(records, prices)

    if args.command == "report":
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print("calls: %d   total: $%s" % (report["calls"], report["total"]))
            for tenant, amount in report["tenants"].items():
                print("  %-16s $%s" % (tenant, amount))
            if report["unpriced"]:
                print("unpriced models:")
                for model, stats in report["unpriced"].items():
                    print("  %-16s %d call(s), %d token(s)"
                          % (model, stats["calls"], stats["tokens"]))
        if args.strict and not report["complete"]:
            return 2
        return 0

    computed = Decimal(report["total"])
    try:
        invoiced = Decimal(args.invoice)
        tolerance = Decimal(args.tolerance)
    except Exception:
        print("invoice and tolerance must be decimal numbers", file=sys.stderr)
        return 2
    result = reconcile(computed, invoiced, tolerance)
    result["unpriced"] = report["unpriced"]
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print("computed: $%s   invoiced: $%s   difference: $%s   [%s]"
              % (result["computed"], result["invoiced"],
                 result["difference"], result["status"]))
    return 0 if result["status"] == "match" else 1
