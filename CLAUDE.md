# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Maintenance rule

**Whenever you change the code in a way that affects behavior, flags, file
layout, or setup steps, update `README.md` and this file (`CLAUDE.md`) in
the same change.** Concretely:

- `README.md` is user-facing: usage, flags, resolver behavior, setup steps,
  known limitations. If a change alters any of those, update the relevant
  section there.
- `CLAUDE.md` (this file) is agent-facing: architecture, module map, dev
  conventions. If a change adds/renames/removes a module, changes the
  pipeline flow, or changes a convention documented below, update it here.

Do this as part of the same task, not as a follow-up -- a change whose docs
lag the code is the thing this rule exists to prevent. If a change is purely
internal (no behavior/flag/layout change visible to a user or a future
agent), no doc update is needed.

## What this project does

Aggregates the Israeli Ministry of Energy vehicle registry by cadastral
parcel (gush/helka) and engine/fuel type. See `README.md` for the full
problem description, address-matching tier design, and usage -- this file
only covers what a coding agent needs to orient itself.

## Pipeline flow

```
reader.py       parse the pipe-delimited input -> VehicleRow
matcher.py      VehicleRow -> RowResolution (tiered address matching, calls a Resolver)
cache.py        SQLite memoization of resolver calls and row resolutions (db/cache.sqlite by default)
aggregate.py    RowResolution + fuel category -> gush/helka x engine-type table
report.py       write the output CSVs + print the run summary
fuel.py         fuel code/name -> canonical category mapping
normalize.py    text/address normalization helpers used by matcher.py
cli.py          argparse entrypoint wiring the above together (two stages: resolve, aggregate)
```

`resolvers/` holds the pluggable address -> gush/helka backends behind the
`Resolver` protocol in `resolvers/base.py`:

- `crosswalk.py` -- offline, CSV-driven (default).
- `govmap.py` -- online, against GovMap's portal API (see README for the
  live endpoint details and the TLS quirk it works around).
- `nominatim.py` -- online, OpenStreetMap Nominatim (geocode) + GovMap's
  public open-data WFS (point -> gush/helka); the two-call flow
  parametric.co.il's gush/helka tool uses. See README for the endpoint
  details and the caveat that this module's exact request shapes are
  reconstructed from public API docs, not verified against that site's
  live JS.
- `spatial.py` -- offline, point-in-polygon against a local parcels layer;
  needs a `Geocoder` supplied programmatically (not wired into the CLI).
- `fake.py` -- test-only stub resolver.

## Conventions

- Standard library only for the core pipeline (Python 3.9+). `shapely` is
  imported lazily inside `resolvers/spatial.py` only -- don't add a
  hard dependency on it (or anything else) to the core pipeline.
- **Reconciliation invariant**: the sum of every engine-type cell in the
  output must always equal the number of input vehicle rows. Nothing is
  silently dropped; unresolved rows go to `unresolved_report.csv`. Any
  change to `aggregate.py`/`report.py` must preserve this and it's asserted
  in the end-to-end tests.
- Resolvers must fail loudly (raise, e.g. `GovMapResponseError`) rather than
  guess when a response shape doesn't match what's expected -- silently
  wrong gush/helka is worse than a crash. The online resolvers' HTTP
  helpers retry (default 4 retries, exponential backoff) on any `OSError`
  or `http.client.HTTPException`, not just `urllib.error.URLError`:
  urllib only wraps connect/send-phase failures in `URLError`, so a
  `ConnectionResetError` while reading the response would otherwise skip
  the retry loop entirely.
- Tests are fully offline (`tests/`, run via
  `python -m unittest discover -s tests`) and use `resolvers/crosswalk.py`
  or `resolvers/fake.py`, never a live resolver.
- The resolve stage prints one row per input row to stderr as a fixed-width
  table -- see `cli.py`'s `_TRACE_COLUMNS` (column name/width/alignment),
  `_trace_row`/`_fit` (rendering + truncation), `_print_trace_header`
  (header + separator, printed once at the top of `run_resolve`), and the
  per-column value helpers `_address_columns`/`_result_text`/`_flags_text`.
  Cache hits leave `IDX` blank and get `CACHE HIT` in `FLAGS` (plus
  `SECONDARY` when `RowResolution.used_secondary_address` is set) and
  aren't numbered/counted; newly-resolved rows are numbered and count
  toward `--limit`. Keep `_address_columns`/`_result_text` in sync with
  `VehicleRow`'s/`RowResolution`'s fields if either changes shape, and keep
  `_TRACE_COLUMNS` in sync with the values passed to `_trace_row` in
  `run_resolve` -- they're positional and must line up. `_address_columns`
  reverses the `settlement`/`street` field values (via `_rtl_display`)
  before printing, since a terminal without bidi reordering would
  otherwise render Hebrew text backwards -- this is purely a display
  transform for the stderr trace, not applied anywhere data is stored or
  written to a file. A column value longer than its width is truncated
  (via `_fit`) to keep the table aligned.
- Each printed row also carries the average rows/minute over the most
  recent 500 rows (`RATE`, from `run_resolve`'s local `_rate()` helper,
  backed by a `collections.deque(..., maxlen=500)` of `time.monotonic()`
  timestamps, `recent_times` -- cache hits and newly-resolved rows both
  count toward it) and the cache-hit ratio over the most recent 500 rows
  (`CACHE HIT % (last 500)`, from the local `_hit_ratio()` helper backed by
  a `collections.deque(..., maxlen=500)` of per-row hit/miss booleans,
  `recent_hits`).
- Tier-level cache entries (`tier_cache`, keyed by `ResolveQuery.cache_key`)
  are negative-cached too: a resolver call that returns no match caches
  that "no match" indefinitely (including across runs, since the cache is
  on disk). A transient resolver failure that got cached as a false miss
  will stay wrong until that key is cleared from `db/cache.sqlite` -- if a
  resolver bug is suspected, verify against a fresh cache before concluding
  the data itself doesn't resolve. `AddressCache.clear()` (wired to the
  CLI's `--clear-cache` flag) wipes both `tier_cache` and `row_resolution`
  wholesale -- there's no per-key clear from the CLI, only the whole cache
  or manual SQL against `db/cache.sqlite`.
- Row-level cache keys (`row_resolution`) depend on how a row resolved --
  see `matcher.lookup_row_resolution` / `store_row_resolution` (cli.py must
  go through these, not build keys itself): a parcel found via the primary
  address is keyed `primary|semel|street|house|mikud`; one found via the
  secondary address is keyed `secondary|...` on the secondary fields alone
  (the main address didn't resolve, so it isn't stored), and its
  `settlement_code/name` are the secondary's; centroid/failed outcomes (and
  everything under `--no-secondary-address`) use the full
  `row_identity_key` (`sec|`/`nosec|` + primary [+ secondary]). Lookup tries
  primary, then secondary (only when `_primary_known_failed`, i.e. every
  primary tier query is negative-cached -- otherwise a different row with
  a resolvable main address would wrongly inherit the secondary result),
  then the full key. Old caches with full-key entries still work.
- `--export-cache CSV_PATH` dumps the `row_resolution` table to CSV via
  `report.write_cache_export` (rows from `AddressCache.iter_row_resolutions`;
  the address columns are recovered by splitting the row key string, so
  keep `CACHE_EXPORT_COLUMNS` in sync with the key layouts above in
  `matcher.py`; `secondary|` keys export with blank main-address columns). FAILED rows are skipped. It runs after
  `--clear-cache` and before the resolve/aggregate stages.
- `--input` is optional at the argparse level (not `required=True`) so that
  `--clear-cache` / `--export-cache` can run standalone; `main()` enforces
  it's required instead, unless one of those was also passed. `do_resolve`/
  `do_aggregate` in `main()` both additionally check `args.input is not
  None` -- keep that check if either stage's invocation changes, since
  `run_resolve`/`run_aggregate` assume a non-`None` path.
- `cli.py`'s `run_resolve` wraps its row loop in `try/except (Exception,
  KeyboardInterrupt)`: on any error (a resolver's `GovMapResponseError`
  after retries are exhausted, a dropped connection, Ctrl+C, ...) it
  flushes the cache, prints how much progress was made and that rerunning
  the same command resumes, then re-raises so the real traceback/exit code
  is preserved. Resolvers should keep failing loudly (raise, don't guess)
  -- this catch only makes the *CLI's* response to that graceful, it
  doesn't change resolver behavior.
