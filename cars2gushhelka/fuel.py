"""Engine/fuel-type categorization.

`rc_sug_delek` in the source file is a small numeric code with a matching
Hebrew name in `sug_delek_nm`. The codes actually present in the 200-row
sample are 1 (בנזין/gasoline), 2 (דיזל/diesel) and 3 (נפט/kerosene), but the
full Ministry of Energy registry is known to also carry electric, hybrid,
plug-in hybrid, LPG and hydrogen vehicles, so the map below is not limited to
what was observed. Any code that isn't recognized (by number or by name)
falls into "other" -- it is never dropped -- and is reported in the run
summary so it stays visible instead of silently disappearing into a bucket.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Optional

from .normalize import clean_text

OTHER = "other"

# Canonical display/column order. "other" is always last.
CANONICAL_ORDER = [
    "gasoline",
    "diesel",
    "electric",
    "hybrid",
    "plugin_hybrid",
    "lpg",
    "kerosene",
    "hydrogen",
    OTHER,
]

# rc_sug_delek code -> canonical category.
FUEL_CODE_CATEGORIES = {
    "1": "gasoline",
    "2": "diesel",
    "3": "kerosene",
    "4": "electric",
    "5": "hybrid",
    "6": "plugin_hybrid",
    "7": "lpg",
    "8": "hydrogen",
}

# sug_delek_nm (cleaned Hebrew name) -> canonical category. Used as a
# secondary matcher when the code is missing/unrecognized, and as a
# cross-check against the code-based mapping.
FUEL_NAME_CATEGORIES = {
    "בנזין": "gasoline",
    "דיזל": "diesel",
    "סולר": "diesel",
    "נפט": "kerosene",
    "חשמל": "electric",
    "חשמלי": "electric",
    "היברידי": "hybrid",
    "היבריד": "hybrid",
    "היברידי נטען": "plugin_hybrid",
    "פלאג אין": "plugin_hybrid",
    "פלאג-אין": "plugin_hybrid",
    "גז": "lpg",
    "גפ\"מ": "lpg",
    "מימן": "hydrogen",
}


def load_fuel_map(path: str) -> dict:
    """Load a user-supplied override map: {"<code-or-name>": "<category>"}.

    Keys are matched against both the raw fuel code and the cleaned Hebrew
    name; values may be any category string (not limited to CANONICAL_ORDER
    -- a novel category widens the output table's columns).
    """
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def categorize(
    sug_delek: str,
    sug_delek_nm: str,
    *,
    overrides: Optional[dict] = None,
    unmapped_tracker: Optional[Counter] = None,
) -> str:
    """Map a fuel code + Hebrew name to a canonical engine-type category."""
    code = (sug_delek or "").strip()
    name = clean_text(sug_delek_nm)

    if overrides:
        if code in overrides:
            return overrides[code]
        if name in overrides:
            return overrides[name]

    if code in FUEL_CODE_CATEGORIES:
        return FUEL_CODE_CATEGORIES[code]
    if name in FUEL_NAME_CATEGORIES:
        return FUEL_NAME_CATEGORIES[name]

    if unmapped_tracker is not None:
        unmapped_tracker[(code, name)] += 1
    return OTHER


def ordered_categories(seen: set) -> list:
    """Return categories actually present, canonical order first, then any
    extra categories introduced by an override map, alphabetically."""
    ordered = [c for c in CANONICAL_ORDER if c in seen]
    extra = sorted(seen - set(CANONICAL_ORDER))
    return ordered + extra
