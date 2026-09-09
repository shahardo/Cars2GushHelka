"""Hebrew text normalization for addresses.

Rules here are derived from concrete quirks measured in the Ministry of
Energy sample file (see the plan doc / README for the evidence):

- Fields are fixed-width, space-padded, and terminated with CRLF.
- Some street names use "flipped" parentheses, e.g. ``שריגים )לי-און(``
  instead of ``שריגים (לי-און)`` -- a legacy RTL export artifact.
- Gershayim/geresh (Hebrew abbreviation quote marks) appear in several
  interchangeable Unicode forms: ``"`` / ``״`` / smart quotes.
- Common street-type abbreviations (``רח``, ``שד``, ``שכ``, ``סמ``) are
  sometimes spelled out and sometimes not; expanding them helps a geocoder,
  but must not corrupt purely numeric "street names" like ``רח 8805``
  (a real street name pattern used in Nazareth).
"""

from __future__ import annotations

import re

# --- quote unification -----------------------------------------------------

_GERSHAYIM_CHARS = {
    "״": '"',  # HEBREW PUNCTUATION GERSHAYIM ״
    "”": '"',  # RIGHT DOUBLE QUOTATION MARK
    "“": '"',  # LEFT DOUBLE QUOTATION MARK
    "ʺ": '"',  # MODIFIER LETTER DOUBLE PRIME
}
_GERESH_CHARS = {
    "׳": "'",  # HEBREW PUNCTUATION GERESH ׳
    "’": "'",  # RIGHT SINGLE QUOTATION MARK
    "‘": "'",  # LEFT SINGLE QUOTATION MARK
}


def _fix_quotes(s: str) -> str:
    for ch, repl in _GERSHAYIM_CHARS.items():
        s = s.replace(ch, repl)
    for ch, repl in _GERESH_CHARS.items():
        s = s.replace(ch, repl)
    return s


# --- flipped parentheses ----------------------------------------------------

# Matches a ')' that opens a parenthetical, closed by a '(' -- the reversed
# form seen in RTL exports, e.g. "ג'ש )גוש חלב(" or "שריגים )לי-און(".
_FLIPPED_PAREN_RE = re.compile(r"\)([^()]*)\(")


def _fix_flipped_parens(s: str) -> str:
    return _FLIPPED_PAREN_RE.sub(lambda m: "(" + m.group(1) + ")", s)


def clean_text(raw: str) -> str:
    """Strip fixed-width padding/CR, collapse whitespace, fix quotes+parens.

    This is the baseline cleanup applied to every free-text field before any
    further, field-specific normalization.
    """
    if raw is None:
        return ""
    s = raw.replace("\r", "").replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    s = _fix_quotes(s)
    s = _fix_flipped_parens(s)
    return s


# --- street abbreviation expansion -----------------------------------------

# Applied only at the start of the string (Hebrew street records are almost
# always "<type> <name>"), and only when followed by a non-digit, so numeric
# street names such as "רח 8805" are left untouched.
_ABBREVIATIONS = [
    (re.compile(r"^שד'\s+(?=\D)"), "שדרות "),
    (re.compile(r"^שד\s+(?=\D)"), "שדרות "),
    (re.compile(r"^שכ\s+(?=\D)"), "שכונת "),
    (re.compile(r"^סמ\s+(?=\D)"), "סמטת "),
    (re.compile(r"^רח\s+(?=\D)"), "רחוב "),
]


def normalize_street(raw: str) -> str:
    """Clean a street name and expand common abbreviations for geocoding.

    Numeric street "names" (e.g. ``רח 8805``) are cleaned but not expanded,
    since ``רח`` there is part of the real name, not an abbreviation to spell
    out.
    """
    s = clean_text(raw)
    if not s:
        return s
    for pattern, repl in _ABBREVIATIONS:
        s = pattern.sub(repl, s)
    return s


# --- house numbers -----------------------------------------------------

_HOUSE_RE = re.compile(r"^(\d+)\s*([א-ת]?)$")


def split_house_number(raw: str) -> tuple[int | None, str]:
    """Split a house-number field into (number, letter-suffix).

    Returns (None, "") for empty or unparseable input.
    """
    s = clean_text(raw)
    if not s:
        return None, ""
    m = _HOUSE_RE.match(s)
    if not m:
        return None, ""
    return int(m.group(1)), m.group(2)


# --- mikud (postal code) -----------------------------------------------

def is_mikud_specific(mikud: str) -> bool:
    """True iff `mikud` looks like a building-level 7-digit postal code.

    Measured in the sample: mikudim ending in "00" behave as legacy
    settlement/zone-wide codes shared by many streets (e.g. 8496500 in Omer
    covers three unrelated streets), so they must never be trusted to pin a
    single building. A non-"00"-ending 7-digit code is treated as
    building-specific.
    """
    m = clean_text(mikud)
    if not (m.isdigit() and len(m) == 7):
        return False
    return not m.endswith("00")


# --- rurality / settlement-level detection ------------------------------

def is_settlement_level(street: str, settlement_name: str) -> bool:
    """True if there's no real street to geocode -- it's empty, or it's just
    a repeat of the settlement name (the common pattern for small
    settlements/moshavim in the sample, e.g. street="דור", settlement="דור").
    """
    s = normalize_street(street)
    if not s:
        return True
    return s == clean_text(settlement_name)


# --- cache keys --------------------------------------------------------

def address_key(semel_yishuv: str, street: str, house_number: str) -> str:
    """Stable cache key for a street+house-number address."""
    street_n = normalize_street(street)
    num, suffix = split_house_number(house_number)
    house_part = f"{num}{suffix}" if num is not None else ""
    return f"addr:{clean_text(semel_yishuv)}:{street_n}:{house_part}"


def street_only_key(semel_yishuv: str, street: str) -> str:
    return f"street:{clean_text(semel_yishuv)}:{normalize_street(street)}"


def mikud_key(mikud: str) -> str:
    return f"mikud:{clean_text(mikud)}"


def settlement_key(semel_yishuv: str) -> str:
    return f"settlement:{clean_text(semel_yishuv)}"
