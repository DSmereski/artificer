// Hive jobs admin page. Auth via the same Bearer-token-in-sessionStorage
// pattern as nodes.js.

const TOKEN_KEY = "hive_admin_token";

function getToken() {
  let t = sessionStorage.getItem(TOKEN_KEY);
  if (!t) {
    t = prompt("Owner Bearer token (paste from your paired device):");
    if (t) sessionStorage.setItem(TOKEN_KEY, t.trim());
  }
  return t;
}

async function api(method, path) {
  const token = getToken();
  const resp = await fetch(path, {
    method,
    headers: { "Authorization": "Bearer " + token },
  });
  if (resp.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    throw new Error("unauthorized");
  }
  if (!resp.ok) throw new Error(`${method} ${path} -> ${resp.status}`);
  return resp.status === 204 ? null : resp.json();
}

function fmtAge(ts) {
  if (!ts) return "—";
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function fmtMs(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return ms + "ms";
  return (ms / 1000).toFixed(1) + "s";
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function buildQuery() {
  const params = new URLSearchParams();
  const status = document.getElementById("f-status").value.trim();
  const kind = document.getElementById("f-kind").value.trim();
  const node = document.getElementById("f-node").value.trim();
  if (status) params.set("status", status);
  if (kind) params.set("kind", kind);
  if (node) params.set("node_id", node);
  params.set("limit", "200");
  const q = params.toString();
  return q ? "?" + q : "";
}

const VALID_STATUSES = new Set(["queued", "dispatched", "done", "error", "failed"]);

async function refresh() {
  const tbody = document.getElementById("jobs-tbody");
  const jobs = await api("GET", "/v1/jobs" + buildQuery());
  const frag = document.createDocumentFragment();
  if (!jobs.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="8" style="opacity:.6">No jobs match.</td>`;
    frag.appendChild(tr);
  } else {
    for (const j of jobs) {
      const statusClass = VALID_STATUSES.has(j.status) ? j.status : "unknown";
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="id">${escapeHtml(j.id)}</td>
        <td class="kind">${escapeHtml(j.kind)}</td>
        <td class="${statusClass}">${escapeHtml(j.status)}</td>
        <td>${j.attempts}/${j.max_attempts}</td>
        <td>${escapeHtml(j.node_id || "—")}</td>
        <td>${fmtMs(j.duration_ms)}</td>
        <td>${fmtAge(j.created)}</td>
        <td>${escapeHtml(j.error || "")}</td>
      `;
      frag.appendChild(tr);
    }
  }
  tbody.replaceChildren(frag);
}

document.getElementById("refreshBtn").onclick = refresh;
for (const id of ["f-status", "f-kind", "f-node"]) {
  document.getElementById(id).addEventListener("change", refresh);
}
refresh();
setInterval(refresh, 5000);
