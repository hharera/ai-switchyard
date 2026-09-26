import base64
import subprocess
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from ninerouter_orchestrator import web
from ninerouter_orchestrator.models import CommandResult
from ninerouter_orchestrator.pull_requests import (
    ProviderRedirect,
    PullRequestError,
    PullRequestService,
    parse_remote,
)
from ninerouter_orchestrator.workspace_config import (
    Workspace,
    WorkspaceConfiguration,
    WorkspaceStore,
)


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "GITLAB_TOKEN", "BITBUCKET_TOKEN", "BITBUCKET_EMAIL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("remote,provider,path", [
    ("git@github.com:team/project.git", "github", "team/project"),
    ("https://github.com/team/project.git", "github", "team/project"),
    ("ssh://git@gitlab.com/team/group/project.git", "gitlab", "team/group/project"),
    ("git@gitlab.com:team/project.git", "gitlab", "team/project"),
    ("https://bitbucket.org/team/project.git", "bitbucket", "team/project"),
    ("git@bitbucket.org:team/project.git", "bitbucket", "team/project"),
])
def test_parse_remote(remote, provider, path):
    result = parse_remote(remote)
    assert result.provider == provider
    assert result.path == path
    assert not result.public()["credential_available"]
    assert "remote_url" not in result.public()


@pytest.mark.parametrize("remote", [
    "/tmp/repo", "file:///tmp/repo", "https://evil.example/team/repo", "https://github.com/one",
    "https://github.com/team/../repo", "https://github.com/team//repo", "https://github.com/team/repo/extra",
    "https://github.com/team/%2e%2e", "https://github.com.evil.test/team/repo", "https://[bad/repo",
])
def test_reject_unsafe_remote(remote):
    with pytest.raises(PullRequestError):
        parse_remote(remote)


def test_provider_must_match_remote():
    with pytest.raises(PullRequestError):
        parse_remote("https://github.com/team/repo", provider="gitlab")


@pytest.mark.parametrize("host,env,header,value", [
    ("github.com", "GH_TOKEN", "Authorization", "Bearer test-secret"),
    ("gitlab.com", "GITLAB_TOKEN", "PRIVATE-TOKEN", "test-secret"),
    ("bitbucket.org", "BITBUCKET_TOKEN", "Authorization", "Bearer test-secret"),
])
def test_credentials_remain_server_side(monkeypatch, host, env, header, value):
    repo = parse_remote(f"https://{host}/team/repo")
    service = PullRequestService()
    with pytest.raises(PullRequestError):
        service._headers(repo, auth_required=True)
    monkeypatch.setenv(env, "test-secret")
    assert service._headers(repo, auth_required=True)[header] == value
    assert "test-secret" not in str(repo.public())


@pytest.mark.parametrize("provider,command,remote,token", [
    ("github", "gh", "https://github.com/team/repo", "github-keyring-token"),
    ("gitlab", "glab", "https://gitlab.com/team/repo", "gitlab-keyring-token"),
])
def test_provider_cli_credential_is_used_without_exposing_it(monkeypatch, provider, command, remote, token):
    service = PullRequestService()
    repository = parse_remote(remote)
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: f"/usr/bin/{command}")
    monkeypatch.setattr(
        "ninerouter_orchestrator.pull_requests.run_process",
        lambda *args, **kwargs: CommandResult(
            command=f"{command} auth token", return_code=0, stdout=f"{token}\n", stderr="", duration_seconds=0
        ),
    )

    resolved = service._provider_cli_credentials(repository)

    assert resolved.public()["credential_available"]
    expected_header = "Authorization" if provider == "github" else "PRIVATE-TOKEN"
    expected_value = f"Bearer {token}" if provider == "github" else token
    assert service._headers(resolved, auth_required=True)[expected_header] == expected_value
    assert token not in str(resolved.public())


@pytest.mark.parametrize("remote,command", [
    ("https://github.com/team/repo", "gh"),
    ("https://gitlab.com/team/repo", "glab"),
])
def test_provider_cli_setup_hint_identifies_missing_or_unauthenticated_cli(monkeypatch, remote, command):
    repository = parse_remote(remote)
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: None)
    assert "not installed" in PullRequestService()._provider_cli_credentials(repository).credential_hint

    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: f"/usr/bin/{command}")
    monkeypatch.setattr(
        "ninerouter_orchestrator.pull_requests.run_process",
        lambda *args, **kwargs: CommandResult(
            command=f"{command} auth token", return_code=1, stdout="", stderr="not logged in", duration_seconds=0
        ),
    )
    assert "not authenticated" in PullRequestService()._provider_cli_credentials(repository).credential_hint


def test_bitbucket_api_token_basic_auth(monkeypatch):
    monkeypatch.setenv("BITBUCKET_TOKEN", "secret")
    monkeypatch.setenv("BITBUCKET_EMAIL", "person@example.test")
    headers = PullRequestService()._headers(parse_remote("https://bitbucket.org/a/b"), auth_required=True)
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"person@example.test:secret").decode()


@pytest.mark.parametrize("host,command", [("github.com", "gh"), ("gitlab.com", "glab")])
def test_cli_status_is_public_but_credentials_are_not(monkeypatch, host, command):
    repository = parse_remote(f"https://{host}/team/repo")
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: None)
    resolved = PullRequestService._provider_cli_credentials(repository)
    assert resolved.public()["cli"] == {
        "command": command, "installed": False, "credential_available": False,
    }
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: command)
    monkeypatch.setattr(
        "ninerouter_orchestrator.pull_requests.run_process",
        lambda *args, **kwargs: CommandResult(
            command="auth token", return_code=0, stdout="secret-token", stderr="", duration_seconds=0,
        ),
    )
    resolved = PullRequestService._provider_cli_credentials(repository)
    assert resolved.public()["cli"] == {
        "command": command, "installed": True, "credential_available": True,
    }
    assert "secret-token" not in str(resolved.public())


def test_environment_credential_does_not_hide_missing_cli(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "environment-secret")
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: None)
    result = PullRequestService._provider_cli_credentials(parse_remote("https://github.com/a/b")).public()
    assert result["credential_available"]
    assert not result["cli"]["installed"]
    assert "environment-secret" not in str(result)


@pytest.mark.parametrize("return_code,stdout", [(0, "cli-secret"), (1, "")])
def test_environment_credential_does_not_skip_cli_check(monkeypatch, return_code, stdout):
    monkeypatch.setenv("GH_TOKEN", "environment-secret")
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: "gh")
    monkeypatch.setattr(
        "ninerouter_orchestrator.pull_requests.run_process",
        lambda *args, **kwargs: CommandResult(
            command="auth token", return_code=return_code, stdout=stdout, stderr="", duration_seconds=0,
        ),
    )
    repository = PullRequestService._provider_cli_credentials(parse_remote("https://github.com/a/b"))
    assert repository.token == "environment-secret"
    assert repository.public()["cli"]["installed"]
    assert repository.public()["cli"]["credential_available"] == (return_code == 0)


def test_bitbucket_does_not_probe_an_unofficial_cli(monkeypatch):
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: pytest.fail("No Bitbucket CLI"))
    result = PullRequestService._provider_cli_credentials(parse_remote("https://bitbucket.org/a/b")).public()
    assert result["cli"] is None


def test_setup_endpoint_resolves_remote_without_fetching_private_requests(monkeypatch, tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "upstream", "git@gitlab.com:team/project.git"], check=True)
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(WorkspaceConfiguration(workspaces=[Workspace(
        id="project", name="Project", repository=str(tmp_path), delivery={"remote": "upstream"},
    )]))
    monkeypatch.setattr(web, "workspace_store", store)
    monkeypatch.setattr("ninerouter_orchestrator.pull_requests.find_tool", lambda name: None)
    monkeypatch.setattr(web.pull_request_service, "_request", lambda *args, **kwargs: pytest.fail("No provider request"))
    client = TestClient(web.app, headers={"X-Switchyard-Client": "local-ui"})
    response = client.get("/api/pull-requests/setup?workspace_id=project")
    assert response.status_code == 200
    assert response.json()["provider"] == "gitlab"
    assert response.json()["remote"] == "upstream"
    assert not response.json()["cli"]["installed"]
    assert TestClient(web.app).get("/api/pull-requests/setup?workspace_id=project").status_code == 403
    assert client.get("/api/pull-requests/setup", params={"workspace_id": "project", "repository": str(tmp_path.parent)}).status_code == 422


def raw_request(provider, state="open"):
    common = {"title": "Review this", "id": 7}
    if provider == "github":
        return {**common, "number": 7, "state": state, "head": {"sha": "abc123", "ref": "feature"}, "base": {"ref": "main"}, "user": {"login": "alex"}}
    if provider == "gitlab":
        return {**common, "iid": 7, "state": "opened" if state == "open" else state, "sha": "abc123", "source_branch": "feature", "target_branch": "main", "author": {"username": "alex"}}
    return {**common, "state": state.upper(), "source": {"commit": {"hash": "abc123"}, "branch": {"name": "feature"}}, "destination": {"branch": {"name": "main"}}, "author": {"display_name": "alex"}}


HOSTS = {"github": "github.com", "gitlab": "gitlab.com", "bitbucket": "bitbucket.org"}
TOKENS = {"github": "GH_TOKEN", "gitlab": "GITLAB_TOKEN", "bitbucket": "BITBUCKET_TOKEN"}


@pytest.mark.parametrize("provider", HOSTS)
def test_list_normalizes_providers(monkeypatch, provider):
    service = PullRequestService()
    repo = parse_remote(f"https://{HOSTS[provider]}/team/repo")
    calls = []
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        items = [raw_request(provider)]
        return {"values": items, "next": "https://ignored.example"} if provider == "bitbucket" else items
    monkeypatch.setattr(service, "_request", request)
    result = service.list(repo, page=2)
    assert result["items"][0]["number"] == 7
    assert result["items"][0]["author"] == "alex"
    assert result["items"][0]["head_sha"] == "abc123"
    assert calls[0][1]["query"]["page"] == 2
    assert result["repository"]["provider"] == provider


@pytest.mark.parametrize("provider", HOSTS)
def test_detail_normalizes_files_comments_and_approvals(monkeypatch, provider):
    service = PullRequestService()
    repo = parse_remote(f"https://{HOSTS[provider]}/team/repo")
    def request(_repo, _method, endpoint, **kwargs):
        if endpoint.endswith("/files"):
            return [{"filename": "app.py", "patch": "@@ -1 +1 @@\n-old\n+new", "additions": 1, "deletions": 1}]
        if endpoint.endswith("/reviews"):
            return [{"id": 1, "user": {"login": "alex"}, "state": "APPROVED", "body": "Looks good"}]
        if endpoint.endswith("/changes"):
            return {"changes": [{"new_path": "app.py", "diff": "@@ -1 +1 @@\n-old\n+new"}]}
        if endpoint.endswith("/approvals"):
            raise PullRequestError("Not supported")
        if endpoint.endswith("/diffstat"):
            return {"values": [{"new": {"path": "app.py"}, "lines_added": 1}]}
        if endpoint.endswith(("/comments", "/notes")):
            comment = {"id": 1, "body": "Question", "content": {"raw": "Question"}}
            return {"values": [comment]} if provider == "bitbucket" else [comment]
        return raw_request(provider)
    monkeypatch.setattr(service, "_request", request)
    detail = service.detail(repo, 7)
    assert detail["files"][0]["path"] == "app.py"
    assert any(item["body"] == "Question" for item in detail["comments"])
    assert detail["approval_count"] == {"github": 1, "gitlab": None, "bitbucket": 0}[provider]
    assert ("merge" in detail["capabilities"]) == (provider != "bitbucket")


@pytest.mark.parametrize("provider,action,method,suffix", [
    ("github", "comment", "POST", "/issues/7/comments"),
    ("github", "approve", "POST", "/pulls/7/reviews"),
    ("github", "request_changes", "POST", "/pulls/7/reviews"),
    ("github", "merge", "PUT", "/pulls/7/merge"),
    ("github", "close", "PATCH", "/pulls/7"),
    ("github", "reopen", "PATCH", "/pulls/7"),
    ("gitlab", "comment", "POST", "/merge_requests/7/notes"),
    ("gitlab", "approve", "POST", "/merge_requests/7/approve"),
    ("gitlab", "unapprove", "POST", "/merge_requests/7/unapprove"),
    ("gitlab", "merge", "PUT", "/merge_requests/7/merge"),
    ("gitlab", "close", "PUT", "/merge_requests/7"),
    ("gitlab", "reopen", "PUT", "/merge_requests/7"),
    ("bitbucket", "comment", "POST", "/pullrequests/7/comments"),
    ("bitbucket", "approve", "POST", "/pullrequests/7/approve"),
    ("bitbucket", "unapprove", "DELETE", "/pullrequests/7/approve"),
    ("bitbucket", "close", "POST", "/pullrequests/7/decline"),
])
def test_actions_use_provider_protocol(monkeypatch, provider, action, method, suffix):
    monkeypatch.setenv(TOKENS[provider], "secret")
    service = PullRequestService()
    repo = parse_remote(f"https://{HOSTS[provider]}/team/repo")
    calls = []
    state = "closed" if action == "reopen" else "open"
    def request(_repo, verb, endpoint, **kwargs):
        calls.append((verb, endpoint, kwargs))
        return raw_request(provider, state) if verb == "GET" else {"merged": True}
    monkeypatch.setattr(service, "_request", request)
    service.action(repo, 7, action, message="Review", head_sha="abc123", expected_state=state)
    assert calls[-1][0] == method
    assert calls[-1][1].endswith(suffix)
    assert calls[-1][2]["auth_required"]
    if action == "merge":
        assert calls[-1][2]["payload"]["sha"] == "abc123"
    if provider == "github" and action == "approve":
        assert calls[-1][2]["payload"]["commit_id"] == "abc123"


@pytest.mark.parametrize("action,head,state,message", [
    ("approve", "stale", "open", ""), ("close", "abc123", "closed", ""),
    ("comment", "abc123", "open", " "), ("request_changes", "abc123", "open", ""),
])
def test_reject_stale_or_empty_actions(monkeypatch, action, head, state, message):
    monkeypatch.setenv("GH_TOKEN", "secret")
    service = PullRequestService()
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return raw_request("github")
    monkeypatch.setattr(service, "_request", request)
    with pytest.raises(PullRequestError):
        service.action(parse_remote("https://github.com/a/b"), 7, action, head_sha=head, expected_state=state, message=message)
    assert all(call[1] == "GET" for call in calls)


def test_bitbucket_redirect_is_narrowly_scoped():
    handler = ProviderRedirect(parse_remote("https://bitbucket.org/team/repo"))
    request = Request("https://api.bitbucket.org/2.0/repositories/team/repo/pullrequests/7/diffstat", headers={"Authorization": "Bearer secret"})
    good = "https://api.bitbucket.org/2.0/repositories/team/repo/diffstat/abc..def"
    assert handler.redirect_request(request, None, 302, "", {}, good).full_url == good
    for bad in (good.replace("api.bitbucket.org", "evil.test"), good.replace("team/repo", "other/repo"), good.replace("https:", "http:"), good.replace("abc..def", "../other")):
        assert handler.redirect_request(request, None, 302, "", {}, bad) is None
    request.method = "POST"
    assert handler.redirect_request(request, None, 302, "", {}, good) is None


def test_endpoints_bind_workspace_and_require_confirmation(monkeypatch, tmp_path):
    nested = tmp_path / "frontend"
    nested.mkdir()
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(WorkspaceConfiguration(workspaces=[Workspace(id="project", name="Project", repository=str(tmp_path), delivery={"remote": "upstream"})]))
    monkeypatch.setattr(web, "workspace_store", store)
    repo = parse_remote("https://github.com/team/repo")
    calls = []
    def repository(path, remote, provider):
        calls.append((path, remote, provider))
        return repo
    monkeypatch.setattr(web.pull_request_service, "repository", repository)
    monkeypatch.setattr(web.pull_request_service, "list", lambda *args: {"items": []})
    monkeypatch.setattr(web.pull_request_service, "action", lambda *args, **kwargs: {"action": "approve"})
    client = TestClient(web.app, headers={"X-Switchyard-Client": "local-ui"})
    assert client.get("/api/pull-requests", params={"workspace_id": "project"}).status_code == 200
    assert calls[-1] == (tmp_path, "upstream", "auto")
    assert client.get("/api/pull-requests", params={"workspace_id": "project", "repository": str(nested)}).status_code == 200
    assert calls[-1] == (nested, "upstream", "auto")
    outside = tmp_path.parent / "outside"
    outside.mkdir()
    assert client.get("/api/pull-requests", params={"workspace_id": "project", "repository": str(outside)}).status_code == 422
    payload = {"workspace_id": "project", "repository": str(nested), "number": 7, "action": "approve", "head_sha": "abc123", "expected_state": "open", "expected_repository": repo.web_url}
    assert client.post("/api/pull-requests/action", json=payload).status_code == 422
    payload["confirmed"] = True
    assert client.post("/api/pull-requests/action", json=payload).status_code == 200
    assert calls[-1] == (nested, "upstream", "auto")
    payload["expected_repository"] = "https://github.com/other/repo"
    assert client.post("/api/pull-requests/action", json=payload).status_code == 422
    with web.pull_request_lock:
        assert client.post("/api/pull-requests/action", json=payload).status_code == 409
    assert client.get("/api/pull-requests?workspace_id=project&page=0").status_code == 422
    assert TestClient(web.app).get("/api/pull-requests?workspace_id=project").status_code == 403
    assert client.get("/api/pull-requests?workspace_id=project", headers={"Origin": "https://evil.test"}).status_code == 403


def test_pull_request_endpoint_reports_a_non_git_workspace(monkeypatch, tmp_path):
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(
        WorkspaceConfiguration(
            workspaces=[Workspace(id="project", name="Project", repository=str(tmp_path))]
        )
    )
    monkeypatch.setattr(web, "workspace_store", store)
    client = TestClient(web.app, headers={"X-Switchyard-Client": "local-ui"})

    response = client.get("/api/pull-requests", params={"workspace_id": "project"})

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "The workspace folder is not a readable Git repository. "
        "Choose a Git workspace or repair its repository before loading pull requests."
    )


def test_nested_git_checkout_is_used_for_list_detail_and_actions(monkeypatch, tmp_path):
    nested = tmp_path / "website"
    nested.mkdir()
    subprocess.run(["git", "init", str(nested)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(nested), "remote", "add", "origin", "https://github.com/team/website.git"], check=True)
    store = WorkspaceStore(tmp_path / "workspaces.json")
    store.save(WorkspaceConfiguration(workspaces=[Workspace(id="project", name="Project", repository=str(tmp_path))]))
    monkeypatch.setattr(web, "workspace_store", store)
    calls = []

    def response(repository, *args, **kwargs):
        calls.append(repository.path)
        return {"repository": repository.public(), "items": []}

    for method in ("list", "detail", "action"):
        monkeypatch.setattr(web.pull_request_service, method, response)
    client = TestClient(web.app, headers={"X-Switchyard-Client": "local-ui"})
    discovered = client.get("/api/git/repositories", params={"workspace_id": "project"}).json()
    assert discovered["repositories"][0]["path"] == str(nested)
    params = {"workspace_id": "project", "repository": str(nested)}
    assert client.get("/api/pull-requests", params=params).status_code == 200
    assert client.get("/api/pull-requests/7", params=params).status_code == 200
    payload = {**params, "number": 7, "action": "approve", "head_sha": "abc123", "expected_state": "open", "expected_repository": "https://github.com/team/website", "confirmed": True}
    assert client.post("/api/pull-requests/action", json=payload).status_code == 200
    assert calls == ["team/website"] * 3
    payload["repository"] = str(tmp_path.parent)
    assert client.post("/api/pull-requests/action", json=payload).status_code == 422
    assert len(calls) == 3


def test_assets_and_navigation():
    client = TestClient(web.app)
    html = client.get("/").text
    assert 'href="#pull-requests"' in html
    for asset in ("pull-requests.js", "pull-requests.css"):
        assert asset in html
        assert client.get(f"/assets/{asset}").status_code == 200
