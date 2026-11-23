# Boundaries (`data/sources/boundaries.py`)

Code: `src/visionhousing/data/sources/boundaries.py`.
Tests: `tests/test_boundaries.py` (offline), `tests/test_pack_realdata.py` (shape coverage on
the real pack).

## What it does, in plain words

The map needs the outline of every county and tract. This module reads the Census Bureau's
map-ready boundary files, keeps the state's shapes, makes them simpler and smaller, and writes
them as GeoJSON that a browser can draw.

## Terms used

| Term | Meaning |
|---|---|
| Cartographic boundary file | Census shapes already generalized for maps at 1:500,000 |
| Shapefile | A set of files: `.shp` (shapes), `.shx` (index), `.dbf` (names and codes) |
| Ring | The closed outline of one polygon or of a hole in it |
| Douglas-Peucker | A simplification method: keep the ends of a line; if the point farthest from the straight line between them is farther than the tolerance, keep it and repeat on both halves; otherwise drop the points between |
| Tolerance | The largest distance a dropped point may be from the simplified outline, in degrees |
| GeoJSON | A text format for map shapes; RFC 7946 is its standard |

## Inputs

| Source id | File | Size | Kept |
|---|---|---|---|
| `cb_2020_tract` | `cb_2020_42_tract_500k.zip` | 1.9 MB | `boundaries/cb_2020_42_tract_500k.geojson.gz` (0.95 MB) |
| `cb_2020_county` | `cb_2020_us_county_500k.zip` (national) | 12.6 MB | `boundaries/cb_2020_42_county_500k.geojson.gz` (0.16 MB) |

Both zips are read with pyshp (a pure-Python shapefile reader) and deleted at once; only the
state's full-detail shapes are kept in `data/raw/boundaries/`. The 2020 shapes match the tracts
of the 2020-2024 ACS.

## Steps

1. Keep the state's shapes (`STATEFP`).
2. Simplify each ring with Douglas-Peucker. A ring is cut at its first point and the point
   farthest from it, and each half is simplified, so a ring never folds onto itself.
   Tolerances: counties 0.001° (about 100 m), tracts 0.0004° (about 40 m).
3. Round to 5 decimals (about 1 m) and drop repeated points.
4. Orient rings as RFC 7946 asks: outer rings counter-clockwise, holes clockwise.
5. Drop rings that collapse below three distinct points (tiny islands, small holes). The
   largest outer ring of a shape is always kept, at a finer tolerance if needed, so no county
   or tract loses its shape.
6. Write `boundaries/counties.geojson` and `boundaries/tracts/{county}.geojson`. Each feature
   has the Testland properties `level`, `geoid`, `name`.

## Size budgets (Pennsylvania)

| File | Size | Budget |
|---|---|---|
| `counties.geojson` (67 counties) | 146,895 bytes | ≤ 300 KB, enforced by `write_boundaries` |
| `tracts/*.geojson` (67 files, 3,445 tracts) | 2,556,171 bytes in all; the largest (Allegheny) 233 KB | loaded one county at a time |

## Coverage

The pack builder stops with an error if any county or tract in the tables has no shape. In
Pennsylvania every county and tract has one. One ACS tract, `42049990000` (open water in Lake
Erie), has no cartographic shape; it has no land and no housing units and is left out of the
pack's tables (the build prints a note).

## Limits

- Neighbouring shapes are simplified one at a time, so a shared edge can differ by up to the
  tolerance. This is not visible at map zoom levels.
- Shapes are for drawing only. Areas in the tables come from the Census tables, never from
  these shapes.
