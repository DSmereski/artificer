"""Tiny helper for one-shot smoke scripts that need a paired device.

Use as a context manager so the device is auto-revoked on exit (success
or failure) — keeps the user's paired-device list short and prevents
half-finished tests from leaving zombie tokens around.

  from smoke_lib import paired_smoke_device

  with paired_smoke_device('vault-smoke') as (token, device_id, http):
      r = http.get('/v1/vault/stats')
      ...
"""

from __future__ import annotations

import contextlib
import json
import urllib.request
import urllib.error
from typing import Iterator


class _Http:
    """Minimal Bearer-auth HTTP client. Dodges the requests dep so this
    helper imports clean from anywhere in the repo."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _req(self, method: str, path: str, body: dict | None = None,
             timeout: float = 30.0) -> tuple[int, str]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def get(self, path: str, timeout: float = 30.0) -> tuple[int, str]:
        return self._req("GET", path, timeout=timeout)

    def post(self, path: str, body: dict, timeout: float = 30.0) -> tuple[int, str]:
        return self._req("POST", path, body, timeout=timeout)

    def delete(self, path: str, timeout: float = 30.0) -> tuple[int, str]:
        return self._req("DELETE", path, timeout=timeout)


def _pair(base_url: str, name: str, platform: str = "smoke") -> tuple[str, str]:
    """Pair a fresh device. Returns (device_id, token)."""
    code_status, code_body = _open(f"{base_url}/v1/pair/new")
    if code_status >= 300:
        raise RuntimeError(f"pair/new failed: {code_status} {code_body}")
    code = json.loads(code_body)["code"]
    pair_status, pair_body = _open(
        f"{base_url}/v1/pair", method="POST",
        body={"code": code, "name": name, "platform": platform},
    )
    if pair_status >= 300:
        raise RuntimeError(f"pair claim failed: {pair_status} {pair_body}")
    data = json.loads(pair_body)
    return data["device_id"], data["token"]


def _open(url: str, *, method: str = "GET", body: dict | None = None) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


@contextlib.contextmanager
def paired_smoke_device(
    name: str, *, base_url: str = "http://127.0.0.1:8766",
) -> Iterator[tuple[str, str, _Http]]:
    """Pair a fresh test device and revoke it on exit. Use as
    `with paired_smoke_device('my-smoke') as (token, device_id, http):`.

    The unpair runs even on exception so a failed smoke doesn't leak a
    zombie pairing into the user's device list.
    """
    device_id, token = _pair(base_url, name)
    http = _Http(base_url, token)
    try:
        yield token, device_id, http
    finally:
        # Best-effort cleanup. If the gateway's already gone, nothing
        # we can do here — but we tried.
        try:
            http.delete(f"/v1/devices/{device_id}")
        except Exception:  # noqa: BLE001
            pass
