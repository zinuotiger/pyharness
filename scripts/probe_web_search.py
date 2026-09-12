"""Real no-key web.search probe (DuckDuckGo HTML backend)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyharness.core.tool_web import BingRssBackend, web_search


async def main() -> int:
    ctx = SimpleNamespace(
        counters={},
        config={"security": {"network": {
            "web_search_per_session": 20,
            "search_backend": "bing"}},
            "loop": {"content": {"search_result_chars": 8000}}},
        get_secret=lambda name: None,
        search_backend=BingRssBackend())
    out = await web_search({"query": "Python asyncio", "top_k": 3}, ctx)
    hits = out.get("results") or []
    print(f"hits={len(hits)}")
    if hits:
        print("first_url=", hits[0].get("url"))
    print("WEB_SEARCH_REAL_PASS" if hits else "WEB_SEARCH_REAL_FAIL")
    return 0 if hits else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
