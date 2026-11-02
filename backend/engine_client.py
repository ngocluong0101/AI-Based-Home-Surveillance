"""Client of the engine manager's HTTP API (engine_manager/service.py)."""

from __future__ import annotations

import httpx


class EngineManagerError(Exception):
    """The manager cannot be reached or answered with an error."""


class EngineClient:
    def __init__(self, base_url: str | None = None, timeout: float = 3.0, http: httpx.Client | None = None) -> None:
        # `http` lets the tests put the manager's own app behind it.
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)

    def _call(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self._http.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Not the request/response: the body of PUT /cameras holds camera passwords.
            raise EngineManagerError(f"engine manager {method} {path}: {type(exc).__name__}") from None

    def status(self) -> dict:
        return self._call("GET", "/status")

    def put_cameras(self, cameras: list[dict], revision: str) -> dict:
        return self._call("PUT", "/cameras", json={"cameras": cameras, "revision": revision})

    def close(self) -> None:
        self._http.close()
