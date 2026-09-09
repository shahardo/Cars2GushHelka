"""CLI: orchestrates the resolve and aggregate stages.

    python -m cars2gushhelka --input FILE --resolver {govmap,spatial,crosswalk} ...

See README.md for the full flag reference and backend trade-offs.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import fuel, report
from .aggregate import Aggregator
from .cache import AddressCache
from .matcher import resolve_row, row_identity_key, RowResolution
from .reader import iter_rows


def _on_parse_error(line_no: int, raw_line: str, exc: Exception) -> None:
    print(f"WARNING: skipping malformed line {line_no}: {exc}", file=sys.stderr)


def build_resolver(args: argparse.Namespace):
    if args.resolver == "crosswalk":
        from .resolvers.crosswalk import CrosswalkResolver
        if not args.crosswalk:
            raise SystemExit("--resolver crosswalk requires --crosswalk PATH")
        return CrosswalkResolver.from_csv(args.crosswalk)

    if args.resolver == "govmap":
        from .resolvers.govmap import GovMapResolver
        return GovMapResolver(rate_limit_seconds=1.0 / args.rate if args.rate else 0.2)

    if args.resolver == "spatial":
        from .resolvers.spatial import ParcelIndex, SpatialResolver
        if not args.parcels:
            raise SystemExit("--resolver spatial requires --parcels PATH")
        raise SystemExit(
            "--resolver spatial also requires a Geocoder implementation, which "
            "must be supplied programmatically (see resolvers/spatial.py and "
            "README.md) -- there is no built-in offline Israeli geocoder."
        )

    raise SystemExit(f"unknown resolver: {args.resolver!r}")


def run_resolve(input_path: str, resolver, cache: AddressCache, *, use_secondary_address: bool = True, limit: Optional[int] = None) -> int:
    resolved_count = 0
    seen = 0
    for row in iter_rows(input_path, on_error=_on_parse_error):
        seen += 1
        key = row_identity_key(row, use_secondary_address)
        if cache.get_row(key) is not cache.MISS:
            continue
        resolution = resolve_row(row, resolver, cache=cache, use_secondary_address=use_secondary_address)
        cache.set_row(key, resolution)
        resolved_count += 1
        if limit is not None and resolved_count >= limit:
            break
    cache.flush()
    print(f"Resolved {resolved_count} new distinct addresses ({seen} rows scanned).", file=sys.stderr)
    return resolved_count


def run_aggregate(
    input_path: str,
    cache: AddressCache,
    *,
    use_secondary_address: bool = True,
    fuel_overrides: Optional[dict] = None,
) -> Aggregator:
    aggregator = Aggregator()
    unresolved_in_cache = 0
    for row in iter_rows(input_path, on_error=_on_parse_error):
        key = row_identity_key(row, use_secondary_address)
        resolution = cache.get_row(key)
        if resolution is cache.MISS:
            unresolved_in_cache += 1
            resolution = RowResolution(
                gush="FAILED", helka="UNRESOLVED_NOT_YET_RUN", sub_helka=None,
                match_method="failed", confidence=0.0, is_aggregated=True,
                used_secondary_address=False,
                settlement_code=row.semel_yishuv, settlement_name=row.shem_yishuv,
            )
        category = fuel.categorize(
            row.sug_delek, row.sug_delek_nm,
            overrides=fuel_overrides, unmapped_tracker=aggregator.unmapped_fuel_codes,
        )
        aggregator.add_row(resolution, category)

    if unresolved_in_cache:
        print(
            f"WARNING: {unresolved_in_cache} rows had no cached resolution "
            f"(run --resolve-only first, or without --aggregate-only).",
            file=sys.stderr,
        )
    return aggregator


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cars2gushhelka", description=__doc__)
    p.add_argument("--input", required=True, help="Ministry of Energy pipe-delimited vehicle file")
    p.add_argument("--resolver", choices=["govmap", "spatial", "crosswalk"], default="crosswalk")
    p.add_argument("--cache", default="cache.sqlite", help="SQLite cache path (resumable)")
    p.add_argument("--output", default="gush_helka_by_engine.csv")
    p.add_argument("--unresolved-output", default="unresolved_report.csv")
    p.add_argument("--crosswalk", help="CSV crosswalk path (--resolver crosswalk)")
    p.add_argument("--parcels", help="GeoJSON parcels layer path (--resolver spatial)")
    p.add_argument("--fuel-map", help="JSON override for fuel code/name -> category")
    p.add_argument(
        "--no-secondary-address", dest="use_secondary_address", action="store_false",
        help="Disable falling back to the *_nosaf address when the primary address fails to resolve "
             "(the fallback is used by default)",
    )
    p.set_defaults(use_secondary_address=True)
    p.add_argument("--resolve-only", action="store_true")
    p.add_argument("--aggregate-only", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="Stop after resolving N new distinct addresses")
    p.add_argument("--rate", type=float, default=5.0, help="Max resolver calls/sec (govmap)")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.resolve_only and args.aggregate_only:
        raise SystemExit("--resolve-only and --aggregate-only are mutually exclusive")

    fuel_overrides = fuel.load_fuel_map(args.fuel_map) if args.fuel_map else None

    do_resolve = not args.aggregate_only
    do_aggregate = not args.resolve_only

    with AddressCache(args.cache) as cache:
        if do_resolve:
            resolver = build_resolver(args)
            run_resolve(args.input, resolver, cache, use_secondary_address=args.use_secondary_address, limit=args.limit)

        if do_aggregate:
            aggregator = run_aggregate(
                args.input, cache,
                use_secondary_address=args.use_secondary_address,
                fuel_overrides=fuel_overrides,
            )
            report.write_report(aggregator, args.output)
            failed = report.write_unresolved_report(aggregator, args.unresolved_output)
            report.print_summary(aggregator, use_secondary_address=args.use_secondary_address)
            print(f"Wrote {args.output}", file=sys.stderr)
            print(f"Wrote {args.unresolved_output} ({failed} unresolved vehicles)", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
