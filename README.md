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

By default (disable with `--no-secondary-address`), if all three primary-address
tiers fail, A-C are retried on the secondary (`*_nosaf`) address before
falling back to D/E, and every row resolved that way is flagged
(`via_secondary_address` column + printed summary line).

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
  --cache db/cache.sqlite \
  --output gush_helka_by_engine.csv
```

Key flags:

- `--resolver {crosswalk,govmap,spatial}` -- which backend resolves an
  address to a gush/helka (see below).
- `--cache PATH` -- SQLite cache (default `db/cache.sqlite`, directory created
  automatically). **Resumable**:
  re-running only resolves addresses not already cached, so an interrupted
  run (or a long govmap resolve) can be safely restarted.
- `--resolve-only` / `--aggregate-only` -- run the two stages separately, so
  a long network resolve doesn't need to be redone just to change the fuel
  map or re-run aggregation.
- `--no-secondary-address` -- disable falling back to the `*_nosaf` address
  when the primary address doesn't resolve (the fallback is **on by
  default**).
- `--fuel-map map.json` -- override/extend the fuel code/name → category
  mapping (`{"<code-or-name>": "<category>"}`).
- `--limit N` -- resolve at most N new distinct addresses (useful to test a
  resolver cheaply before a full run).

During the resolve stage, every row prints one line to stderr with the
address fields it processed and the outcome -- newly-resolved rows are
numbered, and rows already answered by the cache (a previous run, or an
earlier row with the identical address) are marked `*** CACHE HIT ***`:

```
[5] line 6: settlement='פרדס חנה-כרכור' street='המושב' house='64' mikud='3706964' -> gush=10074 helka=77 (method=street_house, confidence=0.80)
line 7: settlement='פרדס חנה-כרכור' street='המושב' house='64' mikud='3706964' -> gush=10074 helka=77 (method=street_house, confidence=0.80) *** CACHE HIT ***
```

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

### `govmap` -- online, via the (unofficial) GovMap portal API

Verified working against the live API (`www.govmap.gov.il`) as of writing.
Two-step flow per address, matching what the GovMap portal's own UI does
internally:

1. `POST /api/search-service/autocomplete` -- geocode the free-text address
   (`street house_number settlement`) to an EPSG:3857 (Web Mercator) point.
   Tiers A-C search with `filterType: "address"` and match `type: "address"`
   results; the `settlement_centroid` tier searches with
   `filterType: "settlement"` and matches `type: "settlement"` instead --
   GovMap indexes a settlement's centroid (its `SETL_MID_POINT` layer) as a
   separate result type from individual street/house records, and many
   small settlements have no literal `"address"` entry matching just their
   own name, so searching with the wrong filter silently finds nothing for
   them.
2. `GET /api/layers-catalog/apps/parcel-search/address?x=&y=` -- look up the
   gush/helka containing that point (server-side point-in-parcel, no local
   CRS reprojection needed).

Try it with a small batch before a full run:

```bash
python -m cars2gushhelka --input file.txt --resolver govmap --limit 20
```

Two things worth knowing:

- **It's reverse-engineered, not documented.** These endpoints were
  recovered by reading network calls out of the live portal's JS bundle,
  not from published API docs (GovMap does publish an official
  `api.govmap.gov.il` product, but that one requires a registered API token
  and drives an embedded JS map rather than plain HTTP calls). GovMap could
  change this backend again without notice -- if it does, this fails loudly
  (`GovMapResponseError`) rather than silently returning wrong gush/helka
  values; the fix is to re-derive the current endpoint/shape and adjust
  `resolvers/govmap.py`'s `_extract_coords`/`_extract_parcel`.
- **TLS quirk:** `www.govmap.gov.il` only offers a legacy, non-forward-secret
  TLS 1.2 cipher suite. Python's default `SSLContext` refuses it at its
  default security level even though curl/browsers connect fine, so
  `resolvers/govmap.py` connects with `SECLEVEL=1` explicitly (certificate
  verification stays on).

### `spatial` -- fully offline, point-in-polygon against a local parcels layer

Needs a parcels GeoJSON layer and a `Geocoder` implementation, neither of
which is included here -- see **Setting up offline mode** below for where
to get them and how to wire one up. `shapely` is only imported lazily
inside `resolvers/spatial.py`, so `pip install shapely` is only needed if
you actually use this resolver.

## Setting up offline mode

"Offline" here means no live network calls during the actual run --
i.e. anything other than `--resolver govmap`. There are two paths, and
which one to set up depends on what you already have access to.

### Path 1: `crosswalk` -- if you have (or can get) any address/mikud → gush/helka table

This is the lighter-weight path. You need exactly one file: a CSV in the
format `cars2gushhelka/resolvers/crosswalk.py` documents (`match_method,
semel_yishuv, street, house_number, mikud, gush, helka, sub_helka`).

- It doesn't need to be complete -- rows with no crosswalk entry just fall
  through to `settlement_centroid`/`failed` rather than breaking the run,
  so a partial table (e.g. covering one municipality) is still useful.
- No downloads, no extra Python packages.
- If your source is an existing address→parcel table in some other shape
  (a spreadsheet, a database export), the fastest route is usually a small
  one-off script that reshapes it into the crosswalk CSV columns above,
  rather than writing a new resolver.

### Path 2: `spatial` -- fully self-contained, but more setup

This is the "download things" path proper. You need three pieces:

**1. `shapely`** (Python package):

```bash
pip install shapely
```

**2. A parcels layer** -- a GeoJSON `FeatureCollection` of parcel polygons
with `gush`/`helka` properties. Sources, most-authoritative first:

- **GovMap** (govmap.gov.il) -- has a layer-download tool covering the
  cadastral (גושים/חלקות) layer; you pick an extent and export as
  Shapefile/GeoJSON. Usually needs a free account. This is the same layer
  the online `govmap` resolver queries, so it's the most trustworthy
  source if you can get it.
- **data.gov.il** -- search "גוש חלקה" / "cadastre" / "מקרקעין"; cadastral
  datasets turn up there occasionally, coverage and freshness vary by
  dataset.
- **Israel Land Authority (רשות מקרקעי ישראל)** / Survey of Israel -- the
  official custodians, worth going to directly if you need something more
  authoritative than a GovMap export.

  If you only need one region/settlement rather than nationwide coverage,
  exporting just that extent from GovMap keeps the file small and setup
  much simpler.

If a Shapefile is all you can get, convert it once with `ogr2ogr` (from
GDAL) or `geopandas`:

```bash
ogr2ogr -f GeoJSON parcels.geojson parcels.shp
```

**3. A geocoder** -- turns address text into (x, y) coordinates in the
*same CRS as the parcels layer* (Israeli cadastral data is normally ITM /
EPSG:2039). This is the harder piece; nothing ships in the repo for it.
Options, roughly by effort:

- **A bulk-geocoded lookup table** -- if you already have street+house →
  coordinates for the addresses you care about, wrap a dict lookup as a
  `Geocoder`. Zero new infrastructure.
- **Self-hosted Nominatim over an OSM Israel/Palestine extract** --
  download the extract from Geofabrik (~150-250MB), run Nominatim locally
  (Docker + Postgres import, a few GB once built). The most complete
  option, and fully offline once set up, but real infrastructure to stand
  up.
- **A lighter OSM-based lookup** (e.g. via `osmnx`) -- workable for
  street-centroid-level geocoding with much less setup than a full
  Nominatim install, at the cost of precision.

Wire whichever one you pick into `Geocoder`'s one method:

```python
from cars2gushhelka.resolvers.spatial import ParcelIndex, SpatialResolver, CallableGeocoder

def my_geocode_fn(address_text: str):
    ...  # -> (x, y) in ITM, or None
    return x, y

index = ParcelIndex("parcels.geojson")
resolver = SpatialResolver(CallableGeocoder(my_geocode_fn), index)
```

Then pass `resolver` wherever the CLI's `build_resolver()` would otherwise
build one (the CLI itself only wires up `crosswalk` and `govmap` --
`spatial` needs a real `Geocoder` instance, which can't be expressed on
the command line, so drive it via `cli.run_resolve`/`run_aggregate`
directly, or add a small wrapper script).

**In practice**: unless you specifically need per-parcel precision
everywhere, Path 1 is usually the pragmatic choice -- standing up
Nominatim plus sourcing a nationwide parcels export is a real project on
its own.

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
- The `govmap` resolver depends on undocumented, reverse-engineered GovMap
  endpoints (see above) that could change without notice -- test it with
  `--limit 20` before a full run.
- The `govmap` resolver's parcel-search layer has no cadastral coverage for
  some Judea-and-Samaria (West Bank) settlements (e.g. אלפי מנשה, בית אריה)
  -- geocoding finds the settlement/address fine, but the parcel lookup
  returns no polygon even at the exact settlement centroid, so those rows
  correctly end up `failed` rather than a wrong answer. This is a gap in
  GovMap's own data, not a bug in this resolver.
- `is_mikud_specific` (mikud not ending in `"00"`) is a heuristic derived
  from the sample, not a documented rule -- it can have false negatives
  (some real building-level mikudim happen to end in `"00"`) but the
  matcher only ever uses it as a fallback behind street+house, so a false
  negative just means one extra resolver call, not a wrong answer.
