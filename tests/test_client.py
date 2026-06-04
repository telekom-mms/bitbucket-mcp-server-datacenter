"""Offline unit tests for the Bitbucket DC client (no network).

These use httpx.MockTransport to validate the pure logic that the live smoke
test does not isolate: auth header selection, URL/key encoding, paging,
error extraction, and the multipart file upload.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from bitbucket_mcp.client import (
    BitbucketClient,
    BitbucketError,
    project_path,
)


def make_client(handler, **kwargs) -> BitbucketClient:
    transport = httpx.MockTransport(handler)
    return BitbucketClient(
        "https://bb.example.com",
        token=kwargs.pop("token", "BBDC-xyz"),
        transport=transport,
        **kwargs,
    )


# -- project_path -----------------------------------------------------------
def test_project_path_normal_key():
    assert project_path("PROJ") == "PROJ"


def test_project_path_personal_key():
    assert project_path("~sbwo") == "~sbwo"


def test_project_path_encodes_special_chars():
    # spaces / slashes in a key must be encoded; the leading ~ preserved
    assert project_path("~a b") == "~a%20b"


# -- auth selection ---------------------------------------------------------
def test_bearer_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={})

    c = make_client(handler, token="BBDC-tok")
    c.get("/rest/api/1.0/projects")
    assert seen["auth"] == "Bearer BBDC-tok"


def test_basic_auth_header_when_username_present():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    c = BitbucketClient(
        "https://bb.example.com",
        username="sbwo",
        password="secret",
        transport=transport,
    )
    c.get("/x")
    expected = "Basic " + base64.b64encode(b"sbwo:secret").decode()
    assert seen["auth"] == expected


def test_no_credentials_raises():
    with pytest.raises(ValueError):
        BitbucketClient("https://bb.example.com")


def test_empty_base_url_raises():
    with pytest.raises(ValueError):
        BitbucketClient("", token="x")


# -- error handling ---------------------------------------------------------
def test_error_response_raises_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404, json={"errors": [{"message": "Repository not found"}]}
        )

    c = make_client(handler)
    with pytest.raises(BitbucketError) as exc:
        c.get("/missing")
    assert exc.value.status_code == 404
    assert "Repository not found" in str(exc.value)


def test_error_response_plain_text_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    c = make_client(handler)
    with pytest.raises(BitbucketError) as exc:
        c.get("/x")
    assert "boom" in str(exc.value)


# -- paging -----------------------------------------------------------------
def test_paging_walks_all_pages():
    pages = {
        0: {"values": [{"id": 1}, {"id": 2}], "isLastPage": False, "nextPageStart": 2},
        2: {"values": [{"id": 3}], "isLastPage": True},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("start", "0"))
        return httpx.Response(200, json=pages[start])

    c = make_client(handler)
    items = c.paged("/things", limit=2)
    assert [i["id"] for i in items] == [1, 2, 3]


def test_paging_respects_max_items():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"values": [{"id": 1}, {"id": 2}, {"id": 3}], "isLastPage": True},
        )

    c = make_client(handler)
    items = c.paged("/things", max_items=2)
    assert len(items) == 2


# -- multipart upload -------------------------------------------------------
def test_put_form_sends_multipart():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.content.decode("utf-8", "replace")
        return httpx.Response(200, json={"id": "abc"})

    c = make_client(handler)
    result = c.put_form("/browse/README.md", {"content": "hi", "message": "m", "branch": "main"})
    assert result["id"] == "abc"
    assert "multipart/form-data" in seen["content_type"]
    assert "hi" in seen["body"]


# -- whoami -----------------------------------------------------------------
def test_whoami_reads_ausername_header():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"version": "9.4.20", "displayName": "Bitbucket"},
            headers={"X-AUSERNAME": "sbwo"},
        )

    c = make_client(handler)
    info = c.whoami()
    assert info["user"] == "sbwo"
    assert info["authenticated"] is True
    assert info["version"] == "9.4.20"


def test_whoami_anonymous_not_authenticated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, headers={"X-AUSERNAME": "anonymous"})

    c = make_client(handler)
    info = c.whoami()
    assert info["authenticated"] is False
