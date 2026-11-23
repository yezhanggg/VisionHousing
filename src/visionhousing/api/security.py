"""Security layers around the app: a body-size limit and response headers.

Plain-language summary
----------------------
Two small ASGI middlewares (an ASGI middleware wraps the app and sees every
request and response):

- :class:`BodyLimit` refuses a request body over 64 KB with a 413 before the app
  reads it, both when the sender declares the size and when it does not.
- :class:`SecurityHeaders` adds the same safety headers to every response: a
  Content Security Policy, ``X-Content-Type-Options: nosniff``,
  ``Referrer-Policy: no-referrer``, a ban on framing, and ``Cache-Control:
  no-store`` on the JSON interface. It also drops any ``server`` header (the
  server itself is started with its banner off).

The Content Security Policy (CSP) tells the browser to run only scripts from
this site. The built page (``web/dist/index.html``, made by Vite) has no inline
script. Should one ever be added, the policy names it by its SHA-256
fingerprint, computed from the file when the app starts
(:func:`inline_script_hashes`), rather than allowing every inline script.
``vercel.json`` repeats the same headers for the static site.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

__all__ = ["BodyLimit", "SecurityHeaders", "content_security_policy", "inline_script_hashes"]

Scope = dict[str, Any]

_INLINE = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.DOTALL | re.IGNORECASE)


def inline_script_hashes(html: str) -> list[str]:
    """Return the CSP hash source (``'sha256-...'``) of every inline script in a page.

    Scripts with a ``src`` attribute are skipped; they load from this site.
    """
    out = []
    for match in _INLINE.finditer(html):
        if re.search(r"\bsrc\s*=", match.group("attrs"), re.IGNORECASE):
            continue
        digest = hashlib.sha256(match.group("body").encode("utf-8")).digest()
        out.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return out


def content_security_policy(script_hashes: Iterable[str] = ()) -> str:
    """Build the policy: this site only, the named inline scripts, no framing.

    ``blob:`` workers are allowed because the map library starts its worker from
    a blob; ``data:`` images because the page icon is a data address.
    """
    scripts = " ".join(["'self'", *script_hashes])
    parts = [
        "default-src 'self'",
        f"script-src {scripts}",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "child-src 'self' blob:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(parts)


class BodyLimit:
    """Refuse a request body larger than ``limit`` bytes with a 413 JSON error."""

    def __init__(self, app: Any, limit: int):
        self.app = app
        self.limit = limit

    async def _refuse(self, send: Any) -> None:
        body = json.dumps(
            {
                "error": "body_too_large",
                "message": f"the request body is over the {self.limit // 1024} KB limit",
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        """Read the body up to the limit, then hand the app a replay of it."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.limit + 1
                if declared > self.limit:
                    await self._refuse(send)
                    return
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.limit:
                await self._refuse(send)
                return
            chunks.append(chunk)
            more = message.get("more_body", False)
        replay = [{"type": "http.request", "body": b"".join(chunks), "more_body": False}]

        async def replayed() -> dict[str, Any]:
            if replay:
                return replay.pop()
            return await receive()

        await self.app(scope, replayed, send)


class SecurityHeaders:
    """Add the safety headers to every response and drop any ``server`` header."""

    def __init__(self, app: Any, csp: str, api_prefix: str = "/api/"):
        self.app = app
        self.api_prefix = api_prefix
        self.headers = [
            (b"content-security-policy", csp.encode("ascii")),
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"cross-origin-resource-policy", b"same-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ]

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        """Pass the request through and rewrite the response headers."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope.get("path", "").startswith(self.api_prefix)

        async def with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                ours = {name for name, _ in self.headers}
                kept = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in ours and k.lower() != b"server"
                ]
                kept += self.headers
                if is_api and not any(k.lower() == b"cache-control" for k, _ in kept):
                    kept.append((b"cache-control", b"no-store"))
                message = {**message, "headers": kept}
            await send(message)

        await self.app(scope, receive, with_headers)


def page_hashes(web_dir: Path) -> list[str]:
    """Return the inline-script hashes of ``web_dir/index.html`` (none if it is missing)."""
    index = web_dir / "index.html"
    if not index.is_file():
        return []
    return inline_script_hashes(index.read_text(encoding="utf-8"))
