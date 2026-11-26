# Module: Hazards and land screen (`features/hazards.py`)

**What it does.** For every census tract and county it works out five screening shares: homes in FEMA's flood zone, developed land in that zone, developed land, impervious cover and steep land. They describe where building may be harder in practice. They feed the place profile section "Hazards and land" and the optional practical capacity screen ([capacity.md](capacity.md)); without that option nothing here changes a model estimate.

**Why.** A reform changes what the rules allow (nominal capacity). Some of that land is in a floodplain, already paved, or too steep for a small project without major grading (practical capacity). Official federal grids and flood maps exist for every state, so the same screen runs for any state.

## Terms

- **Special Flood Hazard Area (SFHA)**: FEMA's regulatory 1% annual chance floodplain (flood zones A and V and their subtypes); FEMA flags its polygons with `SFHA_TF = 'T'`.
- **NFHL**: FEMA's National Flood Hazard Layer, the digital flood maps, read from FEMA's public map service. Its "NFHL availability" layer shows where a digital map exists.
- **Annual NLCD**: the USGS National Land Cover Database, one 30 m grid per year (Collection 1). Developed land is classes 21 to 24 (open space, low, medium and high intensity); land is every cell that is not open water (class 11) or empty. Fractional impervious surface is the paved or built share of each cell, 0 to 100%.
- **3DEP**: the USGS 3D Elevation Program; its image service returns bare-earth elevation for any box.
- **Slope**: rise over run, in percent. A cell is steep above 15% (about 8.5 degrees), a common limit for building without major grading (`assumptions/hazards.yaml`).

## How each number is worked out

1. **One grid.** Every county is measured on a 30 m grid in the national Albers equal-area projection (EPSG:5070), aligned with the NLCD cells, so every cell has the same area (900 m²). Tract outlines and block points are projected with the Albers formulas for the GRS 1980 ellipsoid (Snyder 1987); the result matched FEMA's own server to under a millimetre (a test keeps the two points).
2. **Read the county's box** from three keyless services: land cover and impervious surface for 2025 (the MRLC coverage service, an uncompressed GeoTIFF on the NLCD's own grid), elevation resampled by the 3DEP service to the same grid (bilinear, 32-bit floats; asked for in tiles of at most 2,000 x 2,000 cells, under the service's 8,000-pixel limit, and a tile whose reply cannot be read, for example because it was cut off, is asked for again, up to 3 times; the service leaves out inner tiles that hold no land, over the sea, and the reader fills those as missing elevation), and the SFHA and availability polygons (FEMA's map service, in Albers, vertices thinned to 5 m by the server, page by page). Each grid must come back exactly where it was asked for, and the polygon pages must hold at least as many polygons as the service counts, or the build stops.
3. **Burn the shapes.** A cell belongs to a tract, or is in the SFHA, when its centre is inside the polygon; a polygon's rings are combined by the even-odd rule. The test is a vectorised ray cast over each polygon's box of cells, row by row (`features/planar.py`, plain NumPy).
4. **Slope** by central differences over 60 m: `slope = 100 x sqrt((dz/dx)^2 + (dz/dy)^2)` (`numpy.gradient`); cells next to missing elevation are left out.
5. **Count per tract** with `numpy.bincount`: land, developed, impervious sum and the land cells with a valid impervious value, steep, cells with a slope, cells in the SFHA, developed cells in the SFHA, cells and developed cells inside the digital flood map. Homes: a 2020 block's homes count as in the SFHA when its internal point lies on an SFHA cell, and as mapped (`nfhl_homes`) when it lies inside the digital flood map.
6. **Shares** (3 decimals):

| Column | Formula |
|---|---|
| `sfha_share` | homes in SFHA cells / homes inside the digital flood map (2020 block counts) |
| `sfha_dev_share` | developed cells in the SFHA / developed cells inside the digital flood map |
| `developed_share` | developed cells / land cells |
| `impervious_share` | sum of impervious percent over land cells / 100 / land cells with a valid impervious value (0-100) |
| `steep_share` | land cells steeper than 15% / land cells with a slope |

Flood shares count only what FEMA has mapped, so homes outside the digital map are never counted as dry. They are **blank, not zero**, when less than half of a tract's homes lie inside FEMA's digital flood map (`min_nfhl_coverage`; a tract with no homes is judged by its land), the rule `hazards.flood_coverage_rule` in `assumptions/hazards.yaml`. County rows add the tract counts, so they are exact; a county's flood shares use only the tracts that show them, and in them only the mapped homes (`nfhl_homes`) and mapped developed land (`nfhl_developed_acres`).

Only the per-tract counts and a note per county (grid, polygons read, each answer's SHA-256 and size, the read date) are kept, in `data/raw/hazards/<state code>/`. A county already done under the same rules is reused, so a stopped run picks up where it left off. The grids and polygons themselves are never saved.

## Tables in the pack

| Table | One row per | Columns |
|---|---|---|
| `hazard_tract` | tract | `geoid`, `sfha_share`, `sfha_dev_share`, `developed_share`, `impervious_share`, `steep_share` |
| `hazard_county` | county | `tracts`, `tracts_flood_unmapped`, `homes`, `nfhl_homes`, `sfha_homes`, `land_acres`, `developed_acres`, `nfhl_developed_acres`, the five shares, `nfhl_coverage`, `sfha_polygons`, `nlcd_year`, `read_on` |

## Coverage (built 2026-11-24; Massachusetts 2026-11-25)

| State | Tracts | Flood shares blank (no digital FEMA map) | Homes in the SFHA (state) | Land steeper than 15% (state) |
|---|---|---|---|---|
| Pennsylvania | 3,445 | 25 | 2.7% | 32% |
| Vermont | 193 | 60 (eight counties with little or no digital map) | 5.1% | 42% |
| Massachusetts | 1,613 | 114 (Berkshire and Franklin counties have no digital map; 28 of 35 tracts in Hampshire, 19 of 191 in Worcester) | 6.8% | 14% |

## Vintages

| Input | Edition |
|---|---|
| FEMA NFHL | the layer as read (FEMA updates it continuously); the read date is in `hazard_county.read_on` |
| Annual NLCD | year 2025, the latest in the service's time list on 2026-11-24 |
| 3DEP | DEMs published as of the read date (the service said October 26, 2026) |
| Homes | 2020 Census blocks |

## Engine use: the practical capacity screen

The practical capacity screen (run option `capacity_screen`, [capacity.md](capacity.md), method [../methods/capacity.md](../methods/capacity.md)) reads `sfha_share` and `steep_share`; without that option no estimate uses them. The columns: `hazard_tract.sfha_share` (share of existing homes, a stand-in for lots with a single-family home, in the SFHA; ADU and missing-middle exposure), `sfha_dev_share`, `steep_share` (land a small project would need to grade) and `developed_share` / `impervious_share` (how built-up a tract already is). A natural use: discount eligible lots by `sfha_share` in a stated range (a floodplain permit is harder, not impossible), and carry blank flood shares as unknown, never zero.

## Use

```
visionhousing hazards --state PA [--county 42101] [--workers 2]
```

The command reads one county at a time (a few seconds to half a minute each; well under 100 MB of memory for the largest Pennsylvania county), then writes `hazard_tract` and `hazard_county` and rewrites the pack's data dictionary and manifest. A full `make data` runs it as one of the add-on steps.

Profile section `hazards` ("Hazards and land"): facts for each share (and, for counties and the state, the share of land with a digital flood map). Charts (stacks): `hazards.floodplain_homes`, `hazards.developed_land`, `hazards.steep_land`; a tract's acres are its Census land area times its share.

## Limits

- The SFHA is the regulatory floodplain: it leaves out heavy-rain flooding away from mapped streams, and some flood studies are decades old. Several Vermont counties have no digital flood map yet, so their flood shares are blank.
- Homes are placed by block centre points on a 30 m grid, so a large rural block counts wholly in or out.
- Land cover is a satellite classification at 30 m; "developed" includes parks and lawns (class 21).
- A 30 m elevation grid smooths short steep banks, so `steep_share` is a lower bound for small sites.
- These are screening shares for planning questions, not site findings; they say nothing about insurance costs or legal limits on building in a floodplain.

## Files

| File | What it does |
|---|---|
| `data/sources/geotiff.py` | A small GeoTIFF reader (and writer for test fixtures): one band, strips or tiles, uncompressed or Deflate; a left-out ("sparse") tile reads as no data |
| `data/sources/hazards.py` | Reads the NLCD and 3DEP grids and the FEMA polygons for one box, with checks |
| `features/hazards.py` | Albers projection, burning shapes, slope, counts and the two tables |
| `features/planar.py` | The grid, point-in-polygon, polygon areas and shapes on grids (plain NumPy) |
| `features/hazards_build.py` | `add_hazards` (the pack add-on, county by county, resumable) and the `visionhousing hazards` command |
| `profile/sections/hazards.py` | The profile section and its charts |
| `assumptions/hazards.yaml` | Grid, land-cover year and classes, the slope threshold, flood rules |

No raster or geometry library (GDAL, rasterio, GEOS) is needed: grids are plain NumPy arrays and shapes are burned by `features/planar.py` (ray casting by cell centre). The project declines libraries that wrap GEOS or GDAL, which are LGPL.

## Tests

`tests/test_hazards.py`: the projection against FEMA's own numbers, grid alignment, GeoTIFF round trips and refusals, even-odd burning, slope of a plane, counts and shares on a 10 x 10 synthetic scene (including a blank flood share where no digital map exists), the county sums, and the fetchers against a fake server. `tests/test_profile_readiness.py` checks the profile section on a Testland copy. No network:

```
.venv/bin/python -m pytest -q tests/test_hazards.py tests/test_profile_readiness.py -p no:cacheprovider
```
