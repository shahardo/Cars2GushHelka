"""Online resolver backed by GovMap's (unofficial) portal search API.

The endpoints this module originally targeted (`es.govmap.gov.il/TldSearch/...`
and `ags.govmap.gov.il/.../identify`) have been decommissioned -- GovMap's
portal moved to a new backend. The endpoints below were recovered by reading
network calls out of the live portal's JS bundle (www.govmap.gov.il) and
verified against the real API:

  1. Geocode the free-text address via the portal's search-service
     autocomplete endpoint -> EPSG:3857 (Web Mercator) X/Y coordinates.
     Must be sent as UTF-8 JSON bytes -- Hebrew text mangled through a
     shell's argv (e.g. `curl -d '...'` on Windows/MSYS) silently degrades
     into unrelated low-relevance matches instead of erroring, so this is
     validated to go over urllib's request body, not argv.
  2. Look up those coordinates directly against GovMap's parcel-search
     endpoint (a server-side point-in-polygon lookup, no separate spatial
     `identify` step or CRS reprojection needed) -> גוש (gush) / חלקה (helka).

Both steps are wrapped in retry-with-backoff and raise `GovMapResponseError`
(rather than silently mis-parsing) if the JSON shape doesn't match any of the
known variants -- fail loudly so a live shape change is obvious immediately
instead of producing quietly-wrong gush/helka values.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from .base import MatchMethod, ResolvedParcel, ResolveQuery

DEFAULT_SEARCH_URL = "https://www.govmap.gov.il/api/search-service/autocomplete"
DEFAULT_PARCEL_SEARCH_URL = "https://www.govmap.gov.il/api/layers-catalog/apps/parcel-search/address"

USER_AGENT = "Mozilla/5.0 (compatible; Cars2GushHelka/0.1)"

_POINT_RE = re.compile(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)\s*\)")


class GovMapResponseError(RuntimeError):
    """Raised when a GovMap response doesn't match any known shape."""


def _build_ssl_context() -> ssl.SSLContext:
    # www.govmap.gov.il only offers legacy, non-forward-secret TLS 1.2 cipher
    # suites (e.g. AES128-SHA). Python's default context enforces OpenSSL's
    # SECLEVEL=2, which excludes those, so the handshake fails with
    # SSLV3_ALERT_HANDSHAKE_FAILURE even though curl/browsers connect fine.
    # Lower the security level (cert verification stays on) to allow it.
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx


_SSL_CONTEXT = _build_ssl_context()


def _http_request_json(
    url: str, *, method: str, data: Optional[bytes], timeout: float, retries: int, backoff: float
) -> Optional[dict]:
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            headers = {"User-Agent": USER_AGENT}
            if data is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
                body = resp.read().decode("utf-8")
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    raise GovMapResponseError(f"GovMap request to {url} failed after {retries + 1} attempts: {last_exc}")


def _http_get_json(url: str, params: dict, *, timeout: float, retries: int, backoff: float) -> Optional[dict]:
    query = urllib.parse.urlencode(params)
    return _http_request_json(f"{url}?{query}", method="GET", data=None, timeout=timeout, retries=retries, backoff=backoff)


def _http_post_json(url: str, body: dict, *, timeout: float, retries: int, backoff: float) -> Optional[dict]:
    data = json.dumps(body).encode("utf-8")
    return _http_request_json(url, method="POST", data=data, timeout=timeout, retries=retries, backoff=backoff)


def _extract_coords(payload: Optional[dict]) -> Optional[tuple]:
    """Pull (x, y) EPSG:3857 coordinates out of the top address-type search
    result. Fails loudly (returns None) rather than guessing if the search
    turned up nothing address-shaped."""
    if payload is None:
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    for item in results:
        if not isinstance(item, dict) or item.get("type") != "address":
            continue
        shape = item.get("shape")
        if not isinstance(shape, str):
            continue
        match = _POINT_RE.search(shape)
        if not match:
            continue
        try:
            return float(match.group(1)), float(match.group(2))
        except (TypeError, ValueError):
            continue
    return None


def _extract_parcel(payload: Optional[dict]) -> Optional[ResolvedParcel]:
    """Pull gush/helka out of a parcel-search GeoJSON Feature response.
    The endpoint responds with a JSON `null` body (no Feature) when the point
    doesn't land inside any parcel -- that's a legitimate no-match, not a
    shape error."""
    if payload is None:
        return None
    props = payload.get("properties")
    if not isinstance(props, dict):
        return None

    gush = props.get("gushnumber")
    helka = props.get("parcelnumber")
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
        parcel_search_url: str = DEFAULT_PARCEL_SEARCH_URL,
        rate_limit_seconds: float = 0.2,
        timeout: float = 15.0,
        retries: int = 2,
        backoff: float = 1.0,
    ):
        self.search_url = search_url
        self.parcel_search_url = parcel_search_url
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
        payload = _http_post_json(
            self.search_url,
            {
                "searchText": address_text,
                "language": "he",
                "isAccurate": False,
                "maxResults": 5,
                "filterType": "address",
            },
            timeout=self.timeout, retries=self.retries, backoff=self.backoff,
        )
        return _extract_coords(payload)

    def _identify(self, x: float, y: float) -> Optional[ResolvedParcel]:
        self._throttle()
        payload = _http_get_json(
            self.parcel_search_url,
            {"x": x, "y": y},
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
