# Cars2GushHelka

Aggregate the Ministry of Energy vehicle registry by cadastral parcel
(**gush**/**helka**) and engine (fuel) type: one output row per gush/helka,
one column per engine type (gasoline, diesel, electric, hybrid, …), cells =
vehicle counts.

## Why this is harder than a join

The source file (`data/sample_200.txt` is a 200-line sample) has no gush/helka
column -- only an owner address (settlement + street + house number + 7-digit
mikud/postal code) and a **secondary** address (`*_nosaf` fields). There is no
public mikud → gush/helka crosswalk, and **mikud alone cannot substitute for
one**: in the 199-row sample, 8 distinct mikudim each cover more than one
street (e.g. `8496500` in Omer spans three unrelated streets), because mikudim
ending in `"00"` behave as legacy settlement/zone-wide codes, not
building-specific ones.

So this pipeline treats **address text as the primary matching key**, uses
mikud only as a fallback/cross-check, and geocodes/looks up a real cadastral
backend to get gush/helka. See `PLAN.md`-equivalent reasoning inline in
`cars2gushhelka/matcher.py`.

## Address matching tiers

Tried in this order, first success wins:

| Tier | method | when |
|---|---|---|
| A | `street_house` | named street + house number |
| B | `mikud_specific` | street+house failed/unavailable, but mikud is building-level (doesn't end in `"00"`) |
| C | `street_only` | mikud not usable, but a named street exists (no house number) |
| D | `settlement_centroid` | rural / nothing usable -- **flagged `is_aggregated=true`** |
| E | `failed` | nothing resolved at all -- bucketed as `FAILED:<settlement>` |

If `--use-secondary-address` is passed, tiers A-C are retried on the
secondary (`*_nosaf`) address before falling back to D/E, and every row
resolved that way is flagged (`via_secondary_address` column + printed
summary line).

**Core invariant, checked on every run:** the sum of every engine-type cell
across the whole output always equals the number of input vehicle rows.
Nothing is silently dropped -- unresolved rows land in
`unresolved_report.csv` instead.

## Install

Standard library only for the core pipeline (Python 3.9+). No install step
needed unless you use the `spatial` resolver (see below).

## Usage

```bash
python -m cars2gushhelka \
  --input ministry_of_energy.txt \
  --resolver crosswalk \
  --crosswalk my_crosswalk.csv \
  --cache cache.sqlite \
  --output gush_helka_by_engine.csv \
  --use-secondary-address
```

Key flags:

- `--resolver {crosswalk,govmap,spatial}` -- which backend resolves an
  address to a gush/helka (see below).
- `--cache PATH` -- SQLite cache (default `cache.sqlite`). **Resumable**:
  re-running only resolves addresses not already cached, so an interrupted
  run (or a long govmap resolve) can be safely restarted.
- `--resolve-only` / `--aggregate-only` -- run the two stages separately, so
  a long network resolve doesn't need to be redone just to change the fuel
  map or re-run aggregation.
- `--use-secondary-address` -- fall back to the `*_nosaf` address when the
  primary address doesn't resolve.
- `--fuel-map map.json` -- override/extend the fuel code/name → category
  mapping (`{"<code-or-name>": "<category>"}`).
- `--limit N` -- resolve at most N new distinct addresses (useful to test a
  resolver cheaply before a full run).

Output: `gush_helka_by_engine.csv` (one row per gush/helka; UTF-8 with BOM
for Excel) and `unresolved_report.csv` (the Tier-E long tail, so it's
inspectable/fixable). A run summary (tier distribution, unmapped fuel codes,
reconciliation check) prints to stdout.

## Resolver backends

### `crosswalk` (default) -- fully offline, deterministic

You supply a CSV mapping address/mikud/settlement → gush/helka. See
`cars2gushhelka/resolvers/crosswalk.py` for the exact column format. This is
what the test suite uses, and it's the right choice if you already have (or
can obtain) a gush/helka reference table.

### `govmap` -- online, via the (unofficial) GovMap search API

**This backend could not be exercised while writing it**: this session's
network egress policy blocks `es.govmap.gov.il` / `ags.govmap.gov.il`
(confirmed by direct probe -- 403 at the proxy). It's written against
GovMap's publicly documented request/response shapes with defensive parsing
and retry/backoff, but **you must validate it against the live API** before
relying on it:

```bash
python -m cars2gushhelka --input file.txt --resolver govmap --limit 20
```

If GovMap's response shape has drifted, this fails loudly
(`GovMapResponseError`) rather than silently returning wrong gush/helka
values -- if you hit that, the fix is to adjust the field-name candidates in
`resolvers/govmap.py`'s `_extract_coords`/`_extract_parcel`.

### `spatial` -- fully offline, point-in-polygon against a local parcels layer

Needs two things not included here:

1. A parcels GeoJSON layer (polygons with `gush`/`helka` properties) -- e.g.
   Israel's Survey/Mapping Authority (Mapi) cadastral layer, or an export
   from GovMap.
2. A `Geocoder` implementation (anything with
   `geocode(address_text) -> (x, y) | None`, matching the parcels layer's
   CRS -- typically ITM/EPSG:2039). No offline Israeli geocoder ships here;
   wire up whatever you have.

`shapely` is only imported lazily inside `resolvers/spatial.py`, so
`pip install shapely` is only needed if you actually use this resolver --
the rest of the pipeline has zero extra dependencies.

```python
from cars2gushhelka.resolvers.spatial import ParcelIndex, SpatialResolver, CallableGeocoder

index = ParcelIndex("parcels.geojson")
resolver = SpatialResolver(CallableGeocoder(my_geocode_fn), index)
```

## Fuel/engine categories

`rc_sug_delek` (numeric code) + `sug_delek_nm` (Hebrew name) map to a
canonical category (gasoline, diesel, electric, hybrid, plugin_hybrid, lpg,
kerosene, hydrogen). Anything unrecognized lands in `other` -- never
dropped -- and is listed in the run summary so it stays visible. Override or
extend the mapping with `--fuel-map map.json`.

## Running the tests

```bash
python -m unittest discover -s tests
```

All tests are offline (no network): normalization, reader, fuel mapping,
tier-matching logic (including the secondary-address fallback), aggregation,
and two end-to-end suites -- a small hand-built fixture with an exactly
asserted output table, and a run over the full `data/sample_200.txt` sample
that checks the reconciliation invariant and the tier distribution.

## Known limitations

- ~32% of the sample rows are rural addresses (street name == settlement
  name, or empty) and can only be resolved to a settlement centroid --
  that's a property of the source data, not something this code can improve
  on without better source addresses. Those rows are always flagged
  `is_aggregated=true` so they're never confused with a genuine per-parcel
  match.
- The `govmap` resolver is unverified against the live API in this
  environment (see above) -- test it with `--limit 20` before a full run.
- `is_mikud_specific` (mikud not ending in `"00"`) is a heuristic derived
  from the sample, not a documented rule -- it can have false negatives
  (some real building-level mikudim happen to end in `"00"`) but the
  matcher only ever uses it as a fallback behind street+house, so a false
  negative just means one extra resolver call, not a wrong answer.
