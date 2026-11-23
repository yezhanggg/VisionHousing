"""The local web interface: a small FastAPI app and static file serving.

Exposes the engine to the browser front end. It adds no logic of its own; every
number it returns comes from the engine. ``create_app()`` builds the app;
``visionhousing serve`` (or ``make app``) starts it. See ``docs/modules/api.md``.
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_app"]


def create_app(**kwargs: Any) -> Any:
    """Build the interface app (see ``visionhousing.api.app.create_app``)."""
    from visionhousing.api.app import create_app as _create

    return _create(**kwargs)
