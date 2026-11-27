"""Profile section ``state_law``: state law and bills that bear on the reforms here.

Plain-language summary
----------------------
From the ``state_law_provisions`` table (see :mod:`visionhousing.law.state`):

- one fact per benchmark reform listing the Municipalities Planning Code
  sections that bear on it (the code does not reach Philadelphia or
  Pittsburgh, and the section says so for them);
- one fact per bill of the current session: its status (pending, enacted or
  lapsed), its last action and date, and the share of this place's residents
  (a tract: its 2020 homes) it would reach, worked out from each bill's scope
  and the DCED zoning record. For ADU bills, a place whose ADU rule was read as
  already "by right" is said to change little.

Reach by scope: ``all`` every municipality; ``all_zoned`` those with any
zoning ordinance (their own, joint or the county's); ``mpc`` all but
Philadelphia and Pittsburgh; ``mpc_zoned`` both tests; ``conditional`` a test
the tool cannot apply (shown as "depends").

Every other state (no ``state_law_provisions`` table): the section is built
from the state's law files (:mod:`visionhousing.law.states`), see
:func:`build_from_files`:

- one fact per state provision or dated record that bears on a reform: its
  status (pending, enacted, legally effective, stayed, locally implemented),
  its effective date, its citation, the date a person checked it, and whether
  it is **in force on the run's as-of date** (``as_of`` in
  ``assumptions/outcome.yaml``), worked out by
  :func:`visionhousing.law.timeline.eligibility_status` from its dated records.
  A provision with no dated record says so ("no dated record"), never "no";
- one fact per already-legal range of the state's calibration file (the share
  of lots where the benchmark is already legal under state law);
- when no provision has a dated record (Wyoming), one fact saying "No state
  reform law found (checked <date>)";
- in Massachusetts, the Section 3A districts from the pack table
  ``mbta_3a_districts``: for a tract, every district whose outline reaches it
  (pack table ``mbta_3a_district_tracts``, with the share of the district
  inside the tract), or, in a pack without that table, the districts whose
  centre lies in it; for a county, how many districts and towns it has.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from visionhousing.law import inforce
from visionhousing.law import states as law_states
from visionhousing.law.state import load_provisions
from visionhousing.law.timeline import IN_FORCE, TimelineRecord, eligibility_status
from visionhousing.profile.context import MissingDataError, ProfileContext
from visionhousing.profile.model import Fact, Section
from visionhousing.profile.sections import _law
from visionhousing.profile.sections._common import finish, sentence
from visionhousing.profile.sections.zoning import _munis, _tract_pieces

KEY = "state_law"
TITLE = "State law and bills"
REFORM_WORDS = {
    "adu": "accessory dwelling units",
    "missing_middle": "missing-middle housing",
    "tod": "density near transit",
    "parking": "parking",
    "fees": "development fees",
}


def _weights(ctx: ProfileContext, geoid: str, level: str, munis: pd.DataFrame) -> pd.DataFrame:
    """Municipalities of the place with a weight (residents, or a tract's 2020 homes)."""
    m = munis.copy()
    m["geoid"] = _law.muni_geoids(m)
    if level == "county":
        rows = m[m["county"] == geoid].copy()
        rows["weight"] = rows["population"]
        return rows
    pieces = _tract_pieces(ctx, munis)
    mine = pieces[pieces["tract"].astype(str) == geoid].copy()
    mine["geoid"] = mine["county"].astype(str).str.zfill(5) + mine["cousub"].astype(str)
    mine["weight"] = mine["housing_units_2020"]
    return mine


def _reach(rows: pd.DataFrame, scope: str, outside: set[str]) -> float | None:
    total = rows["weight"].sum()
    if scope == "conditional" or total <= 0:
        return None
    keep = pd.Series(True, index=rows.index)
    if scope in ("all_zoned", "mpc_zoned"):
        keep &= rows["zoning_regime"].isin(["own", "joint", "county"])
    if scope in ("mpc", "mpc_zoned"):
        keep &= ~rows["geoid"].isin(outside)
    return float(rows.loc[keep, "weight"].sum() / total)


def build(ctx: ProfileContext, geoid: str) -> Section | None:
    """Build the ``state_law`` section for a county or tract.

    A pack with the ``state_law_provisions`` table (Pennsylvania) is read as
    before (:func:`build_from_table`). The state whose law is built into that
    table (``state`` of ``assumptions/law/state_provisions.yaml``) without it
    says "no data". Any other state is read from its law files
    (:func:`build_from_files`).
    """
    table = ctx.table("state_law_provisions")
    if table is not None and not table.empty:
        return build_from_table(ctx, geoid)
    if ctx.state.upper() == str(load_provisions().get("state", "")).upper():
        ctx.need("state_law_provisions")  # raises: the table this state needs is missing
    return build_from_files(ctx, geoid)


def build_from_table(ctx: ProfileContext, geoid: str) -> Section | None:
    """The section from the pack's ``state_law_provisions`` table (Pennsylvania)."""
    place, _ = ctx.place(geoid)
    prov = ctx.need("state_law_provisions")
    munis = _munis(ctx)
    munis["county"] = munis["county"].astype(str)
    rows = _weights(ctx, place.geoid, place.level, munis)
    if rows.empty:
        raise MissingDataError(f"no municipalities for {place.geoid}")
    outside = {str(g) for g in load_provisions().get("outside_mpc", [])}
    unit_word = "residents" if place.level == "county" else "2020 homes"
    mpc_reach = _reach(rows, "mpc", outside)

    # "Little changes" is said only when one municipality holds the place (a tract's
    # main municipality, or a county that is one municipality) and its verified rule
    # already allows ADUs by right.
    main = rows.sort_values("weight", ascending=False).iloc[0]
    already_by_right = False
    if place.level == "tract" or len(rows) == 1:
        try:
            rules = _law.rules_table(ctx)
            hit = rules[(rules["geoid"] == main["geoid"]) & (rules["status"] == "verified")]
            already_by_right = bool(len(hit)) and hit.iloc[0]["adu_status"] == "by_right"
        except MissingDataError:
            pass

    facts: list[Fact] = []
    enacted = prov[prov["bill_id"].isna() | (prov["bill_id"].astype(str) == "")]
    for reform, words in REFORM_WORDS.items():
        part = enacted[enacted["reform"] == reform]
        if part.empty:
            continue
        cites = "; ".join(part["citation"].astype(str))
        text = cites
        if mpc_reach is not None and mpc_reach < 1:
            text += (
                " (not in force for Philadelphia or Pittsburgh, which have their own zoning laws)"
                if mpc_reach == 0
                else f" (reaches {mpc_reach:.0%} of {unit_word} here)"
            )
        sid = str(part["source_id"].iloc[0])
        facts.append(
            Fact(
                id=f"{KEY}.code_{reform}",
                label=f"Planning-code sections on {words}",
                value=len(part),
                unit="sections",
                text=text,
                source=sid,
                vintage=ctx.vintage(sid),
                year=str(part["checked_on"].iloc[0]),
                measured_at="state",
                note=" ".join(part["summary_own_words"].astype(str)),
            )
        )
    bills = prov[prov["bill_id"].notna() & (prov["bill_id"].astype(str) != "")]
    live = []
    for _, b in bills.iterrows():
        reach = _reach(rows, str(b["scope"]), outside)
        reach_text = (
            "depends on tests this tool does not apply"
            if reach is None
            else f"would reach {reach:.0%} of {unit_word} here"
        )
        text = f"{b['status']}: {b['last_action']} ({b['last_action_date']}); {reach_text}"
        main_reached = _reach(main.to_frame().T, str(b["scope"]), outside)
        if b["reform"] == "adu" and already_by_right and main_reached:
            where = main.get("name", "the main municipality")
            text += f"; ADUs are already allowed by right in {where}, so little would change there"
        sid = str(b["source_id"])
        facts.append(
            Fact(
                id=f"{KEY}.bill_{str(b['bill_id']).replace(' ', '').lower()}",
                label=f"{b['bill_id']} ({REFORM_WORDS.get(str(b['reform']), b['reform'])})",
                value=str(b["status"]),
                unit="text",
                text=text,
                source=sid,
                vintage=ctx.vintage(sid),
                year=str(b["last_action_date"]),
                measured_at=place.level,
                note=f"{b['citation']}. {b['summary_own_words']} Applies to: {b['applies_to']}.",
            )
        )
        if b["status"] == "pending" and (reach is None or reach > 0):
            live.append(str(b["bill_id"]))
    parts = []
    if live:
        parts.append(
            f"{len(live)} pending state bills would change the rules here ({', '.join(live)})"
        )
    if mpc_reach == 0:
        parts.append("the state planning code does not govern zoning here")
    limits = (
        "Summaries are ours, not legal advice; the bill pages and the code's text are the "
        "authority. A bill's status is as read from its page when the pack was built; bills not "
        "enacted by the session's last day lapse. Reach counts municipalities by the DCED record "
        "of who zones; a bill's own exceptions (deed restrictions, local options) are not modelled."
    )
    return finish(KEY, TITLE, sentence(parts), facts, limits)


# ---------------------------------------------------------------- any other state

STATUS_WORDS = {
    "pending": "pending",
    "enacted": "enacted",
    "legally_effective": "legally effective",
    "stayed": "stayed",
    "locally_implemented": "locally implemented",
    "practically_usable": "practically usable",
}
TYPE_WORDS = {**REFORM_WORDS, "multifamily": "multifamily homes", "all": "all homes"}
CALIBRATION_WORDS = {
    "adu.already_legal_share": "Lots where one ADU is already legal under state law",
    "missing_middle.sf_only_zoned_share": (
        "Single-family lots still limited to one home under state law"
    ),
}
FILES_LIMITS = (
    'Summaries are ours, not legal advice; the statute\'s text is the authority. "In force" is '
    "read from dated records a person checked against the source, on the run's as-of date; a "
    "provision with no dated record is listed but not dated. Town and county rules are not read "
    "for this state, so how far each town has followed a state law is not shown. Already-legal "
    "shares are team judgement, not a measurement."
)


def _iso(v: Any) -> str:
    if v is None or v == "":
        return ""
    return v.isoformat() if isinstance(v, date) else str(v)


def _groups(
    provisions: list[dict[str, Any]], records: list[TimelineRecord]
) -> list[tuple[str, dict[str, Any], list[TimelineRecord]]]:
    """Provisions of the state file, then provisions known only from dated records."""
    by_id: dict[str, list[TimelineRecord]] = {}
    for r in records:
        by_id.setdefault(r.provision_id or r.record_id, []).append(r)
    out = [(str(p["provision_id"]), p, by_id.pop(str(p["provision_id"]), [])) for p in provisions]
    order = list(TYPE_WORDS)
    extra = sorted(by_id.items(), key=lambda kv: order.index(kv[1][0].housing_type))
    out += [(pid, {}, recs) for pid, recs in extra]
    return out


def _dated(
    recs: list[TimelineRecord], state: str, fips: str, geoid: str, as_of: date
) -> tuple[str, TimelineRecord | None, bool | None, str]:
    """``(status, record shown, in force, words)`` for one provision on ``as_of``."""
    seen = [r for r in recs if r.verified_on is not None and r.verified_on <= as_of]
    if not seen:
        return "", None, None, f"in force on {as_of}: not known (no dated record)"
    kind = seen[0].housing_type
    e = eligibility_status(
        seen, state=state, housing_type=kind, on=as_of, as_of=as_of, geoid=geoid, state_fips=fips
    )
    if e.status != "unknown":
        rec = next(r for r in seen if r.record_id == e.record_id)
        words = f"in force on {as_of}" if e.in_force else f"not in force on {as_of}"
        return e.status, rec, e.in_force, words
    later = [r for r in seen if r.in_force_from is not None and r.in_force_from > as_of]
    if later:
        rec = min(later, key=lambda r: r.in_force_from or as_of)
        return rec.status, rec, False, f"not in force on {as_of} (from {_iso(rec.in_force_from)})"
    rec = max(seen, key=lambda r: (r.end or date.min, r.record_id))
    return rec.status, rec, False, f"not in force on {as_of} (ended {_iso(rec.end)})"


def _scheduled(
    recs: list[TimelineRecord], current: TimelineRecord | None, as_of: date
) -> TimelineRecord | None:
    """The next enacted, verified version of the provision that starts after ``as_of``.

    A pending bill is never one: only a record whose status puts it in force
    on its own start date counts (the current-law convention).
    """
    later = [
        r
        for r in recs
        if r.review == "verified"
        and r.verified_on is not None
        and r.verified_on <= as_of
        and r.start is not None
        and r.start > as_of
        and r.status in IN_FORCE
        and (current is None or r.record_id != current.record_id)
    ]
    return min(later, key=lambda r: r.start or as_of) if later else None


def _provision_fact(
    ctx: ProfileContext,
    pid: str,
    p: dict[str, Any],
    recs: list[TimelineRecord],
    *,
    geoid: str,
    as_of: date,
    fips: str,
    fallbacks: list[str],
) -> tuple[Fact | None, bool | None, str]:
    status, rec, live, words = _dated(recs, ctx.state.upper(), fips, geoid, as_of)
    status = status or str(p.get("status") or "")
    kind = str(p.get("reform") or (rec.housing_type if rec else "") or "all")
    citation = str(p.get("citation") or (rec.citation if rec else pid))
    bits = [STATUS_WORDS.get(status, status)] if status else []
    start = p.get("effective") or (rec.start if rec else None)
    if start:
        bits.append(f"effective {_iso(start)}")
    elif rec is not None and rec.start_unknown:
        bits.append("start date not stated in its source")
    if rec is not None and rec.end:
        bits.append(f"until {_iso(rec.end)}")
    bits.append(words)
    if rec is not None and rec.court_status not in ("", "none_known"):
        bits.append(f"court: {rec.court_status}")
    scheduled = _scheduled(recs, rec, as_of)
    if scheduled is not None:
        bits.append(f"enacted change from {_iso(scheduled.start)} applies in both forecast arms")
    urls = [str(p.get("url") or "")] + [u for r in recs for u in r.sources]
    if rec is not None:
        urls = list(rec.sources) + urls
    own = str(p.get("source_id") or "") or next((r.source_id for r in recs if r.source_id), "")
    sid = law_states.source_for(ctx.registry, source_id=own, urls=urls, fallbacks=fallbacks)
    if not sid:
        return None, live, status
    # the latest check: the provision's own re-reading or its record's verification
    checked = max(_iso(rec.verified_on) if rec is not None else "", _iso(p.get("checked_on")))
    note = [" ".join(str(p.get("summary_own_words") or "").split())]
    if p.get("benchmark_gap"):
        note.append("Against the benchmark: " + " ".join(str(p["benchmark_gap"]).split()))
    if scheduled is not None:
        note.append(
            f"Current-law convention: {scheduled.citation} takes effect on "
            f"{_iso(scheduled.start)} and is applied from that date in the baseline and the "
            'reform alike. The separately named comparison "Freeze today\'s rules" keeps the '
            f"text in force on {as_of} throughout; the two differ from {_iso(scheduled.start)}."
        )
    if rec is not None:
        note.append(f"Record {rec.record_id} ({rec.citation}), verified {_iso(rec.verified_on)}.")
        for label, text in (("Scope", rec.scope), ("Exclusions", rec.exclusions)):
            if text:
                note.append(f"{label}: {' '.join(text.split())}.")
        note.append("Sources: " + "; ".join(rec.sources) + ".")
    fact = Fact(
        id=f"{KEY}.law_{pid}",
        label=f"{citation} ({TYPE_WORDS.get(kind, kind)})",
        value=status or None,
        unit="text",
        text="; ".join(bits),
        source=sid,
        vintage=ctx.vintage(sid),
        year=checked or "check date not recorded",
        measured_at="state",
        note=" ".join(n for n in note if n),
    )
    return fact, live, status


def _calibration_facts(ctx: ProfileContext, cal: dict[str, Any]) -> list[Fact]:
    prov = cal.get("provenance") or {}
    ids = [str(s.get("id")) for s in prov.get("sources", []) if str(s.get("id")) in ctx.registry]
    if not ids:
        return []
    cites = "; ".join(" ".join(str(s.get("citation", "")).split()) for s in prov["sources"])
    entries = [(str(cal["parameter"]), cal["range"]), *(cal.get("also") or {}).items()]
    out = []
    for param, rng in entries:
        low, mid, high = (float(rng[k]) for k in ("low", "central", "high"))
        out.append(
            Fact(
                id=f"{KEY}.already_legal_{param.split('.')[0]}",
                label=CALIBRATION_WORDS.get(param, param),
                value=mid,
                unit="share",
                text=f"{mid:.0%}",
                source=ids[0],
                vintage=ctx.vintage(ids[0]),
                year=str(prov.get("read_between") or ""),
                measured_at="state",
                low=low,
                high=high,
                range_text=f"{low:.0%} to {high:.0%}",
                note=(
                    f"{' '.join(str(prov.get('summary', '')).split())} Read: {cites}. Team "
                    f"judgement, not a measurement (calibration {cal.get('id')}, ledger entry "
                    f"{param})."
                ),
            )
        )
    return out


def _district_fact(ctx: ProfileContext, level: str, geoid: str) -> Fact | None:
    """Massachusetts Section 3A districts: centred in a tract, or counted in a county."""
    table = ctx.table("mbta_3a_districts")
    sid = "massgis_mbta3a_atlas"
    if table is None or table.empty or sid not in ctx.registry:
        return None
    col = "tract" if level == "tract" else "county"
    if col not in table.columns:
        return None
    links = ctx.table("mbta_3a_district_tracts") if level == "tract" else None
    by_outline = links is not None and not links.empty
    share: dict[str, float] = {}
    if by_outline:
        mine = links[links["tract"].astype(str) == geoid]
        share = dict(
            zip(mine["district_id"].astype(str), pd.to_numeric(mine["district_share"]), strict=True)
        )
        rows = table[table["district_id"].astype(str).isin(share)]
    else:
        rows = table[table[col].astype(str) == geoid]
    if level == "tract" and rows.empty:
        return None
    when = str(rows["atlas_read_on"].iloc[0]) if len(rows) and "atlas_read_on" in rows else ""
    if level == "tract":
        parts = []
        for _, r in rows.iterrows():
            cap = pd.to_numeric(r.get("capacity_district"), errors="coerce")
            upa = pd.to_numeric(r.get("units_per_acre"), errors="coerce")
            extra = [str(r.get("status") or "").lower()]
            if not pd.isna(cap):
                extra.append(f"room for {cap:,.0f} homes")
            if not pd.isna(upa):
                extra.append(f"{upa:.0f} homes per acre")
            part = share.get(str(r["district_id"]))
            if part is not None and not pd.isna(part) and part < 0.995:
                extra.append(f"{part:.0%} of it in this tract")
            said = ", ".join(e for e in extra if e)
            parts.append(f"{r['zone_name']} ({r['municipality']}; {said})")
        text = "; ".join(parts)
        if by_outline:
            label = "Section 3A districts in this tract"
            note = (
                "M.G.L. c. 40A § 3A: multi-family homes as of right in the district. Every "
                "district whose simplified outline reaches this tract is listed (pack table "
                "mbta_3a_district_tracts: outline and tract drawn on a flat grid; a tract holding "
                "less than the settings' smallest share is left out unless it holds the "
                "district's centre). Capacity is the whole district's, not this tract's share."
            )
        else:
            label = "Section 3A districts centred in this tract"
            note = (
                "M.G.L. c. 40A § 3A: multi-family homes as of right in the district. The "
                "district's centre lies in this tract (pack column tract); its outline may reach "
                "other tracts."
            )
    else:
        towns = rows["municipality"].nunique() if len(rows) else 0
        n = len(rows)
        text = (
            f"{n} district{'s' * (n != 1)} in {towns} town{'s' * (towns != 1)}"
            if n
            else "no compliant district"
        )
        label = "Section 3A districts in this county"
        note = "Districts found compliant, from the MassGIS 3A District Atlas (mbta_3a_districts)."
    return Fact(
        id=f"{KEY}.mbta_3a_districts",
        label=label,
        value=len(rows),
        unit="districts",
        text=text,
        source=sid,
        vintage=ctx.vintage(sid),
        year=when or "date not recorded",
        measured_at=level,
        note=note,
    )


def _survey_fact(ctx: ProfileContext, level: str, geoid: str) -> Fact | None:
    """Massachusetts ADU survey for the place's county: observed context, never a rate."""
    from visionhousing.features import ma_intake as ma_fx

    sid = "ma_adu_survey_file"
    if ctx.state.upper() != "MA" or sid not in ctx.registry:
        return None
    county = str(geoid)[:5]
    table = ctx.table(ma_fx.SURVEY_TABLE)
    got = ma_fx.survey_context(table, county=county)
    whole = ma_fx.survey_context(table) if table is not None else None
    approvals = sum(int(p.get("approvals", 0)) for p in got["periods"])
    where = "this county" if level == "county" else "this tract's county"
    note = got["note"]
    if whole is not None and whole["periods"]:
        note += f" Statewide: {whole['text']}."
    when = ", ".join(f"{p['period_start']} to {p['period_end']}" for p in got["periods"])
    return Fact(
        id=f"{KEY}.ma_adu_survey",
        label=f"ADUs approved under the state ADU law, towns of {where} (state survey)",
        value=approvals if got["state"] == "observed" else None,
        unit="ADUs approved" if got["state"] == "observed" else "text",
        text=got["text"],
        source=sid,
        vintage=ctx.vintage(sid),
        year=when or "not supplied",
        measured_at="county",
        note=note,
    )


def build_from_files(ctx: ProfileContext, geoid: str) -> Section | None:
    """The section from the state's law files (any state without the table)."""
    from visionhousing.data.sources import STATE_FIPS

    place, _ = ctx.place(geoid)
    state = ctx.state.upper()
    fips = STATE_FIPS.get(state, "")
    data = law_states.load_state_file(state) or {}
    cals = law_states.calibration_files(state)
    records = [
        r
        for r in law_states.state_records(state, fips)
        if r.review == "verified" and r.jurisdiction in (state, fips)
    ]
    provisions = list(data.get("provisions") or [])
    if not provisions and not records and not cals:
        raise MissingDataError(f"no state law file or dated record for {state}")
    as_of = inforce.run_as_of()
    docs = data.get("documents") or []
    fallbacks = [str(d.get("source_id") or "") for d in docs if d.get("access") == "fetched"]
    for c in cals:
        fallbacks += [str(s.get("id")) for s in (c.get("provenance") or {}).get("sources", [])]

    facts: list[Fact] = []
    live, pending, dated = 0, 0, 0
    for pid, p, recs in _groups(provisions, records):
        fact, in_force, status = _provision_fact(
            ctx, pid, p, recs, geoid=place.geoid, as_of=as_of, fips=fips, fallbacks=fallbacks
        )
        if fact is None:
            continue
        facts.append(fact)
        dated += in_force is not None
        live += bool(in_force)
        pending += status == "pending" and in_force is not True
    parts = []
    if live:
        parts.append(f"{live} state provisions on the reforms are in force on {as_of}")
    if pending:
        parts.append(f"{pending} are pending")
    if not dated:
        checked = max((_iso(p.get("checked_on")) for p in provisions), default="") or str(as_of)
        sid = law_states.source_for(ctx.registry, fallbacks=fallbacks)
        if sid:
            facts.insert(
                0,
                Fact(
                    id=f"{KEY}.no_reform_law",
                    label="State reform law on the five reforms",
                    value="none found",
                    unit="text",
                    text=f"No state reform law found (checked {checked})",
                    source=sid,
                    vintage=ctx.vintage(sid),
                    year=checked,
                    measured_at="state",
                    note=(
                        "No provision listed for this state has a dated record of enactment or "
                        "effect; the statutes listed leave these rules to local codes."
                    ),
                ),
            )
        parts.append(f"no state reform law found (checked {checked})")
    for cal in cals:
        facts += _calibration_facts(ctx, cal)
    survey = _survey_fact(ctx, place.level, place.geoid)
    if survey is not None:
        facts.append(survey)
    district = _district_fact(ctx, place.level, place.geoid)
    if district is not None:
        facts.append(district)
        if place.level == "tract":
            parts.append("a Section 3A multi-family district reaches this tract")
    return finish(KEY, TITLE, sentence(parts), facts, FILES_LIMITS)
