# Zoning districts: reviewed rules and the lots they reach (`law/zone_rules.py`, `law/rule_weights.py`, `law/zone_build.py`)

Code: `src/visionhousing/law/zone_rules.py`, `src/visionhousing/law/rule_weights.py`, `src/visionhousing/law/zone_build.py`, `src/visionhousing/data/sources/zoning_layers.py`.
Settings: `assumptions/law/zone_weights.yaml`. Reviewed rows: `assumptions/law/zone_rules.yaml`.
Command: `visionhousing zone-rules --state PA` (also the pack add-on step "zoning districts").
Tests: `tests/test_zone_rules.py`, `tests/test_rule_area_weights.py`, `tests/test_zone_build.py`.

## What it does, in plain words

The municipal law table ([law.md](law.md)) holds one rule per municipality. But a city has many **zoning districts** (mapped areas, each with its own uses and standards), **overlays** (a second map laid over the districts that changes some rules) and rules that change over time. A state reform changes different lots in different districts. This pilot keeps the rule at that finer grain for a small contrasting sample of Pennsylvania, and counts the lots each rule reaches:

| Jurisdiction | Kind | District map | Rule text read |
|---|---|---|---|
| Philadelphia | first-class city | City base districts (current, and the December 2021 snapshot) and overlays | The City's ADU checklist (guidance). The Zoning Code itself is only on a commercial code host: **not read by script** |
| Pittsburgh | second-class city | City districts and the Parking Reduction overlay | Ordinance 31 of 2026, from the City's legislative system (verified) |
| Frankstown Township (Blair) | rural township | none published as data | The township's own ordinance (verified, with a clause barring unlisted uses) |

It is a sample, not the state: the three hold 4.1% of Pennsylvania's single-family lots.

## Terms

- **Zone**: one district plus the overlays a rule names. **Zone version**: a zone on one interval of time between rule changes.
- **Prototype**: the lot every benchmark starts from, a lot with one existing single-family home (`sf_lot`).
- **Opportunity class**: what the benchmark changes on that lot, comparing what today's rule allows with what the benchmark needs:
  - `known_by_right`: allowed by right and the benchmark changes nothing (the pure legalization increment is zero);
  - `conditions_relaxed`: allowed by right, but the benchmark drops a condition (size, form, owner rule, parking), or a condition was not read;
  - `newly_eligible`: not allowed today (prohibited, or not listed where a verified clause bars unlisted uses);
  - `discretionary`: allowed today only after a hearing;
  - `unresolved`: the rule needed is unknown.
  "Known by right" in the shares below is `known_by_right` + `conditions_relaxed`, with the split shown.
- **Unmatched**: block area inside no district outline (streets, water, land the map gives another municipality).
- **Sliver**: a piece under 2% of a block (`split.min_piece_share`), folded into the block's largest piece and reported.

## The `zone_rule` table

One row per reviewed row of `assumptions/law/zone_rules.yaml`: jurisdiction, district (`*` for every district), overlay (`none` for the base rule), prototype, `valid_from`, `valid_to`, review state, document and section, and the rule fields: use permission, review path, homes by right, approval path for two or more homes, density or floor area, height, smallest lot, ADU permission, ADU forms and largest size, owner occupancy, ADUs per lot, smallest lot for an ADU, parking per home and per ADU, how an overlay changes parking, utility conditions and the clause on unlisted uses. Rules of the file, checked when it loads:

- **Every field is stated.** `unknown` means nobody read it, or the text does not answer it. It is never "no". An overlay row may say `inherit` (the overlay does not change that field).
- **Not listed is not prohibited.** `not_listed` counts as prohibited only with `exclusivity_clause: verified`, and as a hearing route only with `hearing_route`; otherwise the class is `unresolved`.
- **Review states.** `verified_code` (the enacted text, read by script from the jurisdiction's own site or legislative system), `guidance` (the jurisdiction's own published guidance, not the code), `unread` (only `unknown` allowed).
- **Dates.** A row whose source does not state its start (`valid_from_unknown`) counts only from its `reviewed_on` date, as in the law timeline; adoption never stands in for effect. Pittsburgh's rows name their timeline records and the test suite checks the dates agree.
- **No rule from a district's name.** A district code is the map's own code; nothing is inferred from what it is called.

**Resolution** (`zone_rules.resolve`): the base row of the district (an exact code beats `*`; then the latest start), then each overlay row of an overlay the lot lies in, in ascending `precedence` and then overlay code, each replacing the fields it states. No base row means the district is missing from the table: every field is `unknown` and the lots stay in the denominator as `unresolved`.

What the rows say (read 2026-11-24 to 2026-11-26):

- **Philadelphia.** RSA-5 and CMX-1: ADUs by right, per the L&I checklist (Rev 3.2022, re-read by script from phila.gov with the same fingerprint): owner on the lot with a deed restriction, one per lot, at most 800 sq ft, inside the main building or in a detached accessory building that already existed, lots of at least 1,600 sq ft. So `conditions_relaxed` for the ADU benchmark (no new detached unit, owner rule). The checklist sends lots in /NE and /FNE, and in /FDO, /VDO, /NDO and /EDO, to a flow chart drawn as a picture, which a text reader cannot read: those overlays make the ADU answer `unknown`. Every other district, and every rule that lives only in the code (use tables § 14-602, dimensions § 14-701, parking § 14-802), is `unread`. The City links its own code to a commercial code host; its legislative system's web API answers 403 to scripts. Bill 250042 (ADUs) is pending and not applied.
- **Pittsburgh.** From October 29, 2026 (Ordinance 31): ADUs by right on any lot whose primary use is residential, in every district; up to 1,000 sq ft, detached allowed, two per lot, no owner rule; no automobile parking minimum, so the Parking Reduction overlay no longer changes anything. Before it, ADUs only inside an ADU overlay whose outline is not published as data: unknown, and its start is not stated, so it applies on no date. What each district allows besides (homes per lot, height) is in Title Nine on a commercial code host: not read.
- **Frankstown.** No district lists an ADU-type use and unlisted uses "are specifically prohibited" (preamble item 4): `newly_eligible` in every district. Two parking spaces per home. Homes per lot by district cannot be placed on lots without a district map: unknown.

## The `rule_area_weights` table

How many lots each zone version reaches (`law/rule_weights.py`):

1. **Block weights.** A block's single-family lots are its tract's ACS one-unit detached homes times the block's share of the tract's 2020 housing units, the same split the law coverage uses. Its 2020 homes and Census land area come with it. The jurisdiction of a block is its county subdivision in the 2020 block assignment file.
2. **Area split, never a centre point.** Each block, the district outlines and the overlay outlines are drawn on a small flat grid over the block (40 cells along its longer side, none under 0.5 m), in one equirectangular projection in metres centred on the jurisdiction. A cell belongs to the district whose outline holds its centre; a block's share in each district and overlay set is its cells there over its cells inside any district. The homes are spread over the block's zoned part; the unmatched area is reported. A block with no zoned cell goes whole to the unresolved bucket. A block too small for any cell at 0.5 m goes to the district holding most of its corners. Two different district codes on the same cell are counted as overlap (the later outline in the layer is kept). A jurisdiction with no district map is one whole-jurisdiction zone.
3. **Zone versions.** Overlays no rule names are dropped from the zone key (they cannot change an answer). Each zone is resolved on every interval of the 2027-2031 window between rule changes; a rule that starts mid-window gives each version its share of the window's days (`window_share`).
4. **Rows.** `level = zone` (jurisdiction, map, period, district, overlays, rules used, review state, interval, benchmark, class, why, lots, homes, land, `weight`), `level = tract` (tract, benchmark, class: lots and share of the tract's lots), `level = jurisdiction` (lots, homes, blocks, unmatched area share, sliver share, overlap share, lots of unresolved blocks, and for an older map the lots whose district changed). The current map is `period = window`; the December 2021 Philadelphia map is `period = map_history`, classified with the same rules, so a difference between the two is due to remapping alone.

**Checks** (`check_weights`, run by the command and the tests): the zone weights of each jurisdiction, map, period and benchmark sum to 1 (within the pack's 4-decimal rounding); the zone rows' lots add up to the jurisdiction's lots; every block's pieces sum to 1; the window shares of each zone sum to 1; every class is one of the five.

## Results (run 2026-11-26, maps read that day)

Denominator: single-family lots (ACS 2020-2024 one-unit detached homes split by 2020 block housing units). Pennsylvania has 3,287,467.

| Jurisdiction | Lots | 2020 homes | Blocks with homes | Unmatched area | Slivers |
|---|---|---|---|---|---|
| Philadelphia | 63,292 | 726,797 | 14,486 | 27.6% (district outlines stop at the street line) | 0.09% |
| Pittsburgh | 70,572 | 157,695 | 5,946 | 0.05% | 0.23% |
| Frankstown | 2,508 | 3,211 | 166 | none (no map) | none |

Shares of each jurisdiction's lots, window 2027-2031:

| Benchmark | Philadelphia | Pittsburgh | Frankstown |
|---|---|---|---|
| ADU | by right with conditions relaxed 18.9%; unresolved 81.1% | known by right 100% | newly eligible 100% |
| Missing middle | unresolved 100% | unresolved 100% | unresolved 100% |
| Transit density | unresolved 100% | unresolved 100% | unresolved 100% |
| Parking | unresolved 100% | known by right 100% (no minimum) | conditions relaxed 100% (2 spaces) |

In Philadelphia, 11,975 lots rest on guidance (RSA-5: 11,747; CMX-1: 228); 9,758 more are in RSA-5 or CMX-1 but inside an overlay the checklist sends to its picture flow chart; 41,559 are in districts nobody has read, 17 of them in blocks outside every district. Between the December 2021 map and today's, the district changed under 977 lots (1.5%); with the same rules the 2021 map gives 11,930 lots by right with conditions, against 11,975 today. In Pittsburgh, 3,606 of the 70,572 lots are inside the Parking Reduction overlay, which no longer changes anything.

**State output.** As shares of Pennsylvania's single-family lots, the sample's ADU lots are: known by right 2.15%, by right with conditions relaxed 0.36%, newly eligible 0.08%, discretionary 0%, unresolved 1.56%; 95.85% of the state is outside the sample and not resolved by this table. Parking: known by right 2.15%, conditions relaxed 0.08%, unresolved 1.93%. Missing middle and transit density: 4.15% unresolved.

**Coverage gained.** Lots with a zone-level ADU answer: 73,080 on verified text (2.22% of the state's single-family lots; Pittsburgh and Frankstown, already verified at the municipal level, now placed district by district) and 11,975 on guidance (0.36%, new: Philadelphia had only a draft municipal row). No new verified coverage for missing middle, transit density or parking: the use tables, height limits and parking schedules of both cities are on commercial code hosts.

**The law-changes view (U13).** That view counts only verified readings. With the zone-level answers put in place of the sample municipalities' single answers, its Pennsylvania ADU shares do not move (already allowed 2.15%, condition changed 6.32%, newly allowed 1.10%, unresolved 90.44%): the district-level answers agree with the verified municipal rows, a consistency check. Counting the Philadelphia guidance as a sensitivity would move 11,975 lots from unresolved to "condition changed" (6.32% to 6.68%; unresolved 90.44% to 90.07%). Missing middle and parking do not move. Since 2026-11-26 the page offers this as an opt-in district view (`view=districts`, and `view=districts_guidance` for the sensitivity); see the next section. The default view is unchanged.

## Engine use: named ADU variants and the district view

Code: `src/visionhousing/engine/zone_scope.py`. Settings: `assumptions/law/zone_scope.yaml`. Tests: `tests/test_zone_scope.py`.

The benchmark does not read these tables, and its headline does not change. Following the rule "keep every option, one headline per reform", the district reading is shown beside it as two **named variants** of the ADU reform (Alternatives table, Pennsylvania only):

| Variant | Rows counted | Question |
|---|---|---|
| `adu_zoning_districts` | `verified_code` only | In the three sample jurisdictions, if the already-legal share comes from their zoning districts, how many ADUs does the benchmark add? |
| `adu_zoning_districts_guidance` | `verified_code` and `guidance` (labelled sensitivity) | The same, also counting Philadelphia's 2022 L&I ADU checklist (City guidance, not the code) |

**How a tract's already-legal share is formed.** For each tract and draw:

`legal = (1 - z) x base + sum over classes k of z_k x legal_k`

- `z_k` = the tract's single-family detached lots in the sample and in class `k` (the table's `level = tract` rows) / the tract's ACS one-unit detached homes, times the detached lots' share of all lots the scenario reaches (one-unit attached and 2-4 unit lots are not in the table, so they keep the benchmark's share). `z` = the sum of the `z_k`.
- `base` = the benchmark's own share for that tract (the statewide draw; a county's read-local-law rule where one applies; no Pennsylvania county passes the hooks' 50% bar today).
- `legal_k`: known by right 1 (already legal, nothing changes); conditions relaxed `1 - adu.conditions_exposure_share`; newly eligible and discretionary 0 (fully exposed); unresolved the statewide draw of `adu.already_legal_share` (the benchmark's labelled range, never the base).
- A class resting on a row the variant does not count (guidance, in the verified variant) is read as unresolved: unknown stays unknown. The tract rows carry no review state, so the counted share of each class is taken from the jurisdiction's zone rows; in today's table every class is all counted or all not, so this is exact (the run notes say otherwise if not).
- Outside the sample nothing changes. A pack without the table gives the benchmark draw for draw (tested on Testland).

The variant's records add `already_legal_share_zoning_districts` (statewide reached lots), `zoning_districts_sample_lots` and `zoning_districts_sample_legal_share` (the sample alone), each with its formula and ids; the run notes cite the table's SHA-256 and the class mix.

**Results (PA, seed 0, 2,000 draws, run 2026-11-26; table sha256 `92baf1313345`).** Added ADUs authorized 2027-2031, P50 (P10-P90):

| Run | Added ADUs | Paired change against the benchmark (same draws) | Draws below the benchmark |
|---|---|---|---|
| Benchmark (unchanged) | 8,011 (3,481-15,671) | | |
| `adu_zoning_districts` | 7,931 (3,448-15,507) | -85 (-171 to -37) | 100% (MC SE 0.0%) |
| `adu_zoning_districts_guidance` | 7,930 (3,447-15,505) | -86 (-172 to -37) | 100% (MC SE 0.0%) |

Why lower: Pittsburgh's 70,572 detached lots are known by right under Ordinance 31, so the benchmark adds nothing there, where the benchmark range counts only part of them as already legal; Frankstown's 2,508 lots become fully exposed (newly eligible), which adds far fewer. In the verified variant Philadelphia stays at the statewide draw (its rows are guidance or unread); counting the guidance moves its RSA-5 and CMX-1 lots to "conditions relaxed" and changes the result by about one more ADU (P50). Sample reached lots (detached, after the lot-count correction): 122,612 (119,206-126,080); their already-legal share 58.8% (57.3%-60.3%) verified, 60.1% (58.3%-61.9%) with guidance.

**Law-changes district view.** `GET /api/v1/states/PA/law-changes?r=adu&view=districts` replaces each sample municipality's single answer with its mix of district answers (`jurisdiction_mix`: lots per class, homes for a residents denominator; a class resting on an uncounted row is unresolved), scaled to the municipality's own weight so the denominator does not change. Capacity per lot: known by right and conditions relaxed 0, newly eligible and discretionary the reform's full gain, unresolved 0 to the full gain. The table's benchmark for each of the page's rules: ADU category -> `adu`, missing middle -> `missing_middle`, parking -> `parking`; fees keep the municipal answers. Pennsylvania ADU shares today (2026-11-26): the verified district view equals the default view (already allowed 2.15%, condition changed 6.91%, newly allowed 1.10%, unresolved 89.85%); `view=districts_guidance` moves Philadelphia's 11,975 guidance lots to "condition changed" (6.91% -> 7.27%; unresolved 89.85% -> 89.49%). Missing middle and parking do not move. (These shares moved from the P5 figures above because other municipal readings were added since.)

## Worked trace: one citation to the state output

1. **Citation.** Pittsburgh Zoning Code § 912.08.C.2 as amended by Ordinance 31 of 2026, at offset 237,803 of the version 7 text (SHA-256 `b6fcbc49…`): "Accessory Dwelling Units are permitted to be located on any lot where a primary use is a Residential use…". Timeline record `pgh_ord31_adu_effective`, in force from October 29, 2026.
2. **Rule.** Row `pgh_ord31` (every district): ADU by right, detached allowed, up to 1,000 sq ft, no owner rule, no parking for the ADU. Every benchmark test passes: `known_by_right`.
3. **Weight.** Pittsburgh's 5,946 blocks with homes split by area among 1,068 district outlines: 66,966 lots under `pgh_ord31` alone and 3,606 under `pgh_ord31` with the Parking Reduction overlay, 70,572 in all (the same 18.9% of Allegheny County's single-family lots as the SB 1346 place weights).
4. **State output.** 70,572 / 3,287,467 = 2.15% of Pennsylvania's single-family lots are known by right for the ADU benchmark from this sample. Without the row, those lots fall to `unresolved` (unknown, never "no"), and the state's known-by-right share from the sample is 0.

The same chain is a test: `test_trace_one_citation_to_the_state_output` in `tests/test_zone_rules.py`.

## Rights and distribution

| Layer | Retrieval | Internal analysis | Raw outlines redistributed | Derived counts redistributed | Attribution |
|---|---|---|---|---|---|
| Philadelphia base districts, 2021 snapshot, overlays | Allowed (City of Philadelphia License, "Public Use; Free") | Allowed | **No**: the City reserves all rights in the data; outlines stay in `data/raw` | Yes, as lot counts and shares; to be confirmed with the City before release | City of Philadelphia, Department of Planning and Development |
| Pittsburgh districts and Parking Reduction overlay | Allowed (informational use notice) | Allowed | **No**: the notice says nothing on redistribution; outlines stay in `data/raw` | Yes, as counts | City of Pittsburgh, Department of City Planning |
| TIGER/Line 2020 blocks | Public domain | Allowed | Allowed, but not needed | Yes | U.S. Census Bureau |
| Philadelphia L&I checklist; Pittsburgh Ordinance 31; Frankstown ordinance | Public documents | Read; quotes of at most 300 characters kept in the YAML | Not copied | Our own summaries | Each publisher |

robots.txt and terms were checked before any data request (2026-11-26): www.arcgis.com allows all; the services hosts publish no rule; opendataphilly.org allows all; phila.gov has no robots file; pittsburghpa.gov refuses scripts, so nothing is read from it (the City's map services are on ArcGIS Online). Commercial code hosts are never read.

## Limits

- A sample of three jurisdictions, 4.1% of the state's single-family lots. It shows the method and a contrast; it is not a statewide rate.
- Philadelphia's answer rests on City guidance, not the code; the overlay answers are in a picture the reader cannot read. Both cities' use tables and district standards are on commercial code hosts, so homes per lot, height and parking schedules stay unknown, and missing middle and transit density are unresolved everywhere in the sample.
- District outlines are the cities' own map data, not legal boundaries; Philadelphia's are self-reported. Block outlines include streets and water; homes are spread over a block's zoned part by area, not placed on parcels.
- Lots are ACS one-unit detached homes split by 2020 block housing units: rowhouses (one-unit attached) are not counted, as elsewhere in the law layer.
- A historic designation, a lot under 1,600 sq ft or an existing accessory building in Philadelphia are lot facts this table does not have.
- The benchmark does not read these tables; only the named variants `adu_zoning_districts` and `adu_zoning_districts_guidance` and the opt-in law-changes district view do. The table's lots are detached single-family lots, so Philadelphia's rowhouses keep the benchmark's share in the variants.

## How to run and check

```
visionhousing zone-rules --state PA     # fetch the maps if missing, add the two tables, print shares, checks and pack size
.venv/bin/python -m pytest -q tests/test_zone_rules.py tests/test_rule_area_weights.py tests/test_zone_build.py -p no:cacheprovider
```

The five difficult intersections in `tests/test_rule_area_weights.py`: a tract split between municipalities with an L-shaped block whose centre lies in its minority district (area gives 60/40, a centre point would give 0/100); a station block half in a river (unmatched area reported, homes on the zoned half) and a block wholly in it (unresolved bucket); an overlay that removes the base parking minimum inside it only; a district code with no reviewed row (unresolved, kept in the denominator); a rule that starts on July 1, 2029 (two versions weighted by their days in the window).
