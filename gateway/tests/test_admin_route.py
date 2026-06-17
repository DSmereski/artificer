"""Smoke tests for /admin/* — landing page + nodes table HTML/JS served."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_admin_index_loads(client: TestClient) -> None:
    r = client.get("/admin/")
    assert r.status_code == 200
    assert "Hive Admin" in r.text


def test_admin_nodes_page_loads(client: TestClient) -> None:
    r = client.get("/admin/nodes")
    assert r.status_code == 200
    assert "<table" in r.text
    assert "/admin/nodes.js" in r.text


def test_admin_nodes_js_served(client: TestClient) -> None:
    r = client.get("/admin/nodes.js")
    assert r.status_code == 200
    assert "fetch" in r.text


def test_admin_index_sets_security_headers(client: TestClient) -> None:
    """Defense-in-depth — admin pages declare strict CSP, no framing,
    nosniff, no referrer leakage. Locks the surface even if a future
    edit accidentally lets unsafe content into the static files.
    """
    r = client.get("/admin/")
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_admin_blocks_public_ip_origin() -> None:
    """The /admin/* routes must refuse requests from non-loopback,
    non-private addresses. A misconfigured tailscale exposing the
    gateway publicly should not also expose the admin UI.
    """
    from gateway.routes.admin import _admin_origin_allowed
    # Allow:
    assert _admin_origin_allowed("127.0.0.1")
    assert _admin_origin_allowed("10.0.0.5")
    assert _admin_origin_allowed("192.168.1.50")
    assert _admin_origin_allowed("100.64.1.2")  # tailscale CGNAT
    assert _admin_origin_allowed("::1")
    assert _admin_origin_allowed("testclient")  # TestClient default
    # Block:
    assert not _admin_origin_allowed("8.8.8.8")
    assert not _admin_origin_allowed("203.0.113.5")
    assert not _admin_origin_allowed("not-an-ip")


def test_admin_nodes_js_does_not_interpolate_node_fields_into_innerhtml(
    client: TestClient,
) -> None:
    """Regression: a malicious node name like `<script>` must not be
    rendered as live markup in the admin page. The renderer must use
    DOM construction (createElement + textContent), not innerHTML
    template-literal interpolation of API data.
    """
    r = client.get("/admin/nodes.js")
    assert r.status_code == 200
    js = r.text
    assert "createElement" in js
    assert "textContent" in js
    # Node-supplied fields must not appear inside backtick template
    # interpolation that targets innerHTML.
    for unsafe in (
        "innerHTML = `${n.name",
        "innerHTML = `${n.agent_version",
        "innerHTML = `${n.labels",
        "${n.name}</td>",
        "${n.agent_version",
        "${n.labels",
    ):
        assert unsafe not in js, f"unsafe interpolation found: {unsafe!r}"
