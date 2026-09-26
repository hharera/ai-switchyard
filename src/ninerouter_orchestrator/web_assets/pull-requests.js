(() => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const names = {github: "GitHub", gitlab: "GitLab", bitbucket: "Bitbucket"};
  const labels = {comment: "Post comment", approve: "Approve", unapprove: "Remove approval", request_changes: "Request changes", merge: "Merge", close: "Close request", reopen: "Reopen request"};
  const headers = {"X-Switchyard-Client": "local-ui", "Content-Type": "application/json"};
  const drafts = new Map();
  const repositories = new Map();
  let items = [], detail = null, page = 1, hasMore = false;
  let generation = 0, selection = 0, loading = false, writing = false;
  let listController = null, detailController = null;
  const workspace = () => window.Workspaces?.active();
  const active = () => location.hash === "#pull-requests";
  const query = () => new URLSearchParams({workspace_id: workspace()?.id || "", repository: $("pr-repository-select").value, provider: $("pr-provider").value});
  const draftKey = request => `${workspace()?.id}:${request.repository.web_url}:${request.number}`;
  const date = value => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString() : "";

  function providerLink(request, label = "Open on provider") {
    try {
      const url = new URL(request.web_url);
      if (url.protocol !== "https:" || url.host !== request.repository.host || url.username || url.password) return "";
      return `<a class="secondary-button" href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`;
    } catch { return ""; }
  }

  async function read(url, options = {}) {
    const response = await fetch(url, {...options, headers});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Provider request failed. Refresh and try again.");
    return body;
  }

  function controls() {
    $("pr-refresh").disabled = loading || writing;
    $("pr-check-setup").disabled = loading || writing;
    $("pr-provider").disabled = writing;
    $("pr-repository-select").disabled = loading || writing || !$("pr-repository-select").value;
    $("pr-state").disabled = writing;
    $("pr-previous").disabled = loading || writing || page <= 1;
    $("pr-next").disabled = loading || writing || !hasMore;
    $("pr-list").setAttribute("aria-busy", String(loading));
    $("pr-detail").querySelectorAll("[data-pr-action]").forEach(button => {
      button.disabled = writing || !detail?.repository.credential_available;
    });
  }

  function renderRepository(repository) {
    const name = names[repository.provider];
    const cli = repository.cli;
    $("pr-repository").textContent = `${name} / ${repository.path} / ${repository.remote}`;
    $("pr-provider").options[0].textContent = `Detected: ${name}`;
    $("pr-heading").textContent = repository.provider === "gitlab" ? "Merge requests" : "Pull requests";
    $("pr-credentials").textContent = repository.credential_available
      ? `${cli?.installed ? `${name} CLI (${cli.command}) is installed. ` : ""}Credential found. Actions use its provider identity and permissions; repository rules still apply.`
      : "No credential found. Public requests may still be readable; sign in to access private requests and take actions.";
    const needsSetup = cli ? !cli.installed || !cli.credential_available : !repository.credential_available;
    $("pr-setup").hidden = !needsSetup;
    $("pr-auth-instructions").hidden = !cli || !needsSetup;
    $("pr-copy-auth").hidden = !cli || !needsSetup;
    $("pr-install-cli").hidden = true;
    $("pr-setup-status").textContent = "";
    if (!needsSetup) return;
    const link = $("pr-install-cli");
    if (cli) {
      $("pr-setup-title").textContent = cli.installed ? `Sign in to ${name}` : `Install ${name} CLI`;
      $("pr-setup-message").textContent = cli.installed
        ? `${name} CLI (${cli.command}) is installed, but no credential was found. Sign in with the account you want to use for reviews.`
        : `${name} CLI (${cli.command}) was not found on the machine running Switchyard. Install it there, then sign in.${repository.credential_available ? " An environment credential is available, so requests and actions can still use it." : ""}`;
      $("pr-auth-command").textContent = `${cli.command} auth login --hostname ${repository.host}`;
      if (!cli.installed) {
        link.href = repository.provider === "github" ? "https://cli.github.com/" : "https://gitlab.com/gitlab-org/cli#installation";
        link.textContent = `Install ${name} CLI`;
        link.hidden = false;
      }
    } else {
      $("pr-setup-title").textContent = "Authorize Bitbucket";
      $("pr-setup-message").textContent = "Bitbucket Cloud has no supported first-party CLI. Create an API token with repository and pull request permissions, then set BITBUCKET_TOKEN and BITBUCKET_EMAIL on the machine running Switchyard. Restart Switchyard after changing its environment.";
      link.href = "https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/";
      link.textContent = "Set up a Bitbucket API token";
      link.hidden = false;
    }
  }

  function renderList() {
    const term = $("pr-search").value.trim().toLocaleLowerCase();
    const visible = items.filter(item => [item.title, item.author, item.source_branch, item.number].join(" ").toLocaleLowerCase().includes(term));
    $("pr-list").innerHTML = visible.length ? visible.map(item => `<button type="button" class="pr-item" data-pr-number="${Number(item.number)}" aria-pressed="${detail?.number === item.number}">
      <span class="pr-item-meta">#${Number(item.number)} <span>${escape(item.state)}${item.draft ? " / draft" : ""}</span></span>
      <strong>${escape(item.title)}</strong><small>${escape(item.author)} / ${escape(item.source_branch)}</small>
    </button>`).join("") : `<div class="git-empty"><b>${term ? "No matching requests" : "No requests on this page"}</b><span>${term ? "Clear the search or try another title." : "Try another state, page, or refresh the inbox."}</span></div>`;
    $("pr-page").textContent = `Page ${page}`;
  }

  function reset() {
    generation++; selection++;
    listController?.abort(); detailController?.abort();
    if ($("pr-confirm").open) $("pr-confirm").close("cancel");
    items = []; detail = null; hasMore = false; loading = false;
    $("pr-detail").setAttribute("aria-busy", "false");
    $("pr-detail").innerHTML = '<div class="git-empty"><b>Select a request to review</b><span>Read the changes and discussion before taking action.</span></div>';
    $("pr-error").textContent = "";
    $("pr-status").textContent = "";
    $("pr-setup").hidden = true;
    $("pr-setup-status").textContent = "";
    $("pr-provider").options[0].textContent = "Detect from remote";
    $("pr-heading").textContent = "Pull requests";
    $("pr-repository").textContent = "Uses the active workspace's delivery remote.";
    $("pr-credentials").textContent = "Supports GitHub.com, GitLab.com, and Bitbucket Cloud. Credentials stay on the server.";
    renderList(); controls();
  }

  async function loadList(selectedNumber = null) {
    reset();
    if (!workspace()) {
      $("pr-status").textContent = "Choose or create a workspace to load pull requests.";
      return;
    }
    const version = generation;
    listController = new AbortController();
    loading = true; controls();
    $("pr-status").textContent = "Finding workspace repositories...";
    try {
      const workspaceId = workspace().id;
      const discovered = await read(`/api/git/repositories?workspace_id=${encodeURIComponent(workspaceId)}`, {signal: listController.signal});
      if (version !== generation) return;
      const choices = discovered.repositories;
      const previous = repositories.get(workspaceId);
      $("pr-repository-select").replaceChildren(...choices.map(item => new Option(item.relative_path === "." ? `${item.name} (workspace root)` : item.relative_path, item.path)));
      if (!choices.length) {
        $("pr-repository-select").replaceChildren(new Option("No Git repositories found", ""));
        throw new Error("No Git repositories were found inside this workspace. Choose a workspace containing a Git checkout.");
      }
      const selected = choices.find(item => item.path === previous)?.path || choices[0].path;
      if (selected !== previous) { page = 1; selectedNumber = null; }
      $("pr-repository-select").value = selected;
      repositories.set(workspaceId, selected);
      $("pr-status").textContent = "Checking remote provider and CLI setup...";
      const setup = await read(`/api/pull-requests/setup?${query()}`, {signal: listController.signal});
      if (version !== generation) return;
      renderRepository(setup);
      $("pr-status").textContent = "Loading review inbox...";
      const params = query(); params.set("state", $("pr-state").value); params.set("page", page);
      const data = await read(`/api/pull-requests?${params}`, {signal: listController.signal});
      if (version !== generation) return;
      items = data.items; hasMore = data.has_more;
      renderRepository(data.repository);
      $("pr-status").textContent = `${items.length} requests on page ${page}. Search filters this page only.`;
      renderList();
      if (selectedNumber) openDetail(selectedNumber);
    } catch (error) {
      if (version !== generation || error.name === "AbortError") return;
      $("pr-error").textContent = error.message;
      $("pr-status").textContent = "Inbox unavailable. Resolve the issue above, then refresh.";
    } finally {
      if (version === generation) { loading = false; controls(); }
    }
  }

  async function openDetail(number) {
    if (writing) return;
    const version = generation, chosen = ++selection;
    detailController?.abort(); detailController = new AbortController();
    detail = null;
    $("pr-detail").setAttribute("aria-busy", "true");
    $("pr-detail").innerHTML = '<div class="git-empty">Loading request, changes, and discussion...</div>';
    $("pr-error").textContent = "";
    try {
      const data = await read(`/api/pull-requests/${number}?${query()}`, {signal: detailController.signal});
      if (version !== generation || chosen !== selection) return;
      detail = data; renderDetail(); renderList();
    } catch (error) {
      if (version !== generation || chosen !== selection || error.name === "AbortError") return;
      $("pr-error").textContent = error.message;
      $("pr-detail").innerHTML = '<div class="git-empty"><b>Request unavailable</b><span>Select the request again to retry.</span></div>';
    } finally {
      if (version === generation && chosen === selection) $("pr-detail").setAttribute("aria-busy", "false");
    }
  }

  function renderDetail() {
    const request = detail;
    $("pr-detail").innerHTML = `<header class="pr-title"><div><p class="eyebrow">${escape(names[request.repository.provider])} / #${request.number} / ${escape(request.state)}${request.draft ? " / Draft" : ""}</p><h3>${escape(request.title)}</h3><p>${escape(request.author)} <span class="pr-branches">${escape(request.source_branch)} &rarr; ${escape(request.target_branch)}</span></p></div>${providerLink(request)}</header>
      <dl class="pr-evidence"><div><dt>Approvals in preview</dt><dd>${request.approval_count ?? "Unknown"}</dd></div><div><dt>Merge status</dt><dd>${escape(request.mergeable)}</dd></div><div><dt>Files in preview</dt><dd>${request.file_count}</dd></div><div><dt>Head commit</dt><dd><code>${escape(request.head_sha.slice(0, 12))}</code></dd></div></dl>
      <p class="pr-notice">${escape(request.preview_notice)}</p>
      <div class="pr-review-layout"><div class="pr-evidence-body">
        <section class="pr-section"><h4>Description</h4><p class="pr-prose">${escape(request.body || "No description provided.")}</p></section>
        <section class="pr-section"><h4>Changed files <span>${request.file_count}</span></h4><div class="pr-files">${request.files.map((file, index) => `<details data-pr-file="${index}"><summary><span>${escape(file.path)}</span><small>${escape(file.status)}${file.additions == null ? "" : ` / +${file.additions} -${file.deletions}`}</small></summary><div class="pr-patch"></div></details>`).join("") || '<p class="field-note">No files returned by the provider.</p>'}</div></section>
        <section class="pr-section"><h4>Discussion <span>${request.comments.length}</span></h4>${request.comments.map(comment => `<article class="pr-comment"><header><b>${escape(comment.author)}</b><span>${escape(comment.kind.replaceAll("_", " "))}</span><time>${escape(date(comment.created_at))}</time></header><p class="pr-prose">${escape(comment.body || "No message.")}</p></article>`).join("") || '<p class="field-note">No discussion in this preview.</p>'}</section>
      </div><aside class="pr-action-rail" aria-label="Review actions"><h4>Review actions</h4><p class="field-note">Every action is sent to ${escape(names[request.repository.provider])} after confirmation.</p>
        ${!request.repository.credential_available ? `<p class="pr-notice">${escape(request.repository.credential_hint)}</p>` : ""}
        <label for="pr-message">Review message</label><textarea id="pr-message" rows="6" maxlength="30000" placeholder="Explain your review or ask a question" aria-describedby="pr-message-note"></textarea><p id="pr-message-note" class="field-note">Required for comments and change requests. Kept in this tab until sent. Included with GitHub reviews; for other actions, post it separately.</p>
        ${request.capabilities.includes("merge") ? `<label for="pr-merge-method">Merge method</label><select id="pr-merge-method"><option value="merge">${request.repository.provider === "gitlab" ? "Project default (no squash)" : "Merge commit"}</option><option value="squash">Squash</option>${request.repository.provider === "github" ? '<option value="rebase">Rebase</option>' : ""}</select>` : ""}
        <div class="pr-actions">${request.capabilities.map(action => `<button type="button" class="secondary-button${action === "close" ? " danger-button" : ""}" data-pr-action="${action}">${action === "close" && request.repository.provider === "bitbucket" ? "Decline request" : labels[action]}</button>`).join("")}</div>
        <p class="field-note">${request.repository.provider === "bitbucket" ? "Merge and reopen on Bitbucket. " : ""}Review required checks and full threads on the provider before merging.</p></aside></div>`;
    $("pr-message").value = drafts.get(draftKey(request)) || "";
    $("pr-message").addEventListener("input", event => drafts.set(draftKey(request), event.target.value));
    $("pr-detail").querySelectorAll("[data-pr-file]").forEach(element => element.addEventListener("toggle", () => {
      if (!element.open || element.dataset.rendered) return;
      const file = request.files[Number(element.dataset.prFile)];
      element.querySelector(".pr-patch").innerHTML = GitDiff.render({...file, message: file.patch ? undefined : "No inline diff supplied. Open this request on the provider to inspect the complete changes."});
      element.dataset.rendered = "true";
    }));
    controls();
  }

  async function act(action, trigger) {
    if (writing || !detail || !detail.repository.credential_available) return;
    const request = detail, version = generation, chosen = selection;
    const key = draftKey(request), workspaceId = workspace().id;
    const repository = $("pr-repository-select").value;
    const message = $("pr-message").value.trim();
    const mergeMethod = $("pr-merge-method")?.value || "merge";
    if (["comment", "request_changes"].includes(action) && !message) {
      $("pr-error").textContent = "Write a review message before continuing.";
      $("pr-message").setAttribute("aria-invalid", "true"); $("pr-message").focus(); return;
    }
    writing = true; controls();
    const dialog = $("pr-confirm");
    const label = trigger.textContent;
    $("pr-confirm-title").textContent = `${label}?`;
    $("pr-confirm-accept").textContent = label;
    $("pr-confirm-message").textContent = `${names[request.repository.provider]} / ${request.repository.path} / #${request.number}: ${request.title}\nHead: ${request.head_sha}\n${action === "merge" ? `Merge ${request.source_branch} into ${request.target_branch} using ${mergeMethod}. This changes the remote target branch.` : "This updates the request on the provider using the server credential."}${message && (action === "comment" || ["approve", "request_changes"].includes(action) && request.repository.provider === "github") ? `\n\nMessage:\n${message}` : ""}`;
    dialog.returnValue = "cancel";
    const confirmation = new Promise(resolve => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true}));
    dialog.showModal();
    try {
      if (!await confirmation || version !== generation || chosen !== selection) return;
      $("pr-status").textContent = `Sending ${label.toLocaleLowerCase()} to ${names[request.repository.provider]}...`;
      const result = await read("/api/pull-requests/action", {method: "POST", body: JSON.stringify({workspace_id: workspaceId, repository, provider: request.repository.provider, number: request.number, action, message, merge_method: mergeMethod, head_sha: request.head_sha, expected_state: request.state, expected_repository: request.repository.web_url, confirmed: true})});
      if (action === "comment" || request.repository.provider === "github" && ["approve", "request_changes"].includes(action)) drafts.delete(key);
      if (version !== generation) return;
      writing = false;
      await loadList(request.number);
      $("pr-status").textContent = `${result.message}. Request #${request.number}.`;
    } catch (error) {
      if (version === generation) {
        $("pr-error").textContent = `${error.message} If the outcome is uncertain, check the provider before retrying.`;
        $("pr-status").textContent = "Action could not be confirmed.";
      }
    } finally {
      writing = false; controls();
      if (trigger.isConnected) trigger.focus();
    }
  }

  $("pr-list").addEventListener("click", event => {
    const button = event.target.closest("[data-pr-number]");
    if (button) openDetail(Number(button.dataset.prNumber));
  });
  $("pr-detail").addEventListener("click", event => {
    const button = event.target.closest("[data-pr-action]");
    if (button) act(button.dataset.prAction, button);
  });
  $("pr-search").addEventListener("input", renderList);
  $("pr-refresh").addEventListener("click", () => loadList(detail?.number));
  $("pr-check-setup").addEventListener("click", () => loadList(detail?.number));
  $("pr-copy-auth").addEventListener("click", async () => {
    const version = generation;
    try {
      await navigator.clipboard.writeText($("pr-auth-command").textContent);
      if (version === generation) $("pr-setup-status").textContent = "Sign-in command copied. Run it in your terminal, then check again.";
    } catch {
      if (version === generation) $("pr-setup-status").textContent = "Could not copy. Select and copy the command above, then run it in your terminal.";
    }
  });
  $("pr-repository-select").addEventListener("change", () => {
    repositories.set(workspace().id, $("pr-repository-select").value);
    page = 1; loadList();
  });
  ["pr-provider", "pr-state"].forEach(id => $(id).addEventListener("change", () => { page = 1; loadList(); }));
  $("pr-previous").addEventListener("click", () => { page--; loadList(); });
  $("pr-next").addEventListener("click", () => { page++; loadList(); });
  window.addEventListener("workspacechange", () => {
    page = 1;
    $("pr-repository-select").replaceChildren(new Option("Choose a repository", ""));
    reset(); if (active()) loadList();
  });
  window.addEventListener("hashchange", () => {
    if (active()) loadList();
    else if ($("pr-confirm").open) $("pr-confirm").close("cancel");
  });
  if (active()) loadList();
})();
