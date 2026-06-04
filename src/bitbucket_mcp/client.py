"""HTTP client for the Bitbucket Data Center (Server) REST API.

This client targets Bitbucket *Data Center / Server* REST semantics
(`/rest/api/1.0`, project/repo-centric paths, `start`/`limit`/`isLastPage`
paging). It deliberately does NOT use Bitbucket Cloud (`/2.0`, workspaces)
conventions.

Personal repositories live under a personal project whose key is
`~<username>` (e.g. `~sbwo`). Callers may pass either a normal project key
(`PROJ`) or a personal one (`~sbwo`); both work transparently.
"""

from __future__ import annotations

import base64
from typing import Any, Iterator, Optional
from urllib.parse import quote

import httpx

DEFAULT_PAGE_LIMIT = 25
MAX_PAGE_LIMIT = 1000


class BitbucketError(RuntimeError):
    """Raised when the Bitbucket API returns an error response."""

    def __init__(self, status_code: int, message: str, *, url: str = "") -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(message)


class BitbucketClient:
    """Thin wrapper around the Bitbucket Data Center REST API."""

    def __init__(
        self,
        base_url: str,
        *,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        verify_ssl: bool | str = True,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")

        headers = {"Accept": "application/json"}
        # Auth selection:
        #   - username + secret  -> Basic auth (works with password or API token)
        #   - token only         -> Bearer (Bitbucket DC HTTP access token, "BBDC-...")
        secret = password or token
        if username and secret:
            raw = f"{username}:{secret}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            raise ValueError("Provide either a token or username+password.")

        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            verify=verify_ssl,
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BitbucketClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level ---------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[Any] = None,
        raw: bool = False,
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        resp = self._client.request(method, path, params=clean, json=json)
        if resp.status_code >= 400:
            raise BitbucketError(
                resp.status_code,
                _extract_error(resp),
                url=str(resp.request.url),
            )
        if raw:
            return resp.text
        if not resp.content:
            return None
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            return resp.json()
        return resp.text

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._request("DELETE", path, **kwargs)

    def put_form(self, path: str, data: dict[str, Any]) -> Any:
        """PUT multipart/form-data (used by the file `browse` write endpoint).

        The Bitbucket browse endpoint requires `multipart/form-data`; sending
        URL-encoded form data yields HTTP 415. We encode each text field as a
        multipart part via httpx `files` with `(None, value)` tuples.
        """
        files = {
            k: (None, str(v)) for k, v in data.items() if v is not None
        }
        resp = self._client.put(path, files=files)
        if resp.status_code >= 400:
            raise BitbucketError(
                resp.status_code, _extract_error(resp), url=str(resp.request.url)
            )
        if not resp.content:
            return None
        if "application/json" in resp.headers.get("content-type", ""):
            return resp.json()
        return resp.text

    def whoami(self) -> dict[str, Any]:
        """Return the authenticated user plus server version.

        Bitbucket DC has no dedicated "current user" endpoint, but every
        authenticated response includes the `X-AUSERNAME` header. We probe the
        `application-properties` endpoint and read that header.
        """
        resp = self._client.get("/rest/api/1.0/application-properties")
        if resp.status_code >= 400:
            raise BitbucketError(
                resp.status_code, _extract_error(resp), url=str(resp.request.url)
            )
        data = resp.json() if resp.content else {}
        user = resp.headers.get("X-AUSERNAME")
        return {
            "user": user,
            "authenticated": bool(user) and user.lower() != "anonymous",
            "version": data.get("version") if isinstance(data, dict) else None,
            "displayName": data.get("displayName") if isinstance(data, dict) else None,
        }

    # -- paging ------------------------------------------------------------
    def paged(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        max_items: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Collect items across Bitbucket DC paged responses."""
        items: list[dict[str, Any]] = []
        for item in self._iter_paged(path, params=params, limit=limit):
            items.append(item)
            if max_items is not None and len(items) >= max_items:
                break
        return items

    def _iter_paged(
        self,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Iterator[dict[str, Any]]:
        start = 0
        page_limit = max(1, min(limit, MAX_PAGE_LIMIT))
        while True:
            page_params = dict(params or {})
            page_params["start"] = start
            page_params["limit"] = page_limit
            page = self.get(path, params=page_params)
            if not isinstance(page, dict):
                return
            for value in page.get("values", []):
                yield value
            if page.get("isLastPage", True):
                return
            next_start = page.get("nextPageStart")
            if next_start is None:
                return
            start = next_start


def project_path(project_key: str) -> str:
    """Return the API path segment for a project key.

    Accepts a normal key (`PROJ`) or a personal one (`~sbwo`). A bare
    username may be passed with a leading `~`; the `~` is percent-encoded
    so it survives URL building.
    """
    key = project_key.strip()
    if key.startswith("~"):
        return "~" + quote(key[1:], safe="")
    return quote(key, safe="")


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return resp.text or f"HTTP {resp.status_code}"
    errors = data.get("errors") if isinstance(data, dict) else None
    if errors:
        msgs = [e.get("message", "") for e in errors if isinstance(e, dict)]
        joined = "; ".join(m for m in msgs if m)
        if joined:
            return joined
    return resp.text or f"HTTP {resp.status_code}"
