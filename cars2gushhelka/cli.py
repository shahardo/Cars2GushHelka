"""CLI: orchestrates the resolve and aggregate stages.

    python -m cars2gushhelka --input FILE --resolver {govmap,spatial,crosswalk} ...

See README.md for the full flag reference and backend trade-offs.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import deque
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


def _rtl_display(text: str) -> str:
    # Terminals print left-to-right without bidi reordering, so Hebrew text
    # comes out reversed-looking; flipping character order here makes it
    # read correctly (RTL) in the trace printout.
    return text[::-1]


# Column layout for the per-row resolve trace (see run_resolve). Each entry is
# (header, width, alignment) and drives both the header/separator lines and
# every data row, so the trace prints as an equi-spaced table -- long values
# are truncated to the column width rather than pushing later columns out of
# alignment.
_TRACE_COLUMNS = [
    ("IDX", 6, ">"),
    ("LINE", 7, ">"),
    ("SETTLEMENT", 15, "<"),
    ("STREET", 15, "<"),
    ("HOUSE", 4, "<"),
    ("MIKUD", 8, "<"),
    ("RESULT", 40, "<"),
    ("FLAGS", 20, "<"),
    ("RATE", 13, ">"),
    ("CACHE HIT % (last 100)", 22, ">"),
]


def _fit(value, width: int, align: str) -> str:
    text = str(value)
    if len(text) > width:
        return text[: width - 1] + "…" if width > 1 else text[:width]
    return f"{text:{align}{width}}"


def _trace_row(values: list) -> str:
    cells = [_fit(value, width, align) for value, (_, width, align) in zip(values, _TRACE_COLUMNS)]
    return " ".join(cells)


def _print_trace_header() -> None:
    print(_trace_row([header for header, _, _ in _TRACE_COLUMNS]), file=sys.stderr)
    print(_trace_row(["-" * width for _, width, _ in _TRACE_COLUMNS]), file=sys.stderr)


def _address_columns(row) -> list:
    return [_rtl_display(row.shem_yishuv), _rtl_display(row.rechov), row.mispar_bait, row.mikud]


def _result_text(resolution: RowResolution) -> str:
    if resolution.match_method == "failed":
        return f"FAILED (bucket={resolution.helka})"
    return (
        f"g:{resolution.gush} h:{resolution.helka} "
        f"(m:{resolution.match_method}/{resolution.confidence:.2f})"
    )


def _flags_text(*, is_cache_hit: bool, resolution: RowResolution) -> str:
    flags = []
    if is_cache_hit:
        flags.append("CACHE HIT")
    if resolution.used_secondary_address:
        flags.append("SECONDARY")
    return ", ".join(flags)


def run_resolve(input_path: str, resolver, cache: AddressCache, *, use_secondary_address: bool = True, limit: Optional[int] = None) -> int:
    resolved_count = 0
    seen = 0
    start_time = time.monotonic()
    recent_hits = deque(maxlen=100)  # True/False per row, oldest evicted past 100

    def _rate() -> str:
        elapsed_minutes = (time.monotonic() - start_time) / 60.0
        lines_per_minute = seen / elapsed_minutes if elapsed_minutes > 0 else 0.0
        return f"{lines_per_minute:.1f}/min"

    def _hit_ratio() -> str:
        hits = sum(recent_hits)
        window = len(recent_hits)
        pct = (hits / window * 100.0) if window else 0.0
        return f"{hits}/{window} ({pct:.1f}%)"

    _print_trace_header()
    try:
        for row in iter_rows(input_path, on_error=_on_parse_error):
            seen += 1
            key = row_identity_key(row, use_secondary_address)
            cached = cache.get_row(key)
            if cached is not cache.MISS:
                recent_hits.append(True)
                print(
                    _trace_row([
                        "", row.line_no, *_address_columns(row), _result_text(cached),
                        _flags_text(is_cache_hit=True, resolution=cached), _rate(), _hit_ratio(),
                    ]),
                    file=sys.stderr,
                )
                continue
            resolution = resolve_row(row, resolver, cache=cache, use_secondary_address=use_secondary_address)
            cache.set_row(key, resolution)
            resolved_count += 1
            recent_hits.append(False)
            print(
                _trace_row([
                    resolved_count, row.line_no, *_address_columns(row), _result_text(resolution),
                    _flags_text(is_cache_hit=False, resolution=resolution), _rate(), _hit_ratio(),
                ]),
                file=sys.stderr,
            )
            if limit is not None and resolved_count >= limit:
                break
    except (Exception, KeyboardInterrupt):
        # A network hiccup (or Ctrl+C) shouldn't lose already-resolved rows:
        # flush what's committed so far, then let the caller's `with
        # AddressCache(...)` close normally -- resuming is just rerunning
        # the same command, since already-cached rows are skipped above.
        cache.flush()
        print(
            f"STOPPED after resolving {resolved_count} new distinct addresses "
            f"({seen} rows scanned) -- progress is saved in {cache.path!r}. "
            f"Rerun the same command to resume from here.",
            file=sys.stderr,
        )
        raise
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
    p.add_argument(
        "--input", help="Ministry of Energy pipe-delimited vehicle file "
                         "(required unless --clear-cache is the only action requested)",
    )
    p.add_argument("--resolver", choices=["govmap", "spatial", "crosswalk"], default="crosswalk")
    p.add_argument("--cache", default="db/cache.sqlite", help="SQLite cache path (resumable)")
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
    p.add_argument(
        "--clear-cache", action="store_true",
        help="Delete all cached resolver/row entries in --cache before running "
             "(irreversible; forces every row to be re-resolved)",
    )
    p.add_argument("--limit", type=int, default=None, help="Stop after resolving N new distinct addresses")
    p.add_argument("--rate", type=float, default=5.0, help="Max resolver calls/sec (govmap)")
    return p


def main(argv=None) -> int:
    # Hebrew address fields in the per-row printouts otherwise come out as
    # \uXXXX escapes on a Windows console/pipe stuck on its legacy codepage.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    args = build_arg_parser().parse_args(argv)
    if args.resolve_only and args.aggregate_only:
        raise SystemExit("--resolve-only and --aggregate-only are mutually exclusive")
    if args.input is None and not args.clear_cache:
        raise SystemExit("--input is required")

    fuel_overrides = fuel.load_fuel_map(args.fuel_map) if args.fuel_map else None

    do_resolve = not args.aggregate_only and args.input is not None
    do_aggregate = not args.resolve_only and args.input is not None

    with AddressCache(args.cache) as cache:
        if args.clear_cache:
            cache.clear()
            print(f"Cleared cache {args.cache!r}.", file=sys.stderr)

        if do_resolve:
            assert args.input is not None
            resolver = build_resolver(args)
            run_resolve(args.input, resolver, cache, use_secondary_address=args.use_secondary_address, limit=args.limit)

        if do_aggregate:
            assert args.input is not None
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
