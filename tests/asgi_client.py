"""A minimal ASGI caller for the interface tests: no HTTP client library needed.

ASGI is the standard way a Python web server talks to a web app: the server
calls the app with a ``scope`` (method, path, headers), a ``receive`` function
that hands over the request body, and a ``send`` function that collects the
response. This helper plays the server's part in memory, so the tests exercise
the full app (middlewares, routing, validation) without a network socket and
without ``httpx`` (which the plan keeps out of the dependency tree).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit


@dataclass
class Reply:
    """One response: status, headers (lower-case names) and body."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    raw_headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""

    def json(self) -> Any:
        """Parse the body as JSON."""
        return json.loads(self.body)

    @property
    def text(self) -> str:
        """The body as text."""
        return self.body.decode("utf-8")


class AsgiClient:
    """Call an ASGI app in memory.

    Args:
        app: The ASGI app.
        chunk: Send the body in pieces of this many bytes (tests the streaming path).
    """

    def __init__(self, app: Any, chunk: int | None = None):
        self.app = app
        self.chunk = chunk

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
        send_length: bool = True,
    ) -> Reply:
        """Send one request and return the reply."""
        return asyncio.run(self._call(method, url, body, json_body, headers or {}, send_length))

    def get(self, url: str, **kw: Any) -> Reply:
        """Send a GET."""
        return self.request("GET", url, **kw)

    def post(self, url: str, json_body: Any = None, **kw: Any) -> Reply:
        """Send a POST with a JSON body."""
        return self.request("POST", url, json_body=json_body, **kw)

    async def _call(self, method, url, body, json_body, headers, send_length) -> Reply:
        parts = urlsplit(url)
        if json_body is not None:
            body = json.dumps(json_body).encode()
            headers = {"content-type": "application/json", **headers}
        body = body or b""
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        if send_length and body:
            raw.append((b"content-length", str(len(body)).encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parts.path,
            "raw_path": parts.path.encode(),
            "query_string": parts.query.encode(),
            "root_path": "",
            "headers": raw,
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        size = self.chunk or max(len(body), 1)
        pieces = [body[i : i + size] for i in range(0, len(body), size)] or [b""]
        messages = [
            {"type": "http.request", "body": p, "more_body": i < len(pieces) - 1}
            for i, p in enumerate(pieces)
        ]
        done = asyncio.Event()

        async def receive() -> dict[str, Any]:
            if messages:
                return messages.pop(0)
            await done.wait()
            return {"type": "http.disconnect"}

        reply = Reply(status=0)
        chunks: list[bytes] = []

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                reply.status = message["status"]
                for k, v in message.get("headers", []):
                    name, value = k.decode().lower(), v.decode()
                    reply.raw_headers.append((name, value))
                    reply.headers[name] = value
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    done.set()

        await self.app(scope, receive, send)
        reply.body = b"".join(chunks)
        return reply
