from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .git import GitRepository
from .process import ProcessError, find_tool, run_process

Provider = Literal["github", "gitlab", "bitbucket"]
PullRequestState = Literal["open", "closed", "all"]

# These CLIs keep credentials in the user's local credential store. Bitbucket Cloud
# has no equivalent first-party CLI, so it continues to use its explicit environment variables.
PROVIDER_CLIS = {
    "github": ("gh", ("auth", "token", "--hostname")),
    "gitlab": ("glab", ("auth", "token", "--hostname")),
}


class PullRequestError(RuntimeError):
    pass


class ProviderRedirect(HTTPRedirectHandler):
    def __init__(self, repository):
        self.repository = repository
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Bitbucket's PR diffstat route redirects to the repository comparison route.
        target = urlsplit(newurl)
        prefix = f"/2.0/repositories/{self.repository.path}/diffstat/"
        if (
            self.repository.provider == "bitbucket"
            and req.get_method() == "GET"
            and target.scheme == "https"
            and target.netloc == "api.bitbucket.org"
            and target.path.startswith(prefix)
            and not any(part in {".", ".."} for part in target.path.split("/"))
            and "%" not in target.path
        ):
            return super().redirect_request(req, fp, code, msg, headers, newurl)
        return None


@dataclass(frozen=True)
class ScmRepository:
    provider: Provider
    host: str
    path: str
    owner: str
    name: str
    api_base: str
    web_url: str
    remote_name: str
    remote_url: str
    cli_installed: bool = False
    cli_authenticated: bool = False
    cli_credential: str = field(default="", repr=False, compare=False)

    @property
    def token(self) -> str:
        if self.cli_credential:
            return self.cli_credential
        names = {
            "github": ("GH_TOKEN", "GITHUB_TOKEN"),
            "gitlab": ("GITLAB_TOKEN",),
            "bitbucket": ("BITBUCKET_TOKEN",),
        }[self.provider]
        return next((os.environ[name] for name in names if os.environ.get(name)), "")

    @property
    def credential_hint(self) -> str:
        if self.provider in PROVIDER_CLIS:
            command = PROVIDER_CLIS[self.provider][0]
            provider_name = "GitHub" if self.provider == "github" else "GitLab"
            if not self.cli_installed:
                return (
                    f"{provider_name} CLI ({command}) is not installed. Install it and run "
                    f"`{command} auth login`, or set {self._credential_variable_hint()}."
                )
            if not self.cli_authenticated:
                return (
                    f"{provider_name} CLI is installed but not authenticated. Run "
                    f"`{command} auth login`, or set {self._credential_variable_hint()}."
                )
        return {
            "github": "Set GH_TOKEN or GITHUB_TOKEN for private repositories and actions.",
            "gitlab": "Set GITLAB_TOKEN for private repositories and actions.",
            "bitbucket": "Set BITBUCKET_TOKEN for private repositories and actions.",
        }[self.provider]

    def _credential_variable_hint(self) -> str:
        return {
            "github": "GH_TOKEN or GITHUB_TOKEN",
            "gitlab": "GITLAB_TOKEN",
            "bitbucket": "BITBUCKET_TOKEN",
        }[self.provider]

    def public(self) -> dict:
        return {
            "provider": self.provider,
            "host": self.host,
            "path": self.path,
            "name": self.name,
            "web_url": self.web_url,
            "remote": self.remote_name,
            "credential_available": bool(self.token),
            "credential_hint": self.credential_hint,
            "cli": {
                "command": PROVIDER_CLIS[self.provider][0],
                "installed": self.cli_installed,
                "credential_available": self.cli_authenticated,
            } if self.provider in PROVIDER_CLIS else None,
        }


def _remote_parts(remote_url: str) -> tuple[str, str]:
    remote_url = remote_url.strip()
    scp = re.fullmatch(r"(?:[^@\s]+@)?([^:/\s]+):(.+)", remote_url)
    if scp and "://" not in remote_url:
        return scp.group(1), scp.group(2)
    try:
        parsed = urlsplit(remote_url)
    except ValueError as exc:
        raise PullRequestError("Choose a valid hosted repository URL.") from exc
    if parsed.scheme not in {"http", "https", "ssh", "git"} or not parsed.hostname:
        raise PullRequestError("The selected Git remote is not a hosted repository URL.")
    return parsed.hostname, parsed.path


def parse_remote(
    remote_url: str,
    *,
    remote_name: str = "origin",
    provider: Literal["auto", "github", "gitlab", "bitbucket"] = "auto",
) -> ScmRepository:
    host, raw_path = _remote_parts(remote_url)
    host = host.lower()
    path = raw_path.strip("/").removesuffix(".git")
    parts = path.split("/")
    if len(parts) < 2 or any(part in {".", ".."} for part in parts):
        raise PullRequestError("The Git remote must identify an account and repository.")
    detected = {"github.com": "github", "gitlab.com": "gitlab", "bitbucket.org": "bitbucket"}.get(
        host
    )
    selected = detected if provider == "auto" else provider
    if not detected or selected != detected:
        raise PullRequestError(
            "Use a GitHub.com, GitLab.com, or Bitbucket Cloud remote. Self-hosted servers are not supported."
        )
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part in {".", ".."} for part in parts):
        raise PullRequestError("The remote contains an unsupported repository path.")
    if selected in {"github", "bitbucket"} and len(parts) != 2:
        raise PullRequestError(f"{selected.title()} remotes must use account/repository format.")
    api_base = {
        "github": "https://api.github.com",
        "gitlab": "https://gitlab.com/api/v4",
        "bitbucket": "https://api.bitbucket.org/2.0",
    }[selected]
    return ScmRepository(
        provider=selected,
        host=host,
        path="/".join(parts),
        owner=parts[0],
        name=parts[-1],
        api_base=api_base,
        web_url=f"https://{host}/{'/'.join(parts)}",
        remote_name=remote_name,
        remote_url=remote_url,
    )


class PullRequestService:
    @staticmethod
    def _provider_cli_credentials(repository: ScmRepository) -> ScmRepository:
        """Reuse a provider CLI's keyring-backed credential without exposing it to the UI."""
        cli = PROVIDER_CLIS.get(repository.provider)
        if not cli:
            return repository
        command, auth_arguments = cli
        executable = find_tool(command)
        if not executable:
            return replace(repository, cli_installed=False)
        try:
            result = run_process(
                [executable, *auth_arguments, repository.host],
                cwd=Path.cwd(),
                timeout=15,
                check=False,
            )
        except ProcessError:
            return replace(repository, cli_installed=True)
        token = result.stdout.strip() if result.passed else ""
        return replace(
            repository,
            cli_installed=True,
            cli_authenticated=bool(token),
            cli_credential=token if not repository.token else "",
        )

    def repository(
        self,
        repository: Path,
        remote_name: str,
        provider: Literal["auto", "github", "gitlab", "bitbucket"] = "auto",
    ) -> ScmRepository:
        git = GitRepository(repository)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", remote_name):
            raise PullRequestError("Choose a named Git remote, not a URL or path.")
        try:
            git.validate()
        except ProcessError as exc:
            raise PullRequestError(
                "The workspace folder is not a readable Git repository. "
                "Choose a Git workspace or repair its repository before loading pull requests."
            ) from exc
        try:
            result = git.git("remote", "get-url", remote_name, check=False)
        except ProcessError as exc:
            raise PullRequestError(
                f"Cannot read Git remote {remote_name}. Check Git and repository access."
            ) from exc
        if not result.passed or not result.stdout.strip():
            raise PullRequestError(
                f"Git remote {remote_name} is unavailable. Configure it in the repository or workspace."
            )
        parsed = parse_remote(result.stdout.strip(), remote_name=remote_name, provider=provider)
        return self._provider_cli_credentials(parsed)

    def _headers(self, repository: ScmRepository, *, auth_required: bool) -> dict[str, str]:
        token = repository.token
        if auth_required and not token:
            raise PullRequestError(repository.credential_hint)
        headers = {
            "Accept": "application/json",
            "User-Agent": "Switchyard/0.1",
        }
        if token:
            if repository.provider == "gitlab":
                headers["PRIVATE-TOKEN"] = token
            elif repository.provider == "bitbucket" and os.environ.get("BITBUCKET_EMAIL"):
                pair = f"{os.environ['BITBUCKET_EMAIL']}:{token}".encode()
                headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
            else:
                headers["Authorization"] = f"Bearer {token}"
        if repository.provider == "github":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
            headers["Accept"] = "application/vnd.github+json"
        return headers

    def _request(
        self,
        repository: ScmRepository,
        method: str,
        endpoint: str,
        *,
        query: dict | list[tuple[str, str | int]] | None = None,
        payload: dict | None = None,
        auth_required: bool = False,
    ):
        url = f"{repository.api_base}{endpoint}"
        if query:
            url += "?" + urlencode(query, doseq=True)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = self._headers(repository, auth_required=auth_required)
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, method=method, headers=headers, data=data)
        try:
            with build_opener(ProviderRedirect(repository)).open(request, timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise PullRequestError("The provider response is too large to display.")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            detail = f"Provider request failed (HTTP {exc.code}). Check permissions and repository rules on the provider."
            if exc.code in {401, 403}:
                detail = f"Provider authorization failed. {repository.credential_hint}"
            elif exc.code == 404:
                detail = "Repository or pull request not found. Check the remote and token access."
            elif exc.code == 429:
                detail = "Provider rate limit reached. Wait before refreshing."
            elif 300 <= exc.code < 400:
                detail = "The provider redirected this repository. Update the Git remote before retrying."
            raise PullRequestError(
                detail or f"Provider request failed with status {exc.code}."
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PullRequestError(
                "Provider request could not be completed. For actions, check the provider before retrying; the action may already have completed."
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise PullRequestError(
                "The provider returned an unreadable response. Refresh or open the provider."
            ) from exc

    @staticmethod
    def _timestamp(value) -> str:
        return value or ""

    def _summary(self, repository: ScmRepository, item: dict) -> dict:
        if repository.provider == "github":
            merged = bool(item.get("merged_at"))
            return {
                "id": str(item["number"]),
                "number": item["number"],
                "title": item.get("title", "Untitled pull request"),
                "body": item.get("body") or "",
                "state": "merged" if merged else item.get("state", "open"),
                "draft": bool(item.get("draft")),
                "web_url": item.get("html_url", ""),
                "author": (item.get("user") or {}).get("login", "Unknown"),
                "avatar_url": (item.get("user") or {}).get("avatar_url", ""),
                "source_branch": (item.get("head") or {}).get("ref", ""),
                "head_sha": (item.get("head") or {}).get("sha", ""),
                "target_branch": (item.get("base") or {}).get("ref", ""),
                "updated_at": self._timestamp(item.get("updated_at")),
                "labels": [label.get("name", "") for label in item.get("labels", [])],
            }
        if repository.provider == "gitlab":
            state = item.get("state", "opened")
            return {
                "id": str(item["iid"]),
                "number": item["iid"],
                "title": item.get("title", "Untitled merge request"),
                "body": item.get("description") or "",
                "state": {"opened": "open", "merged": "merged"}.get(state, "closed"),
                "draft": bool(item.get("draft") or item.get("work_in_progress")),
                "web_url": item.get("web_url", ""),
                "author": (item.get("author") or {}).get("username", "Unknown"),
                "avatar_url": (item.get("author") or {}).get("avatar_url", ""),
                "source_branch": item.get("source_branch", ""),
                "head_sha": item.get("sha") or (item.get("diff_refs") or {}).get("head_sha", ""),
                "target_branch": item.get("target_branch", ""),
                "updated_at": self._timestamp(item.get("updated_at")),
                "labels": item.get("labels", []),
            }
        state = item.get("state", "OPEN").lower()
        return {
            "id": str(item["id"]),
            "number": item["id"],
            "title": item.get("title", "Untitled pull request"),
            "body": (item.get("description") or ""),
            "state": "open" if state == "open" else "merged" if state == "merged" else "closed",
            "draft": bool(item.get("draft")),
            "web_url": (item.get("links", {}).get("html") or {}).get("href", ""),
            "author": (item.get("author") or {}).get("display_name", "Unknown"),
            "avatar_url": ((item.get("author") or {}).get("links", {}).get("avatar") or {}).get(
                "href", ""
            ),
            "source_branch": (item.get("source") or {}).get("branch", {}).get("name", ""),
            "head_sha": (item.get("source") or {}).get("commit", {}).get("hash", ""),
            "target_branch": (item.get("destination") or {}).get("branch", {}).get("name", ""),
            "updated_at": self._timestamp(item.get("updated_on")),
            "labels": [],
        }

    @staticmethod
    def _matches_state(item: dict, state: PullRequestState) -> bool:
        return (
            state == "all"
            or item["state"] == state
            or state == "closed"
            and item["state"] == "merged"
        )

    def list(
        self, repository: ScmRepository, state: PullRequestState = "open", page: int = 1
    ) -> dict:
        if repository.provider == "github":
            raw = self._request(
                repository,
                "GET",
                f"/repos/{repository.path}/pulls",
                query={
                    "state": state,
                    "per_page": 50,
                    "page": page,
                    "sort": "updated",
                    "direction": "desc",
                },
            )
        elif repository.provider == "gitlab":
            project = quote(repository.path, safe="")
            query_state = "opened" if state == "open" else "all"
            raw = self._request(
                repository,
                "GET",
                f"/projects/{project}/merge_requests",
                query={
                    "state": query_state,
                    "per_page": 50,
                    "page": page,
                    "order_by": "updated_at",
                    "sort": "desc",
                },
            )
        else:
            endpoint = f"/repositories/{repository.owner}/{repository.name}/pullrequests"
            query = {"pagelen": 50, "page": page, "sort": "-updated_on"}
            if state == "open":
                query["state"] = "OPEN"
            else:
                query["state"] = ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"]
            response = self._request(repository, "GET", endpoint, query=query)
            raw = response.get("values", [])
        items = [self._summary(repository, item) for item in raw]
        return {
            "repository": repository.public(),
            "state": state,
            "items": [item for item in items if self._matches_state(item, state)],
            "page": page,
            "has_more": bool(response.get("next"))
            if repository.provider == "bitbucket"
            else len(raw) == 50,
        }

    def _comments(self, repository: ScmRepository, number: int) -> list[dict]:
        if repository.provider == "github":
            raw = self._request(
                repository,
                "GET",
                f"/repos/{repository.path}/issues/{number}/comments",
                query={"per_page": 100},
            )
            return [
                {
                    "id": str(item["id"]),
                    "author": (item.get("user") or {}).get("login", "Unknown"),
                    "avatar_url": (item.get("user") or {}).get("avatar_url", ""),
                    "body": item.get("body") or "",
                    "created_at": item.get("created_at", ""),
                    "kind": "comment",
                }
                for item in raw
            ]
        if repository.provider == "gitlab":
            project = quote(repository.path, safe="")
            raw = self._request(
                repository,
                "GET",
                f"/projects/{project}/merge_requests/{number}/notes",
                query={"per_page": 100, "sort": "asc"},
            )
            return [
                {
                    "id": str(item["id"]),
                    "author": (item.get("author") or {}).get("username", "Unknown"),
                    "avatar_url": (item.get("author") or {}).get("avatar_url", ""),
                    "body": item.get("body") or "",
                    "created_at": item.get("created_at", ""),
                    "kind": "system" if item.get("system") else "comment",
                }
                for item in raw
            ]
        raw = self._request(
            repository,
            "GET",
            f"/repositories/{repository.owner}/{repository.name}/pullrequests/{number}/comments",
            query={"pagelen": 100},
        ).get("values", [])
        return [
            {
                "id": str(item["id"]),
                "author": (item.get("user") or {}).get("display_name", "Unknown"),
                "avatar_url": ((item.get("user") or {}).get("links", {}).get("avatar") or {}).get(
                    "href", ""
                ),
                "body": (item.get("content") or {}).get("raw", ""),
                "created_at": item.get("created_on", ""),
                "kind": "comment",
            }
            for item in raw
            if not item.get("deleted")
        ]

    def detail(self, repository: ScmRepository, number: int) -> dict:
        if number < 1:
            raise PullRequestError("Choose a valid pull request number.")
        if repository.provider == "github":
            raw = self._request(repository, "GET", f"/repos/{repository.path}/pulls/{number}")
            files_raw = self._request(
                repository,
                "GET",
                f"/repos/{repository.path}/pulls/{number}/files",
                query={"per_page": 100},
            )
            reviews = self._request(
                repository,
                "GET",
                f"/repos/{repository.path}/pulls/{number}/reviews",
                query={"per_page": 100},
            )
            files = [
                {
                    "path": item.get("filename", ""),
                    "old_path": item.get("previous_filename"),
                    "status": item.get("status", "modified"),
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                    "patch": item.get("patch") or "",
                    "binary": "patch" not in item,
                }
                for item in files_raw
            ]
            latest = {}
            for review in reviews:
                if review.get("state") != "COMMENTED":
                    latest[(review.get("user") or {}).get("login")] = review.get("state")
            approvals = sum(value == "APPROVED" for value in latest.values())
            review_comments = [
                {
                    "id": f"review-{item['id']}",
                    "author": (item.get("user") or {}).get("login", "Unknown"),
                    "avatar_url": (item.get("user") or {}).get("avatar_url", ""),
                    "body": item.get("body") or "",
                    "created_at": item.get("submitted_at", ""),
                    "kind": item.get("state", "review").lower(),
                }
                for item in reviews
                if item.get("body")
            ]
            mergeable = raw.get("mergeable_state") or (
                "mergeable" if raw.get("mergeable") else "unknown"
            )
        elif repository.provider == "gitlab":
            project = quote(repository.path, safe="")
            raw = self._request(repository, "GET", f"/projects/{project}/merge_requests/{number}")
            changes = self._request(
                repository, "GET", f"/projects/{project}/merge_requests/{number}/changes"
            )
            try:
                approval_data = self._request(
                    repository, "GET", f"/projects/{project}/merge_requests/{number}/approvals"
                )
            except PullRequestError:
                approval_data = None
            files = [
                {
                    "path": item.get("new_path", ""),
                    "old_path": item.get("old_path"),
                    "status": "renamed"
                    if item.get("renamed_file")
                    else "added"
                    if item.get("new_file")
                    else "deleted"
                    if item.get("deleted_file")
                    else "modified",
                    "additions": None,
                    "deletions": None,
                    "patch": item.get("diff") or "",
                    "binary": False,
                }
                for item in changes.get("changes", [])[:100]
            ]
            approvals = (
                len(approval_data.get("approved_by", [])) if approval_data is not None else None
            )
            review_comments = []
            mergeable = raw.get("detailed_merge_status") or raw.get("merge_status", "unknown")
        else:
            endpoint = f"/repositories/{repository.owner}/{repository.name}/pullrequests/{number}"
            raw = self._request(repository, "GET", endpoint)
            diffstat = self._request(
                repository, "GET", f"{endpoint}/diffstat", query={"pagelen": 100}
            ).get("values", [])
            files = [
                {
                    "path": (item.get("new") or item.get("old") or {}).get("path", ""),
                    "old_path": (item.get("old") or {}).get("path"),
                    "status": item.get("status", "modified").lower(),
                    "additions": item.get("lines_added", 0),
                    "deletions": item.get("lines_removed", 0),
                    "patch": "",
                    "binary": False,
                }
                for item in diffstat
            ]
            approvals = sum(bool(item.get("approved")) for item in raw.get("participants", []))
            review_comments = []
            mergeable = "unknown"
        summary = self._summary(repository, raw)
        comments = self._comments(repository, number) + review_comments
        comments.sort(key=lambda item: item.get("created_at") or "")
        capabilities = self.capabilities(repository, summary)
        return {
            **summary,
            "repository": repository.public(),
            "files": files,
            "comments": comments,
            "approval_count": approvals,
            "comment_count": len(comments),
            "file_count": len(files),
            "additions": sum(item.get("additions") or 0 for item in files),
            "deletions": sum(item.get("deletions") or 0 for item in files),
            "mergeable": mergeable,
            "preview_notice": "Preview: up to 100 files, 100 discussion comments, and 100 reviews. Diffs may be truncated by the provider. GitHub inline review threads and Bitbucket diffs are available on the provider. Open the provider for complete discussion and checks.",
            "capabilities": capabilities,
        }

    @staticmethod
    def capabilities(repository: ScmRepository, summary: dict) -> list[str]:
        actions = ["comment"]
        if summary["state"] == "open":
            actions.extend(["approve", "close"])
            if repository.provider != "bitbucket" and not summary["draft"]:
                actions.append("merge")
            if repository.provider == "github":
                actions.append("request_changes")
            else:
                actions.append("unapprove")
        elif summary["state"] == "closed" and repository.provider != "bitbucket":
            actions.append("reopen")
        return actions

    def action(
        self,
        repository: ScmRepository,
        number: int,
        action: str,
        *,
        message: str = "",
        merge_method: str = "merge",
        head_sha: str,
        expected_state: str,
    ) -> dict:
        self._headers(repository, auth_required=True)
        endpoint = (
            f"/repos/{repository.path}/pulls/{number}"
            if repository.provider == "github"
            else f"/projects/{quote(repository.path, safe='')}/merge_requests/{number}"
            if repository.provider == "gitlab"
            else f"/repositories/{repository.path}/pullrequests/{number}"
        )
        current = self._summary(repository, self._request(repository, "GET", endpoint))
        if not head_sha or current["head_sha"] != head_sha or current["state"] != expected_state:
            raise PullRequestError(
                "This request changed since you opened it. Refresh and review it again."
            )
        if action not in self.capabilities(repository, current):
            raise PullRequestError(
                "This action is not available for this provider or request state."
            )
        if (
            merge_method not in {"merge", "squash", "rebase"}
            or repository.provider == "gitlab"
            and merge_method == "rebase"
        ):
            raise PullRequestError("This merge method is not supported by the provider.")
        message = message.strip()
        if action in {"comment", "request_changes"} and not message:
            raise PullRequestError("Write a comment before continuing.")
        if repository.provider == "github":
            root = f"/repos/{repository.path}"
            if action == "comment":
                self._request(
                    repository,
                    "POST",
                    f"{root}/issues/{number}/comments",
                    payload={"body": message},
                    auth_required=True,
                )
            elif action in {"approve", "request_changes"}:
                event = "APPROVE" if action == "approve" else "REQUEST_CHANGES"
                self._request(
                    repository,
                    "POST",
                    f"{root}/pulls/{number}/reviews",
                    payload={"event": event, "body": message, "commit_id": head_sha},
                    auth_required=True,
                )
            elif action == "merge":
                result = self._request(
                    repository,
                    "PUT",
                    f"{root}/pulls/{number}/merge",
                    payload={"merge_method": merge_method, "sha": head_sha},
                    auth_required=True,
                )
                if not result.get("merged"):
                    raise PullRequestError(
                        "GitHub did not merge this request. Check required reviews and branch rules on GitHub."
                    )
            elif action in {"close", "reopen"}:
                self._request(
                    repository,
                    "PATCH",
                    f"{root}/pulls/{number}",
                    payload={"state": "closed" if action == "close" else "open"},
                    auth_required=True,
                )
            else:
                raise PullRequestError("This action is not supported by GitHub.")
        elif repository.provider == "gitlab":
            project = quote(repository.path, safe="")
            root = f"/projects/{project}/merge_requests/{number}"
            if action == "comment":
                self._request(
                    repository,
                    "POST",
                    f"{root}/notes",
                    payload={"body": message},
                    auth_required=True,
                )
            elif action == "approve":
                self._request(
                    repository,
                    "POST",
                    f"{root}/approve",
                    payload={"sha": head_sha},
                    auth_required=True,
                )
            elif action == "unapprove":
                self._request(
                    repository, "POST", f"{root}/unapprove", payload={}, auth_required=True
                )
            elif action == "merge":
                self._request(
                    repository,
                    "PUT",
                    f"{root}/merge",
                    payload={
                        "sha": head_sha,
                        "squash": merge_method == "squash",
                        "should_remove_source_branch": False,
                    },
                    auth_required=True,
                )
            elif action in {"close", "reopen"}:
                self._request(
                    repository,
                    "PUT",
                    root,
                    payload={"state_event": "close" if action == "close" else "reopen"},
                    auth_required=True,
                )
            else:
                raise PullRequestError("This action is not supported by GitLab.")
        else:
            root = f"/repositories/{repository.owner}/{repository.name}/pullrequests/{number}"
            if action == "comment":
                self._request(
                    repository,
                    "POST",
                    f"{root}/comments",
                    payload={"content": {"raw": message}},
                    auth_required=True,
                )
            elif action == "approve":
                self._request(repository, "POST", f"{root}/approve", payload={}, auth_required=True)
            elif action == "unapprove":
                self._request(repository, "DELETE", f"{root}/approve", auth_required=True)
            elif action == "close":
                self._request(repository, "POST", f"{root}/decline", payload={}, auth_required=True)
            else:
                raise PullRequestError("This action is not supported by Bitbucket.")
        labels = {
            "comment": "Comment posted",
            "approve": "Review approved",
            "unapprove": "Approval removed",
            "request_changes": "Changes requested",
            "merge": "Merge request accepted by provider",
            "close": "Pull request closed",
            "reopen": "Pull request reopened",
        }
        return {"action": action, "message": labels[action], "number": number}
