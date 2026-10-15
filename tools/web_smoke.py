"""Opt-in public-network smoke; separate from deterministic pytest tests."""

import argparse
import asyncio
import json

from use_lllm.web.pages import PageReader
from use_lllm.web.search import SearchService


async def run(query, url):
    search = SearchService()
    try:
        result = await search.search(query)
        page = await PageReader().read_page(url)
        value = dict(page.payload)
        value["text"] = value.get("text", "")[:800]
        print(json.dumps({"search": result.payload, "page": value}, ensure_ascii=False, indent=2))
        return int(result.is_error or page.is_error)
    finally:
        search.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.query, args.url)))
