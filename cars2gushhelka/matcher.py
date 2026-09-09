"""Address-matching tier logic: turns one VehicleRow into a RowResolution.

Tier order (first success wins), per the plan:

    A. street_house         -- named street + house number
    B. mikud_specific        -- building-level (non-"00") mikud
    C. street_only           -- named street, no usable house number/mikud
    D. settlement_centroid   -- rural / nothing usable (always PRIMARY settlement)
    E. failed                -- resolver had no answer at all

A/B/C are tried on the primary address first. If all three fail and
`use_secondary_address` is set, the same three tiers are retried on the
secondary (*_nosaf) address before falling back to D/E.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .normalize import clean_text, is_mikud_specific, is_settlement_level, split_house_number
from .reader import VehicleRow
from .resolvers.base import MatchMethod, ResolvedParcel, Resolver, ResolveQuery

FAILED_GUSH = "FAILED"

# Sentinel distinguishing "not in cache yet" from "cached, and the answer is
# that this key doesn't resolve to anything". Owned here (not in cache.py) so
# cache.py can depend on matcher.py without a back-reference.
MISS = object()


@dataclass(frozen=True)
class AddressCandidate:
    semel_yishuv: str
    settlement_name: str
    street: str
    house_number: str
    mikud: str

    @property
    def has_street(self) -> bool:
        return not is_settlement_level(self.street, self.settlement_name)

    @property
    def has_house_number(self) -> bool:
        num, _ = split_house_number(self.house_number)
        return num is not None

    @property
    def has_specific_mikud(self) -> bool:
        return is_mikud_specific(self.mikud)

    @property
    def is_usable(self) -> bool:
        return bool(self.has_street or self.has_specific_mikud)


@dataclass(frozen=True)
class RowResolution:
    gush: Optional[str]
    helka: Optional[str]
    sub_helka: Optional[str]
    match_method: str
    confidence: float
    is_aggregated: bool
    used_secondary_address: bool
    settlement_code: str
    settlement_name: str


def primary_candidate(row: VehicleRow) -> AddressCandidate:
    return AddressCandidate(
        semel_yishuv=row.semel_yishuv,
        settlement_name=row.shem_yishuv,
        street=row.rechov,
        house_number=row.mispar_bait,
        mikud=row.mikud,
    )


def secondary_candidate(row: VehicleRow) -> Optional[AddressCandidate]:
    """Build the secondary (*_nosaf) address candidate, or None if the row
    carries no secondary-address data at all.

    The source file has no settlement-NAME field for the secondary address
    (only `semel_yishuv_nosaf`, a code). When that code is blank we assume
    the same settlement as the primary address and reuse its name; when it's
    a *different* code, we have no name for it, so settlement_name is left
    blank (settlement_centroid still isn't attempted for the secondary
    address at all -- see resolve_row -- so this only affects logging).
    """
    code = clean_text(row.semel_yishuv_nosaf)
    street = clean_text(row.rechov_nosaf)
    house = clean_text(row.mispar_bait_nosaf)
    mikud = clean_text(row.mikud_nosaf)
    if not (code or street or house or mikud):
        return None
    primary_code = clean_text(row.semel_yishuv)
    if not code:
        code = primary_code
    name = clean_text(row.shem_yishuv) if code == primary_code else ""
    return AddressCandidate(
        semel_yishuv=code,
        settlement_name=name,
        street=street,
        house_number=house,
        mikud=mikud,
    )


def _tier_queries(candidate: AddressCandidate) -> List[ResolveQuery]:
    queries = []
    if candidate.has_street and candidate.has_house_number:
        queries.append(
            ResolveQuery(
                method=MatchMethod.STREET_HOUSE,
                semel_yishuv=candidate.semel_yishuv,
                settlement_name=candidate.settlement_name,
                street=candidate.street,
                house_number=candidate.house_number,
            )
        )
    if candidate.has_specific_mikud:
        queries.append(
            ResolveQuery(
                method=MatchMethod.MIKUD_SPECIFIC,
                semel_yishuv=candidate.semel_yishuv,
                settlement_name=candidate.settlement_name,
                mikud=candidate.mikud,
            )
        )
    if candidate.has_street:
        queries.append(
            ResolveQuery(
                method=MatchMethod.STREET_ONLY,
                semel_yishuv=candidate.semel_yishuv,
                settlement_name=candidate.settlement_name,
                street=candidate.street,
            )
        )
    return queries


def _resolve_cached(resolver: Resolver, cache, query: ResolveQuery) -> Optional[ResolvedParcel]:
    cached = cache.get_tier(query.cache_key) if cache is not None else MISS
    if cached is not MISS:
        return cached
    parcel = resolver.resolve(query)
    if cache is not None:
        cache.set_tier(query.cache_key, parcel)
    return parcel


def _try_queries(resolver, cache, queries):
    for query in queries:
        parcel = _resolve_cached(resolver, cache, query)
        if parcel is not None:
            return parcel, query
    return None, None


def resolve_row(
    row: VehicleRow,
    resolver: Resolver,
    *,
    cache=None,
    use_secondary_address: bool = False,
) -> RowResolution:
    primary = primary_candidate(row)
    settlement_code = clean_text(row.semel_yishuv)
    settlement_name = clean_text(row.shem_yishuv)
    used_secondary = False

    parcel, query = _try_queries(resolver, cache, _tier_queries(primary))

    if parcel is None and use_secondary_address:
        secondary = secondary_candidate(row)
        if secondary is not None and secondary.is_usable:
            parcel, query = _try_queries(resolver, cache, _tier_queries(secondary))
            if parcel is not None:
                used_secondary = True

    if parcel is not None:
        return RowResolution(
            gush=parcel.gush,
            helka=parcel.helka,
            sub_helka=parcel.sub_helka,
            match_method=query.method.value,
            confidence=parcel.confidence,
            is_aggregated=False,
            used_secondary_address=used_secondary,
            settlement_code=settlement_code,
            settlement_name=settlement_name,
        )

    # Tier D: settlement centroid, always keyed on the PRIMARY settlement.
    centroid_query = ResolveQuery(
        method=MatchMethod.SETTLEMENT_CENTROID,
        semel_yishuv=primary.semel_yishuv,
        settlement_name=primary.settlement_name,
    )
    centroid_parcel = _resolve_cached(resolver, cache, centroid_query)

    if centroid_parcel is not None:
        return RowResolution(
            gush=centroid_parcel.gush,
            helka=centroid_parcel.helka,
            sub_helka=centroid_parcel.sub_helka,
            match_method=MatchMethod.SETTLEMENT_CENTROID.value,
            confidence=centroid_parcel.confidence,
            is_aggregated=True,
            used_secondary_address=False,
            settlement_code=settlement_code,
            settlement_name=settlement_name,
        )

    # Tier E: nothing resolved. Bucketed by settlement so totals still
    # reconcile and the failure is inspectable rather than silently dropped.
    bucket = settlement_name or settlement_code or "UNKNOWN"
    return RowResolution(
        gush=FAILED_GUSH,
        helka=bucket,
        sub_helka=None,
        match_method=MatchMethod.FAILED.value,
        confidence=0.0,
        is_aggregated=True,
        used_secondary_address=False,
        settlement_code=settlement_code,
        settlement_name=settlement_name,
    )


def row_identity_key(row: VehicleRow, use_secondary_address: bool) -> str:
    """Cache key for the row-level resolution cache: two rows with byte-
    identical address fields (extremely common -- many vehicles share one
    owner address) resolve once and are looked up thereafter."""
    primary = primary_candidate(row)
    parts = [
        "sec" if use_secondary_address else "nosec",
        primary.semel_yishuv,
        primary.street,
        primary.house_number,
        primary.mikud,
    ]
    if use_secondary_address:
        secondary = secondary_candidate(row)
        if secondary is not None:
            parts += [secondary.semel_yishuv, secondary.street, secondary.house_number, secondary.mikud]
    return "|".join(clean_text(p) for p in parts)
