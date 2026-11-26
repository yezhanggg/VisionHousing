"""Profile section ``affordability``: who is cost-burdened, what incomes are, what is assisted.

Plain-language summary
----------------------
Three separate measures, kept apart on purpose:

1. **Cost burden** (HUD CHAS 2018-2022): the share of renter households paying
   more than 30% of income for housing ("cost-burdened") or more than 50%
   ("severely"), by income as a share of HUD's area median family income
   (AMI). Lead number: the cost-burdened share of renters at or below 80% of
   AMI. Owners are shown for counties and the state.
2. **Income limits and affordable rents** (HUD income limits, fiscal year
   2026): the AMI for a family of four, the 50% and 80% limits, and the
   highest monthly housing cost (rent plus tenant-paid utilities) a two-bedroom
   household at 50% and 80% of AMI can pay at 30% of income
   (:func:`visionhousing.features.affordability.max_rent`). A tract shows its
   county's HUD area.
3. **Income-restricted and assisted homes** (HUD lists): homes with
   project-based rental assistance, public housing and tax-credit
   restrictions; units whose contract or restriction ends within 5 and 10
   years; households using vouchers. Programs overlap, so the total is an
   upper bound.

Also: the share of renter homes whose rent is affordable at 50% of AMI or
less, the share of renters in buildings built before 1980, and, as state
context with credit, NLIHC's count of affordable and available homes per 100
extremely low-income renters.

How shares are worked out: households in the numerator bands / households in
the denominator bands, from the same table. For counties and the state the
margin uses the Census Bureau's formula for a proportion with margins of sums
from the square root of the sum of squared cell margins; tract rows carry no
margins. Renters with no or negative income ("not computed") are left out of
cost-burden shares.

Charts: renter cost burden in three income groups (one stacked bar each), renter
homes by the income their rent is affordable to, assisted homes by program,
and contract units by when their contract ends.

What it does not do: predict displacement, rent changes or the loss of any
home. Severe burden among lower-income renters is context, not a forecast.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Callable, Sequence

from visionhousing.data.sources import state_fips as to_state_fips
from visionhousing.features import affordability as af
from visionhousing.profile.charts import items_from_counts
from visionhousing.profile.context import Area, Measure, MissingDataError, ProfileContext
from visionhousing.profile.format import fmt
from visionhousing.profile.model import Chart, ChartCompare, Section
from visionhousing.profile.sections._common import FactMaker, finish, point, sentence

KEY = "affordability"
TITLE = "Cost and affordability"
CHAS = "hud_chas"
IL = "hud_income_limits"
MF = "hud_mf_assisted"
CHAS_YEARS = "2018-2022"
IL_YEAR = "FY2026"
BEDROOMS = 2
"""Home size used for the affordable-rent facts (a common family rental)."""

BURDENED = ("30_50", "gt50")
COMPUTED = ("lt30", "30_50", "gt50")
GROUPS = tuple(g for g, _, _ in af.TRACT_GROUPS)


# --------------------------------------------------------------------------- reading


def _code(area: Area) -> str:
    """The area's code in the affordability tables (the state's two-digit code for the state)."""
    return to_state_fips(area.geoid) if area.level == "state" else area.geoid


def _chas_row(ctx: ProfileContext, area: Area) -> tuple[dict[str, object], bool] | None:
    """The area's CHAS row and whether it is the wide (county/state) layout."""
    if area.single:
        table = ctx.table("afford_chas_tract")
        if table is None:
            raise MissingDataError("no afford_chas_tract table")
        hit = table[table["geoid"].astype(str) == area.geoid]
        return (hit.iloc[0].to_dict(), False) if len(hit) else None
    table = ctx.table("afford_chas")
    if table is None:
        raise MissingDataError("no afford_chas table")
    hit = table[table["geoid"].astype(str) == _code(area)]
    return (hit.iloc[0].to_dict(), True) if len(hit) else None


def _num(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def chas_sum(
    ctx: ProfileContext, area: Area, keys: Sequence[str]
) -> tuple[float | None, float | None]:
    """Sum of CHAS columns for an area, with its margin (``None`` for a tract).

    ``keys`` are collapsed columns
    (:data:`~visionhousing.features.affordability.COLLAPSED`) or raw
    ``afford_chas`` columns.
    """
    found = _chas_row(ctx, area)
    if found is None:
        return None, None
    row, wide = found
    if not wide:
        vals = [_num(row.get(k)) for k in keys]
        return (None, None) if any(v is None for v in vals) else (float(sum(vals)), None)  # type: ignore[arg-type]
    cells = [c for k in keys for c in af.COLLAPSED.get(k, (k,))]
    vals = [_num(row.get(c)) for c in cells]
    if any(v is None for v in vals):
        return None, None
    moes = [_num(row.get(f"{c}_moe")) or 0.0 for c in cells]
    return float(sum(vals)), math.sqrt(sum(m * m for m in moes))  # type: ignore[arg-type]


def chas_share(
    ctx: ProfileContext, num: Sequence[str], den: Sequence[str]
) -> Callable[[Area], Measure]:
    """A share measure: households in ``num`` / households in ``den`` (``num`` inside ``den``)."""

    def measure(area: Area) -> Measure:
        n, n_moe = chas_sum(ctx, area, num)
        d, d_moe = chas_sum(ctx, area, den)
        if n is None or d is None or d <= 0:
            return Measure(None)
        p = n / d
        if n_moe is None or d_moe is None:
            return Measure(p)
        under = n_moe**2 - p**2 * d_moe**2
        if under < 0:
            under = n_moe**2 + p**2 * d_moe**2
        return Measure(p, math.sqrt(under) / d)

    return measure


def chas_count(ctx: ProfileContext, keys: Sequence[str]) -> Callable[[Area], Measure]:
    """A count measure: households in ``keys``, with the margin of the sum."""

    def measure(area: Area) -> Measure:
        v, m = chas_sum(ctx, area, keys)
        return Measure(v, m)

    return measure


def _renter_keys(groups: Sequence[str], burdens: Sequence[str]) -> list[str]:
    return [f"renter_{g}_{b}" for g in groups for b in burdens]


def _owner_keys(burdens: Sequence[str]) -> list[str]:
    return [f"owner_{b}_{c}" for b, _ in af.INCOME_BANDS for c in burdens]


ALL_RENTERS = [*_renter_keys(GROUPS, COMPUTED), "renter_nc"]


def _limits_row(ctx: ProfileContext, area: Area) -> dict[str, object]:
    if area.level == "state":
        raise MissingDataError("income limits are set per HUD area, not per state")
    table = ctx.table("afford_income_limits")
    if table is None:
        raise MissingDataError("no afford_income_limits table")
    county = area.geoid[:5]
    hit = table[table["county"].astype(str).str.zfill(5) == county]
    if hit.empty:
        raise MissingDataError(f"no income limits for {county}")
    return hit.iloc[0].to_dict()


def _limit(ctx: ProfileContext, column: str) -> Callable[[Area], Measure]:
    def measure(area: Area) -> Measure:
        row = _limits_row(ctx, area)
        return Measure(_num(row.get(column)), note=f"HUD area: {row.get('hud_area_name')}")

    return measure


def _rent(ctx: ProfileContext, pct: int) -> Callable[[Area], Measure]:
    settings = af.load_settings()

    def measure(area: Area) -> Measure:
        row = _limits_row(ctx, area)
        value = af.max_rent(row, pct, BEDROOMS, settings=settings)
        size = settings["persons_per_bedroom"][BEDROOMS]
        note = (
            f"{settings['rent_share_of_income']:.0%} of the {pct}% income limit for "
            f"{size:g} people (1.5 per bedroom) / 12; HUD area: {row.get('hud_area_name')}"
        )
        return Measure(round(value), note=note)

    return measure


def _assisted_row(ctx: ProfileContext, area: Area) -> dict[str, object]:
    table = ctx.table("afford_assisted")
    if table is None:
        raise MissingDataError("no afford_assisted table")
    hit = table[(table["level"] == area.level) & (table["geoid"].astype(str) == _code(area))]
    if hit.empty:
        if area.single:  # a tract with no assisted homes is left out of the table
            return {c: 0.0 for c in af.ASSISTED_SUMS} | {"as_of": str(table["as_of"].iloc[0])}
        raise MissingDataError(f"no afford_assisted row for {area.geoid}")
    return hit.iloc[0].to_dict()


def _assisted(ctx: ProfileContext, columns: Sequence[str]) -> Callable[[Area], Measure]:
    def measure(area: Area) -> Measure:
        row = _assisted_row(ctx, area)
        vals = [_num(row.get(c)) for c in columns]
        return Measure(None if any(v is None for v in vals) else sum(vals))  # type: ignore[arg-type]

    return measure


TOTAL_ASSISTED = ("mf_units", "ph_units", "lihtc_li_units")
AT_RISK = {w: (f"at_risk_{w}y",) for w in (5, 10)}
"""Ending contract units + ending tax-credit units, a property on both lists counted once."""


def _assisted_share(ctx: ProfileContext) -> Callable[[Area], Measure]:
    count = _assisted(ctx, TOTAL_ASSISTED)

    def measure(area: Area) -> Measure:
        units = count(area).value
        renters, _ = chas_sum(ctx, area, ALL_RENTERS)
        if units is None or not renters:
            return Measure(None)
        return Measure(units / renters)

    return measure


# --------------------------------------------------------------------------- charts


def _chart(
    chart_id: str,
    kind: str,
    title: str,
    unit: str,
    rows_for: Callable[[Area], list[tuple[str, str, float | None, float | None]] | None],
    place: Area,
    other: Area | None,
    sources: tuple[str, ...],
    vintage: str,
    note: str,
) -> Chart | None:
    try:
        rows = rows_for(place)
    except MissingDataError:
        return None
    if not rows or all(r[2] is None for r in rows):
        return None
    items, total = items_from_counts(rows)
    if not total:
        return None
    compare = None
    if other is not None:
        try:
            theirs = rows_for(other)
        except MissingDataError:
            theirs = None
        if theirs and any(r[2] for r in theirs):
            compare = ChartCompare(other.name, items_from_counts(theirs)[0])
    return Chart(
        id=f"{KEY}.{chart_id}",
        kind=kind,
        title=title,
        unit=unit,
        items=items,  # type: ignore[arg-type]
        total=total,
        compare=compare,
        source_ids=sources,
        vintage=vintage,
        note=note,
    )


def _burden_rows(ctx: ProfileContext, group: str):
    labels = dict(af.BURDEN_BANDS)

    def rows(area: Area):
        out = []
        for b in COMPUTED:
            v, m = chas_sum(ctx, area, [f"renter_{group}_{b}"])
            out.append((b, labels[b], v, m))
        return out

    return rows


def _rent_rows(ctx: ProfileContext):
    def rows(area: Area):
        out = []
        for key, label, _ in af.RENT_BANDS:
            v, m = chas_sum(ctx, area, [key])
            out.append((key, label, v, m))
        return out

    return rows


def _program_rows(ctx: ProfileContext):
    labels = (
        ("mf_units", "Project-based rental assistance"),
        ("ph_units", "Public housing"),
        ("lihtc_li_units", "Tax-credit (LIHTC) low-income units"),
    )

    def rows(area: Area):
        row = _assisted_row(ctx, area)
        return [(k, label, _num(row.get(k)), None) for k, label in labels]

    return rows


def _expiry_rows(ctx: ProfileContext):
    def rows(area: Area):
        row = _assisted_row(ctx, area)
        total = _num(row.get("mf_units")) or 0.0
        e5 = _num(row.get("mf_units_exp_5y")) or 0.0
        e10 = _num(row.get("mf_units_exp_10y")) or 0.0
        past = _num(row.get("mf_units_exp_past")) or 0.0
        if total <= 0:
            return None
        later = max(total - e10 - past, 0.0)
        return [
            ("within_5y", "Ends within 5 years", e5, None),
            ("5_10y", "Ends in 5-10 years", e10 - e5, None),
            ("later", "Ends later, or no date listed", later, None),
            ("past", "End date passed (renewal pending)", past, None),
        ]

    return rows


# --------------------------------------------------------------------------- build


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``affordability`` section for a county or tract."""
    place, other = ctx.place(geoid)
    if ctx.table("afford_chas") is None and ctx.table("afford_assisted") is None:
        raise MissingDataError(f"the {ctx.state} pack has no affordability tables")
    fm = FactMaker(ctx, KEY, place, other)
    low_groups = ("le30", "30_80")
    lead = fm.add(
        "lowinc_renters_burdened_share",
        "Renters at or below 80% of AMI paying over 30% of income for housing",
        "share",
        CHAS,
        CHAS_YEARS,
        chas_share(ctx, _renter_keys(low_groups, BURDENED), _renter_keys(low_groups, COMPUTED)),
        note="AMI = HUD area median family income; renters with no or negative income left out",
    )
    fm.add(
        "renters_burdened_share",
        "Renter households paying over 30% of income for housing",
        "share",
        CHAS,
        CHAS_YEARS,
        chas_share(ctx, _renter_keys(GROUPS, BURDENED), _renter_keys(GROUPS, COMPUTED)),
    )
    fm.add(
        "renters_severe_share",
        "Renter households paying over 50% of income for housing",
        "share",
        CHAS,
        CHAS_YEARS,
        chas_share(ctx, _renter_keys(GROUPS, ("gt50",)), _renter_keys(GROUPS, COMPUTED)),
    )
    fm.add(
        "eli_renters_severe_share",
        "Renters at or below 30% of AMI paying over 50% of income",
        "share",
        CHAS,
        CHAS_YEARS,
        chas_share(ctx, ["renter_le30_gt50"], _renter_keys(("le30",), COMPUTED)),
    )
    fm.add(
        "renters_le80",
        "Renter households at or below 80% of AMI",
        "households",
        CHAS,
        CHAS_YEARS,
        chas_count(ctx, _renter_keys(low_groups, COMPUTED)),
        compare=False,
        note="with a computed cost burden",
    )
    if not place.single:
        fm.add(
            "owners_burdened_share",
            "Owner households paying over 30% of income for housing",
            "share",
            CHAS,
            CHAS_YEARS,
            chas_share(ctx, _owner_keys(BURDENED), _owner_keys(COMPUTED)),
        )
    fm.add(
        "lower_rent_share",
        "Renter homes with rents affordable at 50% of AMI or less",
        "share",
        CHAS,
        CHAS_YEARS,
        chas_share(ctx, ["rent_le30", "rent_30_50"], [k for k, _, _ in af.RENT_BANDS]),
        note="of renter homes with a complete kitchen and plumbing (CHAS 15C)",
    )
    fm.add(
        "older_rental_share",
        "Renter households in buildings built before 1980",
        "share",
        CHAS,
        CHAS_YEARS,
        chas_share(ctx, ["older_rental"], ALL_RENTERS),
        note="CHAS Table 12",
    )
    at = "county" if place.single else None
    fm.add(
        "ami",
        "Area median family income (HUD, family of four)",
        "dollars per year",
        IL,
        IL_YEAR,
        _limit(ctx, "median"),
        compare=False,
        measured_at=at,
    )
    fm.add(
        "limit50_4",
        "Income limit at 50% of AMI, family of four",
        "dollars per year",
        IL,
        IL_YEAR,
        _limit(ctx, "l50_4"),
        compare=False,
        measured_at=at,
    )
    fm.add(
        "limit80_4",
        "Income limit at 80% of AMI, family of four",
        "dollars per year",
        IL,
        IL_YEAR,
        _limit(ctx, "l80_4"),
        compare=False,
        measured_at=at,
    )
    rent50 = fm.add(
        "rent_2br_50",
        "Highest affordable monthly housing cost, 2 bedrooms, 50% of AMI",
        "dollars per month",
        IL,
        IL_YEAR,
        _rent(ctx, 50),
        compare=False,
        measured_at=at,
    )
    fm.add(
        "rent_2br_80",
        "Highest affordable monthly housing cost, 2 bedrooms, 80% of AMI",
        "dollars per month",
        IL,
        IL_YEAR,
        _rent(ctx, 80),
        compare=False,
        measured_at=at,
    )
    as_of = ""
    with contextlib.suppress(MissingDataError):
        as_of = str(_assisted_row(ctx, place).get("as_of") or "")
    year = f"as listed on {as_of}" if as_of else "latest"
    total = fm.add(
        "assisted_units",
        "Income-restricted and assisted homes (upper bound)",
        "units",
        MF,
        year,
        _assisted(ctx, TOTAL_ASSISTED),
        compare=False,
        note="contract + public housing + tax-credit low-income units; programs overlap",
    )
    fm.add(
        "assisted_share",
        "Assisted homes per renter household",
        "share",
        MF,
        year,
        _assisted_share(ctx),
        note="assisted homes (upper bound) / CHAS renter households",
    )
    for name, label, cols in (
        ("mf_units", "Homes with project-based rental assistance", ("mf_units",)),
        ("ph_units", "Public housing homes", ("ph_units",)),
        ("lihtc_li_units", "Tax-credit (LIHTC) low-income homes", ("lihtc_li_units",)),
        ("hcv_households", "Households using a Housing Choice Voucher", ("hcv_households",)),
    ):
        src = {
            "ph_units": "hud_public_housing",
            "lihtc_li_units": "hud_lihtc",
            "hcv_households": "hud_hcv_tract",
        }.get(name, MF)
        unit = "households" if name == "hcv_households" else "units"
        fm.add(name, label, unit, src, year, _assisted(ctx, cols), compare=False)
    risk5 = fm.add(
        "at_risk_5y",
        "Assisted homes whose contract or restriction ends within 5 years",
        "units",
        MF,
        year,
        _assisted(ctx, AT_RISK[5]),
        compare=False,
        note=(
            "contract units ending, plus tax-credit restrictions reaching year 30, minus "
            "units on both lists (same property within 50 m with matching unit counts); "
            "an upper bound where rows could not be matched"
        ),
    )
    fm.add(
        "at_risk_10y",
        "Assisted homes whose contract or restriction ends within 10 years",
        "units",
        MF,
        year,
        _assisted(ctx, AT_RISK[10]),
        compare=False,
    )
    gap = af.gap_context(ctx.state) if not ctx.synthetic else None
    gap_fact = None
    if gap:
        gap_fact = fm.add(
            "gap_per_100_eli",
            "Affordable and available rental homes per 100 extremely "
            "low-income renter households (statewide, NLIHC)",
            "count",
            "nlihc_gap_2026",
            "2026 edition",
            lambda _a: Measure(
                float(gap["affordable_available_per_100_eli"]),
                note=f"quoted from {gap['locator']}; context only",
            ),
            compare=False,
            measured_at="state",
        )

    chas_v = ctx.vintage(CHAS)
    burden_note = (
        "CHAS Table 8, renter households with a computed cost burden; share = "
        "count / total shown; AMI = HUD area median family income"
    )
    charts = [
        _chart(
            f"burden_{g}",
            "stack",
            f"Renters {label[0].lower()}{label[1:]}: share of income spent on housing",
            "households",
            _burden_rows(ctx, g),
            place,
            other,
            (CHAS,),
            chas_v,
            burden_note + ".",
        )
        for g, label, _ in af.TRACT_GROUPS
    ]
    charts.append(
        _chart(
            "rent_bands",
            "bar",
            "Renter homes by the income their rent is affordable to",
            "units",
            _rent_rows(ctx),
            place,
            other,
            (CHAS,),
            chas_v,
            "CHAS Table 15C: rent at most 30% of the income at each AMI level; homes lacking a "
            "complete kitchen or plumbing left out.",
        )
    )
    charts.append(
        _chart(
            "assisted_programs",
            "bar",
            "Income-restricted and assisted homes by program",
            "units",
            _program_rows(ctx),
            place,
            other,
            (MF, "hud_public_housing", "hud_lihtc"),
            ctx.vintage(MF),
            "Programs overlap (a tax-credit property can also hold a contract), so the bars can "
            "count a home twice; vouchers are households, not homes, and are left out.",
        )
    )
    charts.append(
        _chart(
            "assisted_expiry",
            "stack",
            "Homes with rental assistance contracts, by when the contract ends",
            "units",
            _expiry_rows(ctx),
            place,
            other,
            (MF,),
            ctx.vintage(MF),
            f"Counted from {as_of or 'the day the list was read'}; only a property's first two "
            "contracts are dated. Most contracts are renewed; an end date marks a renewal "
            "decision.",
        )
    )

    parts = []
    if lead and lead.value is not None:
        parts.append(
            f"{lead.text} of renters at or below 80% of AMI in {place.name} pay over "
            "30% of income for housing"
        )
    if total and total.value is not None:
        parts.append(f"{total.text} homes are income-restricted or assisted")
    ami = fm.get("ami")
    points = [
        point(ami, "Area median income {text} (HUD, family of four)")
        + (
            f"; a 2-bedroom home affordable at 50% of AMI costs at most {rent50.text}"
            if rent50 and rent50.value is not None and ami and ami.value is not None
            else ""
        ),
        point(risk5, "{text} assisted homes have a contract or restriction ending within 5 years"),
    ]
    if gap_fact and gap_fact.value is not None:
        national = gap["national"]["affordable_available_per_100_eli"]
        points.append(
            f"Statewide: {fmt(gap_fact.value, 'count')} affordable and available homes per 100 "
            f"extremely low-income renters (NLIHC 2026; {national} "
            "nationally)"
        )
    limits = (
        "CHAS is a special tabulation of the 2018-2022 survey, rounded and with margins of "
        "error; it is older than the 2020-2024 figures in other sections. Income bands are "
        "shares of HUD's area median family income for the household's HUD area. Tract "
        "figures carry no margins and are often very uncertain. Income limits and rents are "
        "for the county's HUD area. Assisted homes are counted from HUD's lists; programs "
        "overlap, only two contracts per property are dated, tax-credit records stop at "
        "2020 projects, and tax-credit restrictions are counted as ending at year 30 (some "
        "last longer). Most expiring contracts are renewed. Nothing here predicts "
        "displacement, rent changes or the loss of any home."
    )
    return finish(
        KEY, TITLE, sentence(parts), fm.facts, limits, charts=charts, lead=lead, points=points
    )
