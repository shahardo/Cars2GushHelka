"""CSV-based resolver: look up gush/helka from a user-supplied crosswalk.

Useful when you already have (or can obtain) an address/mikud -> gush/helka
reference table and want a fully offline, deterministic, no-network run --
also what the end-to-end test uses to validate the pipeline.

Expected CSV columns (header required):

    match_method,semel_yishuv,street,house_number,mikud,gush,helka,sub_helka

Only the columns relevant to a row's `match_method` need to be filled in:

- street_house:        semel_yishuv, street, house_number
- mikud_specific:       mikud
- street_only:          semel_yishuv, street
- settlement_centroid:  semel_yishuv

`gush`/`helka` are required; `sub_helka` is optional.
"""

from __future__ import annotations

import csv
from typing import Dict, Optional

from ..normalize import clean_text, normalize_street
from .base import MatchMethod, ResolvedParcel, ResolveQuery

_REQUIRED_COLUMNS = {
    "match_method",
    "semel_yishuv",
    "street",
    "house_number",
    "mikud",
    "gush",
    "helka",
}


def _row_cache_key(method: str, semel_yishuv: str, street: str, house_number: str, mikud: str) -> str:
    """Build the same cache_key shape as ResolveQuery.cache_key, using the
    identical normalization so crosswalk rows match matcher-issued queries
    regardless of how the CSV author formatted whitespace/quotes."""
    semel_yishuv = clean_text(semel_yishuv)
    street_n = normalize_street(street)
    house = clean_text(house_number)
    mikud_n = clean_text(mikud)
    if method == MatchMethod.STREET_HOUSE.value:
        return f"street_house:{semel_yishuv}:{street_n}:{house}"
    if method == MatchMethod.MIKUD_SPECIFIC.value:
        return f"mikud:{mikud_n}"
    if method == MatchMethod.STREET_ONLY.value:
        return f"street:{semel_yishuv}:{street_n}"
    if method == MatchMethod.SETTLEMENT_CENTROID.value:
        return f"settlement:{semel_yishuv}"
    raise ValueError(f"unknown match_method in crosswalk: {method!r}")


class CrosswalkResolver:
    def __init__(self, rows: Optional[list] = None):
        self._table: Dict[str, ResolvedParcel] = {}
        if rows:
            for row in rows:
                self._add_row(row)

    def _add_row(self, row: dict) -> None:
        key = _row_cache_key(
            row["match_method"],
            row.get("semel_yishuv", ""),
            row.get("street", ""),
            row.get("house_number", ""),
            row.get("mikud", ""),
        )
        self._table[key] = ResolvedParcel(
            gush=row["gush"] or None,
            helka=row["helka"] or None,
            sub_helka=(row.get("sub_helka") or None),
            confidence=1.0,
            source="crosswalk",
        )

    @classmethod
    def from_csv(cls, path: str) -> "CrosswalkResolver":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise ValueError(f"crosswalk CSV missing required columns: {sorted(missing)}")
            return cls(list(reader))

    def resolve(self, query: ResolveQuery) -> Optional[ResolvedParcel]:
        return self._table.get(query.cache_key)
