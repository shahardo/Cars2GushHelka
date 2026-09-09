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
  wrong gush/helka is worse than a crash.
- Tests are fully offline (`tests/`, run via
  `python -m unittest discover -s tests`) and use `resolvers/crosswalk.py`
  or `resolvers/fake.py`, never a live resolver.
- The resolve stage prints one line per newly-resolved row to stderr
  (address fields processed + outcome) -- see `cli.py`'s
  `_describe_address`/`_describe_resolution`. Keep this in sync with
  `VehicleRow`'s/`RowResolution`'s fields if either changes shape.
