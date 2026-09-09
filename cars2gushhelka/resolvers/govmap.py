"""Online resolver backed by the (unofficial) GovMap search API.

IMPORTANT -- this backend could NOT be exercised in the session that wrote
it: the sandbox's egress policy returns a 403 at the proxy for
`es.govmap.gov.il` / `ags.govmap.gov.il` (confirmed by direct probe). This
module is written against GovMap's publicly documented request/response
shapes and defends against small shape drift, but **you must validate it
against the live API** in an environment with network access before relying
on it, e.g.:

    python -m cars2gushhelka --input file.txt --resolver govmap --limit 20

Two-step flow, mirroring how the GovMap web UI itself resolves an address to
a parcel:

  1. Geocode the free-text address via the TldSearch autocomplete/search
     endpoint -> ITM (EPSG:2039) X/Y coordinates.
  2. `identify` those coordinates against GovMap's cadastral (Parcels/
     גושים וחלקות) map layer -> גוש (gush) / חלקה (helka).

Both steps are wrapped in retry-with-backoff and raise `GovMapResponseError`
(rather than silently mis-parsing) if the JSON shape doesn't match any of the
known variants -- fail loudly so a live shape change is obvious immediately
instead of producing quietly-wrong gush/helka values.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .base import MatchMethod, ResolvedParcel, ResolveQuery

DEFAULT_SEARCH_URL = "https://es.govmap.gov.il/TldSearch/api/DetailsByQuery"
DEFAULT_IDENTIFY_URL = (
    "https://ags.govmap.gov.il/arcgis/rest/services/Parcels/MapServer/identify"
)
# The cadastral (parcels) layer id within that identify service; GovMap has
# changed layer numbering before -- override via constructor if it has moved.
DEFAULT_PARCELS_LAYER = "0"

USER_AGENT = "Mozilla/5.0 (compatible; Cars2GushHelka/0.1)"


class GovMapResponseError(RuntimeError):
    """Raised when a GovMap response doesn't match any known shape."""


def _http_get_json(url: str, params: dict, *, timeout: float, retries: int, backoff: float) -> dict:
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    raise GovMapResponseError(f"GovMap request to {url} failed after {retries + 1} attempts: {last_exc}")


def _extract_coords(payload: dict) -> Optional[tuple]:
    """Defensively pull (x, y) ITM coordinates out of a search response.
    GovMap's search endpoint has, at various points, nested results under
    'Result' / 'data' / a list of typed result groups; try the shapes known
    at write time in order, and give up (return None) rather than guess."""
    candidates = []

    results = payload.get("Result") or payload.get("results")
    if isinstance(results, list):
        candidates.extend(results)
    elif isinstance(results, dict):
        for v in results.values():
            if isinstance(v, list):
                candidates.extend(v)

    data = payload.get("data")
    if isinstance(data, list):
        candidates.extend(data)

    for item in candidates:
        if not isinstance(item, dict):
            continue
        x = item.get("X") or item.get("x")
        y = item.get("Y") or item.get("y")
        if x is not None and y is not None:
            try:
                return float(x), float(y)
            except (TypeError, ValueError):
                continue
    return None


def _extract_parcel(payload: dict) -> Optional[ResolvedParcel]:
    """Defensively pull gush/helka out of an ArcGIS `identify` response."""
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    attrs = results[0].get("attributes") if isinstance(results[0], dict) else None
    if not isinstance(attrs, dict):
        return None

    gush = None
    helka = None
    for key, value in attrs.items():
        lk = key.lower()
        if gush is None and ("gush" in lk or lk == "גוש"):
            gush = value
        if helka is None and ("helka" in lk or "parcel" in lk or lk == "חלקה"):
            helka = value
    if gush is None and helka is None:
        return None
    return ResolvedParcel(
        gush=str(gush) if gush is not None else None,
        helka=str(helka) if helka is not None else None,
        confidence=0.8,
        source="govmap",
    )


def _address_text(query: ResolveQuery) -> str:
    if query.method is MatchMethod.STREET_HOUSE:
        return f"{query.street} {query.house_number} {query.settlement_name}".strip()
    if query.method is MatchMethod.STREET_ONLY:
        return f"{query.street} {query.settlement_name}".strip()
    if query.method is MatchMethod.MIKUD_SPECIFIC:
        return query.mikud
    return query.settlement_name


class GovMapResolver:
    def __init__(
        self,
        *,
        search_url: str = DEFAULT_SEARCH_URL,
        identify_url: str = DEFAULT_IDENTIFY_URL,
        parcels_layer: str = DEFAULT_PARCELS_LAYER,
        rate_limit_seconds: float = 0.2,
        timeout: float = 15.0,
        retries: int = 2,
        backoff: float = 1.0,
    ):
        self.search_url = search_url
        self.identify_url = identify_url
        self.parcels_layer = parcels_layer
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_call = time.monotonic()

    def _geocode(self, address_text: str) -> Optional[tuple]:
        if not address_text:
            return None
        self._throttle()
        payload = _http_get_json(
            self.search_url,
            {"query": address_text, "lyrs": "276267", "gid": "govmap"},
            timeout=self.timeout, retries=self.retries, backoff=self.backoff,
        )
        return _extract_coords(payload)

    def _identify(self, x: float, y: float) -> Optional[ResolvedParcel]:
        self._throttle()
        geometry = json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 2039}})
        payload = _http_get_json(
            self.identify_url,
            {
                "geometry": geometry,
                "geometryType": "esriGeometryPoint",
                "sr": "2039",
                "layers": f"all:{self.parcels_layer}",
                "tolerance": "2",
                "mapExtent": f"{x-50},{y-50},{x+50},{y+50}",
                "imageDisplay": "100,100,96",
                "returnGeometry": "false",
                "f": "json",
            },
            timeout=self.timeout, retries=self.retries, backoff=self.backoff,
        )
        return _extract_parcel(payload)

    def resolve(self, query: ResolveQuery) -> Optional[ResolvedParcel]:
        address_text = _address_text(query)
        coords = self._geocode(address_text)
        if coords is None:
            return None
        x, y = coords
        parcel = self._identify(x, y)
        if parcel is None:
            return None
        if query.method is MatchMethod.SETTLEMENT_CENTROID:
            parcel = ResolvedParcel(
                gush=parcel.gush, helka=parcel.helka, sub_helka=parcel.sub_helka,
                confidence=min(parcel.confidence, 0.4), source=parcel.source,
            )
        return parcel
