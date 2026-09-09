"""Offline resolver: geocode address text, then point-in-polygon against a
locally-held cadastral parcels layer.

Fully offline and reproducible once set up, but needs two things this
sandbox does not have and cannot fetch (egress is blocked here):

1. A parcels layer (GeoJSON FeatureCollection, polygons, with `gush`/`helka`
   properties) -- e.g. Israel's Survey/Mapping Authority (Mapi) cadastral
   layer, or an export from GovMap. See README.md for where to obtain it.
2. A `Geocoder` -- anything implementing `geocode(address_text) -> (x, y) |
   None`, in the SAME coordinate system as the parcels layer (typically
   ITM / EPSG:2039 for Israeli cadastral data). No offline Israeli geocoder
   ships with this repo; plug in whatever you have (a local geocoding
   service, a bulk-geocoded lookup table, etc).

`shapely` is only imported inside this module (lazily, on first use) so the
rest of the pipeline has zero dependency on it -- `pip install shapely` is
only needed if you actually select `--resolver spatial`.
"""

from __future__ import annotations

import json
from typing import Callable, Optional, Protocol

from .base import MatchMethod, ResolvedParcel, ResolveQuery


class Geocoder(Protocol):
    def geocode(self, address_text: str) -> Optional[tuple]:
        """Return (x, y) in the parcels layer's CRS, or None if not found."""
        ...


class ParcelIndex:
    """Point-in-polygon lookup over a GeoJSON parcels layer, backed by a
    shapely STRtree for O(log n) candidate lookup."""

    def __init__(
        self,
        geojson_path: str,
        *,
        gush_property: str = "gush",
        helka_property: str = "helka",
        sub_helka_property: Optional[str] = "sub_helka",
    ):
        try:
            import shapely.geometry
            import shapely.strtree
        except ImportError as exc:  # pragma: no cover - exercised only when selected
            raise ImportError(
                "The 'spatial' resolver requires shapely (`pip install shapely`)."
            ) from exc

        self._shapely_geometry = shapely.geometry
        self.gush_property = gush_property
        self.helka_property = helka_property
        self.sub_helka_property = sub_helka_property

        with open(geojson_path, encoding="utf-8") as fh:
            geojson = json.load(fh)

        self._geoms = []
        self._props = []
        for feature in geojson.get("features", []):
            geom = shapely.geometry.shape(feature["geometry"])
            self._geoms.append(geom)
            self._props.append(feature.get("properties", {}))

        self._tree = shapely.strtree.STRtree(self._geoms)

    def lookup(self, x: float, y: float) -> Optional[ResolvedParcel]:
        # Targets shapely >= 2.0, where STRtree.query(geom) returns an array
        # of integer indices into the geometry list the tree was built from.
        point = self._shapely_geometry.Point(x, y)
        for idx in self._tree.query(point):
            geom = self._geoms[idx]
            props = self._props[idx]
            if geom.contains(point) or geom.intersects(point):
                gush = props.get(self.gush_property)
                helka = props.get(self.helka_property)
                sub_helka = props.get(self.sub_helka_property) if self.sub_helka_property else None
                if gush is None and helka is None:
                    continue
                return ResolvedParcel(
                    gush=str(gush) if gush is not None else None,
                    helka=str(helka) if helka is not None else None,
                    sub_helka=str(sub_helka) if sub_helka is not None else None,
                    confidence=1.0,
                    source="spatial",
                )
        return None


def _address_text(query: ResolveQuery) -> str:
    if query.method is MatchMethod.STREET_HOUSE:
        return f"{query.street} {query.house_number}, {query.settlement_name}".strip(", ")
    if query.method is MatchMethod.STREET_ONLY:
        return f"{query.street}, {query.settlement_name}".strip(", ")
    if query.method is MatchMethod.MIKUD_SPECIFIC:
        return query.mikud
    return query.settlement_name


class SpatialResolver:
    def __init__(self, geocoder: Geocoder, parcel_index: ParcelIndex):
        self.geocoder = geocoder
        self.parcel_index = parcel_index

    def resolve(self, query: ResolveQuery) -> Optional[ResolvedParcel]:
        address_text = _address_text(query)
        if not address_text:
            return None
        coords = self.geocoder.geocode(address_text)
        if coords is None:
            return None
        x, y = coords
        parcel = self.parcel_index.lookup(x, y)
        if parcel is None:
            return None
        if query.method is MatchMethod.SETTLEMENT_CENTROID:
            parcel = ResolvedParcel(
                gush=parcel.gush, helka=parcel.helka, sub_helka=parcel.sub_helka,
                confidence=min(parcel.confidence, 0.4), source=parcel.source,
            )
        return parcel


class CallableGeocoder:
    """Adapt a plain function to the Geocoder protocol."""

    def __init__(self, fn: Callable[[str], Optional[tuple]]):
        self._fn = fn

    def geocode(self, address_text: str) -> Optional[tuple]:
        return self._fn(address_text)
