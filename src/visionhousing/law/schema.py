"""Row types for the law, zoning and policy tables, and the values each field may take.

Plain-language summary
----------------------
Every row is checked when it is made, so a typo (``"by-right"`` for
``"by_right"``) or a quote that is too long stops the build with a message
instead of reaching a screen.

The rule that matters most: **a fact nobody has read is unknown, not "no"**.
Every field of :class:`OrdinanceFacts` may be ``None`` (unknown), and the ADU
rule has its own ``"unknown"`` value. ``"not_addressed"`` is different: it
means someone read the ordinance and it does not mention ADUs. Not addressed
is **not** prohibited: what an omitted use may do depends on the ordinance's
clause on unlisted uses (``unlisted_uses``). Only a verified exclusivity
clause (``prohibited``: uses not listed are not allowed) makes it a "no"; a
clause sending unlisted uses to a hearing (``conditional``,
``special_exception``) makes it a discretionary route; with no clause read
the ADU answer stays unknown.

Allowed values
--------------

- reform: ``adu``, ``missing_middle``, ``tod``, ``parking``, ``fees``;
- provision status: ``enacted`` (law now), ``pending`` (a bill still alive),
  ``lapsed`` (a bill that died with its session);
- ADU rule: ``by_right``, ``conditional`` (conditional use, decided by the
  governing body), ``special_exception`` (decided by the zoning hearing board),
  ``prohibited``, ``not_addressed``, ``unknown``;
- occupancy restriction of an ADU rule (``occupancy_restriction``): empty
  (none read) or ``relatives_only`` (only relatives of the main household, or
  a relative needing care, may live in the ADU). The benchmark lets any
  household live there, so a ``by_right`` rule with a restriction counts as
  "by right with conditions" (``assumptions/law/hooks.yaml``);
- clause on unlisted uses (``unlisted_uses``): empty (not read),
  ``prohibited`` (a verified exclusivity clause), ``conditional`` (an
  unlisted use may ask the governing body), ``special_exception`` (it may ask
  the zoning hearing board); ``unlisted_uses_cite`` names the section;
- method: ``hand`` (a person read the text), ``rule`` (drafted by the
  rule-based reader in :mod:`visionhousing.law.extract`), ``unread`` (a
  placeholder row for a municipality nobody has read yet);
- row status: ``draft`` or ``verified``. Only a person marks a row verified,
  and only with a document, a section and a date;
- verification of a state provision: ``verified`` (its heading was found in
  the fetched code text, or the bill's page was read at build time) or
  ``checked_values`` (the page could not be read; the values a person checked
  on ``checked_on`` are used).

Rule-change tracker fields
--------------------------
Every provision, bill and municipal rule carries the dates of its life:
``introduced_on`` (a bill's first referral), ``enacted_on`` (signed, or the
date the code section was added), ``last_amended``, ``effective_date``,
``adopted_on`` (a local ordinance's adoption), ``in_force_by`` (a municipal
rule only: the latest dated enactment inside the cited codified section, or
the "enacted to" date of the posted text, when no adoption date is printed;
the rule stood in force by then, but that is not its adoption date),
``binding_conditions`` (plain
words: what limits the rule), the verification status, the date last checked
and ``future_effective`` (1 when the effective date is after the build date,
so the rule is not yet law). A date may be a year (``2023``) or a month
(``2023-10``) when the source gives no more. Empty means unknown.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "ADU_STATUSES",
    "CONFIDENCE",
    "LEVELS",
    "METHODS",
    "OCCUPANCY_RESTRICTIONS",
    "PROVISION_STATUSES",
    "QUOTE_MAX",
    "REFORMS",
    "ROW_STATUSES",
    "SCOPES",
    "VERIFICATION",
    "CountyLandUse",
    "Evidence",
    "LawDocument",
    "MuniRule",
    "OrdinanceFacts",
    "StateProvision",
    "is_future",
]

REFORMS = ("adu", "missing_middle", "tod", "parking", "fees")
PROVISION_STATUSES = ("enacted", "pending", "lapsed")
ADU_STATUSES = (
    "by_right",
    "conditional",
    "special_exception",
    "prohibited",
    "not_addressed",
    "unknown",
)
METHODS = ("hand", "rule", "unread")
UNLISTED_USES = ("", "prohibited", "conditional", "special_exception")
"""What the ordinance does with a use it does not list (empty: clause not read)."""
OCCUPANCY_RESTRICTIONS = ("", "relatives_only")
"""Who may live in an ADU, when the ordinance limits it: no limit read, or relatives only."""
ROW_STATUSES = ("draft", "verified")
CONFIDENCE = ("high", "medium", "low", "")
LEVELS = ("state", "county", "municipality")
SCOPES = ("all", "all_zoned", "mpc", "mpc_zoned", "conditional")
"""Which municipalities a provision reaches: every one; every one with zoning;
every one under the Municipalities Planning Code (all but Philadelphia and
Pittsburgh); those of them with zoning; or only some, by a test the table
cannot apply (``conditional``)."""
VERIFICATION = ("verified", "checked_values")
PARTIAL_DATE = r"^(\d{4}(-\d{2}(-\d{2})?)?)?$"
"""A full date, a year and month, a year, or empty (unknown)."""
QUOTE_MAX = 300
"""The longest evidence quote kept (characters): enough to find the passage, not a copy."""


def _one_of(value: str, allowed: tuple[str, ...], what: str) -> str:
    if value not in allowed:
        raise ValueError(f"{what} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _date_text(v: Any) -> str:
    """A tracker date as text: dates become ``YYYY-MM-DD``; ``None`` becomes empty."""
    if v is None:
        return ""
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def is_future(effective_date: str, today: date) -> bool | None:
    """True when a full ``effective_date`` is after ``today``; ``None`` when not a full date."""
    if len(effective_date or "") != 10:
        return None
    return date.fromisoformat(effective_date) > today


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    def row(self) -> dict[str, Any]:
        """The row as plain values for a table (lists joined with ``;``, dates as text)."""
        out: dict[str, Any] = {}
        for k, v in self.model_dump().items():
            if isinstance(v, tuple | list):
                v = ";".join(str(x) for x in v)
            elif isinstance(v, date):
                v = v.isoformat()
            elif isinstance(v, bool):
                v = int(v)
            out[k] = v
        return out


class Evidence(_Row):
    """Where a value was read: a short quote, its character offset and its section."""

    quote: str = Field(max_length=QUOTE_MAX)
    offset: int | None = Field(default=None, ge=0)
    section: str = ""


class OrdinanceFacts(_Row):
    """What one zoning ordinance says about the reforms. ``None`` means unknown.

    Attributes:
        adu_status: The ADU rule (see the module summary).
        adu_detached: True when a detached ADU (a separate building) is allowed.
        adu_owner_occupancy: True when the owner must live on the lot.
        min_lot_sf_smallest_res_district: Smallest minimum lot area of any
            residential district, in square feet.
        smallest_res_district: The district that has it.
        max_units_by_right_sf_district: Homes allowed by right on one lot in the
            main single-family district (1 when only a single-family house).
        parking_min_per_unit_sf: Off-street spaces required per single-family home.
        parking_min_per_unit_mf: Off-street spaces required per apartment.
        permitted_types: Housing types the ordinance permits somewhere.
        transport_impact_fee_ordinance: True when the municipality has a
            transportation impact fee ordinance (MPC Article V-A).
        evidence: Field name to where it was read.
        top_ranked: Fields whose evidence came from the section ranked first
            for its topic (used for the draft's confidence).
    """

    adu_status: str = "unknown"
    adu_detached: bool | None = None
    adu_owner_occupancy: bool | None = None
    min_lot_sf_smallest_res_district: float | None = Field(default=None, gt=0)
    smallest_res_district: str | None = None
    max_units_by_right_sf_district: int | None = Field(default=None, ge=0)
    parking_min_per_unit_sf: float | None = Field(default=None, ge=0)
    parking_min_per_unit_mf: float | None = Field(default=None, ge=0)
    permitted_types: tuple[str, ...] | None = None
    transport_impact_fee_ordinance: bool | None = None
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    top_ranked: tuple[str, ...] = ()

    @field_validator("adu_status")
    @classmethod
    def _adu(cls, v: str) -> str:
        return _one_of(v, ADU_STATUSES, "adu_status")

    @property
    def known(self) -> list[str]:
        """Names of the fields that have a value."""
        skip = {"evidence", "smallest_res_district", "top_ranked"}
        return [
            k
            for k, v in self.model_dump().items()
            if k not in skip and v is not None and v != "unknown"
        ]


class MuniRule(_Row):
    """One row of ``muni_zoning_rules``: one municipality's reform-relevant rules.

    ``geoid`` is the 10-digit Census county subdivision code (state, county,
    subdivision). Unknown values stay empty (``None``).
    """

    geoid: str = Field(pattern=r"^\d{10}$")
    dced_id: str = ""
    name: str = ""
    zoning_regime: str = ""
    doc_id: str = ""
    section_cite: str = ""
    adu_status: str = "unknown"
    adu_detached: bool | None = None
    adu_owner_occupancy: bool | None = None
    occupancy_restriction: str = ""
    unlisted_uses: str = ""
    unlisted_uses_cite: str = Field(default="", max_length=200)
    min_lot_sf_smallest_res_district: float | None = Field(default=None, gt=0)
    max_units_by_right_sf_district: int | None = Field(default=None, ge=0)
    parking_min_per_unit_sf: float | None = Field(default=None, ge=0)
    parking_min_per_unit_mf: float | None = Field(default=None, ge=0)
    permitted_types: tuple[str, ...] | None = None
    transport_impact_fee_ordinance: bool | None = None
    evidence_quote: str = Field(default="", max_length=QUOTE_MAX)
    evidence_offset: int | None = Field(default=None, ge=0)
    method: str = "unread"
    confidence: str = ""
    reviewed_on: date | None = None
    status: str = "draft"
    note: str = Field(default="", max_length=600)
    adopted_on: str = Field(default="", pattern=PARTIAL_DATE)
    effective_date: str = Field(default="", pattern=PARTIAL_DATE)
    in_force_by: str = Field(default="", pattern=PARTIAL_DATE)
    binding_conditions: str = Field(default="", max_length=300)
    future_effective: bool | None = None
    sf_lots: float | None = Field(default=None, ge=0)
    res_land_ha: float | None = Field(default=None, ge=0)

    @field_validator("adopted_on", "effective_date", "in_force_by", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> str:
        return _date_text(v)

    @field_validator("adu_status")
    @classmethod
    def _adu(cls, v: str) -> str:
        return _one_of(v, ADU_STATUSES, "adu_status")

    @field_validator("occupancy_restriction")
    @classmethod
    def _occupancy(cls, v: str) -> str:
        return _one_of(v, OCCUPANCY_RESTRICTIONS, "occupancy_restriction")

    @field_validator("unlisted_uses")
    @classmethod
    def _unlisted(cls, v: str) -> str:
        return _one_of(v, UNLISTED_USES, "unlisted_uses")

    @field_validator("method")
    @classmethod
    def _method(cls, v: str) -> str:
        return _one_of(v, METHODS, "method")

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        return _one_of(v, ROW_STATUSES, "status")

    @field_validator("confidence")
    @classmethod
    def _confidence(cls, v: str) -> str:
        return _one_of(v, CONFIDENCE, "confidence")

    @field_validator("zoning_regime")
    @classmethod
    def _regime(cls, v: str) -> str:
        return _one_of(v, ("own", "joint", "county", "none", ""), "zoning_regime")

    @model_validator(mode="after")
    def _verified_needs_proof(self) -> MuniRule:
        if self.status == "verified":
            missing = [
                n
                for n, v in (
                    ("doc_id", self.doc_id),
                    ("section_cite", self.section_cite),
                    ("reviewed_on", self.reviewed_on),
                )
                if not v
            ]
            if missing:
                raise ValueError(f"a verified row needs {', '.join(missing)}")
            if self.method != "hand":
                raise ValueError("only a hand reading can be verified")
        if self.method == "unread" and self.adu_status != "unknown":
            raise ValueError("an unread municipality's ADU rule is unknown")
        if self.unlisted_uses and not self.unlisted_uses_cite:
            raise ValueError("unlisted_uses needs unlisted_uses_cite (the clause's section)")
        return self


class StateProvision(_Row):
    """One row of ``state_law_provisions``: a state law section or bill bearing on a reform."""

    provision_id: str = Field(pattern=r"^[a-z0-9_]+$")
    citation: str = Field(min_length=3)
    reform: str
    status: str
    bill_id: str = ""
    last_action: str = ""
    last_action_date: date | None = None
    applies_to: str = Field(min_length=3)
    scope: str = "all"
    summary_own_words: str = Field(min_length=10, max_length=600)
    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    url: str = ""
    checked_on: date
    introduced_on: str = Field(default="", pattern=PARTIAL_DATE)
    enacted_on: str = Field(default="", pattern=PARTIAL_DATE)
    last_amended: str = Field(default="", pattern=PARTIAL_DATE)
    effective_date: str = Field(default="", pattern=PARTIAL_DATE)
    binding_conditions: str = Field(default="", max_length=300)
    verification: str = "verified"
    future_effective: bool | None = None

    @field_validator("introduced_on", "enacted_on", "last_amended", "effective_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> str:
        return _date_text(v)

    @field_validator("verification")
    @classmethod
    def _verification(cls, v: str) -> str:
        return _one_of(v, VERIFICATION, "verification")

    @field_validator("reform")
    @classmethod
    def _reform(cls, v: str) -> str:
        return _one_of(v, REFORMS, "reform")

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: str) -> str:
        return _one_of(v, SCOPES, "scope")

    @field_validator("status")
    @classmethod
    def _status(cls, v: str) -> str:
        return _one_of(v, PROVISION_STATUSES, "status")


class LawDocument(_Row):
    """One row of ``law_documents``: a legal text, where it is and how it was read.

    ``access``: ``fetched`` (downloaded by the build), ``hand_read`` (a person
    downloaded and read it once; its text checksum is kept) or ``link_only``
    (the host forbids automated reading: listed, never fetched).
    """

    doc_id: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=3)
    level: str
    publisher: str
    url: str = Field(pattern=r"^https://\S+$")
    licence: str
    access: str
    source_id: str
    sha256: str = ""
    text_chars: int | None = None
    fetched_on: date | None = None
    adopted_on: str = Field(default="", pattern=PARTIAL_DATE)
    amended_through: str = Field(default="", pattern=PARTIAL_DATE)
    effective_date: str = Field(default="", pattern=PARTIAL_DATE)

    @field_validator("adopted_on", "amended_through", "effective_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> str:
        return _date_text(v)

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        return _one_of(v, LEVELS, "level")

    @field_validator("access")
    @classmethod
    def _access(cls, v: str) -> str:
        return _one_of(v, ("fetched", "hand_read", "link_only"), "access")


class CountyLandUse(_Row):
    """One row of ``county_land_use``: county zoning reach and the county plan."""

    county: str = Field(pattern=r"^\d{5}$")
    county_name: str = ""
    municipalities: int = Field(ge=0)
    munis_under_county_zoning: int = Field(ge=0)
    residents_under_county_zoning: int = Field(ge=0)
    munis_no_zoning: int = Field(ge=0)
    residents: int = Field(ge=0)
    plan_name: str = ""
    plan_adopted: int | None = None
    plan_url: str = ""
    commission_url: str = ""
    plan_verified: bool = False
    residents_read: int = Field(default=0, ge=0)
    sf_lots: int | None = Field(default=None, ge=0)
    sf_lots_read: int | None = Field(default=None, ge=0)
    res_land_ha: float | None = Field(default=None, ge=0)
    res_land_read_ha: float | None = Field(default=None, ge=0)
