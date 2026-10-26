"""Online resolver matching the two-call flow used by parametric.co.il's
gush/helka lookup tool (https://parametric.co.il/gush-helka.html):

  1. Geocode the free-text address via OpenStreetMap's public Nominatim
     search API -> WGS84 (EPSG:4326) lat/lon.
  2. Look up the gush/helka containing that point via GovMap's open-data WFS
     service (`open.govmap.gov.il`), querying the `opendata:PARCEL_ALL`
     cadastral layer with an `INTERSECTS` CQL filter -- a server-side
     point-in-polygon lookup, same idea as `resolvers/govmap.py`'s
     `parcel-search/address` endpoint, but against the public open-data WFS
     instead of the portal's internal (reverse-engineered) API.

Both steps are wrapped in retry-with-backoff and raise
`NominatimGovMapResponseError` (rather than silently mis-parsing) if a JSON
shape doesn't match what's expected -- fail loudly so a live shape change is
obvious immediately instead of producing quietly-wrong gush/helka values.

Caveat: this module's exact request shapes were reconstructed from
Nominatim's published API docs and GovMap's WFS `GetFeature` conventions,
not verified against parametric.co.il's live JS (this sandbox has no
general internet egress to inspect it). `open.govmap.gov.il`'s TLS setup is
likewise unverified here -- if it turns out to need the same legacy-cipher
workaround `resolvers/govmap.py` uses for `www.govmap.gov.il`
(`SSLV3_ALERT_HANDSHAKE_FAILURE`), apply the same `SECLEVEL=1` fix here.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .base import MatchMethod, ResolvedParcel, ResolveQuery

DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_GOVMAP_WFS_URL = "https://open.govmap.gov.il/geoserver/opendata/wfs"

# Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
# requires a descriptive User-Agent identifying the application (not a
# browser-spoofing one) -- and a hard cap of 1 request/second, enforced
# below regardless of what a caller passes in.
NOMINATIM_USER_AGENT = "Cars2GushHelka/0.1 (+https://github.com/shahardo/cars2gushhelka)"
GOVMAP_USER_AGENT = "Mozilla/5.0 (compatible; Cars2GushHelka/0.1)"

_WFS_PROPERTY_NAMES = (
    "the_geom,GUSH_NUM,GUSH_SUFFI,PARCEL,LEGAL_AREA,STATUS_TEX,"
    "LOCALITY_N,REG_MUN_NA,COUNTY_NAM,TALAR_NUMB,TALAR_YEAR"
)


class NominatimGovMapResponseError(RuntimeError):
    """Raised when a Nominatim or GovMap WFS response doesn't match any
    known shape."""


_SSL_CONTEXT = ssl.create_default_context()


def _http_get_json(
    url: str, params: dict, *, user_agent: str, timeout: float, retries: int, backoff: float
) -> Optional[dict]:
    query = urllib.parse.urlencode(params)
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(f"{url}?{query}", method="GET", headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    raise NominatimGovMapResponseError(f"request to {url} failed after {retries + 1} attempts: {last_exc}")


def _extract_latlon(payload) -> Optional[tuple]:
    """Pull (lon, lat) WGS84 coordinates out of Nominatim's top search
    result. Fails loudly (raises) if the response isn't the expected list
    of objects with "lat"/"lon" string fields; an empty list is a
    legitimate no-match (returns None)."""
    if not isinstance(payload, list):
        raise NominatimGovMapResponseError(f"expected a JSON list from Nominatim, got {type(payload).__name__}")
    if not payload:
        return None
    top = payload[0]
    if not isinstance(top, dict) or "lat" not in top or "lon" not in top:
        raise NominatimGovMapResponseError(f"Nominatim result missing lat/lon: {top!r}")
    try:
        return float(top["lon"]), float(top["lat"])
    except (TypeError, ValueError) as exc:
        raise NominatimGovMapResponseError(f"Nominatim result has non-numeric lat/lon: {top!r}") from exc


def _extract_parcel(payload: Optional[dict]) -> Optional[ResolvedParcel]:
    """Pull gush/helka out of the WFS GetFeature GeoJSON FeatureCollection.
    An empty `features` list means the point landed outside any parcel --
    that's a legitimate no-match, not a shape error."""
    if payload is None:
        return None
    features = payload.get("features")
    if not isinstance(features, list):
        raise NominatimGovMapResponseError(f"expected a GeoJSON FeatureCollection, got: {payload!r}")
    if not features:
        return None
    props = features[0].get("properties")
    if not isinstance(props, dict):
        raise NominatimGovMapResponseError(f"feature missing properties: {features[0]!r}")

    gush = props.get("GUSH_NUM")
    helka = props.get("PARCEL")
    if gush is None and helka is None:
        return None

    gush_value = str(gush) if gush is not None else None
    suffix = props.get("GUSH_SUFFI")
    if gush_value and suffix not in (None, "", "0", 0):
        gush_value = f"{gush_value}/{suffix}"

    return ResolvedParcel(
        gush=gush_value,
        helka=str(helka) if helka is not None else None,
        confidence=0.8,
        source="nominatim_govmap",
    )


def _address_text(query: ResolveQuery) -> str:
    if query.method is MatchMethod.STREET_HOUSE:
        return f"{query.street} {query.house_number}, {query.settlement_name}".strip(", ")
    if query.method is MatchMethod.STREET_ONLY:
        return f"{query.street}, {query.settlement_name}".strip(", ")
    if query.method is MatchMethod.MIKUD_SPECIFIC:
        return query.mikud
    return query.settlement_name


class NominatimResolver:
    def __init__(
        self,
        *,
        nominatim_url: str = DEFAULT_NOMINATIM_URL,
        govmap_wfs_url: str = DEFAULT_GOVMAP_WFS_URL,
        nominatim_rate_limit_seconds: float = 1.0,
        govmap_rate_limit_seconds: float = 0.2,
        timeout: float = 15.0,
        retries: int = 2,
        backoff: float = 1.0,
    ):
        self.nominatim_url = nominatim_url
        self.govmap_wfs_url = govmap_wfs_url
        # Hard floor at 1 req/sec for Nominatim regardless of what's passed
        # in -- see the usage-policy note above NOMINATIM_USER_AGENT.
        self.nominatim_rate_limit_seconds = max(nominatim_rate_limit_seconds, 1.0)
        self.govmap_rate_limit_seconds = govmap_rate_limit_seconds
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self._last_nominatim_call = 0.0
        self._last_govmap_call = 0.0

    def _throttle(self, rate_limit_seconds: float, last_call_attr: str) -> None:
        elapsed = time.monotonic() - getattr(self, last_call_attr)
        if elapsed < rate_limit_seconds:
            time.sleep(rate_limit_seconds - elapsed)
        setattr(self, last_call_attr, time.monotonic())

    def _geocode(self, address_text: str) -> Optional[tuple]:
        if not address_text:
            return None
        self._throttle(self.nominatim_rate_limit_seconds, "_last_nominatim_call")
        payload = _http_get_json(
            self.nominatim_url,
            {"q": address_text, "format": "json", "limit": 1, "countrycodes": "il", "addressdetails": 0},
            user_agent=NOMINATIM_USER_AGENT,
            timeout=self.timeout, retries=self.retries, backoff=self.backoff,
        )
        return _extract_latlon(payload)

    def _identify(self, lon: float, lat: float) -> Optional[ResolvedParcel]:
        self._throttle(self.govmap_rate_limit_seconds, "_last_govmap_call")
        payload = _http_get_json(
            self.govmap_wfs_url,
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "opendata:PARCEL_ALL",
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "CQL_FILTER": f"INTERSECTS(the_geom,SRID=4326;POINT({lon} {lat}))",
                "propertyName": _WFS_PROPERTY_NAMES,
                "count": 3,
            },
            user_agent=GOVMAP_USER_AGENT,
            timeout=self.timeout, retries=self.retries, backoff=self.backoff,
        )
        return _extract_parcel(payload)

    def resolve(self, query: ResolveQuery) -> Optional[ResolvedParcel]:
        address_text = _address_text(query)
        coords = self._geocode(address_text)
        if coords is None:
            return None
        lon, lat = coords
        parcel = self._identify(lon, lat)
        if parcel is None:
            return None
        if query.method is MatchMethod.SETTLEMENT_CENTROID:
            parcel = ResolvedParcel(
                gush=parcel.gush, helka=parcel.helka, sub_helka=parcel.sub_helka,
                confidence=min(parcel.confidence, 0.4), source=parcel.source,
            )
        return parcel
