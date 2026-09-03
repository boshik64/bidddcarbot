#!/usr/bin/env python3
"""Разведка bid.cars: скачать JSON фильтра и сохранить в файл.

Пример:
  python explore.py "https://bid.cars/en/search/results?search-type=filters&status=All&type=Automobile&make=Toyota&model=Camry&year-from=2018&year-to=2026&auction-type=All"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "explore-dummy-token")

from app.client import ParseError, client
from app.parser import filter_url_to_api_url, parse_search_json, validate_filter_url


async def run(url: str, out: Path) -> int:
    try:
        canonical = validate_filter_url(url)
    except Exception as exc:
        print(f"URL error: {exc}", file=sys.stderr)
        return 1
    api_url = filter_url_to_api_url(canonical, page=1)
    print(f"filter: {canonical}")
    print(f"api:    {api_url}")
    try:
        payload = await client._get_json(api_url, referer=canonical)
    except ParseError as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        return 2
    finally:
        await client.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lots, meta = parse_search_json(payload, canonical)
    print(f"saved {out} ({out.stat().st_size} bytes)")
    print(f"lots on page: {len(lots)}")
    print(f"last_page={meta.get('last_page')} total={meta.get('total')} next={meta.get('next_page_url')}")
    for lot in lots[:5]:
        print(f"  {lot.lot_external_id}  {lot.title}  {lot.current_bid}  {lot.damage}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch one bid.cars filter page as JSON")
    parser.add_argument("url", help="Filter URL from bid.cars search results")
    parser.add_argument(
        "-o",
        "--out",
        default="data/explore_response.json",
        help="Where to save the raw JSON",
    )
    args = parser.parse_args()

    import asyncio

    raise SystemExit(asyncio.run(run(args.url, Path(args.out))))


if __name__ == "__main__":
    main()
