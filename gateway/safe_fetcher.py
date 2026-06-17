"""SSRF-guarded HTTP fetcher (M4.1).

Used by the M4.3 research pipeline. Designed so that even an LLM
emitting an attacker-controlled URL can't reach internal services or
DNS-rebinding-trick its way to localhost.

Defences:
  - http(s) only; reject other schemes
  - Resolve DNS BEFORE the request and reject any A/AAAA in private,
    loopback, link-local, or reserved ranges
  - Re-validate after every redirect (DNS rebinding defence)
  - httpx with a redirect cap of 2 + 5s total timeout + 1MB body limit
  - Content-Type whitelist: text/html, text/plain, application/xhtml+xml
  - Strip <script|style|noscript|iframe|object|embed> + entity-decode
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse

import httpx

log = logging.getLogger("gateway.safe_fetcher")


@dataclass(frozen=True)
class FetchResult:
    url_final: str
    title: str
    text: str
    status: int
    fetched_at: float


_BLOCKED_SCHEMES = re.compile(r"^(file|gopher|ftp|data|javascript|jar):", re.I)
_DENY_HOSTNAMES = (".local", ".internal", ".lan")
_ALLOWED_CONTENT_TYPES = (
    "text/html", "text/plain", "application/xhtml+xml",
)
_MAX_BYTES = 1_000_000          # 1 MB
_TIMEOUT_S = 5.0
_REDIRECT_CAP = 2

_TAG_STRIP = re.compile(
    r"<(script|style|noscript|iframe|object|embed)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_ANY = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


# ---------------------------------------------------------------- url validation


def _is_blocked_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    # Explicitly catch IPv4-mapped IPv6 (`::ffff:169.254.169.254`) —
    # `ip.is_link_local` returns False on the v6 form on some Python
    # builds, so unwrap to the v4 representation before checking.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_loopback or ip.is_private or ip.is_reserved
        or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    )


def _normalise_host(host: str) -> str:
    """Lowercase + IDNA-encode so suffix denylists work against
    Unicode lookalike hostnames (e.g. cyrillic 'еxample.com' which
    looks identical to 'example.com'). Falls back to the raw host on
    encoding failure (which lets the IP-resolution path catch it)."""
    h = host.lower().rstrip(".")
    try:
        h = h.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        pass
    return h


def _hostname_blocked(host: str) -> bool:
    h = _normalise_host(host)
    if h in ("localhost",):
        return True
    return any(h.endswith(suf) for suf in _DENY_HOSTNAMES)


def _resolve_host(host: str) -> list[str]:
    """Return all addresses the host resolves to, or [] on failure."""
    try:
        info = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return list({i[4][0] for i in info})


def validate_url(url: str) -> str | None:
    """Return None if the URL is safe to fetch, else a reason string."""
    if not url or not isinstance(url, str):
        return "empty url"
    if _BLOCKED_SCHEMES.match(url):
        return "blocked scheme"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"unsupported scheme: {parsed.scheme!r}"
    host = parsed.hostname or ""
    if not host:
        return "missing host"
    if _hostname_blocked(host):
        return f"blocked host: {host}"
    # If the user hands us a literal IP, validate it directly.
    try:
        ipaddress.ip_address(host)
        if _is_blocked_ip(host):
            return f"private/loopback IP: {host}"
    except ValueError:
        # Hostname → DNS-resolve and validate every record.
        addrs = _resolve_host(_normalise_host(host))
        if not addrs:
            return f"DNS lookup failed: {host}"
        for a in addrs:
            if _is_blocked_ip(a):
                return f"resolves to blocked IP: {host} -> {a}"
    return None


# ---------------------------------------------------------------- html scrubber


def _scrub_html(html: str) -> str:
    """Strip dangerous tags + collapse to plain text."""
    if not html:
        return ""
    body = _TAG_STRIP.sub("", html)
    body = _TAG_ANY.sub(" ", body)
    body = unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    return body


def _extract_title(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    if not m:
        return ""
    return unescape(m.group(1)).strip()[:240]


# ---------------------------------------------------------------- fetcher


async def safe_fetch(
    url: str,
    *,
    max_bytes: int = _MAX_BYTES,
    timeout_s: float = _TIMEOUT_S,
    redirect_cap: int = _REDIRECT_CAP,
) -> FetchResult | None:
    """Fetch a URL with full SSRF protection. Returns None on any failure."""
    reason = validate_url(url)
    if reason:
        log.info("safe_fetch refused %s: %s", url, reason)
        return None

    visited: set[str] = set()
    current = url
    hops = 0
    async with httpx.AsyncClient(
        timeout=timeout_s, follow_redirects=False,
        headers={"User-Agent": "ai-team-research/0.1"},
    ) as http:
        while True:
            if current in visited:
                log.info("safe_fetch redirect loop on %s", current)
                return None
            visited.add(current)
            r2 = validate_url(current)
            if r2 is not None:
                log.info("safe_fetch refused redirect to %s: %s", current, r2)
                return None
            try:
                resp = await http.get(current)
            except httpx.HTTPError as e:
                log.info("safe_fetch transport error on %s: %s", current, e)
                return None

            if resp.is_redirect:
                hops += 1
                if hops > redirect_cap:
                    log.info("safe_fetch redirect cap hit on %s", url)
                    return None
                next_url = resp.headers.get("location")
                if not next_url:
                    return None
                # Resolve relative redirects.
                if next_url.startswith("/"):
                    parsed = urlparse(current)
                    next_url = f"{parsed.scheme}://{parsed.netloc}{next_url}"
                current = next_url
                continue

            # Content-type filter.
            ct = (resp.headers.get("content-type") or "").lower()
            if not any(ct.startswith(t) for t in _ALLOWED_CONTENT_TYPES):
                log.info("safe_fetch wrong content-type %r on %s", ct, current)
                return None

            # Size-bounded body read.
            body = resp.content
            if len(body) > max_bytes:
                body = body[:max_bytes]
            try:
                html = body.decode(resp.encoding or "utf-8", errors="replace")
            except (LookupError, ValueError):
                html = body.decode("utf-8", errors="replace")

            text = _scrub_html(html)
            return FetchResult(
                url_final=current,
                title=_extract_title(html),
                text=text,
                status=resp.status_code,
                fetched_at=time.time(),
            )


# ---------------------------------------------------------------- JS-rendering fetcher


# Hosts that consistently return <1 KB of body to a static httpx fetch
# because they're React/Vue/Next SPAs. Listed by registered domain so
# subdomains (m.fandom.com, *.starcitizen.tools) match.
_SPA_HOSTS = (
    "robertsspaceindustries.com",
    "fandom.com",
    "starcitizen.fandom.com",
    "starcitizen-ships.com",
    "civitai.com",
    "civitaiarchive.com",       # actual domain — no hyphen (matches asset_importer.py)
)
# Min body length that we treat as "real content". Below this on a
# known SPA host, we retry through Playwright.
_SPA_RETRY_THRESHOLD = 800
_PLAYWRIGHT_TIMEOUT_S = 25.0


def _is_spa_host(host: str) -> bool:
    h = (host or "").lower()
    return any(h == s or h.endswith("." + s) for s in _SPA_HOSTS)


async def safe_fetch_js(
    url: str, max_bytes: int = _MAX_BYTES, timeout_s: float = _PLAYWRIGHT_TIMEOUT_S,
) -> FetchResult | None:
    """Render `url` in headless Chromium and return the post-JS text.

    Returns None when:
      - playwright isn't installed
      - chromium isn't downloaded
      - any SSRF gate (same checks as safe_fetch) fails
      - the page errors / times out
    """
    reason = validate_url(url)
    if reason:
        log.info("safe_fetch_js blocked: %s", reason)
        return None
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        log.warning("safe_fetch_js: playwright not installed")
        return None
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            try:
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 1800},
                    java_script_enabled=True,
                )
                page = await ctx.new_page()
                # Block heavy assets to keep this lightweight, AND
                # SSRF-gate every other request the page makes —
                # otherwise the page's JS could fetch internal
                # services (vault_writer, ollama, gitea, cloud
                # metadata) and exfiltrate the response via innerText.
                async def _route(route):  # noqa: ANN001
                    rt = route.request.resource_type
                    if rt in ("image", "media", "font", "stylesheet"):
                        await route.abort()
                        return
                    req_url = route.request.url
                    block = validate_url(req_url)
                    if block:
                        log.info(
                            "safe_fetch_js sub-request blocked: %s — %s",
                            req_url[:120], block,
                        )
                        await route.abort()
                        return
                    await route.continue_()
                await page.route("**/*", _route)
                resp = await page.goto(
                    url, wait_until="domcontentloaded",
                    timeout=int(timeout_s * 1000),
                )
                if resp is None:
                    return None
                # Re-validate the post-redirect URL so a JS-driven redirect
                # to a private IP can't sneak past the SSRF gate.
                final_url = page.url
                final_reason = validate_url(final_url)
                if final_reason:
                    log.info("safe_fetch_js post-redirect blocked: %s", final_reason)
                    return None
                # Wait briefly for client-rendered content to appear.
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=4000,
                    )
                except Exception:  # noqa: BLE001
                    pass
                title = await page.title()
                # Extract main text via the body's innerText, which is
                # post-render and skips scripts/styles for free.
                body_text = await page.evaluate(
                    "() => document.body ? document.body.innerText : ''",
                )
                if not isinstance(body_text, str):
                    body_text = ""
                if len(body_text) > max_bytes:
                    body_text = body_text[:max_bytes]
                # Light scrub: collapse whitespace.
                text = re.sub(r"[ \t]+", " ", body_text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                return FetchResult(
                    url_final=final_url,
                    title=title or "",
                    text=text,
                    status=resp.status,
                    fetched_at=time.time(),
                )
            finally:
                await browser.close()
    except Exception as e:  # noqa: BLE001
        log.warning("safe_fetch_js failed for %s: %s", url, e)
        return None


async def safe_fetch_smart(url: str) -> FetchResult | None:
    """httpx first; if the host is a known SPA AND the body is too thin,
    retry with Playwright. Always falls back to None on hard failures.
    """
    fr = await safe_fetch(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _is_spa_host(host):
        return fr
    body_len = len(fr.text) if fr is not None else 0
    if body_len >= _SPA_RETRY_THRESHOLD:
        return fr
    log.info(
        "safe_fetch_smart: SPA host %r returned %d chars — retrying via JS",
        host, body_len,
    )
    js_fr = await safe_fetch_js(url)
    if js_fr is not None and len(js_fr.text) > body_len:
        return js_fr
    # JS render didn't help — return whatever httpx got, even if thin.
    return fr
