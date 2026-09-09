"""Resumable SQLite cache backing the resolve stage.

Two tables:

- `tier_cache`   -- memoizes individual resolver calls, keyed by
  `ResolveQuery.cache_key` (e.g. one entry per distinct street+house). This
  is what makes repeated streets/mikudim across many vehicle rows cost one
  resolver call, not one per row.
- `row_resolution` -- memoizes the *final* tier outcome for a distinct row
  address identity (primary + secondary fields combined), so the aggregate
  stage never has to re-run tier logic or touch the resolver at all.

Both are keyed by stable string keys and are safe to resume: re-running
`--resolve-only` after an interruption only resolves rows/queries not
already present.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Optional

from .matcher import MISS, RowResolution
from .resolvers.base import ResolvedParcel

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tier_cache (
    cache_key TEXT PRIMARY KEY,
    found INTEGER NOT NULL,
    gush TEXT,
    helka TEXT,
    sub_helka TEXT,
    confidence REAL,
    source TEXT
);

CREATE TABLE IF NOT EXISTS row_resolution (
    row_key TEXT PRIMARY KEY,
    gush TEXT,
    helka TEXT,
    sub_helka TEXT,
    match_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    is_aggregated INTEGER NOT NULL,
    used_secondary_address INTEGER NOT NULL,
    settlement_code TEXT,
    settlement_name TEXT
);
"""


class AddressCache:
    MISS = MISS

    def __init__(self, path: str, *, commit_every: int = 500):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._commit_every = commit_every
        self._pending = 0

    def __enter__(self) -> "AddressCache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _maybe_commit(self) -> None:
        self._pending += 1
        if self._pending >= self._commit_every:
            self.flush()

    def flush(self) -> None:
        self.conn.commit()
        self._pending = 0

    def close(self) -> None:
        self.flush()
        self.conn.close()

    # -- tier-level cache ----------------------------------------------

    def get_tier(self, cache_key: str):
        row = self.conn.execute(
            "SELECT found, gush, helka, sub_helka, confidence, source "
            "FROM tier_cache WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return MISS
        found, gush, helka, sub_helka, confidence, source = row
        if not found:
            return None
        return ResolvedParcel(
            gush=gush, helka=helka, sub_helka=sub_helka,
            confidence=confidence, source=source or "",
        )

    def set_tier(self, cache_key: str, parcel: Optional[ResolvedParcel]) -> None:
        if parcel is None:
            self.conn.execute(
                "INSERT OR REPLACE INTO tier_cache "
                "(cache_key, found, gush, helka, sub_helka, confidence, source) "
                "VALUES (?, 0, NULL, NULL, NULL, NULL, NULL)",
                (cache_key,),
            )
        else:
            self.conn.execute(
                "INSERT OR REPLACE INTO tier_cache "
                "(cache_key, found, gush, helka, sub_helka, confidence, source) "
                "VALUES (?, 1, ?, ?, ?, ?, ?)",
                (cache_key, parcel.gush, parcel.helka, parcel.sub_helka,
                 parcel.confidence, parcel.source),
            )
        self._maybe_commit()

    # -- row-level cache -------------------------------------------------

    def get_row(self, row_key: str):
        row = self.conn.execute(
            "SELECT gush, helka, sub_helka, match_method, confidence, "
            "is_aggregated, used_secondary_address, settlement_code, settlement_name "
            "FROM row_resolution WHERE row_key=?",
            (row_key,),
        ).fetchone()
        if row is None:
            return MISS
        (gush, helka, sub_helka, match_method, confidence,
         is_aggregated, used_secondary, code, name) = row
        return RowResolution(
            gush=gush, helka=helka, sub_helka=sub_helka,
            match_method=match_method, confidence=confidence,
            is_aggregated=bool(is_aggregated),
            used_secondary_address=bool(used_secondary),
            settlement_code=code, settlement_name=name,
        )

    def set_row(self, row_key: str, resolution: RowResolution) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO row_resolution "
            "(row_key, gush, helka, sub_helka, match_method, confidence, "
            "is_aggregated, used_secondary_address, settlement_code, settlement_name) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row_key, resolution.gush, resolution.helka, resolution.sub_helka,
             resolution.match_method, resolution.confidence,
             int(resolution.is_aggregated), int(resolution.used_secondary_address),
             resolution.settlement_code, resolution.settlement_name),
        )
        self._maybe_commit()
