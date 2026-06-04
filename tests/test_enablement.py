"""Tests for ENABLE_TOOLS resolution and blocked-tool enforcement."""

from __future__ import annotations

from bitbucket_mcp import server


def names(spec):
    return server._resolve_enabled_tools(spec)


def test_unset_defaults_to_read_only():
    enabled = names(None)
    assert "get_repository" in enabled
    assert "create_pull_request" not in enabled
    assert "put_file" not in enabled


def test_empty_string_defaults_to_read_only():
    assert names("") == names(None)


def test_read_group():
    enabled = names("read")
    assert all(server._REGISTRY[n][0] == "read" for n in enabled)


def test_write_group():
    enabled = names("write")
    assert all(server._REGISTRY[n][0] == "write" for n in enabled)
    assert "create_pull_request" in enabled


def test_all_group_excludes_blocked():
    enabled = names("all")
    assert "put_file" in enabled
    assert "get_repository" in enabled
    assert enabled.isdisjoint(server.BLOCKED_TOOLS)


def test_single_tool_name():
    enabled = names("get_repository")
    assert enabled == {"get_repository"}


def test_combination_groups_and_names():
    enabled = names("read,create_pull_request")
    assert "create_pull_request" in enabled
    assert "get_repository" in enabled
    assert "merge_pull_request" not in enabled


def test_none_disables_everything():
    assert names("none") == set()
    assert names("off") == set()


def test_unknown_name_ignored():
    assert names("does_not_exist") == set()


def test_blocked_tool_never_enabled_even_with_all():
    enabled = names("all,delete_repository,delete_project,fork_repository")
    assert enabled.isdisjoint(server.BLOCKED_TOOLS)


def test_case_insensitive():
    assert names("READ") == names("read")


def test_blocked_tools_are_not_implemented():
    # blocked tool names must not exist as real, registrable tools
    for name in server.BLOCKED_TOOLS:
        assert name not in server._REGISTRY


# -- TLS enforcement -------------------------------------------------------


def test_verify_ssl_defaults_to_enabled():
    assert server._resolve_verify_ssl(None, None) is True


def test_verify_ssl_cannot_be_disabled():
    for spec in ("false", "0", "no", "off", "FALSE", " No "):
        assert server._resolve_verify_ssl(spec, None) is True


def test_verify_ssl_uses_ca_bundle_when_provided():
    assert server._resolve_verify_ssl(None, "/etc/ssl/internal-ca.pem") == (
        "/etc/ssl/internal-ca.pem"
    )


def test_ca_bundle_used_even_when_disable_attempted():
    assert server._resolve_verify_ssl("false", "/etc/ssl/ca.pem") == "/etc/ssl/ca.pem"


# -- registry composition --------------------------------------------------

EXPECTED_READ = {
    "get_current_user",
    "list_projects",
    "list_repositories",
    "get_repository",
    "list_branches",
    "list_commits",
    "get_file_content",
    "browse_files",
    "list_pull_requests",
    "get_pull_request",
    "get_pull_request_diff",
    "get_pull_request_activities",
}

EXPECTED_WRITE = {
    "put_file",
    "create_branch",
    "create_pull_request",
    "add_pull_request_comment",
    "merge_pull_request",
    "decline_pull_request",
    "delete_branch",
}


def test_registry_has_expected_read_tools():
    read = {n for n, (cat, _) in server._REGISTRY.items() if cat == "read"}
    assert read == EXPECTED_READ


def test_registry_has_expected_write_tools():
    write = {n for n, (cat, _) in server._REGISTRY.items() if cat == "write"}
    assert write == EXPECTED_WRITE


def test_registry_categories_are_valid():
    assert all(cat in {"read", "write"} for cat, _ in server._REGISTRY.values())


def test_register_enabled_tools_returns_all_non_blocked(monkeypatch):
    monkeypatch.setenv("ENABLE_TOOLS", "all")
    registered = server.register_enabled_tools()
    assert set(registered) == EXPECTED_READ | EXPECTED_WRITE
    assert set(registered).isdisjoint(server.BLOCKED_TOOLS)


def test_register_enabled_tools_defaults_to_read(monkeypatch):
    monkeypatch.delenv("ENABLE_TOOLS", raising=False)
    registered = server.register_enabled_tools()
    assert set(registered) == EXPECTED_READ

