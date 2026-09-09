"""Streaming aggregation: RowResolution + engine category -> pivot table.

Grouped by (gush, helka, sub_helka, is_aggregated) rather than just
(gush, helka, sub_helka): a settlement-centroid parcel (Tier D) is kept
separate from a genuine per-building match (Tiers A-C) even on the rare
occasion they'd otherwise share the same gush/helka, so an aggregated,
lower-confidence count is never silently blended into a precise one.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Dict, Set, Tuple

from .matcher import RowResolution

ParcelKey = Tuple[str, str, str, bool]


@dataclass
class ParcelStats:
    settlement_code: str = ""
    settlement_name: str = ""
    match_methods: Counter = field(default_factory=Counter)
    engine_counts: Counter = field(default_factory=Counter)
    via_secondary_address: int = 0
    confidence_sum: float = 0.0
    total: int = 0

    @property
    def dominant_match_method(self) -> str:
        if not self.match_methods:
            return ""
        return self.match_methods.most_common(1)[0][0]

    @property
    def avg_confidence(self) -> float:
        if not self.total:
            return 0.0
        return self.confidence_sum / self.total


class Aggregator:
    def __init__(self) -> None:
        self.parcels: Dict[ParcelKey, ParcelStats] = defaultdict(ParcelStats)
        self.total_rows = 0
        self.secondary_address_rows = 0
        self.unmapped_fuel_codes: Counter = Counter()

    def add_row(self, resolution: RowResolution, engine_category: str) -> None:
        key: ParcelKey = (
            resolution.gush or "",
            resolution.helka or "",
            resolution.sub_helka or "",
            resolution.is_aggregated,
        )
        stats = self.parcels[key]
        if not stats.settlement_code and not stats.settlement_name:
            stats.settlement_code = resolution.settlement_code
            stats.settlement_name = resolution.settlement_name
        stats.match_methods[resolution.match_method] += 1
        stats.engine_counts[engine_category] += 1
        stats.confidence_sum += resolution.confidence
        stats.total += 1
        if resolution.used_secondary_address:
            stats.via_secondary_address += 1
            self.secondary_address_rows += 1
        self.total_rows += 1

    def all_categories(self) -> Set[str]:
        cats: Set[str] = set()
        for stats in self.parcels.values():
            cats.update(stats.engine_counts)
        return cats

    def tier_distribution(self) -> Counter:
        dist: Counter = Counter()
        for stats in self.parcels.values():
            dist.update(stats.match_methods)
        return dist
