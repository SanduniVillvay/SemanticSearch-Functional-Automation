#!/usr/bin/env python3
"""Run benchmark_final.json queries against search API and save actual results."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get(
    "BASE_URL",
    "https://search-api-wurthbaer-qa.search-villvay.workers.dev/search",
)
IN_PATH = Path(__file__).resolve().parent / "benchmark_final.json"
OUT_PATH = Path(__file__).resolve().parent / "benchmark_final_actual_results.json"

PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "24"))
MAX_PAGES_PER_QUERY = int(os.environ.get("MAX_PAGES_PER_QUERY", "200"))
REQUEST_TIMEOUT_S = int(os.environ.get("REQUEST_TIMEOUT_S", "60"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
RETRY_BACKOFF_S = float(os.environ.get("RETRY_BACKOFF_S", "1.5"))
PAGE_DELAY_S = float(os.environ.get("PAGE_DELAY_S", "0.12"))


def extract_titles(payload: dict) -> list[str]:
    results = payload.get("results") if isinstance(payload, dict) else None
    products = results.get("products") if isinstance(results, dict) else None
    if not isinstance(products, list):
        return []

    out: list[str] = []
    for p in products:
        if isinstance(p, str):
            t = p.strip()
        elif isinstance(p, dict):
            t = (
                p.get("productTitle")
                or p.get("primaryProductTitle")
                or p.get("title")
                or p.get("name")
                or ""
            )
            t = str(t).strip()
        else:
            t = ""
        if t:
            out.append(t)
    return out


def fetch_page(query: str, page: int) -> dict:
    params = {
        "page": str(page),
        "query": query,
        "isFilterByBrand": "false",
        "pageSize": str(PAGE_SIZE),
    }
    url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "application/json")
    req.add_header(
        "user-agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    )

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_S * attempt)
            else:
                raise last_err
    raise RuntimeError("unreachable")


def run_one_query(query: str) -> tuple[list[str], dict]:
    all_titles: list[str] = []
    total_pages = 0
    total_results = 0

    for page in range(1, MAX_PAGES_PER_QUERY + 1):
        payload = fetch_page(query, page)
        summary = payload.get("summary") if isinstance(payload, dict) else {}
        total_results = int((summary or {}).get("total") or total_results or 0)
        total_pages = int((summary or {}).get("totalPages") or total_pages or 0)

        page_titles = extract_titles(payload)
        if not page_titles:
            break
        all_titles.extend(page_titles)

        if total_pages and page >= total_pages:
            break
        time.sleep(PAGE_DELAY_S)

    return all_titles, {
        "api_total_results": total_results,
        "api_total_pages": total_pages,
        "titles_returned": len(all_titles),
    }


def main() -> None:
    if not IN_PATH.exists():
        raise SystemExit(f"Missing input file: {IN_PATH}")

    data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("benchmark_final.json must be a JSON array")

    out_rows = []
    total = len(data)
    for idx, row in enumerate(data, start=1):
        query_id = row.get("query_id")
        query = str(row.get("query") or "").strip()
        benchmark_results = row.get("benchmark_results") or []
        number_of_results = int(row.get("number_of_results") or 0)
        if not query:
            continue

        print(f"[{idx}/{total}] query_id={query_id} | {query}", flush=True)

        try:
            actual_results, stats = run_one_query(query)
            out_rows.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "number_of_results": number_of_results,
                    "benchmark_results": benchmark_results,
                    "actual_results": actual_results,
                    "api_stats": stats,
                    "error": "",
                }
            )
        except Exception as e:  # noqa: BLE001
            out_rows.append(
                {
                    "query_id": query_id,
                    "query": query,
                    "number_of_results": number_of_results,
                    "benchmark_results": benchmark_results,
                    "actual_results": [],
                    "api_stats": {},
                    "error": str(e),
                }
            )

    payload = {
        "source_file": IN_PATH.name,
        "api_base": BASE_URL,
        "count": len(out_rows),
        "results": out_rows,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()

