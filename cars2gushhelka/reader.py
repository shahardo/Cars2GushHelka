"""Streaming reader for the Ministry of Energy pipe-delimited vehicle file.

Format (measured directly from the sample): 19 '|'-delimited fields, one
header line, CRLF line endings, UTF-8, fixed-width space-padded text fields,
no quoting/escaping of the '|' delimiter anywhere in the data. A plain
str.split("|") is therefore correct and far cheaper than the csv module here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional, TextIO, Union

EXPECTED_HEADER = [
    "rc_mispar_rechev",
    "rc_sug_rechev",
    "rc_sug_rechev_nm",
    "rc_semel_tozar",
    "rc_kod_degem",
    "rc_sug_delek",
    "sug_delek_nm",
    "moed_aliya",
    "sug_degem",
    "sug_baal",
    "semel_yishuv",
    "shem_yishuv",
    "rechov",
    "mispar_bait",
    "mikud",
    "semel_yishuv_nosaf",
    "rechov_nosaf",
    "mispar_bait_nosaf",
    "mikud_nosaf",
]


@dataclass(frozen=True)
class VehicleRow:
    line_no: int
    mispar_rechev: str
    sug_rechev: str
    sug_rechev_nm: str
    semel_tozar: str
    kod_degem: str
    sug_delek: str
    sug_delek_nm: str
    moed_aliya: str
    sug_degem: str
    sug_baal: str
    semel_yishuv: str
    shem_yishuv: str
    rechov: str
    mispar_bait: str
    mikud: str
    semel_yishuv_nosaf: str
    rechov_nosaf: str
    mispar_bait_nosaf: str
    mikud_nosaf: str


class MalformedRowError(ValueError):
    def __init__(self, line_no: int, detail: str):
        super().__init__(f"line {line_no}: {detail}")
        self.line_no = line_no


def parse_line(line_no: int, raw_line: str) -> VehicleRow:
    line = raw_line.rstrip("\r\n")
    parts = line.split("|")
    if len(parts) != len(EXPECTED_HEADER):
        raise MalformedRowError(
            line_no, f"expected {len(EXPECTED_HEADER)} fields, got {len(parts)}"
        )
    stripped = [p.strip() for p in parts]
    return VehicleRow(line_no, *stripped)


def _lines(source: Union[str, TextIO]) -> Iterable[str]:
    if hasattr(source, "read"):
        yield from source
    else:
        with open(source, "r", encoding="utf-8", newline="") as fh:
            yield from fh


def iter_rows(
    source: Union[str, TextIO],
    *,
    skip_malformed: bool = True,
    on_error: Optional[Callable[[int, str, Exception], None]] = None,
) -> Iterator[VehicleRow]:
    """Stream VehicleRow records from a path or an already-open text stream.

    Line 1 is treated as a header. Its field count/names are checked and any
    mismatch is reported via `on_error` (not fatal -- parsing is positional,
    not name-based, so a superficially different header still works as long
    as field order/count matches). Malformed data lines are, by default,
    skipped and reported via `on_error(line_no, raw_line, exception)`; pass
    skip_malformed=False to raise instead.
    """
    lines = iter(_lines(source))
    try:
        header_line = next(lines)
    except StopIteration:
        return

    header_fields = [h.strip() for h in header_line.rstrip("\r\n").split("|")]
    if header_fields != EXPECTED_HEADER:
        if on_error:
            on_error(
                1,
                header_line,
                MalformedRowError(1, f"unexpected header: {header_fields!r}"),
            )

    for line_no, raw_line in enumerate(lines, start=2):
        if not raw_line.strip():
            continue
        try:
            yield parse_line(line_no, raw_line)
        except MalformedRowError as exc:
            if on_error:
                on_error(line_no, raw_line, exc)
            if skip_malformed:
                continue
            raise
