"""URL → ParsedSource for the asset importer.

Recognises:
  - Civitai model pages (`https://civitai.com/models/<id>`)
  - Civitai image pages (`https://civitai.com/images/<id>` — recipe path)
  - HuggingFace single-file links (`huggingface.co/<repo>/blob/<rev>/<file>.safetensors`)
  - Raw `.safetensors` URLs from any host (subject to SSRF guard at fetch time)

Pulled out of `asset_importer.py` so the URL surface area can be
unit-tested without importing the resolver / downloader / SSRF guard
machinery (analyst's 2026-04-29 review flagged the 1193-LoC monolith).

Pure functions, no I/O. The caller is responsible for running
`_validate_target` before any actual fetch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class ParsedSource:
    """A URL we recognise, decomposed into the bits the resolver needs."""
    kind: str                            # "civitai" | "civitai_image_recipe" | "huggingface" | "raw"
    host: str
    original_url: str
    model_id: int | None = None
    version_id: int | None = None
    file_path: str | None = None         # for HF "<repo>@<rev>/<path>"


_CIVITAI_RE = re.compile(
    r"^https?://(civitai\.(?:com|red))/models/(\d+)(?:/[^?]*)?(\?.*)?$",
    re.IGNORECASE,
)
_CIVITAI_IMAGE_RE = re.compile(
    r"^https?://(civitai\.(?:com|red))/images/(\d+)(\?.*)?$",
    re.IGNORECASE,
)
_HF_FILE_RE = re.compile(
    r"^https?://huggingface\.co/([^/]+/[^/]+)/(?:blob|resolve)/([^/]+)/(.+\.safetensors)(\?.*)?$",
    re.IGNORECASE,
)
_RAW_SAFETENSORS_RE = re.compile(
    r"^https?://[^/]+/.+\.safetensors(\?.*)?$",
    re.IGNORECASE,
)


def parse_url(url: str) -> ParsedSource | None:
    """Best-effort URL → ParsedSource. Returns None on no match.

    Civitai image URLs come back as `kind="civitai_image_recipe"`,
    reusing `model_id` to carry the image_id. The recipe path doesn't
    download the image itself — the user pastes the prompt block
    instead, since Civitai's image API requires auth + nsfw flags.
    """
    if not isinstance(url, str) or not url:
        return None
    url = url.strip()

    m = _CIVITAI_IMAGE_RE.match(url)
    if m:
        return ParsedSource(
            kind="civitai_image_recipe",
            host=m.group(1).lower(),
            original_url=url,
            model_id=int(m.group(2)),    # image_id — reusing field
        )

    m = _CIVITAI_RE.match(url)
    if m:
        host = m.group(1).lower()
        model_id = int(m.group(2))
        version_id: int | None = None
        if m.group(3):
            qs = parse_qs(m.group(3).lstrip("?"))
            v = qs.get("modelVersionId", [None])[0]
            if v and v.isdigit():
                version_id = int(v)
        return ParsedSource(
            kind="civitai", host=host, original_url=url,
            model_id=model_id, version_id=version_id,
        )

    m = _HF_FILE_RE.match(url)
    if m:
        repo, rev, file_path = m.group(1), m.group(2), m.group(3)
        return ParsedSource(
            kind="huggingface", host="huggingface.co",
            original_url=url,
            file_path=f"{repo}@{rev}/{file_path}",
        )

    if _RAW_SAFETENSORS_RE.match(url):
        host = (urlparse(url).hostname or "").lower()
        return ParsedSource(
            kind="raw", host=host, original_url=url,
        )

    return None
