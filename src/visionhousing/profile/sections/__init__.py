"""Profile sections: one module per section, each exposing ``build(ctx, geoid) -> Section | None``.

Built here: ``people``, ``housing``, ``permits``, ``transit``, ``typology``,
``data_quality``.
Reserved for later modules (shown as "coming soon" until their module exists):
``jobs``, ``incentives``, ``safety``, ``pipeline_local``, ``zoning``,
``parcels``, ``owners``, ``plans``, ``state_law``. The order and titles are in
:mod:`visionhousing.profile.registry`.
"""
