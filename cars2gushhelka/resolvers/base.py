"""Resolver interface: turns a normalized address query into a gush/helka.

Every backend (govmap online API, offline spatial join, user-supplied
crosswalk, or a test fake) implements the same `Resolver.resolve` method, so
the matching-tier logic in `matcher.py` never needs to know which backend is
in use.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class MatchMethod(str, Enum):
    """Which address-matching tier produced a result (see plan doc)."""

    STREET_HOUSE = "street_house"
    MIKUD_SPECIFIC = "mikud_specific"
    STREET_ONLY = "street_only"
    SETTLEMENT_CENTROID = "settlement_centroid"
    FAILED = "failed"


@dataclass(frozen=True)
class ResolveQuery:
    """One resolvable unit, already normalized by matcher.py."""

    method: MatchMethod
    semel_yishuv: str
    settlement_name: str
    street: str = ""
    house_number: str = ""
    mikud: str = ""

    @property
    def cache_key(self) -> str:
        """Key used by AddressCache to memoize this exact query."""
        if self.method is MatchMethod.STREET_HOUSE:
            return f"street_house:{self.semel_yishuv}:{self.street}:{self.house_number}"
        if self.method is MatchMethod.MIKUD_SPECIFIC:
            return f"mikud:{self.mikud}"
        if self.method is MatchMethod.STREET_ONLY:
            return f"street:{self.semel_yishuv}:{self.street}"
        if self.method is MatchMethod.SETTLEMENT_CENTROID:
            return f"settlement:{self.semel_yishuv}"
        return f"failed:{self.semel_yishuv}"


@dataclass(frozen=True)
class ResolvedParcel:
    """A resolved cadastral identity, or the resolver's best-effort partial
    answer (e.g. gush known but helka not returned by the backend)."""

    gush: Optional[str]
    helka: Optional[str]
    sub_helka: Optional[str] = None
    confidence: float = 1.0
    source: str = ""


@runtime_checkable
class Resolver(Protocol):
    def resolve(self, query: ResolveQuery) -> Optional[ResolvedParcel]:
        """Return a ResolvedParcel for `query`, or None if this backend has
        no answer (caller should try the next tier)."""
        ...
