"""Offline tests for the server tool layer (no network).

These monkeypatch `_get_client` with a MockTransport-backed client so the pure
request-building logic of the tools (paths, payloads, optimistic-locking
params) is exercised without hitting Bitbucket.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_mcp import server
from bitbucket_mcp.client import BitbucketClient


@pytest.fixture
def captured(monkeypatch):
    """Wire the server tools to a MockTransport and capture requests."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True})

    client = BitbucketClient(
        "https://bb.example.com",
        token="BBDC-tok",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(server, "_get_client", lambda: client)
    return calls


# -- read tool paths --------------------------------------------------------
def test_get_repository_uses_personal_project_path(captured):
    server.get_repository("~sbwo", "sbwo_testing")
    assert captured[-1].url.path == (
        "/rest/api/1.0/projects/~sbwo/repos/sbwo_testing"
    )


def test_list_pull_requests_passes_state(captured):
    server.list_pull_requests("PROJ", "repo", state="ALL", limit=5)
    assert captured[-1].url.params.get("state") == "ALL"


# -- create_pull_request payload -------------------------------------------
def test_create_pull_request_builds_refs_and_repo(captured):
    server.create_pull_request(
        "~sbwo", "repo", "title", "feature/x", "main", description="d"
    )
    req = captured[-1]
    assert req.url.path.endswith("/pull-requests")
    import json as _json

    body = _json.loads(req.content)
    assert body["fromRef"]["id"] == "refs/heads/feature/x"
    assert body["toRef"]["id"] == "refs/heads/main"
    assert body["fromRef"]["repository"]["slug"] == "repo"
    assert body["fromRef"]["repository"]["project"]["key"] == "~sbwo"
    assert body["description"] == "d"


def test_create_pull_request_omits_empty_description(captured):
    server.create_pull_request("PROJ", "repo", "t", "a", "b")
    import json as _json

    body = _json.loads(captured[-1].content)
    assert "description" not in body


def _reviewer_client(monkeypatch, calls, default_reviewers, *, ausername=""):
    """Wire server tools to a client that answers repo/default-reviewer/whoami."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/application-properties"):
            headers = {"X-AUSERNAME": ausername} if ausername else {}
            return httpx.Response(200, json={"version": "8.0"}, headers=headers)
        if "/default-reviewers/" in path:
            return httpx.Response(200, json=default_reviewers)
        if path.endswith("/repos/repo"):
            return httpx.Response(200, json={"id": 42, "slug": "repo"})
        return httpx.Response(200, json={"id": 99})

    client = BitbucketClient(
        "https://bb.example.com",
        token="BBDC-tok",
        transport=httpx.MockTransport(handler),
    )
    monkeypatch.setattr(server, "_get_client", lambda: client)


def test_create_pull_request_applies_default_reviewers(monkeypatch):
    import json as _json

    calls: list[httpx.Request] = []
    _reviewer_client(
        monkeypatch,
        calls,
        [{"name": "alice"}, {"name": "bob"}],
        ausername="carol",
    )
    server.create_pull_request("PROJ", "repo", "t", "feature/x", "main")

    dr = next(r for r in calls if "/default-reviewers/" in r.url.path)
    assert dr.url.params.get("sourceRefId") == "refs/heads/feature/x"
    assert dr.url.params.get("targetRefId") == "refs/heads/main"
    assert dr.url.params.get("sourceRepoId") == "42"

    post = calls[-1]
    body = _json.loads(post.content)
    names = [r["user"]["name"] for r in body["reviewers"]]
    assert names == ["alice", "bob"]


def test_create_pull_request_excludes_author_and_merges_explicit(monkeypatch):
    import json as _json

    calls: list[httpx.Request] = []
    _reviewer_client(
        monkeypatch,
        calls,
        [{"name": "alice"}, {"name": "carol"}],
        ausername="carol",
    )
    server.create_pull_request(
        "PROJ", "repo", "t", "feature/x", "main", reviewers=["dave", "alice"]
    )

    body = _json.loads(calls[-1].content)
    names = [r["user"]["name"] for r in body["reviewers"]]
    # dave + alice explicit, alice from defaults deduped, carol (author) excluded
    assert names == ["dave", "alice"]


def test_create_pull_request_skips_default_reviewers_when_disabled(monkeypatch):
    import json as _json

    calls: list[httpx.Request] = []
    _reviewer_client(monkeypatch, calls, [{"name": "alice"}])
    server.create_pull_request(
        "PROJ", "repo", "t", "feature/x", "main", apply_default_reviewers=False
    )

    assert not any("/default-reviewers/" in r.url.path for r in calls)
    body = _json.loads(calls[-1].content)
    assert "reviewers" not in body


# -- create_branch ----------------------------------------------------------
def test_create_branch_payload(captured):
    server.create_branch("PROJ", "repo", "feature/x", "main")
    import json as _json

    body = _json.loads(captured[-1].content)
    assert body == {"name": "feature/x", "startPoint": "main"}


# -- merge / decline use the version param ----------------------------------
def test_merge_pull_request_sends_version(captured):
    server.merge_pull_request("PROJ", "repo", 7, 3)
    req = captured[-1]
    assert req.url.path.endswith("/pull-requests/7/merge")
    assert req.url.params.get("version") == "3"


def test_decline_pull_request_sends_version(captured):
    server.decline_pull_request("PROJ", "repo", 7, 3)
    req = captured[-1]
    assert req.url.path.endswith("/pull-requests/7/decline")
    assert req.url.params.get("version") == "3"


# -- delete_branch uses branch-utils API ------------------------------------
def test_delete_branch_path_and_payload(captured):
    result = server.delete_branch("~sbwo", "repo", "feature/x")
    req = captured[-1]
    assert req.method == "DELETE"
    assert req.url.path == (
        "/rest/branch-utils/1.0/projects/~sbwo/repos/repo/branches"
    )
    import json as _json

    assert _json.loads(req.content) == {"name": "refs/heads/feature/x"}
    assert result == {"status": "deleted", "branch": "feature/x"}
