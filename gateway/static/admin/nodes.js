// Minimal nodes admin page. Auth via Bearer token entered into a prompt
// (Phase 1 simplification — Phase 2+ will use a session cookie minted
// from the owner device pairing).

const TOKEN_KEY = "hive_admin_token";

function getToken() {
  let t = sessionStorage.getItem(TOKEN_KEY);
  if (!t) {
    t = prompt("Owner Bearer token (paste from your paired device):");
    if (t) sessionStorage.setItem(TOKEN_KEY, t.trim());
  }
  return t;
}

async function api(method, path, body) {
  const token = getToken();
  const resp = await fetch(path, {
    method,
    headers: {
      "Authorization": "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401) {
    sessionStorage.removeItem(TOKEN_KEY);
    throw new Error("unauthorized");
  }
  if (!resp.ok) throw new Error(`${method} ${path} -> ${resp.status}`);
  return resp.status === 204 ? null : resp.json();
}

function fmtAge(ts) {
  const s = Math.floor(Date.now() / 1000 - ts);
  if (s < 60) return s + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

// XSS-safe: every cell is built with createElement + textContent so
// node-supplied strings (name, agent_version, labels, id) cannot inject
// markup into the admin page even if a compute node is compromised.
function td(text, className) {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function statusAllowed(s) {
  return s === "online" || s === "offline" ? s : "";
}

async function refresh() {
  const tbody = document.getElementById("nodes-tbody");
  const nodes = await api("GET", "/v1/nodes");
  const frag = document.createDocumentFragment();
  if (nodes.length === 0) {
    const tr = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 6;
    cell.style.opacity = "0.6";
    cell.textContent = `No nodes paired yet. Click "Add node".`;
    tr.appendChild(cell);
    frag.appendChild(tr);
  } else {
    for (const n of nodes) {
      const tr = document.createElement("tr");
      tr.appendChild(td(n.name));
      tr.appendChild(td(n.status, statusAllowed(n.status)));
      tr.appendChild(td(n.agent_version || "—"));
      tr.appendChild(td((n.labels || []).join(", ")));
      tr.appendChild(td(fmtAge(n.last_seen)));
      const actions = document.createElement("td");
      const btn = document.createElement("button");
      btn.className = "del";
      btn.textContent = "remove";
      btn.dataset.id = n.id;
      btn.onclick = async () => {
        if (!confirm(`Remove node ${btn.dataset.id}?`)) return;
        await api("DELETE", `/v1/nodes/${encodeURIComponent(btn.dataset.id)}`);
        refresh();
      };
      actions.appendChild(btn);
      tr.appendChild(actions);
      frag.appendChild(tr);
    }
  }
  tbody.replaceChildren(frag);
}

async function addNode() {
  const inv = await api("POST", "/v1/invites");
  const el = document.getElementById("invite");
  el.style.display = "block";
  el.replaceChildren();
  const heading = document.createElement("div");
  heading.textContent = "Invite code: ";
  const strong = document.createElement("strong");
  strong.textContent = inv.code;
  heading.appendChild(strong);
  heading.appendChild(document.createTextNode(
    ` (expires in ${inv.expires_in_seconds}s)`
  ));
  el.appendChild(heading);
  el.appendChild(document.createTextNode("Run on the new machine:"));
  el.appendChild(document.createElement("br"));
  const code = document.createElement("code");
  code.textContent =
    `python -m hive_node_agent --host <this-host> --code ${inv.code}`;
  el.appendChild(code);
}

document.getElementById("refreshBtn").onclick = refresh;
document.getElementById("addNodeBtn").onclick = addNode;
refresh();
setInterval(refresh, 5000);
