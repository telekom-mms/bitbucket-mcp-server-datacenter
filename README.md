# bitbucket-mcp-server-datacenter

An MCP server for **Bitbucket Data Center / Server** (not Bitbucket Cloud).
It targets the Data Center REST API (`/rest/api/1.0`, project/repo-centric
paths, `start`/`limit`/`isLastPage` paging) and supports personal
repositories via `~username` project keys.

## Requirements

- Python >= 3.11
- [`uv`](https://docs.astral.sh/uv/) (used to run/test the server)

## Install / use without cloning

Once published to PyPI, the server can be run directly with
[`uvx`](https://docs.astral.sh/uv/guides/tools/) — no repository checkout and no
manual `pip install` required. `uvx` fetches the package into an isolated,
cached environment and runs its console entry point:

```sh
BITBUCKET_BASE_URL="https://your-bitbucket.example.com" \
BITBUCKET_TOKEN="<your-token>" \
ENABLE_TOOLS="read" \
uvx bitbucket-mcp-server-datacenter
```

To wire it into an MCP client (e.g. VS Code), point the server `command` at
`uvx` instead of a local checkout. Example `mcp.json`:

```jsonc
{
  "inputs": [
    {
      "id": "bitbucket_token",
      "type": "promptString",
      "description": "Bitbucket Data Center HTTP access token (sent as Bearer)",
      "password": true
    }
  ],
  "servers": {
    "bitbucket-datacenter": {
      "type": "stdio",
      "command": "uvx",
      "args": ["bitbucket-mcp-server-datacenter"],
      "env": {
        "BITBUCKET_BASE_URL": "https://your-bitbucket.example.com",
        "BITBUCKET_TOKEN": "${input:bitbucket_token}",
        "ENABLE_TOOLS": "read"
      }
    }
  }
}
```

Pin a specific release with `uvx bitbucket-mcp-server-datacenter@0.1.0` (or
`"args": ["bitbucket-mcp-server-datacenter@0.1.0"]`).

### Without a package index (straight from Git)

If the package is not on an index, `uvx` can install it directly from the
repository — still no manual clone:

```sh
uvx --from git+https://github.com/telekom-mms/bitbucket-mcp-server-datacenter \
  bitbucket-mcp-server-datacenter
```

## Configuration

The server reads configuration from environment variables:

| Variable               | Required | Description                                                |
| ---------------------- | -------- | ---------------------------------------------------------- |
| `BITBUCKET_BASE_URL`   | yes      | Base URL, e.g. `https://example.com`   |
| `BITBUCKET_TOKEN`      | \*       | HTTP access token, sent as `Authorization: Bearer <token>` |
| `BITBUCKET_USERNAME`   | \*       | Username for Basic auth (used together with a secret)      |
| `BITBUCKET_PASSWORD`   | \*       | Password / API token for Basic auth                        |
| `BITBUCKET_CA_BUNDLE`  | no       | Path to a CA bundle to trust an internal/private CA        |
| `ENABLE_TOOLS`         | no       | Which tools to expose (see [Tool enablement](#tool-enablement)). Empty = read-only. |

\* Provide **either** `BITBUCKET_TOKEN` **or** `BITBUCKET_USERNAME` +
`BITBUCKET_PASSWORD`. When a username and secret are both set, Basic auth is
used; otherwise the token is sent as a Bearer header.

> **TLS is always verified.** Verification is enforced and cannot be turned
> off (PSA *Web Services* 3.02 Req 18 / *Cryptographic Algorithms* 3.50
> Req 43). Any attempt to disable it via `BITBUCKET_VERIFY_SSL` is ignored and
> logged as a warning. To trust an internal certificate authority, set
> `BITBUCKET_CA_BUNDLE` to its CA bundle path instead of disabling verification.

## Token lifecycle

The Bitbucket HTTP access token is the only long-lived secret used by this
server. Handle it as a technical-account credential:

- **Provisioning** — create a personal **HTTP access token** in Bitbucket Data
  Center (*Manage account → HTTP access tokens*) with the **minimum scope**
  needed (read-only unless write tools are enabled). Match the token scope to
  `ENABLE_TOOLS`.
- **Storage** — never commit the token. In VS Code it is supplied through the
  `bitbucket_token` prompt input (`password: true`) and passed via the
  `BITBUCKET_TOKEN` environment variable only. Keep it out of logs and shell
  history.
- **Transport** — the token is sent only over TLS in the `Authorization`
  header, never in a URL (PSA *Web Services* 3.02 Req 21).
- **Rotation** — rotate regularly (at least every 12 months, sooner for
  technical accounts) and immediately if it may have been exposed. Create the
  new token, update the secret, then revoke the old one.
- **Revocation** — revoke the token in Bitbucket as soon as it is no longer
  needed or on suspected compromise; revocation takes effect immediately.

> If a token ever appears in plain text (chat, logs, screen sharing), treat it
> as compromised and rotate it right away.


## Tool enablement

Tools must be **explicitly enabled**. `ENABLE_TOOLS` is a comma-separated list
of group names and/or individual tool names (case-insensitive):

| Token       | Effect                                                  |
| ----------- | ------------------------------------------------------- |
| `read`      | Enable all read-only tools                              |
| `write`     | Enable all write/content tools                          |
| `all`       | Enable every non-blocked tool                           |
| `<name>`    | Enable a single tool, e.g. `create_pull_request`        |
| `none`/`off`| Enable nothing                                          |

- If `ENABLE_TOOLS` is **unset or empty**, only the **read-only** group is
  enabled, so the server is safe by default.
- Unknown tool names are ignored.
- Examples:
  - `ENABLE_TOOLS=read` — read-only (default).
  - `ENABLE_TOOLS=read,create_pull_request,add_pull_request_comment` — reads plus PR authoring.
  - `ENABLE_TOOLS=all` — everything except blocked tools.

The set of enabled tools is logged to stderr on startup.

### Permanently blocked tools

Destructive, repository/project-wide operations are intentionally **not
implemented** and cannot be enabled even with `all`:

- `delete_repository`
- `delete_project`
- `fork_repository`


## Run in VS Code

The workspace ships a `.vscode/mcp.json`. The base URL and `ENABLE_TOOLS` are
set directly in the server's `env`; only the access token is requested as a
masked input on startup and is never written to the file. Adjust
`ENABLE_TOOLS` in `.vscode/mcp.json` to change which tools are exposed.

## Run / smoke test from the CLI

```sh
uv sync
BITBUCKET_BASE_URL="https://example.com" \
BITBUCKET_TOKEN="<your-token>" \
ENABLE_TOOLS="read" \
uv run bitbucket-mcp-server-datacenter
```

## Releasing (maintainers)

The package ships a console entry point
(`bitbucket-mcp-server-datacenter`) and builds with `hatchling`, so consumers
can run it with `uvx` without cloning (see
[Install / use without cloning](#install--use-without-cloning)).

Releases are automated: the
[`.github/workflows/release.yml`](.github/workflows/release.yml) workflow runs
the tests, builds the wheel + sdist, and publishes to PyPI via
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC, **no
stored token**) whenever a `v*` tag is pushed.

### One-time setup

Already configured for this project; only needed when forking or re-creating it.

1. **PyPI trusted publisher** — on the project's
   [publishing settings](https://pypi.org/manage/project/bitbucket-mcp-server-datacenter/settings/publishing/),
   add a GitHub publisher with:
   - Owner: `telekom-mms`
   - Repository: `bitbucket-mcp-server-datacenter`
   - Workflow: `release.yml`
   - Environment: `pypi`
2. **GitHub environment** — create an environment named `pypi` in the repo
   settings. Optionally restrict its deployment branches/tags to `v*` so only
   release tags can publish.

### Cutting a release

1. Update `version` in [`pyproject.toml`](pyproject.toml) following
   [SemVer](https://semver.org/). PyPI never allows re-uploading an existing
   version, so every release needs a new number.
2. Commit the bump and push to `main`.
3. Tag the release and push the tag — this triggers the workflow:

   ```sh
   git tag v0.1.1
   git push origin v0.1.1
   ```

4. Watch the run under
   [Actions](https://github.com/telekom-mms/bitbucket-mcp-server-datacenter/actions);
   on success the new version appears on
   [PyPI](https://pypi.org/project/bitbucket-mcp-server-datacenter/).
5. Verify the published release installs cleanly:

   ```sh
   uvx bitbucket-mcp-server-datacenter@<version> --help
   ```

### Manual publish (fallback)

Only if CI is unavailable. Build, inspect, and upload locally with a PyPI API
token (never commit it):

```sh
uv build
tar -tzf dist/*.tar.gz                                   # no secrets in sdist
uv publish --publish-url https://test.pypi.org/legacy/   # optional TestPyPI dry run
uv publish                                               # production (prompts for token)
```

## Tools

All tools below are gated by [`ENABLE_TOOLS`](#tool-enablement). The
**Category** column controls which group (`read` / `write`) enables a tool.

### Read tools (category `read`)

| Tool                          | Description                                                        |
| ----------------------------- | ----------------------------------------------------------------- |
| `get_current_user`            | Verify auth/connectivity; returns the user and server version.    |
| `list_projects`               | List projects visible to the user (optional name filter).         |
| `list_repositories`           | List repositories in a project (`~username` for personal).        |
| `get_repository`              | Get details for a single repository.                              |
| `list_branches`               | List branches (optional text filter).                             |
| `list_commits`                | List commits, optionally from a branch/tag/commit ref.            |
| `get_file_content`            | Return raw text content of a file at an optional ref.             |
| `browse_files`                | List files/directories at a path (tree browsing).                 |
| `list_pull_requests`          | List PRs by state (`OPEN`/`DECLINED`/`MERGED`/`ALL`).             |
| `get_pull_request`            | Get a single PR, including its current `version`.                 |
| `get_pull_request_diff`       | Get the unified diff for a PR.                                    |
| `get_pull_request_activities` | List PR activity (comments, approvals, updates).                  |

### Write / content tools (category `write`)

| Tool                       | Description                                                           |
| -------------------------- | -------------------------------------------------------------------- |
| `put_file`                 | Create/update a file via a commit; seeds the default branch if empty.|
| `create_branch`            | Create a branch from a start point.                                  |
| `create_pull_request`      | Create a PR between two branches; resolves & applies the repo's default reviewers by default. |
| `add_pull_request_comment` | Add a general comment to a PR.                                       |
| `merge_pull_request`       | Merge a PR (requires current PR `version`).                          |
| `decline_pull_request`     | Decline a PR (requires current PR `version`).                        |
| `delete_branch`            | Delete a branch (branch-utils API); useful for cleanup.             |

### Notes specific to Bitbucket Data Center

- Personal repositories use the project key `~username` (e.g. `~sbwo`).
- `put_file` uses the `browse` endpoint, which requires `multipart/form-data`.
- `merge_pull_request` / `decline_pull_request` require the current PR
  `version` (obtain it via `get_pull_request`) for optimistic locking.
- `delete_branch` uses the `branch-utils` API (`/rest/branch-utils/1.0/...`).
- `create_pull_request` applies the repository's **default reviewers** by
  default: it resolves them via the default-reviewers add-on API
  (`/rest/default-reviewers/1.0/...`) for the given source/target branch pair
  and adds them to the PR. Pass `apply_default_reviewers=false` to skip this,
  and/or `reviewers=["user1", ...]` to add explicit reviewers by username. The
  PR author is always excluded (Bitbucket rejects a PR whose author is also a
  reviewer). If the default-reviewers endpoint is unavailable, PR creation
  still succeeds without default reviewers.

## Tests

Offline unit tests (no network; httpx `MockTransport`) cover three layers:

- **Client** (`test_client.py`): auth header selection, key/URL encoding,
  paging, error extraction, multipart upload, and `whoami`.
- **Tool enablement** (`test_enablement.py`): `ENABLE_TOOLS` resolution,
  blocked-tool enforcement, TLS enforcement, registry composition, and
  `register_enabled_tools`.
- **Server tools** (`test_server_tools.py`): request paths and payloads built
  by the tools (`refs/heads/` refs, `create_branch`, merge/decline `version`
  param, and the `branch-utils` `delete_branch` path).

```sh
uv sync
uv run pytest -q
```

