# Massachusetts: existing 3A modeled capacity by tract, and MassDEP estimated sewer

Two Massachusetts-only add-on tables answer questions the state's own data can answer better than a fixed rule of thumb:

1. **How many homes does the zoning towns already adopted under Section 3A allow, and in which tracts?** This is the "existing legal capacity" a transit-density reform has to beat. Without it the tool can only take a share of a district's acres out of the TOD benchmark (`tod.state_zoned_overlap_share`).
2. **Which homes lie inside an area MassDEP estimates a sewer system serves?** Before this step the Massachusetts pack had no sewer answer at all (every tract "unknown").

The TOD reform and the practical capacity screen read them (see "What the engine reads").

| Pack table | One row per | Sources (registry ids) |
|---|---|---|
| `mbta_3a_capacity_tract` | 2020 tract holding 3A capacity | `massgis_mbta3a_parcels`, `massgis_mbta3a_atlas`, `cb_2020_tract` |
| `mbta_3a_capacity_district` | 3A district (and zone code of parcels no district carries) | `massgis_mbta3a_parcels`, `massgis_mbta3a_atlas` |
| `ma_sewer_estimated_tract` | 2020 tract with homes | `massdep_sewer_service_areas`, `tiger_2020_tabblock` |

Code: `src/visionhousing/data/sources/ma_capacity.py` (fetch), `src/visionhousing/features/ma_capacity.py` (tables, pure), `src/visionhousing/features/ma_capacity_build.py` (pack step and command). Settings: `capacity` and `sewer_estimated` in `assumptions/ma_intake.yaml`.

```
visionhousing ma-capacity --state MA     # adds the three tables to a built pack
```

A full `make data STATE=MA` runs it as an add-on step after the 3A district tracts. In any other state it does nothing.

## Terms

- **Modeled unit capacity**: the number of homes the state's Section 3A compliance model says a parcel's zoning allows. It is gross (homes already on the parcel are not subtracted) and it is zoning capacity, not a forecast of building.
- **`F_L_M__`** ("Final Lot Multi-Family Unit Capacity"): the atlas parcel field the tool counts. It is the model's capacity after every lot limit (density, lot area per home, floor area, units per lot).
- **`MDL_U_C`** ("Modeled Unit Capacity"): the model's capacity before those lot limits. Carried as context, never added.
- **`RB_RSDN`**: homes already on the parcel (assessor records). Carried as context, never subtracted here.
- **`MFUC_DIST`**: the capacity the town reported for the whole district. **`MFUC_PARC`** is a district field meant to hold the parcel sum; it is 0 in every district at the 2026-11-25 read, so it is not used.
- **Estimated service** (sewer): MassDEP's own label. The polygons are "estimates for broad planning purposes", "not appropriate for site-specific or parcel-specific analysis". In this tool it is its own evidence class, separate from the verified service answers in `infra_tract`.
- **Placeholder circle**: a small circle MassDEP draws near a plant whose service area it has not mapped. It is not a service area.

## How the capacity table is made

1. **Read.** The atlas parcel layer (layer 1) is read by object id in chunks of 100 with a fixed column list: zone code, town, status, station-area flag, acres and the three unit fields. Owner names and addresses are never asked for. The service's own count is compared with what came back; a mismatch fails the read. The cached file records the layer's last edit date (2026-11-03), the read date (2026-11-25) and both counts (29,992 = 29,992). The station radii layer (layer 2, one dissolved shape) is read the same way.
2. **One row per parcel.** Seven parcels appear twice (the same `LOC_ID` in two overlapping districts). The same land cannot hold both, so each keeps its row with the larger capacity (360 units dropped).
3. **District or parcels, never both.** Summed over a district's parcels, `F_L_M__` reproduces the district's `MFUC_DIST` (490 districts with parcels: 534,479 against 534,680). So each district is counted one way only:

   | `basis` | What is counted | Districts on 2026-11-25 |
   |---|---|---|
   | `parcels` | the sum of its parcels | 489 |
   | `district_outline` | no parcel in the atlas: its own `MFUC_DIST`, split by its outline | 4 (all report 0) |
   | `duplicate_code` | same zone code and town as another district: that district counts the parcels | 1 |
   | `parcels_no_district` | parcels whose zone code no district carries: counted on their own | 4 rows, 1,877 units |

   Four districts' parcels add up to less than their reported total (Freetown 102_SA1 −201, Mansfield 167MSROD-B2 −175, Norwood 220NC −174, Ipswich 144MOD2 −11). The parcel sum is counted; the gap is in `difference`.
4. **Split across tracts by area.** Each parcel (and each district counted by outline) is split across 2020 tracts by the share of its area in each, never by its centre (`features/planar.py`, plain NumPy). A shape whose corners all fall in one tract goes wholly to it (29,234 parcels). Otherwise it is drawn on a 40 × 40 grid over its bounding box (cells at least 0.5 m) and each cell is placed by its centre (750 parcels). Capacity, station-area capacity, `MDL_U_C`, `RB_RSDN` and acres are all split by the same shares.
5. **Slivers and unmatched area.** The tract outlines are the Census 1:500,000 cartographic file, generalised by tens of metres, so a thin piece is most likely the generalised line crossing a parcel. A piece under 5% of a shape (`min_piece_share`) is folded into the shape's largest piece. Area outside every tract (water, coast) is kept as unmatched, never dropped silently. On 2026-11-25: 223.1 units folded from slivers, 436.6 units unmatched.
6. **Conservation.** Capacity counted (535,601.0) = capacity in tracts (535,164.4) + unmatched (436.6). The tests check this identity.

### Result (read 2026-11-25)

- 159 towns, all of them "Compliant" in the atlas. The atlas holds only towns HLC found fully compliant; the other MBTA communities (interim, conditional or noncompliant) have no row. A tract not in `mbta_3a_capacity_tract` holds no compliant 3A district. That is not the same as "no 3A capacity will exist there": see `mbta_3a_status`.
- 464 tracts hold 535,164 gross units of modeled capacity on 15,497 parcel acres. 203 tracts hold more than one district.
- 311,299 units (58%) are on parcels the model places in a half-mile station area (`TRNSTST = Y`). That flag agrees with the atlas's own radii: 98.3% of flagged parcels have a corner inside them, 5% of unflagged ones do.
- The same parcels hold 128,061 homes today (`RB_RSDN`). Before the lot limits the model's capacity would be 925,176 (`MDL_U_C`).

### Station areas and walksheds

The pack's walkshed tables (`transit_walk_tract`) hold acres per tract, not shapes, so the capacity cannot be cut by the tool's own walksheds. The table instead carries `station_area_units`: the capacity the compliance model itself places inside its half-mile station areas (straight-line circles). A rule that needs capacity inside a walkshed can scale a tract's `station_area_units` by its walkshed acres; that is an approximation and is labelled as such.

## How the sewer table is made

1. **Read.** MassDEP's two polygon layers (non-public treatment works 280, public treatment works 331) and its table of systems not yet mapped (2) are read by object id with a fixed column list; counts match the service's own. Edition: PubV2025_10 (published October 2025, item updated 2025-12-15). The polygons stay in the local cache; only tract shares enter the pack.
2. **Placeholder circles.** A polygon whose isoperimetric ratio (4π × area ÷ perimeter², 1 for a circle) is at least 0.95 and whose area is under 200 acres is read as a placeholder circle. On this read nine shapes pass (ratios 0.955 to 0.988); the next roundest, a real service area, scores 0.898.
3. **Place homes.** Every 2020 block with homes is placed by its internal point, in order of evidence: a polygon the system confirmed, an unconfirmed polygon, a placeholder circle.
4. **Per tract.** `est_share` (inside an estimated polygon, circles left out), `est_confirmed_share`, `est_potw_share` (inside a public plant's municipal, regional or district area), `circle_only_share`, and `geometry_quality`: `polygon`, `approximate_circle` (only circles), `mixed` or `none`. Every row says `evidence_class = estimated service`.

### Result (read 2026-11-25)

Of 2,998,537 homes, 70.8% are inside an estimated service polygon and 46.9% inside a confirmed one; homes inside only a placeholder circle round to 0.0%. Of 1,605 tracts with homes: 1,498 `polygon`, 4 `mixed`, 103 `none`.

## What the engine reads

- **TOD** (`scenarios/tod.py`, `existing_capacity`): `additional_legal_capacity = max(0, proposed − existing)` per tract on the same developable land, in gross homes. Existing = `capacity_units` (inside the run's distance: `station_area_units` × min(1, eligible ÷ half-mile land), plus the rest × the share of the tract beyond the half mile reached), on `parcel_acres` split the same way. `existing_units` and `modeled_units_before_limits` are context, never added. This replaced the acre share `tod.state_zoned_overlap_share`, now variant `tod_ma_acre_share`. Method: [tod.md](../methods/tod.md), "Land already zoned by right".
- **Capacity screen** (`engine/capacity.py`): `est_share` and `circle_only_share` split the homes with no official sewer answer into estimated inside, estimated outside and unknown (circles). Estimated is its own evidence class, drawn from `capacity.estimated_sewer_served_share` and `capacity.estimated_outside_served_share`, never verified; `infra_tract` stays as it is. Coverage is reported as verified / estimated / unknown. Method: [capacity.md](../methods/capacity.md).

## Terms of use

- **Atlas parcels and radii**: "Data are public" (MassGIS item licence), meant for policy analysis, research and public understanding of zoning. HLC: not a legal record of permitted uses; the compliance determination letter is dispositive. Only sums enter the pack.
- **MassDEP sewer areas**: a data limitations disclaimer and a requested citation (MassDEP, Water Utility Resilience Program, 2025, *Publicly-Owned Treatment Work and Non-Publicly-Owned Sewer Service Areas* (PubV2025_10)). Redistribution is not addressed, so the polygons are not redistributed: only per-tract shares of homes.
- **Scripted access**: no robots.txt rule speaks against the services (checked 2026-11-25 before any data request); both are public ArcGIS REST services.

## Limits

- Modeled capacity is the compliance model's estimate of what the zoning allows, not what owners will build or when. It is gross; it does not subtract homes already there.
- Only fully compliant towns are in the atlas. Missing towns are unknown, not zero.
- Tract lines are generalised (1:500,000), so splits near a tract line are approximate; slivers under 5% are folded.
- MassDEP's areas are estimates: some are buffered sewer lines (300 ft), some whole town boundaries (towns at least 97% served), many unconfirmed. Inside an area is not the same as connected, and nothing here measures spare treatment or pipe capacity.
- Homes are placed by block internal points, so a large block counts wholly in or out.

## Tests

`tests/test_ma_capacity.py`: a parcel inside one tract; a parcel split 1/4 to 3/4 by area (not by centre); unmatched area and sliver folding; capacity conserved through the split; a district never counted on top of its parcels (and the duplicate-code and no-district cases); duplicate parcels; missing values stay missing; circle ratio; the sewer evidence class and circle flag; the fetch reads by id chunks, records its stamp and fails when incomplete; every column described. No network:

```
.venv/bin/python -m pytest -q tests/test_ma_capacity.py -p no:cacheprovider
```
