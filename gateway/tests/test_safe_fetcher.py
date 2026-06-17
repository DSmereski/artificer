"""Tests for the M4.1 SSRF-guarded fetcher."""

from __future__ import annotations

import pytest

from gateway.safe_fetcher import _scrub_html, validate_url


# ---------------------------------------------------------------- url validation


def test_validate_blocks_localhost():
    assert validate_url("http://localhost/admin") is not None
    assert validate_url("https://127.0.0.1") is not None
    assert validate_url("http://[::1]/") is not None


def test_validate_blocks_private_ranges():
    assert validate_url("http://10.0.0.5/") is not None
    assert validate_url("http://192.168.1.1/") is not None
    assert validate_url("http://172.16.0.1/") is not None
    assert validate_url("http://169.254.169.254/") is not None  # AWS metadata


def test_validate_blocks_internal_suffixes():
    assert validate_url("http://foo.local/") is not None
    assert validate_url("http://bar.internal/") is not None
    assert validate_url("http://baz.lan/") is not None


def test_validate_blocks_non_http_schemes():
    assert validate_url("file:///etc/passwd") is not None
    assert validate_url("gopher://example.com/") is not None
    assert validate_url("javascript:alert(1)") is not None
    assert validate_url("data:text/html,<script>") is not None
    assert validate_url("ftp://example.com/") is not None


def test_validate_rejects_empty_or_garbage():
    assert validate_url("") is not None
    assert validate_url("not-a-url") is not None


def test_validate_accepts_public_url():
    # Real DNS resolution; we don't actually fetch.
    assert validate_url("https://example.com/") is None


# ---------------------------------------------------------------- scrubber


def test_scrub_removes_scripts_and_styles():
    raw = """
        <html>
        <head><style>body{color:red}</style></head>
        <body>
            <p>Visible text.</p>
            <script>alert('pwn')</script>
            <iframe src="evil"></iframe>
            <p>More text.</p>
        </body>
        </html>
    """
    out = _scrub_html(raw)
    assert "Visible text." in out
    assert "More text." in out
    assert "alert" not in out
    assert "evil" not in out
    assert "color:red" not in out


def test_scrub_decodes_entities():
    out = _scrub_html("<p>5 &lt; 7 &amp; that&#39;s good</p>")
    assert "5 < 7 & that's good" in out


def test_scrub_handles_empty():
    assert _scrub_html("") == ""
    assert _scrub_html(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------- spa router


def test_is_spa_host():
    from gateway.safe_fetcher import _is_spa_host
    assert _is_spa_host("robertsspaceindustries.com") is True
    assert _is_spa_host("starcitizen.fandom.com") is True
    assert _is_spa_host("CIVITAI.COM") is True
    assert _is_spa_host("m.fandom.com") is True
    assert _is_spa_host("example.com") is False


@pytest.mark.asyncio
async def test_safe_fetch_smart_skips_js_for_non_spa(monkeypatch):
    """Non-SPA hosts use only the httpx path (no Playwright cost)."""
    import gateway.safe_fetcher as sf

    fake_fr = sf.FetchResult(
        url_final="https://example.com/x", title="t", text="ok",
        status=200, fetched_at=0.0,
    )
    js_called = {"n": 0}

    async def fake_safe_fetch(url):
        return fake_fr

    async def fake_safe_fetch_js(url):
        js_called["n"] += 1
        return None

    monkeypatch.setattr(sf, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(sf, "safe_fetch_js", fake_safe_fetch_js)

    out = await sf.safe_fetch_smart("https://example.com/x")
    assert out is fake_fr
    assert js_called["n"] == 0


@pytest.mark.asyncio
async def test_safe_fetch_smart_retries_js_for_spa_thin_body(monkeypatch):
    """Known SPA host returning thin body falls through to JS fetcher."""
    import gateway.safe_fetcher as sf

    thin_fr = sf.FetchResult(
        url_final="https://robertsspaceindustries.com/x",
        title="rsi", text="x" * 50, status=200, fetched_at=0.0,
    )
    js_fr = sf.FetchResult(
        url_final="https://robertsspaceindustries.com/x",
        title="rsi-js", text="A" * 5000, status=200, fetched_at=0.0,
    )

    async def fake_safe_fetch(url):
        return thin_fr

    async def fake_safe_fetch_js(url):
        return js_fr

    monkeypatch.setattr(sf, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(sf, "safe_fetch_js", fake_safe_fetch_js)

    out = await sf.safe_fetch_smart("https://robertsspaceindustries.com/x")
    assert out is js_fr
    assert len(out.text) == 5000


@pytest.mark.asyncio
async def test_safe_fetch_smart_keeps_thin_when_js_fails(monkeypatch):
    """If Playwright also can't render the page, return the thin httpx
    body rather than None (better than nothing)."""
    import gateway.safe_fetcher as sf

    thin_fr = sf.FetchResult(
        url_final="https://fandom.com/x",
        title="fand", text="tiny", status=200, fetched_at=0.0,
    )

    async def fake_safe_fetch(url):
        return thin_fr

    async def fake_safe_fetch_js(url):
        return None

    monkeypatch.setattr(sf, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(sf, "safe_fetch_js", fake_safe_fetch_js)

    out = await sf.safe_fetch_smart("https://fandom.com/x")
    assert out is thin_fr


@pytest.mark.asyncio
async def test_safe_fetch_smart_skips_js_when_spa_returns_real_body(monkeypatch):
    """SPA host that DID get a real body via httpx — don't waste a
    headless render."""
    import gateway.safe_fetcher as sf

    fat_fr = sf.FetchResult(
        url_final="https://starcitizen.fandom.com/x",
        title="t", text="A" * 2000, status=200, fetched_at=0.0,
    )
    js_called = {"n": 0}

    async def fake_safe_fetch(url):
        return fat_fr

    async def fake_safe_fetch_js(url):
        js_called["n"] += 1
        return None

    monkeypatch.setattr(sf, "safe_fetch", fake_safe_fetch)
    monkeypatch.setattr(sf, "safe_fetch_js", fake_safe_fetch_js)

    out = await sf.safe_fetch_smart("https://starcitizen.fandom.com/x")
    assert out is fat_fr
    assert js_called["n"] == 0
