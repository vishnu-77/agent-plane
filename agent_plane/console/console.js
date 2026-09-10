"use strict";
const $ = (s, r = document) => r.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const names = {
  overview: "Overview",
  gateway: "Gateway",
  authority: "Authority leases",
  decisions: "Decisions",
  policies: "Policies",
  usage: "Usage",
  requests: "Request workbench",
  access: "Access & runtime",
};
const state = {
  page: "overview",
  admin: "",
  bearer: "",
  data: {},
  errors: {},
  generation: 0,
  loading: false,
  result: null,
  requestType: "authorize",
  draft: {},
  search: "",
  filter: "all",
  gatewayTab: "models",
};
const labels = {
  allow: "Allowed",
  deny: "Denied",
  approval_required: "Approval required",
  admin_action: "Operator change",
  active: "Active",
  revoked: "Revoked",
  expired: "Expired",
  error: "Request failed",
};
const badge = (v, l) =>
  `<span class="badge ${["allow", "deny", "approval_required", "active", "revoked", "expired", "error", "warning"].includes(v) ? v : ""}">${esc(l || labels[v] || v)}</span>`;
const button = (label, action, extra = "", type = "") =>
  `<button type="button" class="button ${type}" data-do="${action}" ${extra}>${label}</button>`;
const json = (v) => `<pre>${esc(JSON.stringify(v, null, 2))}</pre>`;
const date = (v) =>
  v
    ? new Date(
        v.endsWith?.("Z") || /[+-]\d\d:\d\d$/.test(v) ? v : v + "Z",
      ).toLocaleString()
    : "No expiry";
const idattr = (v) => `data-id="${esc(v)}"`;
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (error ? " error" : "");
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 6500);
}
function reason(data) {
  const d = data?.detail;
  return typeof d === "string"
    ? d
    : d?.reason ||
        d?.violations?.join("; ") ||
        d?.error ||
        data?.reason ||
        data?.error ||
        "The server could not complete this request.";
}
async function api(path, role = "admin", method = "GET", body) {
  const headers = {};
  if (role === "admin" && state.admin) headers["X-Admin-Token"] = state.admin;
  if (role === "bearer" && state.bearer)
    headers.Authorization = "Bearer " + state.bearer;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const controller = new AbortController(),
    timer = setTimeout(() => controller.abort(), 30000);
  try {
    const r = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
    });
    return {
      ok: r.ok,
      status: r.status,
      data: await r
        .json()
        .catch(() => ({ error: "Unexpected server response" })),
    };
  } catch (e) {
    return {
      ok: false,
      status: 0,
      data: {
        error:
          e.name === "AbortError"
            ? "Request timed out. Check gateway readiness."
            : "Cannot reach the gateway. Check your connection.",
      },
    };
  } finally {
    clearTimeout(timer);
  }
}
const reads = {
  gateway: "/admin/gateway",
  leases: "/admin/leases",
  audit: "/v1/audit?limit=50",
  policies: "/admin/policies",
  revocations: "/admin/revocations",
};
async function refresh() {
  const generation = ++state.generation;
  state.loading = true;
  const jobs = [
    ["health", "/healthz", "public"],
    ["ready", "/readyz", "public"],
  ];
  if (state.admin)
    Object.entries(reads).forEach(([k, p]) => jobs.push([k, p, "admin"]));
  if (state.bearer) jobs.push(["usage", "/v1/usage", "bearer"]);
  await Promise.all(
    jobs.map(async ([k, p, role]) => {
      const r = await api(p, role);
      if (generation !== state.generation) return;
      if (r.ok) {
        state.data[k] = r.data;
        delete state.errors[k];
      } else {
        delete state.data[k];
        state.errors[k] =
          r.status === 401
            ? `${role === "admin" ? "Operator" : "Agent"} access was rejected. Reconnect with a valid token.`
            : reason(r.data);
      }
    }),
  );
  if (generation !== state.generation) return;
  state.loading = false;
  $("#server-status").textContent = state.data.ready
    ? "Gateway ready"
    : state.data.health
      ? "Storage unavailable"
      : "Gateway unavailable";
  $("#server-dot").className =
    "status-dot " + (state.data.ready ? "good" : "bad");
  $("#environment").textContent = state.data.gateway
    ? `${state.data.gateway.environment} · ${state.data.gateway.identity_mode}`
    : "Same-origin gateway";
  $("#connection-label").textContent = state.data.gateway
    ? "Operator connected"
    : state.data.usage
      ? "Agent connected"
      : "Public view";
  $("#refreshed").textContent = "Updated " + new Date().toLocaleTimeString();
  render();
}
function head(t, d, a = "") {
  return `<div class="page-head"><div><div class="eyebrow">CONTROL PLANE</div><h1>${t}</h1><p class="description">${d}</p></div><div class="actions">${a}${button(state.loading ? "Refreshing…" : "↻ Refresh", "refresh", state.loading ? "disabled" : "")}</div></div>`;
}
function empty(t, d, c = false) {
  return `<div class="empty"><b>${esc(t)}</b>${esc(d)}${c ? "<br>" + button("Connect access", "connect") : ""}</div>`;
}
function guard(
  k,
  message = "Connect operator access to view live workspace data.",
) {
  if (state.errors[k])
    return empty("Unable to load this view", state.errors[k], true);
  if (!state.data[k])
    return empty(
      state.loading ? "Loading…" : "Access required",
      message,
      !state.loading,
    );
  return "";
}
function panel(t, b, s = "", a = "") {
  return `<section class="panel"><div class="panel-head"><div><h2>${t}</h2>${s ? `<small>${s}</small>` : ""}</div>${a}</div>${b}</section>`;
}
function stats(items) {
  return `<div class="stats">${items.map(([l, v, f]) => `<div class="stat"><div class="stat-label">${l}</div><div class="stat-value">${esc(v ?? "—")}</div><div class="stat-foot">${f}</div></div>`).join("")}</div>`;
}
function edge(e) {
  return (
    {
      tool: "Tool",
      rag: "Retrieval",
      a2a: "Delegation",
      authorize: "Authority",
      "lease-delegate": "Lease delegation",
      "lease-admin": "Lease change",
      admin: "Administration",
    }[(e.model_requested || "").split(":")[0]] || "Model"
  );
}
function eventTable(events, compact = false) {
  if (!events.length)
    return empty(
      "No decisions recorded",
      "Run a request through the gateway to start collecting evidence.",
    );
  return `<div class="table-wrap"><table><thead><tr><th>Decision / resource</th><th>Identity</th><th>Outcome</th>${compact ? "" : "<th>Recorded</th>"}<th></th></tr></thead><tbody>${events.map((e) => `<tr><td><span class="cell-title">${esc(e.model_requested || "Request")}</span><small>${esc(edge(e))} · ${esc(e.reason || "No reason recorded")}</small></td><td>${esc(e.agent_id || e.user_id || "Unknown")}<small>${esc(e.tenant || "—")}</small></td><td>${badge(e.decision)}</td>${compact ? "" : `<td><small>${esc(date(e.created_at))}</small></td>`}<td>${button("Inspect", "event", idattr(e.decision_id), "small")}</td></tr>`).join("")}</tbody></table></div>`;
}
function overview() {
  const a = state.data.audit?.events,
    g = state.data.gateway,
    leases = state.data.leases?.items;
  return (
    head(
      "Your agents. Your boundaries.",
      "Inspect gateway activity and control the authority behind every action.",
      button(
        "↗ Run a request",
        "navigate",
        'data-target="requests"',
        "primary",
      ),
    ) +
    (!state.admin
      ? `<div class="banner"><div><b>Connect your control plane access</b><p>Use an operator token to inspect configuration and evidence. Add an agent token to send governed requests.</p></div>${button("Connect access", "connect", "", "primary")}</div>`
      : "") +
    (state.errors.gateway
      ? `<div class="banner warning"><p>${esc(state.errors.gateway)}</p>${button("Reconnect", "connect")}</div>`
      : "") +
    stats([
      [
        "Active leases",
        leases?.filter((l) => l.status === "active").length,
        "Authority in this running instance",
      ],
      ["Recorded decisions", a?.length, "Latest 50 audit records"],
      [
        "Denied",
        a?.filter((e) => e.decision === "deny").length,
        "Within the displayed audit window",
      ],
      [
        "Approval required",
        a?.filter((e) => e.decision === "approval_required").length,
        "Recorded outcomes; not a pending queue",
      ],
    ]) +
    `<div class="split">${panel(
      "Gateway coverage",
      `<div class="panel-body"><div class="flow"><div class="flow-node"><strong>Agent application</strong><small>Authenticated requests</small></div><span class="flow-arrow">→</span><div class="flow-node hub"><strong>agent-plane</strong><small>Identity · policy · evidence</small></div><span class="flow-arrow">→</span><div class="flow-node"><strong>Connected systems</strong><small>Models · tools · knowledge</small></div></div><div class="route-list">${[
        [
          "Model gateway",
          "Policy, redaction, routing and fallback",
          g ? g.models.length + " configured" : "Connect to inspect",
        ],
        [
          "Tool broker",
          "Policy check and brokered execution",
          g ? g.tools.length + " registered" : "Connect to inspect",
        ],
        [
          "Knowledge retrieval",
          "Filter results by caller access",
          g ? g.knowledge.length + " sources" : "Connect to inspect",
        ],
        [
          "Task authority",
          "Explicit authorization before an action",
          "Separate decision endpoint",
        ],
      ]
        .map(
          ([t, d, v]) =>
            `<div class="route-row"><div>${t}<small>${d}</small></div>${badge(v)}</div>`,
        )
        .join("")}</div></div>`,
      "Only traffic routed through these endpoints is governed.",
      button("View gateway", "navigate", 'data-target="gateway"', "link"),
    )}${panel("Control plane status", `<div class="panel-body"><div class="route-list"><div class="route-row"><span>Gateway</span>${badge(state.data.health ? "active" : "error", state.data.health ? "Available" : "Unavailable")}</div><div class="route-row"><span>Audit storage</span>${badge(state.data.ready ? "active" : "error", state.data.ready ? "Reachable" : "Not ready")}</div><div class="route-row"><span>Policy bundle</span><code>${esc(state.data.health?.policy_version || "—")}</code></div><div class="route-row"><span>Operator access</span>${badge(g ? "active" : "warning", g ? "Connected" : "Not connected")}</div></div><p class="subtle">Task authorization does not execute an action. Tool calls require their own broker integration.</p></div>`, "Runtime availability, not upstream provider health.")}</div>` +
    panel(
      "Recent decisions",
      guard("audit") || eventTable(a.slice(0, 6), true),
      "Recorded policy and authority outcomes.",
      button(
        "View all decisions",
        "navigate",
        'data-target="decisions"',
        "link",
      ),
    )
  );
}
function gateway() {
  const g = state.data.gateway;
  let body = guard("gateway");
  if (g) {
    const type = state.gatewayTab;
    body = `<div class="tabs">${[
      ["models", "Models", g.models.length],
      ["tools", "Tools", g.tools.length],
      ["knowledge", "Knowledge", g.knowledge.length],
    ]
      .map(
        ([k, l, n]) =>
          `<button data-do="gateway-tab" data-tab="${k}" class="${k === type ? "active" : ""}">${l} <span class="count">${n}</span></button>`,
      )
      .join("")}</div>`;
    if (type === "models")
      body += panel(
        "Model routes",
        `<div class="table-wrap"><table><thead><tr><th>Model</th><th>Provider / upstream</th><th>Fallback</th><th>Credentials</th><th></th></tr></thead><tbody>${g.models.map((m) => `<tr><td><b>${esc(m.id)}</b><small>${esc(m.tags.join(" · "))}</small></td><td>${esc(m.provider)}<small>${esc(m.upstream_model)}</small></td><td>${esc(m.fallback.join(" → ") || "None")}</td><td>${badge(m.credentials_configured ? "active" : "warning", m.credentials_configured ? "Configured" : "Missing")}</td><td>${button("Open request", "try-model", idattr(m.id), "small")}</td></tr>`).join("")}</tbody></table></div>`,
        "Credential presence does not verify provider connectivity.",
      );
    if (type === "tools")
      body += panel(
        "Registered tools",
        g.tools.length
          ? `<div class="table-wrap"><table><thead><tr><th>Tool</th><th>Execution</th><th>Method</th><th></th></tr></thead><tbody>${g.tools.map((t) => `<tr><td><b>${esc(t.name)}</b></td><td>${badge(t.type, t.type === "mock" ? "Mock · echoes arguments" : "HTTP integration")}</td><td><code>${esc(t.method)}</code></td><td>${button("Open request", "try-tool", idattr(t.name), "small")}</td></tr>`).join("")}</tbody></table></div>`
          : empty("No tools registered", "Add a tool to the server catalog."),
        "Only registered tools can be invoked. Sensitive calls may require approval.",
      );
    if (type === "knowledge")
      body += panel(
        "Knowledge sources",
        g.knowledge.length
          ? `<div class="table-wrap"><table><thead><tr><th>Source</th><th>Adapter</th><th>Authorization</th><th></th></tr></thead><tbody>${g.knowledge.map((k) => `<tr><td><b>${esc(k.name)}</b></td><td>${badge(k.type)}</td><td>Caller identity and document access</td><td>${button("Open request", "try-retrieve", idattr(k.name), "small")}</td></tr>`).join("")}</tbody></table></div>`
          : empty("No knowledge sources", "Configure a source on the gateway."),
        "Document contents are not exposed in this inventory.",
      );
  }
  return (
    head(
      "Gateway",
      "Inspect the configured routes between your agents and the systems they call.",
    ) +
    body +
    `<div class="banner neutral"><div><b>Configuration is managed on the server</b><p>Model routes, tools and knowledge sources come from the server catalogs. Provider credentials stay in the server environment.</p></div><a class="button" href="/docs" target="_blank" rel="noopener">API reference ↗</a></div>`
  );
}
function authority() {
  const items = state.data.leases?.items;
  return (
    head(
      "Authority leases",
      "Inspect task-scoped grants. Issue, narrow, delegate or revoke authority.",
      button(
        "+ Issue lease",
        "issue",
        state.data.leases ? "" : "disabled",
        "primary",
      ),
    ) +
    `<div class="banner neutral"><div><b>Authority is scoped to this running instance</b><p>Leases, revocations and use counts are in memory. They are not shared across serverless instances or retained after restart.</p></div></div>` +
    panel(
      "Lease inventory",
      guard("leases") ||
        (items.length
          ? `<div class="table-wrap"><table><thead><tr><th>Lease / task</th><th>Agent / tenant</th><th>Scope</th><th>Impact ceiling</th><th>Status</th><th></th></tr></thead><tbody>${items.map((l) => `<tr><td><b>${esc(l.id)}</b><small>${esc(l.task)}</small></td><td>${esc(l.subject)}<small>${esc(l.tenant)}</small></td><td>${l.actions.length} actions<small>${esc(l.resources.join(", "))}</small></td><td>${badge(l.maximum_impact)}</td><td>${badge(l.status)}</td><td>${button("Manage", "lease", idattr(l.id), "small")}</td></tr>`).join("")}</tbody></table></div>`
          : empty(
              "No authority leases",
              "Issue a lease to authorize a specific agent and task.",
            )),
      "Operator inventory across all tenants.",
    )
  );
}
function decisions() {
  const events = state.data.audit?.events || [],
    filtered = events.filter(
      (e) =>
        (state.filter === "all" || e.decision === state.filter) &&
        JSON.stringify(e).toLowerCase().includes(state.search.toLowerCase()),
    );
  return (
    head(
      "Decisions",
      "Understand what was permitted, what was blocked, and the evidence behind it.",
    ) +
    panel(
      "Audit records",
      guard("audit") ||
        `<div class="filters"><input id="decision-search" aria-label="Search decisions" placeholder="Search identity, resource or reason…" value="${esc(state.search)}"><select id="decision-filter" aria-label="Filter outcome">${[
          ["all", "All outcomes"],
          ["allow", "Allowed"],
          ["deny", "Denied"],
          ["approval_required", "Approval required"],
          ["admin_action", "Operator changes"],
        ]
          .map(
            ([v, l]) =>
              `<option value="${v}" ${state.filter === v ? "selected" : ""}>${l}</option>`,
          )
          .join(
            "",
          )}</select><span class="count">${events.length} loaded · latest 50</span></div><div id="decision-rows">${eventTable(filtered)}</div>`,
      "An allow decision alone does not prove successful downstream execution.",
    )
  );
}
function policies() {
  const p = state.data.policies;
  return (
    head(
      "Policies",
      "Inspect the active rule bundle and reload server-side policy changes.",
      button("Reload bundle", "reload", p ? "" : "disabled", "primary"),
    ) +
    panel(
      "Active policy bundle",
      guard("policies") ||
        p.policies
          .map(
            (rule) =>
              `<article class="policy-card"><div class="actions"><h3>${esc(rule.name)}</h3>${badge(rule.decision.action)}</div><p>${esc(rule.decision.reason || "No reason specified")}</p><div class="chips">${badge("Version " + rule.version)}${(rule.decision.obligations || []).map((o) => badge(o)).join("")}</div><details><summary>Inspect rule conditions and obligations</summary><div class="codebox">${json(rule)}</div></details></article>`,
          )
          .join(""),
      esc(p?.policy_version || "Access required"),
    ) +
    `<p class="subtle">Edit the policy files on the server, then reload. This console does not persist policy edits.</p>`
  );
}
function usage() {
  const u = state.data.usage;
  return (
    head(
      "Usage",
      "Inspect metered calls and units for the tenant in your agent credential.",
    ) +
    (guard(
      "usage",
      "Connect an agent bearer token to view its tenant usage.",
    ) ||
      stats([
        ["Tenant", u.tenant, "Scoped by authenticated identity"],
        ["Calls", u.totals.calls, "Recorded usage in the current store"],
        ["Units", u.totals.units, "Tokens or action units, by resource"],
        [
          "Estimated cost",
          u.totals.estimated_cost !== undefined
            ? `${u.totals.currency} ${u.totals.estimated_cost.toFixed(4)}`
            : "—",
          "Price-book estimate; not an invoice",
        ],
      ]) +
        panel(
          "Usage by resource",
          u.items.length
            ? `<div class="table-wrap"><table><thead><tr><th>Resource</th><th>Edge</th><th>Calls</th><th>Units</th></tr></thead><tbody>${u.items.map((i) => `<tr><td>${esc(i.resource)}</td><td>${badge(i.edge)}</td><td>${i.calls}</td><td>${i.units}</td></tr>`).join("")}</tbody></table></div>`
            : empty(
                "No metered usage yet",
                "Run an allowed request with this agent credential.",
              ),
          "Totals depend on the configured storage lifetime.",
        ))
  );
}
const fields = {
  authorize: [
    ["task", "Task", "fix-staging-checkout"],
    ["action", "Action", "deployment.restart"],
    ["resource", "Resource", "staging/checkout"],
  ],
  tool: [["tool", "Tool name", "search_kb"]],
  model: [["model", "Model", "gpt-4.1"]],
  retrieve: [["source", "Knowledge source", "kb"]],
};
function draft() {
  if (!state.draft[state.requestType]) state.draft[state.requestType] = {};
  return state.draft[state.requestType];
}
function field(n, l, v = "", t = "text", required = true) {
  return `<div class="field"><label for="f-${n}">${l}</label><input id="f-${n}" name="${n}" type="${t}" value="${esc(v)}" ${required ? "required" : ""}></div>`;
}
function textarea(n, l, v, rows = 4) {
  return `<div class="field"><label for="f-${n}">${l}</label><textarea id="f-${n}" name="${n}" rows="${rows}">${esc(v)}</textarea></div>`;
}
const paths = {
  authorize: "/v1/authorize",
  tool: "/v1/tools/invoke",
  model: "/v1/chat/completions",
  retrieve: "/v1/retrieve",
};
function requestFields() {
  const d = draft(),
    t = state.requestType;
  let s = (fields[t] || []).map(([k, l, v]) => field(k, l, d[k] ?? v)).join("");
  if (t === "authorize")
    s += `<div class="field"><label for="f-impact">Declared impact</label><select name="impact" id="f-impact"><option value="reversible">Reversible</option><option value="irreversible" ${d.impact === "irreversible" ? "selected" : ""}>Irreversible</option></select></div>`;
  if (t === "tool")
    s += textarea(
      "arguments",
      "Arguments · JSON",
      d.arguments ?? '{"q": "hello"}',
    );
  if (t === "model")
    s += textarea(
      "prompt",
      "Message",
      d.prompt ?? "Say hello in one sentence.",
    );
  if (t === "retrieve")
    s += textarea("query", "Query", d.query ?? "password reset", 3);
  return s;
}
function resultView() {
  const r = state.result;
  if (!r)
    return empty(
      "Ready when you are",
      "Submit a request to inspect the actual gateway response.",
    );
  const data = r.data,
    det = data.detail || data,
    cp = data.x_control_plane || {},
    outcome =
      r.status === 202
        ? "approval_required"
        : det.decision === "deny" || r.status === 403
          ? "deny"
          : r.ok
            ? "allow"
            : "error";
  let title = labels[outcome];
  if (r.ok && r.status !== 202)
    title = r.type === "authorize" ? "Action authorized" : "Request completed";
  const why =
    outcome === "error"
      ? reason(data)
      : det.reason ||
        cp.reason ||
        (r.type === "authorize"
          ? "Within this task’s authority."
          : "The gateway returned a successful response.");
  return `<div class="result ${outcome}">${badge(outcome)} <span class="count">HTTP ${r.status || "—"}</span><h3>${title}</h3><p>${esc(why)}</p>${r.type === "authorize" ? "<p>Decision only. No action was executed. Authorization can consume a lease use.</p>" : ""}${outcome === "approval_required" ? "<p>The request stopped before execution. Approval and resume must be handled by the calling application.</p>" : ""}${det.evidence_id || det.decision_id || cp.decision_id ? `<code>${esc(det.evidence_id || det.decision_id || cp.decision_id)}</code>` : ""}<div class="codebox">${json(data)}</div></div>`;
}
function requests() {
  return (
    head(
      "Request workbench",
      "Send a real request and inspect its authorization, response and evidence.",
    ) +
    (!state.bearer
      ? `<div class="banner warning"><div><b>Agent access required</b><p>Connect a short-lived agent bearer token to run requests.</p></div>${button("Connect access", "connect")}</div>`
      : "") +
    `<div class="workbench">${panel(
      "Compose request",
      `<div class="panel-body"><div class="field"><label for="request-type">Endpoint</label><select id="request-type">${[
        ["authorize", "Task authorization"],
        ["tool", "Tool invocation"],
        ["model", "Model completion"],
        ["retrieve", "Knowledge retrieval"],
      ]
        .map(
          ([v, l]) =>
            `<option value="${v}" ${state.requestType === v ? "selected" : ""}>${l}</option>`,
        )
        .join(
          "",
        )}</select></div><p class="eyebrow mono">POST ${paths[state.requestType]}</p><form id="request-form">${requestFields()}<div class="banner neutral"><p>${state.requestType === "authorize" ? "This evaluates a proposal and may consume a lease use. Impact is caller-declared." : state.requestType === "tool" ? "This invokes the registered tool if permitted. HTTP tools can have real side effects." : state.requestType === "model" ? "This makes a real provider call when configured. Provider charges may apply." : "Results are filtered using the agent’s identity and document access rules."}</p></div><div class="inline-error" id="request-error" role="alert"></div><button class="button primary" type="submit" ${state.bearer ? "" : "disabled"}>Send request ↗</button></form></div>`,
    )}${panel("Gateway response", `<div id="request-result">${resultView()}</div>`, "Live response, including non-success outcomes.")}</div>`
  );
}
function access() {
  const g = state.data.gateway;
  return (
    head(
      "Access & runtime",
      "Inspect identity, storage and runtime credential revocations.",
      button("Manage connection", "connect", "", "primary"),
    ) +
    panel(
      "Server configuration",
      guard("gateway") ||
        `<div class="panel-body"><dl class="detail-grid"><dt>Environment</dt><dd>${esc(g.environment)}</dd><dt>Identity mode</dt><dd><code>${esc(g.identity_mode)}</code></dd><dt>Audit / usage store</dt><dd>${esc(g.storage_backend)}</dd><dt>Lease state</dt><dd>In memory, per instance</dd><dt>Gateway origin</dt><dd><code>${esc(location.origin)}</code></dd></dl><p class="subtle">${g.storage_backend === "local" ? "Local database history may reset on serverless cold starts." : ""} Leases and runtime revocations are not durable or shared across instances.</p></div>`,
    ) +
    panel(
      "Credential revocations",
      guard("revocations") ||
        `<div class="panel-body"><p class="subtle">Runtime JTI revocation applies to signed-delegation identity mode. It does not invalidate HS256 claims-mode tokens.</p><form id="revoke-token-form" class="section-gap"><div class="field"><label for="jti">Credential ID (jti)</label><input id="jti" name="jti" required placeholder="Credential identifier, not the token itself" ${g?.identity_mode === "delegation" ? "" : "disabled"}></div><button class="button danger" ${g?.identity_mode === "delegation" ? "" : "disabled"}>Revoke credential</button></form>${(state.data.revocations?.revoked || []).length ? `<div class="section-gap">${state.data.revocations.revoked.map((jti) => `<div class="route-row"><code>${esc(jti)}</code>${button("Remove revocation", "restore-token", idattr(jti), "small")}</div>`).join("")}</div>` : '<p class="subtle">No runtime credential revocations on this instance.</p>'}</div>`,
    )
  );
}
function render() {
  const page = names[state.page] ? state.page : "overview";
  $("#page-label").textContent = names[page];
  document.title = `${names[page]} · agent-plane`;
  document.querySelectorAll("[data-page]").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === page);
    if (a.dataset.page === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("#content").innerHTML = {
    overview,
    gateway,
    authority,
    decisions,
    policies,
    usage,
    requests,
    access,
  }[page]();
}
function navigate(page) {
  location.hash = page;
  if (state.page === page) render();
}
function dialog(title, body) {
  $("#dialog-content").innerHTML =
    `<div class="dialog-head"><h2>${esc(title)}</h2><button class="close" data-do="close" aria-label="Close dialog">×</button></div><div class="dialog-body">${body}</div>`;
  if (!$("#dialog").open) $("#dialog").showModal();
}
function connect() {
  dialog(
    "Connect control plane access",
    `<p class="muted">Operator access manages the workspace. Agent access sends requests and reads tenant usage.</p><form id="connect-form" class="section-gap">${field("admin", "Operator token · X-Admin-Token", state.admin, "password", false)}${field("bearer", "Agent bearer token", state.bearer, "password", false)}<p class="secret-note">Credentials stay in this tab’s memory and are cleared on reload or disconnect. They are sent only to this gateway’s origin.</p><div class="dialog-actions">${button("Disconnect", "disconnect")}<button type="submit" class="button primary">Connect</button></div></form>`,
  );
}
function showEvent(id) {
  const e = state.data.audit?.events.find((e) => e.decision_id === id);
  if (!e) return;
  dialog(
    "Decision evidence",
    `${badge(e.decision)}<dl class="detail-grid">${[
      ["Identity", e.agent_id || e.user_id],
      ["Tenant", e.tenant],
      ["Edge", edge(e)],
      ["Requested", e.model_requested],
      ["Resource / upstream", e.model_used || "Not recorded"],
      ["Reason", e.reason],
      [
        "Matched rules / leases",
        (e.rules_matched || []).join(", ") || "None recorded",
      ],
      ["Policy version", e.policy_version || "Not recorded"],
      ["Latency", `${e.latency_ms ?? 0} ms`],
      ["Recorded", date(e.created_at)],
      ["Evidence ID", e.decision_id],
      [
        "Signature",
        e.signature
          ? "Signature recorded (not verified here)"
          : "None recorded",
      ],
    ]
      .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
      .join(
        "",
      )}</dl><details><summary>Raw audit record</summary><div class="codebox">${json(e)}</div></details>`,
  );
}
function localExpiry() {
  const d = new Date(Date.now() + 3600000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}
function leaseForm(mode, lease) {
  if (mode === "issue") {
    dialog(
      "Issue task authority",
      `<form id="lease-form" data-mode="issue"><div class="form-grid">${field("id", "Lease ID", "lease-" + Date.now().toString(36))}${field("agent", "Agent ID", "")}${field("task", "Task", "")}${field("tenant", "Tenant", "default")}</div>${textarea("actions", "Permitted actions · one per line", "deployment.read\ndeployment.restart", 3)}${textarea("resources", "Resource patterns · one per line", "staging/*", 3)}${textarea("protected_resources", "Protected resources · one per line", "production/*", 2)}${field("expires_at", "Expiry", localExpiry(), "datetime-local")}<p class="subtle">Expiry uses your local timezone. Impact ceiling is reversible. Delegation is limited to a subset.</p><div class="inline-error" role="alert"></div><div class="dialog-actions"><button class="button primary">Issue lease</button></div></form>`,
    );
    return;
  }
  const delegate = mode === "delegate";
  dialog(
    delegate ? "Delegate a narrower lease" : "Narrow lease authority",
    `<form id="lease-form" data-mode="${mode}" ${idattr(lease.id)}><p class="muted">${esc(lease.id)} · ${esc(lease.task)}</p><p class="subtle">${delegate ? "Only the lease holder’s agent credential can delegate." : "Only a subset of the current grant is accepted."} Existing protections and impact limits remain in effect.</p><div class="section-gap">${delegate ? field("agent", "Child agent ID", "") : ""}${textarea("actions", "Permitted actions · one per line", lease.actions.join("\n"))}${textarea("resources", "Resource patterns · one per line", lease.resources.join("\n"))}</div><div class="inline-error" role="alert"></div><div class="dialog-actions"><button class="button primary">${delegate ? "Delegate authority" : "Apply narrower scope"}</button></div></form>`,
  );
}
function showLease(id) {
  const l = state.data.leases?.items.find((x) => x.id === id);
  if (!l) return;
  dialog(
    l.id,
    `${badge(l.status)}<dl class="detail-grid">${[
      ["Agent", l.subject],
      ["Task", l.task],
      ["Tenant", l.tenant],
      ["Resources", l.resources.join(", ")],
      ["Protected", l.protected_resources.join(", ") || "None"],
      ["Expiry", date(l.expires_at)],
      ["Impact ceiling", l.maximum_impact],
      ["Delegation", l.child_authority],
      ["Approval actions", l.require_approval.join(", ") || "None"],
    ]
      .map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`)
      .join(
        "",
      )}</dl><h3>Action use counts</h3>${l.actions.map((a) => `<div class="route-row"><code>${esc(a)}</code><span>${l.uses[a] || 0} / ${l.max_uses[a] ?? "unlimited"}</span></div>`).join("")}<div class="dialog-actions">${button("Narrow scope", "shrink", idattr(id) + (l.status === "active" ? "" : " disabled"))}${button("Delegate", "delegate", idattr(id) + (state.bearer && l.status === "active" && l.child_authority !== "none" ? "" : " disabled"))}${button("Revoke lease", "revoke-lease", idattr(id) + (l.status === "active" ? "" : " disabled"), "danger")}</div>`,
  );
}
function confirmAction(title, description, action) {
  dialog(
    title,
    `<p>${esc(description)}</p><div class="inline-error" id="mutation-error" role="alert"></div><div class="dialog-actions">${button("Cancel", "close")}<button class="button primary" id="confirm-mutation">Confirm</button></div>`,
  );
  $("#confirm-mutation").onclick = async (e) => {
    e.target.disabled = true;
    try {
      await action();
      $("#dialog").close();
      await refresh();
    } catch (error) {
      $("#mutation-error").textContent = error.message;
      e.target.disabled = false;
    }
  };
}
async function mutate(path, method, body, role = "admin") {
  const r = await api(path, role, method, body);
  if (!r.ok) throw Error(reason(r.data));
  return r.data;
}
document.addEventListener("click", (event) => {
  const b = event.target.closest("[data-do]");
  if (!b) return;
  const action = b.dataset.do,
    id = b.dataset.id,
    l = state.data.leases?.items.find((x) => x.id === id);
  if (action === "refresh") refresh();
  if (action === "navigate") navigate(b.dataset.target);
  if (action === "connect") connect();
  if (action === "close") $("#dialog").close();
  if (action === "disconnect") {
    state.admin = "";
    state.bearer = "";
    state.data = {};
    state.errors = {};
    state.result = null;
    state.draft = {};
    $("#dialog").close();
    refresh();
  }
  if (action === "gateway-tab") {
    state.gatewayTab = b.dataset.tab;
    render();
  }
  if (action === "event") showEvent(id);
  if (action === "lease") showLease(id);
  if (action === "issue") leaseForm("issue");
  if (action === "shrink" && l) leaseForm("shrink", l);
  if (action === "delegate" && l) leaseForm("delegate", l);
  if (action.startsWith("try-")) {
    state.requestType = action.slice(4);
    draft()[
      { model: "model", tool: "tool", retrieve: "source" }[state.requestType]
    ] = id;
    state.result = null;
    navigate("requests");
  }
  if (action === "reload")
    confirmAction(
      "Reload server policies",
      "Load the server policy files into the active gateway. This changes the rules used for subsequent requests.",
      async () => {
        await mutate("/admin/policies/reload", "POST");
        toast("Policy bundle reloaded.");
      },
    );
  if (action === "revoke-lease")
    confirmAction(
      "Revoke " + id,
      "Subsequent authorization checks on this instance will reject this lease. This does not stop actions already executing.",
      async () => {
        await mutate("/v1/leases/" + encodeURIComponent(id), "DELETE");
        toast("Lease revoked.");
      },
    );
  if (action === "restore-token")
    confirmAction(
      "Remove credential revocation",
      "Remove this JTI from this instance’s runtime revocation set. Other configured revocation sources still apply.",
      async () => {
        await mutate("/admin/revocations/" + encodeURIComponent(id), "DELETE");
        toast("Runtime revocation removed.");
      },
    );
});
document.addEventListener("input", (event) => {
  if (event.target.closest("#request-form"))
    draft()[event.target.name] = event.target.value;
  if (event.target.id === "decision-search") {
    state.search = event.target.value;
    updateDecisionRows();
  }
});
function updateDecisionRows() {
  const events = state.data.audit?.events || [];
  $("#decision-rows").innerHTML = eventTable(
    events.filter(
      (e) =>
        (state.filter === "all" || e.decision === state.filter) &&
        JSON.stringify(e).toLowerCase().includes(state.search.toLowerCase()),
    ),
  );
}
document.addEventListener("change", (event) => {
  if (event.target.id === "request-type") {
    state.requestType = event.target.value;
    state.result = null;
    render();
  }
  if (event.target.id === "decision-filter") {
    state.filter = event.target.value;
    updateDecisionRows();
  }
});
document.addEventListener("submit", async (event) => {
  const form = event.target;
  event.preventDefault();
  const values = Object.fromEntries(new FormData(form));
  if (form.id === "connect-form") {
    state.admin = values.admin.trim();
    state.bearer = values.bearer.trim().replace(/^Bearer\s+/i, "");
    state.data = {};
    state.errors = {};
    state.result = null;
    $("#dialog").close();
    await refresh();
    return;
  }
  if (form.id === "request-form") {
    const type = state.requestType,
      submit = $('button[type="submit"]', form),
      generation = state.generation;
    let body;
    try {
      body =
        type === "authorize"
          ? values
          : type === "tool"
            ? { tool: values.tool, arguments: JSON.parse(values.arguments) }
            : type === "model"
              ? {
                  model: values.model,
                  messages: [{ role: "user", content: values.prompt }],
                }
              : { source: values.source, query: values.query };
      if (
        type === "tool" &&
        (!body.arguments ||
          typeof body.arguments !== "object" ||
          Array.isArray(body.arguments))
      )
        throw Error("Arguments must be a JSON object.");
    } catch (error) {
      $("#request-error").textContent = error.message;
      return;
    }
    submit.disabled = true;
    submit.textContent = "Sending…";
    $("#request-error").textContent = "";
    const result = await api(paths[type], "bearer", "POST", body);
    if (generation !== state.generation) return;
    state.result = { ...result, type };
    if (
      state.page === "requests" &&
      state.requestType === type &&
      $("#request-result")
    )
      $("#request-result").innerHTML = resultView();
    submit.disabled = false;
    submit.textContent = "Send request ↗";
    toast(
      result.status === 202
        ? "Approval required. Nothing executed."
        : result.ok
          ? "Gateway response received."
          : reason(result.data),
      !result.ok,
    );
    return;
  }
  if (form.id === "lease-form") {
    const submit = $("button", form);
    submit.disabled = true;
    const split = (s) =>
      s
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
    try {
      const actions = split(values.actions),
        resources = split(values.resources);
      if (!actions.length || !resources.length)
        throw Error("At least one action and resource is required.");
      const mode = form.dataset.mode;
      let body = { actions, resources };
      if (mode === "issue") {
        if (state.data.leases?.items.some((l) => l.id === values.id))
          throw Error("A lease with this ID already exists. Choose a new ID.");
        body = {
          ...body,
          id: values.id,
          agent: values.agent,
          task: values.task,
          tenant: values.tenant,
          protected_resources: split(values.protected_resources),
          expires_at: new Date(values.expires_at).toISOString(),
          maximum_impact: "reversible",
        };
        if (new Date(body.expires_at) <= new Date())
          throw Error("Expiry must be in the future.");
      }
      if (mode === "delegate") body.agent = values.agent;
      const path =
        mode === "issue"
          ? "/v1/leases"
          : "/v1/leases/" +
            encodeURIComponent(form.dataset.id) +
            (mode === "delegate" ? "/delegate" : "");
      await mutate(
        path,
        mode === "shrink" ? "PATCH" : "POST",
        body,
        mode === "delegate" ? "bearer" : "admin",
      );
      $("#dialog").close();
      toast("Authority updated.");
      await refresh();
    } catch (error) {
      $(".inline-error", form).textContent = error.message;
      submit.disabled = false;
    }
    return;
  }
  if (form.id === "revoke-token-form")
    confirmAction(
      "Revoke credential",
      "Revoke this credential ID on the current gateway instance.",
      async () => {
        await mutate("/admin/revocations", "POST", { jti: values.jti });
        toast("Credential revoked.");
      },
    );
});
window.addEventListener("hashchange", () => {
  state.page = location.hash.slice(1) || "overview";
  render();
  window.scrollTo(0, 0);
});
// Remove credentials retained by the previous console. New credentials never persist.
try {
  localStorage.removeItem("ap_bearer");
  localStorage.removeItem("ap_admin");
} catch {}
state.page = location.hash.slice(1) || "overview";
render();
refresh();
