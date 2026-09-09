"""Write the pivoted gush/helka x engine-type report, and a run summary."""

from __future__ import annotations

import csv
import sys
from typing import IO, Optional

from .aggregate import Aggregator
from .fuel import ordered_categories
from .matcher import FAILED_GUSH

BASE_COLUMNS = [
    "gush",
    "helka",
    "sub_helka",
    "settlement_code",
    "settlement_name",
    "is_aggregated",
    "match_method",
    "confidence",
]
TRAILING_COLUMNS = ["total", "via_secondary_address"]


def write_report(aggregator: Aggregator, output_path: str) -> list:
    """Write the main pivot CSV (UTF-8 with BOM, for Excel) and return the
    engine-category columns used, in output order."""
    categories = ordered_categories(aggregator.all_categories())
    header = BASE_COLUMNS + categories + TRAILING_COLUMNS

    rows = sorted(
        aggregator.parcels.items(),
        key=lambda kv: (kv[0][3], kv[0][0], kv[0][1], kv[0][2]),
    )

    with open(output_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for (gush, helka, sub_helka, is_aggregated), stats in rows:
            row = [
                gush,
                helka,
                sub_helka,
                stats.settlement_code,
                stats.settlement_name,
                "true" if is_aggregated else "false",
                stats.dominant_match_method,
                f"{stats.avg_confidence:.2f}",
            ]
            row += [stats.engine_counts.get(cat, 0) for cat in categories]
            row += [stats.total, stats.via_secondary_address]
            writer.writerow(row)

    return categories


def write_unresolved_report(aggregator: Aggregator, output_path: str) -> int:
    """Write the FAILED-tier rows (settlement-bucketed) to a separate CSV so
    the long tail of totally unresolved addresses is inspectable. Returns
    the number of vehicles in that bucket."""
    failed_total = 0
    with open(output_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["settlement_bucket", "settlement_code", "vehicle_count"])
        for (gush, helka, _sub, _agg), stats in sorted(aggregator.parcels.items()):
            if gush != FAILED_GUSH:
                continue
            writer.writerow([helka, stats.settlement_code, stats.total])
            failed_total += stats.total
    return failed_total


def print_summary(aggregator: Aggregator, *, use_secondary_address: bool, out: Optional[IO] = None) -> None:
    out = out or sys.stdout
    tiers = aggregator.tier_distribution()
    failed = sum(
        stats.total
        for (gush, _h, _s, _a), stats in aggregator.parcels.items()
        if gush == FAILED_GUSH
    )

    print(f"Processed {aggregator.total_rows} vehicle rows into {len(aggregator.parcels)} gush/helka groups.", file=out)
    print("Match-tier distribution:", file=out)
    for method, count in tiers.most_common():
        print(f"  {method:22s} {count}", file=out)
    print(f"Unresolved (failed) rows: {failed}", file=out)

    if use_secondary_address:
        print(f"{aggregator.secondary_address_rows} cars identified via secondary (*_nosaf) address", file=out)

    if aggregator.unmapped_fuel_codes:
        print("Unmapped fuel codes (counted into 'other'):", file=out)
        for (code, name), count in aggregator.unmapped_fuel_codes.most_common():
            print(f"  code={code!r} name={name!r}: {count}", file=out)

    reconciled = sum(stats.total for stats in aggregator.parcels.values())
    status = "OK" if reconciled == aggregator.total_rows else "MISMATCH"
    print(f"Reconciliation check: sum of all output cells = {reconciled} ({status})", file=out)
