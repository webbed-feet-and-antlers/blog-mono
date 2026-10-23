"""Cheapest-current-model check against the live OpenRouter catalog.

    uv run python -m kg_data models

Policy: prefer the newest cheap flash tier — newer small models beat older
"mini" models on cost and quality. This prints current candidates (recent,
chat-capable, cheap) sorted by prompt price so bumping
`OPENROUTER_MODEL`/config default is a 30-second decision.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CATALOG_URL = "https://openrouter.ai/api/v1/models"
MIN_CREATED = 1735689600  # 2025-01-01 — keep only recent generations
MAX_PROMPT_USD_PER_M = 0.10
EXCLUDE_SUBSTRINGS = ("vision", "image", "audio", "video", "embed",
                      "whisper", "tts", "rerank", "guard", "online",
                      ":free", "-rfc", "batch", "latest", "auto")


def _assert_public_https_url(url: str) -> None:
    """Only https, and only hosts resolving to public addresses."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"refusing non-https URL: {url!r}")
    for info in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"refusing non-public host: {parsed.hostname!r}")


def fetch_candidates(max_price: float = MAX_PROMPT_USD_PER_M,
                     limit: int = 20) -> list[dict]:
    _assert_public_https_url(CATALOG_URL)
    req = urllib.request.Request(
        CATALOG_URL, headers={"User-Agent": "kg-data/0.1"})
    models = json.load(urllib.request.urlopen(req, timeout=30))["data"]
    out = []
    for m in models:
        pid = m["id"]
        if any(k in pid for k in EXCLUDE_SUBSTRINGS):
            continue
        try:
            prompt_m = float(m["pricing"]["prompt"]) * 1e6
            completion_m = float(m["pricing"]["completion"]) * 1e6
        except (KeyError, TypeError, ValueError):
            continue
        if prompt_m <= 0 or prompt_m > max_price:
            continue
        if m.get("created", 0) < MIN_CREATED:
            continue
        out.append({
            "id": pid,
            "prompt_usd_per_m": round(prompt_m, 4),
            "completion_usd_per_m": round(completion_m, 4),
            "created": datetime.fromtimestamp(m["created"],
                                              timezone.utc).date().isoformat(),
            "context": m.get("context_length", 0),
        })
    out.sort(key=lambda r: r["prompt_usd_per_m"])
    return out[:limit]


def render_table(rows: list[dict]) -> str:
    header = (f"{'$/M in':>8} {'$/M out':>8}  {'created':<10} "
              f"{'ctx':>8}  model")
    lines = [header]
    for r in rows:
        lines.append(f"{r['prompt_usd_per_m']:>8.3f} "
                     f"{r['completion_usd_per_m']:>8.3f}  "
                     f"{r['created']:<10} {r['context']:>8}  {r['id']}")
    return "\n".join(lines)
