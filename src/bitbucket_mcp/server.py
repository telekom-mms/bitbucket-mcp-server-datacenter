"""FastMCP server exposing Bitbucket Data Center (Server) operations.

Configuration is read from environment variables so it can be supplied via
VS Code MCP inputs without hardcoding secrets:

    BITBUCKET_BASE_URL   e.g. https://bitbucket-stage.telekom-mms.com
    BITBUCKET_TOKEN      HTTP access token (sent as `Authorization: Bearer ...`)
    BITBUCKET_USERNAME   optional; if set with a secret -> Basic auth
    BITBUCKET_PASSWORD   optional; password/API token for Basic auth
    BITBUCKET_CA_BUNDLE  optional; path to a CA bundle for internal/private CAs
    ENABLE_TOOLS         which tools to expose (see below)

TLS verification is always enforced (PSA "Web Services" 3.02 Req 18 and
"Cryptographic Algorithms" 3.50 Req 43). It cannot be disabled; to trust an
internal certificate authority, point `BITBUCKET_CA_BUNDLE` at its CA bundle
instead of turning verification off.

Tool enablement (`ENABLE_TOOLS`)
--------------------------------
Tools must be explicitly enabled. `ENABLE_TOOLS` is a comma-separated list of
group names and/or individual tool names (case-insensitive):

    read    -> all read-only tools
    write   -> all write/content tools
    all     -> every non-blocked tool
    <name>  -> a single tool, e.g. `get_repository`

If `ENABLE_TOOLS` is unset or empty, only the read-only group is enabled, so
the server is safe by default. Example to allow reads plus PR creation:

    ENABLE_TOOLS=read,create_pull_request

Destructive operations on whole repositories or projects (e.g. deleting a
repository or a project) are intentionally NOT implemented and cannot be
enabled. See `BLOCKED_TOOLS`.

All paths target Bitbucket Data Center REST 1.0. Personal repos use a project
key like `~sbwo`.
"""

from __future__ import annotations

import os
import sys
from typing import Annotated, Any, Callable, Optional

from fastmcp import FastMCP
from pydantic import Field

from .client import BitbucketClient, BitbucketError, project_path

mcp = FastMCP(
    name="bitbucket-datacenter",
    instructions=(
        "Tools for a Bitbucket Data Center / Server instance (NOT Bitbucket "
        "Cloud). Use a project key for `project`; personal repositories use "
        "`~username` (e.g. `~sbwo`)."
    ),
)

# Registry of all defined tools: name -> (category, function).
# `category` is "read" or "write". Registration into `mcp` happens at the end
# based on ENABLE_TOOLS.
_REGISTRY: dict[str, tuple[str, Callable[..., Any]]] = {}

# Tools that are never registered, regardless of ENABLE_TOOLS. These are
# destructive, repo/project-wide operations we deliberately do not expose.
BLOCKED_TOOLS: frozenset[str] = frozenset(
    {
        "delete_repository",
        "delete_project",
        "fork_repository",
    }
)


def _tool(category: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Record a function in the registry without registering it yet."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[fn.__name__] = (category, fn)
        return fn

    return decorator


_client: Optional[BitbucketClient] = None


def _resolve_verify_ssl(
    disable_spec: Optional[str], ca_bundle: Optional[str]
) -> bool | str:
    """Determine the TLS verification setting for httpx.

    Verification is always enforced. Any attempt to disable it via
    ``BITBUCKET_VERIFY_SSL`` is ignored (with a warning on stderr); to trust an
    internal CA, supply a CA bundle path instead. Returns ``True`` for the
    system trust store, or the CA bundle path when provided.
    """
    if disable_spec is not None and disable_spec.strip().lower() in {
        "false",
        "0",
        "no",
        "off",
    }:
        print(
            "[bitbucket-mcp] WARNING: TLS verification cannot be disabled and "
            "is enforced. Set BITBUCKET_CA_BUNDLE to trust an internal CA.",
            file=sys.stderr,
        )

    bundle = (ca_bundle or "").strip()
    if bundle:
        return bundle
    return True


def _get_client() -> BitbucketClient:
    global _client
    if _client is not None:
        return _client

    base_url = os.environ.get("BITBUCKET_BASE_URL", "").strip()
    if not base_url:
        raise RuntimeError("BITBUCKET_BASE_URL is not set.")
    token = os.environ.get("BITBUCKET_TOKEN") or None
    username = os.environ.get("BITBUCKET_USERNAME") or None
    password = os.environ.get("BITBUCKET_PASSWORD") or None
    verify_ssl = _resolve_verify_ssl(
        os.environ.get("BITBUCKET_VERIFY_SSL"),
        os.environ.get("BITBUCKET_CA_BUNDLE"),
    )

    if not token and not (username and password):
        raise RuntimeError(
            "Provide BITBUCKET_TOKEN, or BITBUCKET_USERNAME + BITBUCKET_PASSWORD."
        )

    _client = BitbucketClient(
        base_url,
        token=token,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )
    return _client


def _api(project: str, slug: str) -> str:
    return f"/rest/api/1.0/projects/{project_path(project)}/repos/{slug}"


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------
@_tool("read")
def get_current_user() -> dict[str, Any]:
    """Verify connectivity/auth and return the authenticated user and server version."""
    return _get_client().whoami()


@_tool("read")
def list_projects(
    name: Annotated[Optional[str], Field(description="Filter by project name.")] = None,
    limit: Annotated[int, Field(description="Max projects to return.", ge=1, le=1000)] = 25,
) -> list[dict[str, Any]]:
    """List Bitbucket projects visible to the authenticated user."""
    return _get_client().paged(
        "/rest/api/1.0/projects", params={"name": name}, max_items=limit
    )


@_tool("read")
def list_repositories(
    project: Annotated[str, Field(description="Project key. Personal repos use `~username`.")],
    limit: Annotated[int, Field(description="Max repos to return.", ge=1, le=1000)] = 25,
) -> list[dict[str, Any]]:
    """List repositories in a project."""
    return _get_client().paged(
        f"/rest/api/1.0/projects/{project_path(project)}/repos", max_items=limit
    )


@_tool("read")
def get_repository(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
) -> dict[str, Any]:
    """Get details for a single repository."""
    return _get_client().get(_api(project, slug))


@_tool("read")
def list_branches(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    filter_text: Annotated[Optional[str], Field(description="Filter branches by text.")] = None,
    limit: Annotated[int, Field(description="Max branches to return.", ge=1, le=1000)] = 25,
) -> list[dict[str, Any]]:
    """List branches in a repository."""
    return _get_client().paged(
        f"{_api(project, slug)}/branches",
        params={"filterText": filter_text},
        max_items=limit,
    )


@_tool("read")
def list_commits(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    until: Annotated[Optional[str], Field(description="Branch/tag/commit to list from (newest).")] = None,
    limit: Annotated[int, Field(description="Max commits to return.", ge=1, le=1000)] = 25,
) -> list[dict[str, Any]]:
    """List commits, optionally starting from a branch/tag/commit ref."""
    return _get_client().paged(
        f"{_api(project, slug)}/commits", params={"until": until}, max_items=limit
    )


@_tool("read")
def get_file_content(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    path: Annotated[str, Field(description="File path within the repository.")],
    at: Annotated[Optional[str], Field(description="Branch/tag/commit ref. Defaults to default branch.")] = None,
) -> str:
    """Return the raw text content of a file."""
    return _get_client().get(
        f"{_api(project, slug)}/raw/{path}", params={"at": at}, raw=True
    )


@_tool("read")
def browse_files(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    path: Annotated[str, Field(description="Directory path; empty for repo root.")] = "",
    at: Annotated[Optional[str], Field(description="Branch/tag/commit ref.")] = None,
) -> dict[str, Any]:
    """List files/directories at a path (tree browsing)."""
    return _get_client().get(
        f"{_api(project, slug)}/browse/{path}", params={"at": at}
    )


@_tool("read")
def list_pull_requests(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    state: Annotated[str, Field(description="OPEN, DECLINED, MERGED, or ALL.")] = "OPEN",
    limit: Annotated[int, Field(description="Max PRs to return.", ge=1, le=1000)] = 25,
) -> list[dict[str, Any]]:
    """List pull requests in a repository."""
    return _get_client().paged(
        f"{_api(project, slug)}/pull-requests",
        params={"state": state},
        max_items=limit,
    )


@_tool("read")
def get_pull_request(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    pull_request_id: Annotated[int, Field(description="Pull request id.")],
) -> dict[str, Any]:
    """Get details for a single pull request, including its current version."""
    return _get_client().get(f"{_api(project, slug)}/pull-requests/{pull_request_id}")


@_tool("read")
def get_pull_request_diff(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    pull_request_id: Annotated[int, Field(description="Pull request id.")],
    context_lines: Annotated[int, Field(description="Diff context lines.", ge=0, le=100)] = 10,
) -> Any:
    """Get the unified diff for a pull request."""
    return _get_client().get(
        f"{_api(project, slug)}/pull-requests/{pull_request_id}/diff",
        params={"contextLines": context_lines},
    )


@_tool("read")
def get_pull_request_activities(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    pull_request_id: Annotated[int, Field(description="Pull request id.")],
    limit: Annotated[int, Field(description="Max activities to return.", ge=1, le=1000)] = 25,
) -> list[dict[str, Any]]:
    """List activity (comments, approvals, updates) for a pull request."""
    return _get_client().paged(
        f"{_api(project, slug)}/pull-requests/{pull_request_id}/activities",
        max_items=limit,
    )


# --------------------------------------------------------------------------
# Write / content tools
# --------------------------------------------------------------------------
@_tool("write")
def put_file(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    path: Annotated[str, Field(description="File path to create or update.")],
    content: Annotated[str, Field(description="New file content.")],
    message: Annotated[str, Field(description="Commit message.")],
    branch: Annotated[str, Field(description="Target branch. Created if the repo is empty.")] = "main",
    source_commit_id: Annotated[Optional[str], Field(description="Expected last commit id (for updates).")] = None,
) -> dict[str, Any]:
    """Create or update a file via a commit. Seeds the default branch in an empty repo."""
    data: dict[str, Any] = {
        "content": content,
        "message": message,
        "branch": branch,
    }
    if source_commit_id:
        data["sourceCommitId"] = source_commit_id
    return _get_client().put_form(f"{_api(project, slug)}/browse/{path}", data)


@_tool("write")
def create_branch(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    name: Annotated[str, Field(description="New branch name, e.g. `feature/x`.")],
    start_point: Annotated[str, Field(description="Ref/commit to branch from, e.g. `main`.")],
) -> dict[str, Any]:
    """Create a branch from a start point."""
    return _get_client().post(
        f"{_api(project, slug)}/branches",
        json={"name": name, "startPoint": start_point},
    )


@_tool("write")
def create_pull_request(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    title: Annotated[str, Field(description="Pull request title.")],
    from_branch: Annotated[str, Field(description="Source branch name.")],
    to_branch: Annotated[str, Field(description="Target branch name.")],
    description: Annotated[Optional[str], Field(description="Optional PR description.")] = None,
    reviewers: Annotated[
        Optional[list[str]],
        Field(description="Reviewer usernames to add, in addition to default reviewers."),
    ] = None,
    apply_default_reviewers: Annotated[
        bool,
        Field(description="Resolve and add the repo's default reviewers for this branch pair."),
    ] = True,
) -> dict[str, Any]:
    """Create a pull request between two branches in the same repository.

    By default the repository's configured *default reviewers* for the given
    source/target branch pair are resolved and added automatically. Pass
    ``apply_default_reviewers=False`` to skip that, and/or ``reviewers`` to add
    explicit reviewers by username. The PR author is always excluded, since
    Bitbucket rejects a PR whose author is also a reviewer.
    """
    client = _get_client()
    ref = {
        "repository": {"slug": slug, "project": {"key": project}},
    }
    payload: dict[str, Any] = {
        "title": title,
        "fromRef": {"id": f"refs/heads/{from_branch}", **ref},
        "toRef": {"id": f"refs/heads/{to_branch}", **ref},
    }
    if description:
        payload["description"] = description

    reviewer_names: list[str] = list(reviewers or [])
    if apply_default_reviewers:
        reviewer_names.extend(
            _resolve_default_reviewers(client, project, slug, from_branch, to_branch)
        )

    author = (client.whoami() or {}).get("user") or ""
    seen: set[str] = set()
    unique: list[str] = []
    for name in reviewer_names:
        key = name.lower()
        if not name or key in seen or key == author.lower():
            continue
        seen.add(key)
        unique.append(name)

    if unique:
        payload["reviewers"] = [{"user": {"name": name}} for name in unique]

    return client.post(f"{_api(project, slug)}/pull-requests", json=payload)


def _resolve_default_reviewers(
    client: BitbucketClient,
    project: str,
    slug: str,
    from_branch: str,
    to_branch: str,
) -> list[str]:
    """Return usernames of the repo's default reviewers for a branch pair.

    Uses the Bitbucket DC default-reviewers add-on API, which evaluates the
    configured reviewer conditions for the given source/target refs. Failures
    are non-fatal: if the endpoint is unavailable, an empty list is returned so
    PR creation still succeeds.
    """
    try:
        repo = client.get(_api(project, slug))
        repo_id = repo.get("id") if isinstance(repo, dict) else None
        if repo_id is None:
            return []
        path = (
            f"/rest/default-reviewers/1.0/projects/{project_path(project)}"
            f"/repos/{slug}/reviewers"
        )
        result = client.get(
            path,
            params={
                "sourceRepoId": repo_id,
                "targetRepoId": repo_id,
                "sourceRefId": f"refs/heads/{from_branch}",
                "targetRefId": f"refs/heads/{to_branch}",
            },
        )
    except BitbucketError:
        return []

    if not isinstance(result, list):
        return []
    return [
        u["name"]
        for u in result
        if isinstance(u, dict) and isinstance(u.get("name"), str)
    ]


@_tool("write")
def add_pull_request_comment(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    pull_request_id: Annotated[int, Field(description="Pull request id.")],
    text: Annotated[str, Field(description="Comment text (Markdown supported).")],
) -> dict[str, Any]:
    """Add a general comment to a pull request."""
    return _get_client().post(
        f"{_api(project, slug)}/pull-requests/{pull_request_id}/comments",
        json={"text": text},
    )


@_tool("write")
def merge_pull_request(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    pull_request_id: Annotated[int, Field(description="Pull request id.")],
    version: Annotated[int, Field(description="Current PR version (from get_pull_request).")],
) -> dict[str, Any]:
    """Merge a pull request. Requires the current PR version for optimistic locking."""
    return _get_client().post(
        f"{_api(project, slug)}/pull-requests/{pull_request_id}/merge",
        params={"version": version},
    )


@_tool("write")
def decline_pull_request(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    pull_request_id: Annotated[int, Field(description="Pull request id.")],
    version: Annotated[int, Field(description="Current PR version (from get_pull_request).")],
) -> dict[str, Any]:
    """Decline (reject) a pull request."""
    return _get_client().post(
        f"{_api(project, slug)}/pull-requests/{pull_request_id}/decline",
        params={"version": version},
    )


@_tool("write")
def delete_branch(
    project: Annotated[str, Field(description="Project key (`~username` for personal).")],
    slug: Annotated[str, Field(description="Repository slug.")],
    name: Annotated[str, Field(description="Branch name to delete, e.g. `feature/x`.")],
) -> dict[str, str]:
    """Delete a branch (uses the branch-utils API). Useful for test cleanup."""
    client = _get_client()
    client.delete(
        f"/rest/branch-utils/1.0/projects/{project_path(project)}/repos/{slug}/branches",
        json={"name": f"refs/heads/{name}"},
    )
    return {"status": "deleted", "branch": name}


# --------------------------------------------------------------------------
# Tool enablement
# --------------------------------------------------------------------------
def _resolve_enabled_tools(spec: Optional[str]) -> set[str]:
    """Translate an ENABLE_TOOLS spec into a concrete set of tool names.

    Unknown/blocked names are ignored. An empty/unset spec defaults to the
    read-only group.
    """
    read_tools = {n for n, (cat, _) in _REGISTRY.items() if cat == "read"}
    write_tools = {n for n, (cat, _) in _REGISTRY.items() if cat == "write"}

    tokens = [t.strip().lower() for t in (spec or "").split(",") if t.strip()]
    if not tokens:
        return set(read_tools)

    enabled: set[str] = set()
    for token in tokens:
        if token == "read":
            enabled |= read_tools
        elif token == "write":
            enabled |= write_tools
        elif token == "all":
            enabled |= read_tools | write_tools
        elif token in {"none", "off"}:
            continue
        elif token in _REGISTRY:
            enabled.add(token)
        # else: unknown name -> ignored
    return enabled - BLOCKED_TOOLS


def register_enabled_tools() -> list[str]:
    """Register the enabled, non-blocked tools with the MCP server."""
    enabled = _resolve_enabled_tools(os.environ.get("ENABLE_TOOLS"))
    registered: list[str] = []
    for name in sorted(enabled):
        if name in BLOCKED_TOOLS:
            continue
        _category, fn = _REGISTRY[name]
        mcp.tool(fn)
        registered.append(name)
    print(
        f"[bitbucket-datacenter] enabled tools ({len(registered)}): "
        + (", ".join(registered) if registered else "<none>"),
        file=sys.stderr,
    )
    return registered


def main() -> None:
    """Entry point: register enabled tools and run the server over stdio."""
    register_enabled_tools()
    mcp.run()


if __name__ == "__main__":
    main()
